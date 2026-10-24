"""Sichere In-place-Updates für PEP-660-Editable-Installationen von MaO.

Nur eine mittels ``pip install -e .`` nachgewiesene *aktive* Quellinstallation
wird verändert. Wheel-Installationen behalten ihre getrennte Updateverwaltung.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

from . import __version__
from .update_install import InstallError, _update_lock


@dataclass(frozen=True)
class EditableSource:
    root: Path

    @property
    def package(self) -> Path:
        return self.root / 'mao_cli'

    @property
    def history(self) -> Path:
        return self.root / '.mao-updates'


def detect_editable() -> EditableSource | None:
    """Nur das *importierte* MaO mit PEP-610/660-Editable-Metadaten finden."""
    try:
        distribution = importlib.metadata.distribution('mao-cli')
        raw = distribution.read_text('direct_url.json')
    except importlib.metadata.PackageNotFoundError:
        return None
    if not raw:
        return None
    try:
        metadata = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if metadata.get('dir_info', {}).get('editable') is not True:
        return None
    uri = urllib.parse.urlsplit(metadata.get('url', ''))
    if uri.scheme != 'file' or uri.netloc not in ('', 'localhost') or not uri.path:
        raise InstallError('Editable-Paket verweist nicht auf einen lokalen Quellordner')
    directory = Path(urllib.request.url2pathname(urllib.parse.unquote(uri.path))).resolve()
    package = Path(__file__).resolve().parent
    if package != directory / 'mao_cli' or not (directory / 'pyproject.toml').is_file():
        raise InstallError('Editable-Metadaten stimmen nicht mit dem tatsächlich gestarteten MaO-Quellordner überein')
    return EditableSource(directory)


def _project(data: bytes) -> dict:
    try:
        return tomllib.loads(data.decode('utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise InstallError('pyproject.toml ist ungültig') from exc


def prepare_pyproject(source: EditableSource, remote_content: bytes, version: str) -> bytes:
    """Versionsstempel ergänzen; geänderte Abhängigkeiten/Entry-Points blockieren."""
    from packaging.version import Version
    old = _project((source.root / 'pyproject.toml').read_bytes())
    new = _project(remote_content)
    previous = old.get('project', {})
    updated = new.get('project', {})
    if previous.get('name', '').lower().replace('_', '-') != 'mao-cli' or updated.get('name', '').lower().replace('_', '-') != 'mao-cli':
        raise InstallError('Projekt ist nicht mao-cli')
    try:
        if Version(updated['version']).public != Version(version).public:
            raise InstallError('pyproject.toml gehört nicht zum geprüften Wheel')
    except (ValueError, KeyError) as exc:
        raise InstallError('Ungültige Versionsangabe in pyproject.toml') from exc
    for key in ('dependencies', 'optional-dependencies', 'scripts', 'entry-points', 'requires-python', 'dynamic'):
        if previous.get(key) != updated.get(key):
            raise InstallError(f'pyproject.toml: {key} geändert; erst kontrolliert mit pip install -e . neu installieren')
    if old.get('build-system') != new.get('build-system'):
        raise InstallError('pyproject.toml: Build-System geändert; erst kontrolliert mit pip install -e . neu installieren')
    expression = re.compile(rb'(?m)^version\s*=\s*["\'][^"\']+["\']\s*$')
    # pyproject.toml des Projekts enthält genau eine statische version-Angabe.
    matches = list(expression.finditer(remote_content))
    if len(matches) != 1:
        raise InstallError('pyproject.toml besitzt keine eindeutige statische Version')
    return remote_content[:matches[0].start()] + f'version = "{version}"'.encode('ascii') + remote_content[matches[0].end():]


def _read_wheel_package(wheel: Path) -> dict[str, bytes]:
    """Nur erlaubte MaO-Paketdateien extrahieren, keine Fremddateien aus dem Wheel."""
    result: dict[str, bytes] = {}
    size = 0
    try:
        with zipfile.ZipFile(wheel) as archive:
            for info in archive.infolist():
                name = info.filename
                if not name.startswith('mao_cli/') or info.is_dir():
                    continue
                parts = name.split('/')
                if (len(parts) < 2 or any(part in ('', '.', '..') for part in parts) or
                        not re.fullmatch(r'[A-Za-z0-9_.+\-/]+', name) or
                        not name.endswith(('.py', '.yaml', '.ini', '.example'))):
                    raise InstallError('Unsicherer Dateiname im Update-Wheel')
                # Datei-Symlinks und andere Unix-Sonderdateien dürfen nicht entpackt werden.
                mode = info.external_attr >> 16
                if mode and (mode & 0o170000) not in (0, 0o100000):
                    raise InstallError('Unerlaubter Dateityp im Update-Wheel')
                relative = '/'.join(parts[1:])
                if relative in result or len(result) >= 500 or info.file_size > 2_000_000:
                    raise InstallError('Doppelte oder zu große Programmdatei im Update-Wheel')
                size += info.file_size
                if size > 25_000_000:
                    raise InstallError('Update-Wheel enthält zu viele Programmdateien')
                result[relative] = archive.read(info)
    except (zipfile.BadZipFile, OSError) as exc:
        raise InstallError('Update-Wheel ist kein lesbares ZIP-Archiv') from exc
    if '__init__.py' not in result or 'cli.py' not in result or 'updater.py' not in result:
        raise InstallError('Update-Wheel enthält kein vollständiges MaO-Paket')
    return result


def _manifest(package: Path, project: Path) -> dict[str, str]:
    names: dict[str, str] = {}
    for path in package.rglob('*'):
        if path.is_file() and (path.suffix in ('.py', '.yaml', '.ini', '.example')) and '__pycache__' not in path.parts:
            relative = path.relative_to(package).as_posix()
            names['mao_cli/' + relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    names['pyproject.toml'] = hashlib.sha256(project.read_bytes()).hexdigest()
    return names


def _validate_clean_since_update(source: EditableSource, state: dict) -> None:
    previous = state.get('manifest')
    if not isinstance(previous, dict) or not previous or _manifest(source.package, source.root / 'pyproject.toml') != previous:
        raise InstallError('Programmdateien wurden seit dem Update verändert; automatischer Rollback abgebrochen')


def _read_state(source: EditableSource) -> dict | None:
    path = source.history / 'state.json'
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise InstallError('Ungültiger Editable-Updatezustand') from exc
    if (not isinstance(state, dict) or state.get('schema') != 1 or
            not isinstance(state.get('backup'), str) or
            not re.fullmatch(r'[0-9a-f]{32}', state['backup'])):
        raise InstallError('Ungültiger Editable-Updatezustand')
    return state


def _store_state(source: EditableSource, state: dict) -> None:
    path = source.history / 'state.json'
    fd, tmp = tempfile.mkstemp(prefix='.state-', dir=source.history)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(state, out, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _smoke(project_root: Path, version: str) -> None:
    """Prüft die neue CLI in isoliertem Python ohne PYTHONPATH aus dem Quellprojekt."""
    code = ('import sys; sys.path.insert(0, sys.argv[1]); '
            'from mao_cli import __version__; from mao_cli.cli import parse_args; '
            'assert __version__ == sys.argv[2]; '
            'parse_args(["--version"])')
    try:
        p = subprocess.run([sys.executable, '-I', '-c', code, str(project_root), version],
                           capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallError(f'CLI-Selbstprüfung kann nicht gestartet werden: {type(exc).__name__}') from exc
    if p.returncode or p.stdout.strip() != f'mao {version}':
        detail = (p.stderr or p.stdout or 'keine Ausgabe').splitlines()[-1][:200]
        raise InstallError(f'CLI-Selbstprüfung fehlgeschlagen (Exitcode {p.returncode}): {detail}')


def _replace(source: EditableSource, new_package: Path, new_project: Path, version: str, previous: str) -> Path:
    """Führt Directory-Swap mit Rückstellung bei Fehlern auf derselben Partition aus."""
    source.history.mkdir(exist_ok=True)
    backup_id = uuid.uuid4().hex
    backup = source.history / backup_id
    backup.mkdir()
    old_pkg = source.package
    old_project = source.root / 'pyproject.toml'
    pkg_backed = project_backed = pkg_installed = project_installed = False
    try:
        os.replace(old_pkg, backup / 'mao_cli')
        pkg_backed = True
        os.replace(new_package, old_pkg)
        pkg_installed = True
        os.replace(old_project, backup / 'pyproject.toml')
        project_backed = True
        os.replace(new_project, old_project)
        project_installed = True
        _smoke(source.root, version)
        _store_state(source, {'schema': 1, 'active': version, 'previous': previous,
                              'backup': backup_id, 'manifest': _manifest(old_pkg, old_project)})
    except BaseException:
        # Auch bei einem fehlgeschlagenen Selbsttest bzw. einem abgebrochenen
        # Dateiaustausch den alten Stand exakt wiederherstellen.
        if project_installed:
            os.replace(old_project, new_project)
        if project_backed:
            os.replace(backup / 'pyproject.toml', old_project)
        if pkg_installed:
            os.replace(old_pkg, new_package)
        if pkg_backed:
            os.replace(backup / 'mao_cli', old_pkg)
        shutil.rmtree(backup, ignore_errors=True)
        raise
    return backup


def _version_in_source(source: EditableSource) -> str:
    try:
        contents = (source.package / '__init__.py').read_text(encoding='utf-8')
    except OSError as exc:
        raise InstallError('Aktuelle MaO-Version im Quellordner nicht lesbar') from exc
    match = re.search(r'(?m)^__version__\s*=\s*["\']([^"\']+)["\']\s*$', contents)
    if not match:
        raise InstallError('Aktuelle MaO-Version im Quellordner nicht erkennbar')
    return match.group(1)


def install_editable(source: EditableSource, wheel: Path, pyproject: bytes, version: str) -> Path:
    """Austausch in genau der Quellinstallation hinter dem laufenden mao.exe."""
    if (source.package.is_symlink() or (source.root / 'pyproject.toml').is_symlink() or
            source.history.is_symlink()):
        raise InstallError('Symlink-Installationen können nicht automatisch überschrieben werden')
    contents = _read_wheel_package(wheel)
    prepared = prepare_pyproject(source, pyproject, version)
    with _update_lock(source.history):
        state = _read_state(source)
        if state is not None:
            _validate_clean_since_update(source, state)
        # Staging liegt auf demselben Datenträger wie der Projektordner.
        with tempfile.TemporaryDirectory(prefix='.mao-stage-', dir=source.root) as temp:
            folder = Path(temp)
            staged_pkg = folder / 'mao_cli'
            for name, content in contents.items():
                target = staged_pkg.joinpath(*name.split('/'))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            new_project = folder / 'pyproject.toml'
            new_project.write_bytes(prepared)
            _smoke(folder, version)
            backup = _replace(source, staged_pkg, new_project, version, _version_in_source(source))
    return backup


def rollback_editable(source: EditableSource) -> str:
    if source.history.is_symlink():
        raise InstallError('Symlink-Installationen können nicht automatisch überschrieben werden')
    with _update_lock(source.history):
        state = _read_state(source)
        if not state:
            raise InstallError('Kein vorheriger Editable-Stand für Rollback verfügbar')
        _validate_clean_since_update(source, state)
        backup = source.history / state['backup']
        if not (backup / 'mao_cli' / '__init__.py').is_file() or not (backup / 'pyproject.toml').is_file():
            raise InstallError('Editable-Sicherung ist unvollständig')
        previous = state.get('previous')
        if not isinstance(previous, str):
            raise InstallError('Ungültige vorherige Editable-Version')
        # Die beiden Verzeichnisse werden mit der gleichen Sicherungslogik getauscht.
        with tempfile.TemporaryDirectory(prefix='.mao-rollback-', dir=source.root) as td:
            folder = Path(td)
            os.replace(backup / 'mao_cli', folder / 'mao_cli')
            os.replace(backup / 'pyproject.toml', folder / 'pyproject.toml')
            try:
                _replace(source, folder / 'mao_cli', folder / 'pyproject.toml', previous, state['active'])
            except BaseException:
                os.replace(folder / 'mao_cli', backup / 'mao_cli')
                os.replace(folder / 'pyproject.toml', backup / 'pyproject.toml')
                raise
        return previous


def editable_status(source: EditableSource) -> str:
    state = _read_state(source)
    return (f'Editable-Installation: {source.root}\n'
            f'Gestartet aus: {source.package}\n'
            f'Aktiv: {_version_in_source(source)}\n'
            f'Rollback: {state.get("previous", "nicht verfügbar") if state else "nicht verfügbar"}\n'
            'Updateziel: Quelldateien (kein separater Starter)')
