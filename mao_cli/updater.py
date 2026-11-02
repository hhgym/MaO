"""Hinweise zur Installation und Aktualisierung von MaO mit ``uv``.

MaO ersetzt sich nicht selbst im laufenden Prozess: Unter Windows lässt sich die laufende ``mao.exe`` nicht
überschreiben. ``mao update`` erkennt daher nur, wie MaO installiert ist, ermittelt
bei Bedarf die neueste Version auf GitHub und nennt den passenden ``uv``-Befehl.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Callable, Sequence

from packaging.version import InvalidVersion, Version

from . import __version__
from .netutil import CERTIFICATE_HINT, is_certificate_error, ssl_context
from .workspaces import user_state_dir

DEFAULT_REPOSITORY = "hhgym/MaO"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
CHECK_INTERVAL = timedelta(hours=24)
NOTICE_ENV = "MAO_NO_UPDATE_CHECK"
CHANNELS = ("stable", "rc", "develop")


class UpdateError(RuntimeError):
    """Update-Hinweis konnte nicht ermittelt werden."""


@dataclass(frozen=True)
class Installation:
    kind: str          # "uv-tool", "editable", "pip" oder "unbekannt"
    version: str
    detail: str = ""
    requested: str = ""   # bei der Installation angegebener Tag/Branch (z. B. v0.6.0, develop)
    commit: str = ""


def repository_name(value: str | None) -> str:
    result = value or os.environ.get("MAO_GITHUB_REPO") or DEFAULT_REPOSITORY
    if not REPO_RE.fullmatch(result) or ".." in result:
        raise UpdateError("Ungültiges GitHub-Repository (OWNER/REPO)")
    return result


def detect_installation() -> Installation:
    """Erkennt Editable-Quellordner, ``uv tool`` und gewöhnliche pip-Installation (PEP 610)."""
    detail = ""
    kind = "unbekannt"
    requested = commit = ""
    try:
        dist = metadata.distribution("mao-cli")
        raw = dist.read_text("direct_url.json")
        info = json.loads(raw) if raw else {}
        if info.get("dir_info", {}).get("editable"):
            kind = "editable"
            detail = str(info.get("url", ""))
        elif "vcs_info" in info:
            commit = str(info["vcs_info"].get("commit_id", ""))
            requested = str(info["vcs_info"].get("requested_revision", ""))
            detail = f"{info.get('url', '')}@{commit[:12]}"
        else:
            detail = str(info.get("url", ""))
        if kind != "editable":
            tools = ("uv", "tools")
            parts = [p.lower() for p in Path(sys.prefix).parts]
            kind = "uv-tool" if any(parts[i:i + 2] == list(tools) for i in range(len(parts) - 1)) else "pip"
    except (metadata.PackageNotFoundError, ValueError, OSError):
        pass
    return Installation(kind, __version__, detail, requested, commit)


def _fetch_tags(repository: str, timeout: int = 10) -> list[str]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/tags?per_page=100",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "mao-cli"})  # anonym, ohne Token
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:  # noqa: S310 - feste https-URL
            data = json.loads(response.read(2_000_000))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        hint = f" {CERTIFICATE_HINT}" if is_certificate_error(exc) else ""
        raise UpdateError(f"GitHub nicht erreichbar ({exc}); Version mit --version angeben.{hint}") from exc
    return [item["name"] for item in data if isinstance(item, dict) and isinstance(item.get("name"), str)]


def latest_tag(repository: str, channel: str, *, tags: Sequence[str] | None = None) -> str:
    """Neuester Tag ``vX.Y.Z`` (stable) bzw. inklusive Release Candidates (rc)."""
    best: tuple[Version, str] | None = None
    for tag in (tags if tags is not None else _fetch_tags(repository)):
        if not tag.startswith("v"):
            continue
        try:
            version = Version(tag[1:])
        except InvalidVersion:
            continue
        if version.is_devrelease or version.is_postrelease or version.local is not None:
            continue
        if version.is_prerelease and (channel == "stable" or version.pre is None or version.pre[0] != "rc"):
            continue
        if best is None or version > best[0]:
            best = (version, tag)
    if best is None:
        raise UpdateError(f"Kein Release im Kanal '{channel}' gefunden; Version mit --version angeben")
    return best[1]


def install_command(repository: str, ref: str) -> str:
    return f"uv tool install --force --reinstall git+https://github.com/{repository}@{ref}"


@dataclass(frozen=True)
class UpdateTarget:
    lines: list[str]
    repository: str
    ref: str | None          # None: bereits aktuell
    installation: Installation
    argv: list[str] = field(default_factory=list)


def plan_update(channel: str, repository: str | None, version: str | None) -> UpdateTarget:
    if channel not in CHANNELS:
        raise UpdateError(f"Kanal muss einer von {', '.join(CHANNELS)} sein")
    repo = repository_name(repository)
    installation = detect_installation()
    lines = [f"Installiert: MaO {installation.version} ({installation.kind}"
             + (f", {installation.detail}" if installation.detail else "") + ")"]
    if version:
        ref = version if version.startswith("v") else f"v{version}"
    elif channel == "develop":
        ref = "develop"
    else:
        ref = latest_tag(repo, channel)
        if ref.lstrip("v") == installation.version:
            lines.append(f"Bereits aktuell (Kanal {channel}: {ref}).")
            return UpdateTarget(lines, repo, None, installation)
    if not REF_RE.fullmatch(ref):
        raise UpdateError("Ungültige Versionsangabe")
    lines.append(f"Ziel: {ref} ({repo})")
    if installation.kind == "editable":
        lines.append("Das ist eine Quellinstallation (pip install -e). Zum Wechsel auf uv zuerst "
                     "'python -m pip uninstall mao-cli', dann:")
    else:
        lines.append("In einem Terminal ausführen (oder 'mao update --run' verwenden):")
    lines.append("  " + install_command(repo, ref))
    lines.append("Konfiguration und Jahrgangsordner bleiben unberührt. uv fehlt? "
                 "Windows: winget install astral-sh.uv · Linux/macOS: curl -LsSf https://astral.sh/uv/install.sh | sh")
    return UpdateTarget(lines, repo, ref, installation)


def update_hint(channel: str, repository: str | None, version: str | None) -> list[str]:
    return plan_update(channel, repository, version).lines


def windows_update_script(pid: int, uv: str, spec: str) -> str:
    """PowerShell-Text: wartet auf das Ende von mao.exe, installiert, zeigt das Ergebnis."""
    quoted_uv = uv.replace("'", "''")
    quoted_spec = spec.replace("'", "''")
    return (
        "Write-Host 'MaO wird aktualisiert ...'; "
        f"Wait-Process -Id {int(pid)} -ErrorAction SilentlyContinue; "
        "Start-Sleep -Seconds 2; "
        f"& '{quoted_uv}' tool install --force --reinstall '{quoted_spec}'; "
        "if ($LASTEXITCODE -eq 0) { Write-Host 'MaO wurde aktualisiert (mao --version zeigt den neuen Stand).' } "
        "else { Write-Host ('Update fehlgeschlagen (Code ' + $LASTEXITCODE + '). Befehl in einem Terminal erneut ausfuehren.') }; "
        "Read-Host 'Mit Enter schliessen'"
    )


def run_update(target: UpdateTarget, *, platform: str | None = None, pid: int | None = None,
               which: Callable[[str], str | None] = shutil.which,
               run: Callable[..., Any] = subprocess.run,
               spawn: Callable[..., Any] = subprocess.Popen) -> list[str]:
    """``mao update --run``: nur für ``uv tool``-Installationen; Windows über ein getrenntes Fenster."""
    if target.ref is None:
        return []
    if target.installation.kind != "uv-tool":
        raise UpdateError("--run ist nur für Installationen mit 'uv tool install' möglich; "
                          "den oben genannten Befehl selbst ausführen")
    uv = which("uv")
    if not uv:
        raise UpdateError("uv wurde nicht gefunden (Windows: winget install astral-sh.uv)")
    spec = f"git+https://github.com/{target.repository}@{target.ref}"
    plat = platform or sys.platform
    if plat.startswith("win"):
        script = windows_update_script(pid if pid is not None else os.getpid(), uv, spec)
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
        spawn(["powershell.exe", "-NoProfile", "-Command", script], creationflags=flags, close_fds=True)
        return ["Das Update läuft in einem neuen Fenster, sobald sich mao beendet hat.",
                "Danach mit 'mao --version' prüfen."]
    result = run([uv, "tool", "install", "--force", "--reinstall", spec], check=False)
    if getattr(result, "returncode", 1) != 0:
        raise UpdateError(f"uv endete mit Code {result.returncode}; Befehl oben in einem Terminal wiederholen")
    return ["MaO wurde aktualisiert. Neue Version prüfen mit: mao --version"]


# ---------------------------------------------------------------- Versionshinweis

def _fetch_head(repository: str, ref: str, timeout: int = 3) -> str:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/commits/{ref}",
        headers={"Accept": "application/vnd.github.sha", "User-Agent": "mao-cli"})
    with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:  # noqa: S310 - feste https-URL
        return response.read(200).decode("ascii", "replace").strip()


def _state_file() -> Path:
    return user_state_dir() / "update-check.json"


def _read_state() -> dict[str, Any]:
    try:
        data = json.loads(_state_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state: dict[str, Any]) -> None:
    try:
        path = _state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # der Hinweis ist optional


def _newer(installation: Installation, repository: str) -> str | None:
    """Kurztext der neueren Version oder None; Netzfehler werfen UpdateError/OSError."""
    requested = installation.requested
    if re.fullmatch(r"v\d.*", requested):
        try:
            current = Version(installation.version)
        except InvalidVersion:
            return None
        channel = "rc" if current.is_prerelease else "stable"
        latest = latest_tag(repository, channel, tags=_fetch_tags(repository, timeout=3))
        try:
            return latest if Version(latest[1:]) > current else None
        except InvalidVersion:
            return None
    if requested and REF_RE.fullmatch(requested) and installation.commit:
        head = _fetch_head(repository, requested)
        return requested if re.fullmatch(r"[0-9a-f]{40}", head) and head != installation.commit else None
    return None


def update_notice(now: datetime | None = None) -> str | None:
    """Einzeiliger Hinweis auf eine neuere Version; höchstens eine Abfrage je 24 Stunden."""
    if os.environ.get(NOTICE_ENV):
        return None
    installation = detect_installation()
    if installation.kind != "uv-tool":
        return None
    repository = repository_name(None)
    now = now or datetime.now(timezone.utc)
    key = f"{repository}|{installation.requested}|{installation.version}|{installation.commit}"
    state = _read_state()
    result: str | None
    try:
        fresh = state.get("key") == key and now - datetime.fromisoformat(str(state["checked"])) < CHECK_INTERVAL
    except (KeyError, ValueError):
        fresh = False
    if fresh:
        result = state.get("newer")
    else:
        try:
            result = _newer(installation, repository)
        except (UpdateError, OSError, ValueError):
            result = None
        _write_state({"key": key, "checked": now.isoformat(), "newer": result})
    if not result:
        return None
    if result.startswith("v"):
        return f"Neue MaO-Version {result} verfügbar (installiert: {installation.version}). Aktualisieren: mao update --run"
    return (f"Neuer Stand von '{result}' verfügbar. Aktualisieren: mao update --channel develop --run")
