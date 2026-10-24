"""Authentifizierte GitHub-Updates für Stable, RC und Develop ohne Git-Checkout.

Release-Updates verwenden versionierte virtuelle Umgebungen mit atomarem
Umschalten und Rollback. Die installierte Python-Umgebung wird nicht überschrieben.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import subprocess
import tempfile
import zipfile
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
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class UpdateError(RuntimeError):
    """Update wurde sicher angehalten."""


@dataclass(frozen=True)
class Release:
    version: Version
    tag: str
    wheel_url: str
    checksum_url: str
    wheel_name: str


@dataclass(frozen=True)
class DevelopBuild:
    repository: str
    commit: str
    run_id: int
    artifact_id: int



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
                                  not host.endswith(".githubusercontent.com") and
                                  not host.endswith(".blob.core.windows.net")):
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


def find_develop(repository: str | None) -> DevelopBuild:
    """Der HEAD von develop muss exakt einen erfolgreich gebauten, gültigen CI-Artefakt haben."""
    repo = _repo(repository)
    branch = _github_json(f"/repos/{repo}/branches/develop")
    if not isinstance(branch, dict) or not isinstance(branch.get("commit"), dict):
        raise UpdateError("GitHub lieferte keinen gültigen develop-Branch")
    sha = branch["commit"].get("sha", "")
    if not isinstance(sha, str) or not COMMIT_RE.fullmatch(sha):
        raise UpdateError("Ungültige develop-Commit-ID")
    runs = _github_json(f"/repos/{repo}/actions/workflows/ci.yml/runs?branch=develop&head_sha={sha}&event=push&per_page=25")
    if not isinstance(runs, dict) or not isinstance(runs.get("workflow_runs"), list):
        raise UpdateError("GitHub-CI-Status konnte nicht ermittelt werden")
    candidates = [r for r in runs["workflow_runs"]
                  if isinstance(r, dict) and r.get("head_sha") == sha and
                  r.get("event") == "push" and r.get("conclusion") == "success" and
                  isinstance(r.get("id"), int)]
    if not candidates:
        raise UpdateError(f"Develop-Commit {sha[:12]} ist noch nicht erfolgreich gebaut (GitHub Actions)")
    run_id = max(r["id"] for r in candidates)
    artifacts = _github_json(f"/repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100")
    if not isinstance(artifacts, dict) or not isinstance(artifacts.get("artifacts"), list):
        raise UpdateError("GitHub-Artefaktliste konnte nicht gelesen werden (Actions: read erforderlich)")
    matches = [a for a in artifacts["artifacts"]
               if isinstance(a, dict) and a.get("name") == f"mao-develop-{sha}" and
               a.get("expired") is False and isinstance(a.get("id"), int)]
    if len(matches) != 1:
        raise UpdateError(f"Kein gültiges Build-Artefakt für Develop {sha[:12]}; CI noch nicht abgeschlossen oder Artefakt abgelaufen")
    return DevelopBuild(repo, sha, run_id, matches[0]["id"])


def stage_develop(build: DevelopBuild, directory: Path) -> tuple[Path, str]:
    """Actions-ZIP kontrolliert entpacken, SHA-256 prüfen und Wheel-Version an Commit binden."""
    repo = build.repository
    archive_bytes = _download(f"{API}/repos/{repo}/actions/artifacts/{build.artifact_id}/zip")
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            items = [i for i in archive.infolist() if not i.is_dir()]
            if len(items) != 2 or any(i.file_size > 40_000_000 or i.file_size < 1 or
                                       i.filename != Path(i.filename).name or i.filename.startswith(".")
                                       for i in items):
                raise UpdateError("Develop-Artefakt enthält unerwartete Dateien")
            wheels = [i for i in items if i.filename.endswith(".whl")]
            if len(wheels) != 1 or {i.filename for i in items} != {wheels[0].filename, wheels[0].filename + ".sha256"}:
                raise UpdateError("Develop-Artefakt benötigt genau ein Wheel und seine SHA-256-Datei")
            wheel_name = wheels[0].filename
            package, version, _build, _tags = parse_wheel_filename(wheel_name)
            if str(package) != "mao-cli" or version.local != f"g{build.commit[:12]}":
                raise UpdateError("Develop-Wheel stimmt nicht mit dem GitHub-Commit überein")
            checksum = archive.read(wheel_name + ".sha256").decode("utf-8").strip()
            match = re.fullmatch(r"([0-9a-fA-F]{64})(?:\s+\*?([^\s/\\]+))?", checksum)
            if not match or (match.group(2) and match.group(2) != wheel_name):
                raise UpdateError("Ungültige Develop-Prüfsumme")
            content = archive.read(wheel_name)
            if hashlib.sha256(content).hexdigest() != match.group(1).lower():
                raise UpdateError("SHA-256-Prüfung fehlgeschlagen; Develop-Wheel wird nicht gespeichert")
    except (zipfile.BadZipFile, UnicodeDecodeError, ValueError) as exc:
        raise UpdateError("Ungültiges Develop-Artefakt (ZIP/Version/Prüfsumme)") from exc
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / wheel_name
    try:
        with path.open("xb") as handle:
            handle.write(content)
    except OSError as exc:
        raise UpdateError(f"Geprüftes Develop-Wheel kann nicht gespeichert werden: {type(exc).__name__}") from exc
    return path, str(version)


def _installed_version(home: Path | None = None) -> str:
    from .update_install import read_state
    state = read_state(home_path(home))
    return state["active"] or __version__


def _update_develop(action: str, repository: str | None, *, yes: bool,
                    dest: Path | None, home: Path | None) -> str:
    if action == "install" and not yes:
        raise UpdateError("Installation erfordert --yes")
    if action == "download" and dest is None:
        raise UpdateError("Für Downloads ist --output erforderlich")
    build = find_develop(repository)
    try:
        installed = _installed_version(home)
    except InstallError as exc:
        raise UpdateError(str(exc)) from exc
    if installed.endswith("+g" + build.commit[:12]):
        return f"Develop ist aktuell (Commit {build.commit[:12]}, MaO {installed})"
    if action == "check":
        return (f"Neuer getesteter Develop-Stand verfügbar: Commit {build.commit[:12]} "
                f"(GitHub Actions {build.run_id}); derzeit MaO {installed}")
    if action == "download":
        path, version = stage_develop(build, dest)
        return f"Geprüftes Develop-Wheel {version} heruntergeladen: {path}"
    with tempfile.TemporaryDirectory(prefix="mao-develop-") as tmp:
        path, version = stage_develop(build, Path(tmp))
        try:
            launcher, old = install_managed(path, version, home)
        except InstallError as exc:
            raise UpdateError(str(exc)) from exc
    return (f"Develop {version} (Commit {build.commit[:12]}) installiert. Starter: {launcher}\n"
            f"Rollback auf: {old or 'keine vorherige verwaltete Version'}\n"
            "Künftig den angegebenen MaO-Starter verwenden. Neustart erforderlich.")


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
        return _update_develop(action, repository, yes=yes, dest=dest, home=home)
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
