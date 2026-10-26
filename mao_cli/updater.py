"""Hinweise zur Installation und Aktualisierung von MaO mit ``uv``.

MaO ersetzt sich nicht selbst: Unter Windows lässt sich die laufende ``mao.exe`` nicht
überschreiben. ``mao update`` erkennt daher nur, wie MaO installiert ist, ermittelt
bei Bedarf die neueste Version auf GitHub und nennt den passenden ``uv``-Befehl.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from packaging.version import InvalidVersion, Version

from . import __version__

DEFAULT_REPOSITORY = "hhgym/MaO"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
CHANNELS = ("stable", "rc", "develop")


class UpdateError(RuntimeError):
    """Update-Hinweis konnte nicht ermittelt werden."""


@dataclass(frozen=True)
class Installation:
    kind: str          # "uv-tool", "editable", "pip" oder "unbekannt"
    version: str
    detail: str = ""


def repository_name(value: str | None) -> str:
    result = value or os.environ.get("MAO_GITHUB_REPO") or DEFAULT_REPOSITORY
    if not REPO_RE.fullmatch(result) or ".." in result:
        raise UpdateError("Ungültiges GitHub-Repository (OWNER/REPO)")
    return result


def detect_installation() -> Installation:
    """Erkennt Editable-Quellordner, ``uv tool`` und gewöhnliche pip-Installation (PEP 610)."""
    detail = ""
    kind = "unbekannt"
    try:
        dist = metadata.distribution("mao-cli")
        raw = dist.read_text("direct_url.json")
        info = json.loads(raw) if raw else {}
        if info.get("dir_info", {}).get("editable"):
            kind = "editable"
            detail = str(info.get("url", ""))
        elif "vcs_info" in info:
            detail = f"{info.get('url', '')}@{str(info['vcs_info'].get('commit_id', ''))[:12]}"
        else:
            detail = str(info.get("url", ""))
        if kind != "editable":
            tools = ("uv", "tools")
            parts = [p.lower() for p in Path(sys.prefix).parts]
            kind = "uv-tool" if any(parts[i:i + 2] == list(tools) for i in range(len(parts) - 1)) else "pip"
    except (metadata.PackageNotFoundError, ValueError, OSError):
        pass
    return Installation(kind, __version__, detail)


def _fetch_tags(repository: str) -> list[str]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/tags?per_page=100",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "mao-cli"})  # anonym, ohne Token
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - feste https-URL
            data = json.loads(response.read(2_000_000))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise UpdateError(f"GitHub nicht erreichbar ({exc}); Version mit --version angeben") from exc
    return [item["name"] for item in data if isinstance(item, dict) and isinstance(item.get("name"), str)]


def latest_tag(repository: str, channel: str) -> str:
    """Neuester Tag ``vX.Y.Z`` (stable) bzw. inklusive Release Candidates (rc)."""
    best: tuple[Version, str] | None = None
    for tag in _fetch_tags(repository):
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


def update_hint(channel: str, repository: str | None, version: str | None) -> list[str]:
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
            return lines
    lines.append(f"Ziel: {ref} ({repo})")
    if installation.kind == "editable":
        lines.append("Das ist eine Quellinstallation (pip install -e). Zum Wechsel auf uv zuerst "
                     "'python -m pip uninstall mao-cli', dann:")
    else:
        lines.append("In einem Terminal ausführen (MaO ersetzt sich nicht selbst):")
    lines.append("  " + install_command(repo, ref))
    lines.append("Konfiguration und Jahrgangsordner bleiben unberührt. uv fehlt? "
                 "Windows: winget install astral-sh.uv · Linux/macOS: curl -LsSf https://astral.sh/uv/install.sh | sh")
    return lines
