"""Authentifizierte GitHub-Updates, getrennte Stable/RC- und Develop-Kanäle.

Release-Updates verwenden versionierte virtuelle Umgebungen mit atomarem
Umschalten und Rollback. Die installierte Python-Umgebung wird nicht überschrieben.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from packaging.utils import parse_wheel_filename
from packaging.version import InvalidVersion, Version

from . import __version__
from .update_install import InstallError, home_path, install_managed, managed_status, rollback_managed

API = "https://api.github.com"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class UpdateError(RuntimeError):
    """Update wurde sicher angehalten."""


@dataclass(frozen=True)
class Release:
    version: Version
    tag: str
    wheel_url: str
    checksum_url: str
    wheel_name: str


def _repo(value: str | None) -> str:
    result = value or os.environ.get("MAO_GITHUB_REPO") or "hhgym/MaO"
    if not REPO_RE.fullmatch(result) or ".." in result:
        raise UpdateError("Ungültiges GitHub-Repository (OWNER/REPO)")
    return result


def _token() -> str | None:
    for key in ("MAO_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        if value := os.environ.get(key):
            return value.strip()
    # Bestehende GitHub-CLI-Anmeldung ohne manuelle Speicherung eines weiteren Tokens nutzen.
    try:
        p = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=8)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _headers(*, binary: bool = False) -> dict[str, str]:
    headers = {"Accept": "application/octet-stream" if binary else "application/vnd.github+json",
               "User-Agent": "MaO-updater/0.5", "X-GitHub-Api-Version": "2022-11-28"}
    token = _token()
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


class _RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    """Sende GitHub-Zugriffstoken niemals an die Asset-CDN-Domain weiter."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        src = urllib.parse.urlsplit(req.full_url)
        dst = urllib.parse.urlsplit(newurl)
        host = (dst.hostname or "").lower()
        if dst.scheme != "https" or (host not in {"github.com", "api.github.com"} and
                                  not host.endswith(".githubusercontent.com")):
            raise UpdateError("Unsicheres GitHub-Redirectziel")
        next_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if next_req is None:
            return None
        if src.hostname != dst.hostname:
            next_req.remove_header("Authorization")
            next_req.unredirected_hdrs.pop("Authorization", None)
        return next_req


def _request(url: str, *, binary: bool = False, limit: int = 40_000_000) -> bytes:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ("api.github.com", "github.com"):
        raise UpdateError("Updatequelle muss GitHub über HTTPS sein")
    req = urllib.request.Request(url, headers=_headers(binary=binary))
    opener = urllib.request.build_opener(_RestrictedRedirect())
    try:
        with opener.open(req, timeout=45) as res:
            if urllib.parse.urlsplit(res.geturl()).scheme != "https":
                raise UpdateError("Unsicherer Update-Download")
            data = res.read(limit + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            raise UpdateError(f"GitHub antwortet mit HTTP {exc.code}; bei privaten Repos Anmeldung über gh auth login oder MAO_GITHUB_TOKEN prüfen") from None
        raise UpdateError(f"GitHub-Anfrage fehlgeschlagen (HTTP {exc.code})") from None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise UpdateError(f"GitHub-Verbindung fehlgeschlagen ({type(exc).__name__})") from None
    if len(data) > limit:
        raise UpdateError("Update-Datei überschreitet Größenlimit")
    return data


def _github_json(path: str):
    try:
        return json.loads(_request(API + path, limit=2_000_000))
    except (ValueError, UnicodeDecodeError) as exc:
        raise UpdateError("Ungültige Antwort der GitHub-API") from exc


def _download(url: str, limit: int = 40_000_000) -> bytes:
    data = _request(url, binary=True, limit=limit)
    # Die Asset-API kann abhängig vom Header auch JSON-Metadaten liefern.
    if data.lstrip().startswith(b"{") and b'"browser_download_url"' in data[:3000]:
        raise UpdateError("GitHub lieferte Metadaten statt der Update-Datei")
    return data


def _version(tag: str):
    try:
        return Version(tag.removeprefix("v"))
    except InvalidVersion:
        return None


def find_release(repository: str | None, channel: str) -> Release | None:
    if channel not in ("stable", "rc"):
        raise UpdateError("Release-Kanal muss stable oder rc sein")
    repo = _repo(repository)
    releases = _github_json(f"/repos/{repo}/releases?per_page=100")
    if not isinstance(releases, list):
        raise UpdateError("Ungültige GitHub-Releaseliste")
    options: list[Release] = []
    for item in releases:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        tag = item.get("tag_name", "")
        if not isinstance(tag, str) or not tag.startswith("v"):
            continue
        v = _version(tag)
        if not v or v.is_devrelease or v.is_postrelease or v.local is not None:
            continue
        if channel == "stable" and (v.is_prerelease or item.get("prerelease")):
            continue
        if channel == "rc" and v.is_prerelease and (v.pre is None or v.pre[0] != "rc"):
            continue
        if not v.is_prerelease and item.get("prerelease"):
            continue
        assets = {a.get("name"): a for a in item.get("assets", []) if isinstance(a, dict)}
        for name, asset in assets.items():
            if not isinstance(name, str) or not name.endswith(".whl"):
                continue
            try:
                package, wheel_version, _, _ = parse_wheel_filename(name)
            except (ValueError, TypeError):
                continue
            if str(package) != "mao-cli" or wheel_version != v:
                continue
            chk = name + ".sha256"
            if chk not in assets:
                continue
            def asset_url(a):
                # Authentifizierbarer GitHub-API-Endpunkt anstelle browser_download_url.
                if isinstance(a.get("id"), int):
                    return f"{API}/repos/{repo}/releases/assets/{a['id']}"
                return a.get("browser_download_url")
            wheel_url, chk_url = asset_url(asset), asset_url(assets[chk])
            if wheel_url and chk_url:
                options.append(Release(v, tag, wheel_url, chk_url, name))
    return max(options, key=lambda r: r.version) if options else None


def stage_release(release: Release, directory: Path) -> Path:
    """Download und SHA-256-Prüfung; vor dem Speichern keine externen Änderungen."""
    wheel_bytes = _download(release.wheel_url)
    try:
        checksum = _download(release.checksum_url, limit=2048).decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise UpdateError("Prüfsumme ist keine Textdatei") from exc
    match = re.fullmatch(r"([0-9a-fA-F]{64})(?:\s+\*?([^\s/\\]+))?", checksum)
    if not match or (match.group(2) and match.group(2) != release.wheel_name):
        raise UpdateError("Ungültige SHA-256-Prüfdatei")
    expected = match.group(1).lower()
    if not HEX_RE.fullmatch(expected) or hashlib.sha256(wheel_bytes).hexdigest() != expected:
        raise UpdateError("SHA-256-Prüfung fehlgeschlagen; Installation abgebrochen")
    path = directory / release.wheel_name
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as out:
            out.write(wheel_bytes)
    except OSError as exc:
        raise UpdateError(f"Geprüftes Wheel kann nicht gespeichert werden: {type(exc).__name__}") from exc
    return path


def _git(*arguments: str, cwd: Path) -> str:
    try:
        p = subprocess.run(["git", *arguments], cwd=cwd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"Git nicht verfügbar: {type(exc).__name__}") from exc
    if p.returncode:
        raise UpdateError(f"Git-Aufruf fehlgeschlagen ({arguments[0]}, Exitcode {p.returncode})")
    return p.stdout.strip()


def git_checkout_root(path: Path | None = None) -> Path:
    source = path or Path(__file__).resolve().parents[1]
    root = _git("rev-parse", "--show-toplevel", cwd=source)
    dest = Path(root).resolve()
    if dest != source.resolve():
        raise UpdateError("Develop-Updates nur aus dem Projektstamm eines Git-Checkouts")
    return dest


def _repository_from_origin(root: Path) -> str:
    remote = _git("remote", "get-url", "origin", cwd=root)
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?", remote)
    if not match:
        raise UpdateError("Develop setzt GitHub-origin voraus")
    return _repo(match.group(1))


def _ci_success(repository: str, sha: str) -> bool:
    runs = _github_json(f"/repos/{repository}/actions/workflows/ci.yml/runs?branch=develop&head_sha={sha}&per_page=25")
    if not isinstance(runs, dict) or not isinstance(runs.get("workflow_runs"), list):
        raise UpdateError("GitHub-CI-Status konnte nicht ermittelt werden")
    return any(r.get("head_sha") == sha and r.get("conclusion") == "success" for r in runs["workflow_runs"])


def develop_status(root: Path, *, fetch: bool = True) -> tuple[str, str]:
    if _git("branch", "--show-current", cwd=root) != "develop":
        raise UpdateError("Develop-Update nur auf Branch develop möglich")
    if _git("status", "--porcelain", cwd=root):
        raise UpdateError("Git-Arbeitsverzeichnis nicht sauber; Änderungen committen/stashen")
    if fetch:
        _git("fetch", "--no-tags", "origin", "develop", cwd=root)
    local = _git("rev-parse", "HEAD", cwd=root)
    remote = _git("rev-parse", "origin/develop", cwd=root)
    _git("merge-base", "--is-ancestor", local, remote, cwd=root)
    return local, remote


def _update_develop(action: str, repository: str | None, *, yes: bool, root: Path | None):
    if action == "download":
        raise UpdateError("Develop nutzt einen Git-Checkout statt Release-Downloads")
    worktree = root or git_checkout_root()
    local, remote = develop_status(worktree)
    if local == remote:
        return f"Develop ist aktuell ({local[:12]})"
    actual_repo = _repository_from_origin(worktree)
    if repository is not None and _repo(repository).lower() != actual_repo.lower():
        raise UpdateError("Repository und Git-origin stimmen nicht überein")
    if not _ci_success(actual_repo, remote):
        raise UpdateError(f"Develop-Commit {remote[:12]} hat keinen erfolgreichen CI-Lauf")
    if action == "check":
        return f"Neuer getesteter Develop-Stand: {local[:12]} -> {remote[:12]}"
    if not yes:
        raise UpdateError("Installation erfordert --yes")
    _git("merge", "--ff-only", "origin/develop", cwd=worktree)
    original_error = None
    try:
        p = subprocess.run([sys.executable, "-m", "pip", "install", "-e", str(worktree)],
                           capture_output=True, text=True, timeout=300)
        if p.returncode:
            raise UpdateError(f"pip-Installation fehlgeschlagen (Exitcode {p.returncode})")
    except (OSError, subprocess.TimeoutExpired, UpdateError) as exc:
        original_error = exc if isinstance(exc, UpdateError) else UpdateError(
            f"pip fehlgeschlagen ({type(exc).__name__})")
    if original_error is not None:
        try:
            _git("reset", "--hard", local, cwd=worktree)
            restore = subprocess.run([sys.executable, "-m", "pip", "install", "-e", str(worktree)],
                                     capture_output=True, text=True, timeout=300)
            if restore.returncode:
                raise UpdateError("Rücksetzen des Quellcodes gelungen, aber pip-Wiederherstellung fehlgeschlagen")
        except (OSError, subprocess.TimeoutExpired, UpdateError) as rollback_error:
            raise UpdateError(f"{original_error}; Rollback nicht vollständig: {type(rollback_error).__name__}") from None
        raise original_error
    return f"Develop auf {remote[:12]} aktualisiert; Neustart erforderlich"


def update_action(action: str, channel: str, repository: str | None, *, yes: bool = False,
                  dest: Path | None = None, root: Path | None = None,
                  home: Path | None = None) -> str:
    if action == "status":
        return managed_status(home)
    if action == "rollback":
        if not yes:
            raise UpdateError("Rollback erfordert --yes")
        try:
            restored = rollback_managed(home)
        except InstallError as exc:
            raise UpdateError(str(exc)) from exc
        return f"Rollback auf MaO {restored} erfolgreich; nächster Start nutzt diese Version"
    if channel not in ("stable", "rc", "develop"):
        raise UpdateError("Ungültiger Updatekanal")
    if action not in ("check", "download", "install"):
        raise UpdateError("Ungültige Updateaktion")
    if channel == "develop":
        return _update_develop(action, repository, yes=yes, root=root)
    latest = find_release(repository, channel)
    if latest is None:
        raise UpdateError(f"Kein nutzbares Release für Kanal {channel} gefunden")
    current = Version(__version__)
    installed = __version__
    from .update_install import read_state
    managed = read_state(home_path(home))
    if managed["active"]:
        installed = managed["active"]
        current = Version(installed)
    if latest.version <= current:
        return f"Kein neueres {channel}-Release: installiert {installed}, verfügbar {latest.version}"
    if action == "check":
        return f"Update verfügbar: {installed} -> {latest.version} ({latest.tag}, {channel})"
    if action == "install" and not yes:
        raise UpdateError("Installation erfordert --yes")
    if action == "download" and dest is None:
        raise UpdateError("Für Downloads ist --output erforderlich")
    if action == "download":
        file = stage_release(latest, dest)
        return f"Geprüftes Release heruntergeladen: {file}"
    with tempfile.TemporaryDirectory(prefix="mao-wheel-") as temp:
        path = stage_release(latest, Path(temp))
        try:
            launcher, old = install_managed(path, str(latest.version), home)
        except InstallError as exc:
            raise UpdateError(str(exc)) from exc
    return (f"MaO {latest.version} installiert. Starter: {launcher}\n"
            f"Bisherige Version bleibt erhalten: {old or 'Erstinstallation'}\n"
            "Verwende künftig diesen Starter für Updates und Programmaufrufe. Neustart erforderlich.")
