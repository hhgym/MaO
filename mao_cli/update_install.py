"""Versionierte, lokale MaO-Installationen mit atomischem Umschalten.

Die Launcher-Dateien liegen außerhalb der virtuellen Umgebungen. Ein Update baut
zunächst eine neue venv, prüft sie und wechselt erst dann active.json. Die zuvor
aktive Umgebung bleibt für ``rollback`` erhalten. Keine Veränderung der User-venv.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import venv
from contextlib import contextmanager
from pathlib import Path


class InstallError(RuntimeError):
    """Installationsfehler, der die bisherige Installation nicht ersetzen darf."""


VERSION_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
STATE_FILE = "active.json"


def home_path(value: Path | None = None) -> Path:
    if value is not None:
        return value.expanduser().resolve()
    env = os.environ.get("MAO_UPDATE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if os.name == "nt":
        return (Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "MaO").resolve()
    return (Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "mao").resolve()


def python_in(env: Path) -> Path:
    return env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _valid_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not VERSION_ID.fullmatch(value) or value in (".", ".."):
        raise InstallError("Ungültige Versionskennung im Updatezustand")
    return value


def read_state(home: Path) -> dict[str, str | None]:
    state_path = home / STATE_FILE
    if not state_path.is_file():
        return {"active": None, "previous": None}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InstallError("Ungültiger Updatezustand; active.json bitte prüfen") from exc
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise InstallError("Unbekanntes Updatezustandsformat")
    return {"active": _valid_id(data.get("active")), "previous": _valid_id(data.get("previous"))}


def _write_state(home: Path, active: str, previous: str | None) -> None:
    home.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".active-", suffix=".json", dir=home)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"schema": 1, "active": active, "previous": previous}, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, home / STATE_FILE)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def _update_lock(home: Path):
    home.mkdir(parents=True, exist_ok=True)
    path = home / ".update.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise InstallError(f"Update läuft bereits oder Sperrdatei vorhanden: {path}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        path.unlink(missing_ok=True)


def _verify_environment(env: Path, version: str) -> None:
    executable = python_in(env)
    try:
        result = subprocess.run([str(executable), "-m", "mao_cli", "--version"],
                                capture_output=True, text=True, timeout=45, cwd=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallError(f"Neue Installation kann nicht gestartet werden: {type(exc).__name__}") from exc
    if result.returncode or result.stdout.strip() != f"mao {version}":
        raise InstallError(f"Neue Installation hat CLI-Selbstprüfung nicht bestanden (erwartet {version})")


def _install_wheel(env: Path, wheel: Path, version: str) -> None:
    try:
        venv.EnvBuilder(with_pip=True, clear=False).create(env)
        p = subprocess.run(
            [str(python_in(env)), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", str(wheel)],
            capture_output=True, text=True, timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        raise InstallError(f"Einrichten der neuen Python-Umgebung fehlgeschlagen: {type(exc).__name__}") from exc
    if p.returncode:
        # Kein pip-Output weitergeben: fremde Paketquellen könnten Zugangsdaten enthalten.
        raise InstallError(f"Installation der Abhängigkeiten fehlgeschlagen (pip Exitcode {p.returncode})")
    _verify_environment(env, version)


LAUNCHER = '''"""Stabiler MaO-Starter. Automatisch erstellt; nicht manuell bearbeiten."""
import json
import os
import subprocess
import sys
from pathlib import Path

home = Path(__file__).resolve().parent.parent
try:
    data = json.loads((home / "active.json").read_text(encoding="utf-8"))
    active = data["active"]
    if not isinstance(active, str) or not active or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for c in active) or active in (".", ".."):
        raise ValueError("ungueltige Versionskennung")
    env = home / "versions" / active
    exe = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not exe.is_file():
        raise FileNotFoundError(str(exe))
    sys.exit(subprocess.call([str(exe), "-m", "mao_cli", *sys.argv[1:]]))
except (ValueError, KeyError, OSError) as exc:
    print(f"MaO-Starter: Keine gueltige Installation: {exc}", file=sys.stderr)
    sys.exit(7)
'''


def _create_launcher(home: Path) -> Path:
    bindir = home / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    source = bindir / "mao-launcher.py"
    source.write_text(LAUNCHER, encoding="utf-8")
    base_python = str(Path(getattr(sys, "_base_executable", sys.executable)).resolve())
    if os.name == "nt":
        launcher = bindir / "mao.cmd"
        launcher.write_text(f'@echo off\r\n"{base_python}" "%~dp0mao-launcher.py" %*\r\n', encoding="utf-8")
    else:
        import shlex
        launcher = bindir / "mao"
        launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(base_python) + ' "$(dirname "$0")/mao-launcher.py" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return launcher


def install_managed(wheel: Path, version: str, home: Path | None = None) -> tuple[Path, str | None]:
    """Installiert in neuer venv und schaltet nur nach Selbsttest um.

    Rückgabe: (Launcher, Kennung der vorherigen Version oder None).
    """
    if not VERSION_ID.fullmatch(version) or version in (".", ".."):
        raise InstallError("Ungültige Version")
    if not wheel.is_file():
        raise InstallError("Update-Wheel nicht vorhanden")
    home = home_path(home)
    with _update_lock(home):
        state = read_state(home)
        previous = state["active"]
        versions = home / "versions"
        versions.mkdir(parents=True, exist_ok=True)
        if previous == version:
            raise InstallError(f"Version {version} ist bereits aktiv")
        target = versions / version
        if target.exists():
            # Retained environments may be selected during rollback; never overwrite them.
            _verify_environment(target, version)
        else:
            # venvs sind nicht zuverlässig verschiebbar: Aktivierungsskripte
            # und Paketmetadaten können absolute Installationspfade enthalten.
            # Das Ziel bleibt bis zur bestandenen Prüfung INAKTIV.
            try:
                target.mkdir(parents=True, exist_ok=False)
                _install_wheel(target, wheel, version)
            except Exception:
                shutil.rmtree(target, ignore_errors=True)
                raise
        launcher = _create_launcher(home)
        _write_state(home, version, previous)
        return launcher, previous


def rollback_managed(home: Path | None = None) -> str:
    home = home_path(home)
    with _update_lock(home):
        state = read_state(home)
        previous = state["previous"]
        if not previous:
            raise InstallError("Keine vorherige Version für einen Rollback verfügbar")
        candidate = home / "versions" / previous
        _verify_environment(candidate, previous)
        _write_state(home, previous, state["active"])
        return previous


def managed_status(home: Path | None = None) -> str:
    home = home_path(home)
    state = read_state(home)
    return (f"Verwaltete Installation: {home}\n"
            f"Aktiv: {state['active'] or 'noch keine'}\n"
            f"Rollback: {state['previous'] or 'nicht verfügbar'}\n"
            f"Starter: {home / 'bin' / ('mao.cmd' if os.name == 'nt' else 'mao')}")
