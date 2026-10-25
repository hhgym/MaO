"""Öffentliche GitHub-Updates für Stable, RC und Develop ohne Git-Checkout.

Release-Updates verwenden versionierte virtuelle Umgebungen mit atomarem
Umschalten und Rollback. Die installierte Python-Umgebung wird nicht überschrieben.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import zipfile
import re
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
from .editable_update import detect_editable, editable_status, install_editable, rollback_editable

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
    wheel_name: str
    wheel_url: str
    checksum_url: str



def _repo(value: str | None) -> str:
    result = value or os.environ.get("MAO_GITHUB_REPO") or "hhgym/MaO"
    if not REPO_RE.fullmatch(result) or ".." in result:
        raise UpdateError("Ungültiges GitHub-Repository (OWNER/REPO)")
    return result


def _headers(*, binary: bool = False) -> dict[str, str]:
    """Öffentliche Releases und GitHub-Metadaten ausschließlich anonym abfragen.

    Sogar ein abgelaufenes GH_TOKEN darf öffentliches Update-Checking nicht
    durch einen HTTP 401 unbrauchbar machen. Es werden keine Tokens aus
    Umgebungsvariablen oder einer gh-Installation mitgeschickt.
    """
    return {"Accept": "application/octet-stream" if binary else "application/vnd.github+json",
            "User-Agent": "MaO-updater/0.6-public", "X-GitHub-Api-Version": "2022-11-28"}


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
            raise UpdateError(f"GitHub antwortet mit HTTP {exc.code}; öffentliche GitHub-API/Release-Datei und Zugriff prüfen") from None
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
            # Bei öffentlichen Repositories browser_download_url nutzen. Der
            # Assets-API-Endpunkt kann eine Authentifizierung verlangen.
            wheel_url = asset.get("browser_download_url")
            chk_url = assets[chk].get("browser_download_url")

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


def _public_asset_url(value: object, repository: str, name: str) -> str:
    """Nur öffentliche Downloadadressen des eigenen Develop-Releases erlauben."""
    if not isinstance(value, str):
        raise UpdateError("Öffentliche Release-Datei besitzt keine Downloadadresse")
    parsed = urllib.parse.urlsplit(value)
    prefix = f"/{repository}/releases/download/develop-build/"
    if (parsed.scheme != "https" or parsed.hostname != "github.com" or
            parsed.username or parsed.password or parsed.query or parsed.fragment or
            not parsed.path.startswith(prefix) or
            urllib.parse.unquote(parsed.path[len(prefix):]) != name):
        raise UpdateError("Ungültige öffentliche GitHub-Downloadadresse")
    return value


def find_develop(repository: str | None) -> DevelopBuild:
    """Entwicklungsstand: erfolgreicher CI-HEAD und öffentliches, SHA-gebundenes Release."""
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
    try:
        release = _github_json(f"/repos/{repo}/releases/tags/develop-build")
    except UpdateError as exc:
        raise UpdateError("Öffentliches Develop-Release fehlt; Build-Veröffentlichung kontrollieren") from exc
    assets = release.get("assets") if isinstance(release, dict) else None
    if not isinstance(assets, list):
        raise UpdateError("Ungültige Liste öffentlicher Develop-Release-Dateien")
    matching = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.endswith(".whl"):
            continue
        try:
            package, version, _, _ = parse_wheel_filename(name)
        except (ValueError, TypeError):
            continue
        if str(package) == "mao-cli" and version.local == "g" + sha[:12]:
            matching.append(item)
    if len(matching) != 1:
        raise UpdateError(f"Kein öffentliches Develop-Wheel für Commit {sha[:12]} gefunden")
    wheel = matching[0]
    checksums = [a for a in assets if isinstance(a, dict) and a.get("name") == wheel["name"] + ".sha256"]
    if len(checksums) != 1:
        raise UpdateError("Öffentliche Develop-SHA-256-Prüfdatei fehlt oder ist doppelt")
    return DevelopBuild(repo, sha, run_id, wheel["name"],
                        _public_asset_url(wheel.get("browser_download_url"), repo, wheel["name"]),
                        _public_asset_url(checksums[0].get("browser_download_url"), repo, wheel["name"] + ".sha256"))


def stage_develop(build: DevelopBuild, directory: Path) -> tuple[Path, str]:
    """Öffentliches Develop-Wheel laden und Prüfsumme + Commitbindung prüfen."""
    name = build.wheel_name
    try:
        package, version, _, _ = parse_wheel_filename(name)
    except (ValueError, TypeError) as exc:
        raise UpdateError("Ungültiger Develop-Wheel-Dateiname") from exc
    if str(package) != "mao-cli" or version.local != "g" + build.commit[:12]:
        raise UpdateError("Develop-Wheel stimmt nicht mit dem GitHub-Commit überein")
    wheel_bytes = _download(build.wheel_url)
    try:
        checksum = _download(build.checksum_url, limit=2048).decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise UpdateError("Develop-Prüfsumme ist keine Textdatei") from exc
    match = re.fullmatch(r"([0-9a-fA-F]{64})(?:\s+\*?([^\s/\\]+))?", checksum)
    if not match or (match.group(2) and match.group(2) != name):
        raise UpdateError("Ungültige Develop-Prüfsumme")
    if hashlib.sha256(wheel_bytes).hexdigest() != match.group(1).lower():
        raise UpdateError("SHA-256-Prüfung fehlgeschlagen; Develop-Wheel wird nicht gespeichert")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    try:
        with path.open("xb") as handle:
            handle.write(wheel_bytes)
    except OSError as exc:
        raise UpdateError(f"Geprüftes Develop-Wheel kann nicht gespeichert werden: {type(exc).__name__}") from exc
    return path, str(version)



def _packaged_source_matches(wheel: Path) -> bool:
    """Vergleicht ZIP-/Quellinstallationen mit dem getesteten Wheel-Inhalt.

    Branch-ZIPs besitzen keine Commit-ID. Verglichen werden deshalb die
    tatsächlich ausgelieferten Programm-/Konfigurationsdateien statt nur die
    gleiche Basis-Versionsnummer. Das Build-spezifische __version__ wird dabei
    ausgenommen; ZIP und CI-Wheel haben hier erwartungsgemäß andere Werte.
    """
    package = Path(__file__).resolve().parent
    def included(relative: str) -> bool:
        path = Path(relative)
        return (path.suffix in (".py", ".yaml", ".ini", ".example")
                and "__pycache__" not in path.parts)
    def normalized(relative: str, data: bytes) -> bytes:
        # Unter Windows können bearbeitete Quell-ZIPs CRLF-Zeilenenden haben,
        # die CI-Wheels dagegen LF. Das ist keine Programmänderung.
        data = data.replace(b"\r\n", b"\n")
        if relative == "__init__.py":
            return re.sub(rb'(?m)^__version__ = ["\'][^"\']+["\']\s*$',
                          b'__version__ = "<VERSION>"', data)
        return data
    local: dict[str, bytes] = {}
    for candidate in package.rglob("*"):
        if candidate.is_file():
            rel = candidate.relative_to(package).as_posix()
            if included(rel):
                local[rel] = normalized(rel, candidate.read_bytes())
    try:
        with zipfile.ZipFile(wheel) as archive:
            remote: dict[str, bytes] = {}
            for name in archive.namelist():
                if not name.startswith("mao_cli/") or name.endswith("/"):
                    continue
                rel = name[len("mao_cli/"):]
                if included(rel):
                    if rel in remote:
                        raise UpdateError("Mehrfache Programmdatei im Develop-Wheel")
                    remote[rel] = normalized(rel, archive.read(name))
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise UpdateError("Develop-Wheel ist kein lesbares ZIP-Archiv") from exc
    return bool(local) and local == remote


def _installed_version(home: Path | None = None) -> str:
    # Bei pip install -e . zählt der *tatsächlich gestartete* Quellstand, nicht
    # die eventuell daneben vorhandene verwaltete Umgebung.
    if detect_editable() is not None:
        return __version__
    from .update_install import read_state
    state = read_state(home_path(home))
    return state["active"] or __version__


def _repository_pyproject(repository: str | None, ref: str) -> bytes:
    """Die zu Wheel/CI gehörende pyproject.toml unverändert aus GitHub lesen."""
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', ref):
        raise UpdateError('Ungültige GitHub-Referenz für pyproject.toml')
    data = _github_json(f'/repos/{_repo(repository)}/contents/pyproject.toml?ref={ref}')
    if (not isinstance(data, dict) or data.get('name') != 'pyproject.toml' or
            data.get('encoding') != 'base64' or not isinstance(data.get('content'), str)):
        raise UpdateError('pyproject.toml vom gewählten GitHub-Stand nicht verfügbar')
    try:
        content = base64.b64decode(data['content'], validate=False)
    except (ValueError, TypeError) as exc:
        raise UpdateError('Ungültige GitHub-Quelle für pyproject.toml') from exc
    if not content or len(content) > 200_000:
        raise UpdateError('Ungültige Größe der pyproject.toml')
    return content


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
    # Für ein ZIP/eine klassische pip-Installation fehlt die Git-Commit-Kennung.
    # Bei identischer Basisversion die tatsächlich installierten Paketdateien
    # mit dem geprüften Build vergleichen (statt ein falsches Update zu melden).
    if "+g" not in installed and installed == str(__version__):
        _, wheel_version, _, _ = parse_wheel_filename(build.wheel_name)
        if Version(installed).base_version == wheel_version.base_version and Version(installed).public == wheel_version.public:
            with tempfile.TemporaryDirectory(prefix="mao-compare-") as tmp:
                verified_wheel, _ = stage_develop(build, Path(tmp))
                if _packaged_source_matches(verified_wheel):
                    return (f"Develop ist aktuell (Paketdateien identisch mit Commit "
                            f"{build.commit[:12]}; installiert aus Quellcode ohne Commit-Kennung)")
    if action == "check":
        return (f"Neuer getesteter Develop-Stand verfügbar: Commit {build.commit[:12]} "
                f"(GitHub Actions {build.run_id}); derzeit MaO {installed}")
    if action == "download":
        if dest is None:  # bereits oben geprüft; hier für die Typprüfung
            raise UpdateError("Für Downloads ist --output erforderlich")
        path, version = stage_develop(build, dest)
        return f"Geprüftes Develop-Wheel {version} heruntergeladen: {path}"
    with tempfile.TemporaryDirectory(prefix="mao-develop-") as tmp:
        path, version = stage_develop(build, Path(tmp))
        try:
            source = detect_editable()
            if source is not None:
                project = _repository_pyproject(repository, build.commit)
                backup = install_editable(source, path, project, version)
                return (f"Develop {version} (Commit {build.commit[:12]}) direkt in {source.root} aktualisiert.\n"
                        f"Sicherung: {backup}\n"
                        "Der bisherige mao.exe verwendet beim nächsten Aufruf den aktualisierten Quellcode.")
            launcher, old = install_managed(path, version, home)
        except InstallError as exc:
            raise UpdateError(str(exc)) from exc
    return (f"Develop {version} (Commit {build.commit[:12]}) installiert. Starter: {launcher}\n"
            f"Rollback auf: {old or 'keine vorherige verwaltete Version'}\n"
            "Künftig den angegebenen MaO-Starter verwenden. Neustart erforderlich.")


def update_action(action: str, channel: str, repository: str | None, *, yes: bool = False,
                  dest: Path | None = None, root: Path | None = None,
                  home: Path | None = None) -> str:
    try:
        source = detect_editable()
    except InstallError as exc:
        raise UpdateError(str(exc)) from exc
    if action == "status":
        return editable_status(source) if source is not None else managed_status(home)
    if action == "rollback":
        if not yes:
            raise UpdateError("Rollback erfordert --yes")
        try:
            if source is not None:
                restored = rollback_editable(source)
                return f"Editable-Rollback auf MaO {restored} in {source.root} erfolgreich; beim nächsten Start aktiv"
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
    installed = _installed_version(home)
    current = Version(installed)
    if latest.version <= current:
        return f"Kein neueres {channel}-Release: installiert {installed}, verfügbar {latest.version}"
    if action == "check":
        return f"Update verfügbar: {installed} -> {latest.version} ({latest.tag}, {channel})"
    if action == "install" and not yes:
        raise UpdateError("Installation erfordert --yes")
    if action == "download":
        if dest is None:
            raise UpdateError("Für Downloads ist --output erforderlich")
        file = stage_release(latest, dest)
        return f"Geprüftes Release heruntergeladen: {file}"
    with tempfile.TemporaryDirectory(prefix="mao-wheel-") as temp:
        path = stage_release(latest, Path(temp))
        try:
            if source is not None:
                project = _repository_pyproject(repository, latest.tag)
                backup = install_editable(source, path, project, str(latest.version))
                return (f"MaO {latest.version} in {source.root} aktualisiert.\n"
                        f"Sicherung: {backup}\n"
                        "Der bestehende mao.exe startet beim nächsten Aufruf den aktualisierten Quellcode.")
            launcher, old = install_managed(path, str(latest.version), home)
        except InstallError as exc:
            raise UpdateError(str(exc)) from exc
    return (f"MaO {latest.version} installiert. Starter: {launcher}\n"
            f"Bisherige Version bleibt erhalten: {old or 'Erstinstallation'}\n"
            "Verwende künftig diesen Starter für Updates und Programmaufrufe. Neustart erforderlich.")
