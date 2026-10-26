"""Zentrale Konfiguration und explizit registrierte Jahrgangs-Workspaces."""
from __future__ import annotations

import os
import configparser
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import ConfigurationError

YEAR_RE = re.compile(r"^\d{4}-\d{2}$")
SETTINGS = "settings.yaml"


CONFIG_FOLDER = "config"      # <MaO-Ordner>/config: Einstellungen, Pipeline, Profile, Zugangsdaten
TEX_FOLDER = "tex"            # <MaO-Ordner>/tex: LaTeX-Vorlagen
JAHRGANG_FOLDER = "Jahrgang"  # <MaO-Ordner>/Jahrgang/<MaO-Nr>_<Schuljahr>: Jahrgangsordner, automatisch erkannt
JAHRGANG_RE = re.compile(r"^(\d{1,4})_(\d{4}-\d{2})$")
POINTER = "mao-ordner.txt"    # nur lokal: merkt sich den MaO-Ordner dieses Rechners


def user_state_dir() -> Path:
    """Rechnerlokaler Ordner (nicht synchronisieren): Verweis auf den MaO-Ordner."""
    if os.name == "nt":
        return (Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "MaO").resolve()
    return (Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "mao").resolve()


def normalize_config_dir(path: Path) -> Path:
    """Akzeptiert auch den MaO-Ordner selbst und liefert dessen ``config``-Unterordner."""
    path = path.expanduser().resolve()
    if not (path / SETTINGS).is_file() and (path / CONFIG_FOLDER / SETTINGS).is_file():
        return path / CONFIG_FOLDER
    return path


def find_config_upwards(start: Path) -> Path | None:
    """Sucht von ``start`` aufwärts nach ``<MaO-Ordner>/config/settings.yaml``."""
    start = start.resolve()
    for folder in (start, *start.parents):
        if (folder / CONFIG_FOLDER / SETTINGS).is_file():
            return folder / CONFIG_FOLDER
    return None


def read_pointer() -> Path | None:
    try:
        text = (user_state_dir() / POINTER).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    return Path(text) if text else None


def write_pointer(mao_folder: Path) -> Path:
    """Merkt sich den MaO-Ordner nur für diesen Rechner."""
    config = normalize_config_dir(mao_folder)
    if not (config / SETTINGS).is_file():
        raise ConfigurationError(f"Kein MaO-Ordner: {SETTINGS} fehlt in {config}. Zuerst 'mao config init <ORDNER>' ausführen.")
    state = user_state_dir()
    state.mkdir(parents=True, exist_ok=True)
    target = state / POINTER
    tmp = target.with_suffix(".tmp")
    tmp.write_text(str(config) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


CONFLICT_RE = re.compile(r"\((?:[^)]*\s)?(?:conflicted copy|konflikt)[^)]*\)", re.IGNORECASE)


def find_conflict_copies(config_dir: Path, limit: int = 20) -> list[Path]:
    """Konfliktkopien von Nextcloud/Dropbox im Konfigurations- und Vorlagenordner (nur Hinweis)."""
    roots = [config_dir]
    if config_dir.name == CONFIG_FOLDER:
        roots.append(config_dir.parent / TEX_FOLDER)
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and CONFLICT_RE.search(path.name):
                found.append(path)
                if len(found) >= limit:
                    return found
    return found


def resolve_config_dir(cli_value: Path | None = None, cwd: Path | None = None) -> tuple[Path, str]:
    """Konfigurationsordner und Quelle.

    Reihenfolge: ``--config-dir``, ``MAO_CONFIG``, Suche aufwärts vom aktuellen
    Ordner, Verweis (``mao config use``), Editable-Quellordner, Benutzerordner.
    ``--config-dir`` und ``MAO_CONFIG`` dürfen auch den MaO-Ordner selbst nennen.
    """
    if cli_value is not None:
        return normalize_config_dir(cli_value), "--config-dir"
    env = os.environ.get("MAO_CONFIG")
    if env:
        return normalize_config_dir(Path(env)), "MAO_CONFIG"
    found = find_config_upwards(cwd or Path.cwd())
    if found is not None:
        return found, "aktueller Ordner"
    pointed = read_pointer()
    if pointed is not None and (pointed / SETTINGS).is_file():
        return pointed.resolve(), "Verweis (mao config use)"
    # Editable Installation: der Quellordner enthaelt pyproject.toml und
    # den privaten Konfigurationsordner direkt neben mao_cli/.
    project = Path(__file__).resolve().parent.parent
    source_config = project / CONFIG_FOLDER
    if (project / "pyproject.toml").is_file() and source_config.is_dir():
        return source_config.resolve(), "Quellordner"
    return user_state_dir(), "Standardordner"


def config_dir_for(cli_value: Path | None = None) -> Path:
    return resolve_config_dir(cli_value)[0]


def check_year(value: str) -> str:
    if not YEAR_RE.fullmatch(value) or int(value[5:]) != (int(value[:4]) + 1) % 100:
        raise ConfigurationError(f"Ungültiges Schuljahr {value!r}; erwartet z. B. 2026-27")
    return value


def _default_settings() -> dict[str, Any]:
    return {"current_year": None, "workspaces": {}, "defaults": {"profile": "vollpaket"}}


def jahrgang_folder_name(mao_number: int, year: str) -> str:
    return f"{mao_number}_{check_year(year)}"


def discover_jahrgaenge(mao_home: Path) -> dict[str, dict[str, Any]]:
    """Erkennt ``<MaO-Ordner>/Jahrgang/<Nr>_<Schuljahr>`` ohne Registrierung."""
    base = mao_home / JAHRGANG_FOLDER
    found: dict[str, dict[str, Any]] = {}
    if not base.is_dir():
        return found
    for folder in sorted(base.iterdir()):
        match = JAHRGANG_RE.fullmatch(folder.name)
        if not folder.is_dir() or match is None:
            continue
        number, year = int(match.group(1)), match.group(2)
        try:
            check_year(year)
        except ConfigurationError:
            continue
        if year in found:
            raise ConfigurationError(
                f"Schuljahr {year} kommt in {JAHRGANG_FOLDER}/ doppelt vor: {found[year]['root'].name} und {folder.name}")
        if number < 1:
            continue
        found[year] = {"root": folder, "mao": number}
    return found


def load_settings(config_dir: Path, *, allow_missing: bool = False) -> dict[str, Any]:
    path = config_dir / SETTINGS
    if not path.is_file():
        if allow_missing:
            return _default_settings()
        raise ConfigurationError(f"Zentrale Konfiguration fehlt: {path}. Zuerst 'mao config init' ausführen.")
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Einstellungen nicht lesbar: {path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise ConfigurationError("settings.yaml muss eine YAML-Zuordnung enthalten")
    extra = set(doc) - {"current_year", "workspaces", "defaults"}
    if extra:
        raise ConfigurationError(f"Unbekannte Einstellungen: {', '.join(sorted(extra))}")
    workspaces = doc.get("workspaces", {})
    if not isinstance(workspaces, dict):
        raise ConfigurationError("'workspaces' muss eine Zuordnung aus Jahrgang und root sein")
    normalized: dict[str, dict[str, Any]] = {}
    for year, entry in workspaces.items():
        if not isinstance(year, str):
            raise ConfigurationError("Schuljahre müssen als Strings angegeben werden: '2026-27'")
        check_year(year)
        if not isinstance(entry, dict) or not isinstance(entry.get("root"), str) or not entry["root"].strip():
            raise ConfigurationError(f"Workspace {year}: 'root' fehlt")
        unexpected = set(entry) - {"root", "status", "mao"}
        if unexpected:
            raise ConfigurationError(f"Workspace {year}: unbekannte Felder {sorted(unexpected)}")
        root = Path(entry["root"]).expanduser()
        if not root.is_absolute():
            # Relative Pfade gelten ab dem MaO-Ordner (Elternordner von config/);
            # so funktioniert dieselbe settings.yaml auf jedem Rechner der Nextcloud.
            if config_dir.name != CONFIG_FOLDER:
                raise ConfigurationError(f"Workspace {year}: 'root' muss ein absoluter Pfad sein")
            root = config_dir.parent / root
        status = entry.get("status", "planned")
        if status not in ("current", "archived", "planned"):
            raise ConfigurationError(f"Workspace {year}: ungültiger Status")
        mao = entry.get("mao")
        if mao is not None and (type(mao) is not int or mao < 1):
            raise ConfigurationError(f"Workspace {year}: 'mao' muss eine positive Ganzzahl sein")
        normalized[year] = {"root": str(root.resolve()), "status": status}
        if mao is not None:
            normalized[year]["mao"] = mao
    current = doc.get("current_year")
    if config_dir.name == CONFIG_FOLDER:
        known = {os.path.normcase(str(item["root"])) for item in normalized.values()}
        for year, item in discover_jahrgaenge(config_dir.parent).items():
            root = Path(item["root"]).resolve()
            if year in normalized or os.path.normcase(str(root)) in known:
                continue
            normalized[year] = {"root": str(root), "status": "archived", "mao": item["mao"], "auto": True}
    if current is not None:
        check_year(current)
        if current not in normalized:
            raise ConfigurationError(f"'current_year' {current} ist nicht als Workspace registriert")
    for year, item in normalized.items():
        if item.get("auto"):
            item["status"] = "current" if year == current else "archived"
    defaults = doc.get("defaults", {})
    if not isinstance(defaults, dict) or set(defaults) - {"profile"}:
        raise ConfigurationError("'defaults' unterstützt nur 'profile'")
    profile = defaults.get("profile", "vollpaket")
    if not isinstance(profile, str) or not profile.strip():
        raise ConfigurationError("Ungültiges Standardprofil")
    # Doppelte Pfade bergen die Gefahr, Daten zweier Jahre zu vermischen.
    paths = [os.path.normcase(item["root"]) for item in normalized.values()]
    if len(paths) != len(set(paths)):
        raise ConfigurationError("Mehrere Jahrgänge verweisen auf dasselbe Arbeitsverzeichnis")
    return {"current_year": current, "workspaces": normalized, "defaults": {"profile": profile}}


def _portable_roots(config_dir: Path, doc: dict[str, Any]) -> dict[str, Any]:
    """Ordner innerhalb des MaO-Ordners relativ speichern (rechnerunabhängig).

    Automatisch erkannte Jahrgangsordner werden nicht gespeichert; sie ergeben sich aus dem Ordner.
    """
    out = dict(doc)
    out["workspaces"] = {}
    base = config_dir.resolve().parent
    for year, entry in doc.get("workspaces", {}).items():
        if entry.get("auto"):
            continue
        entry = dict(entry)
        if config_dir.name != CONFIG_FOLDER:
            out["workspaces"][year] = entry
            continue
        root = Path(str(entry.get("root", "")))
        if root.is_absolute():
            try:
                entry["root"] = root.resolve().relative_to(base).as_posix()
            except ValueError:
                pass
        out["workspaces"][year] = entry
    return out


def save_settings(config_dir: Path, doc: dict[str, Any]) -> Path:
    """Validiert und veröffentlicht settings.yaml über atomaren Dateiersatz."""
    config_dir.mkdir(parents=True, exist_ok=True)
    auto_years = [(year, entry.get("mao")) for year, entry in doc.get("workspaces", {}).items() if entry.get("auto")]
    doc = _portable_roots(config_dir, doc)
    # Erst validieren, bevor die vorhandene Datei verändert wird.
    tmp = None
    path = config_dir / SETTINGS
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=config_dir,
                                         prefix=".settings-", suffix=".tmp", delete=False) as handle:
            tmp = Path(handle.name)
            yaml.safe_dump(doc, handle, allow_unicode=True, sort_keys=False)
        # Gleiche strukturelle Validierung wie beim Lesen, ohne die echte Datei zu überschreiben.
        # Für den temporären Namen erst temporäres Verzeichnis mit Symlinks vermeiden:
        # Eine rein strukturelle Validierung per interner Funktion.
        _validate_document(tmp, config_dir.name, auto_years)
        os.replace(tmp, path)
        return path
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _validate_document(path: Path, folder_name: str = CONFIG_FOLDER,
                       auto_years: list[tuple[str, Any]] | None = None) -> None:
    # Den Parser über einen ausschließlich temporär vorhandenen Unterordner benutzen.
    # settings.yaml wird dabei nie mit der produktiven Datei vertauscht.
    with tempfile.TemporaryDirectory(prefix="mao-settings-") as td:
        check_dir = Path(td) / folder_name
        check_dir.mkdir()
        shutil.copyfile(path, check_dir / SETTINGS)
        for year, number in auto_years or []:
            # Automatisch erkannte Jahrgänge für die Prüfung nachbilden.
            (Path(td) / JAHRGANG_FOLDER / jahrgang_folder_name(int(number), year)).mkdir(parents=True)
        load_settings(check_dir)


def init_config(config_dir: Path, defaults_dir: Path) -> list[Path]:
    config_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    if config_dir.name == CONFIG_FOLDER:
        # Zentraler MaO-Ordner: Platz für die LaTeX-Vorlagen neben config/.
        tex = config_dir.parent / TEX_FOLDER
        if not tex.exists():
            tex.mkdir()
            created.append(tex)
    for filename in ("pipeline.yaml", "profiles.ini", "tables.ini"):
        dest = config_dir / filename
        if not dest.exists():
            shutil.copyfile(defaults_dir / filename, dest)
            created.append(dest)
    if not (config_dir / SETTINGS).exists():
        created.append(save_settings(config_dir, _default_settings()))
    # Alte, bereits personalisierte tables.ini um neue Kennungen ergänzen,
    # ohne bestehende Zuordnungen oder Kommentare umzuschreiben.
    tables_file = config_dir / "tables.ini"
    tables_parser = configparser.ConfigParser(interpolation=None)
    with tables_file.open(encoding="utf-8-sig") as handle:
        tables_parser.read_file(handle)
    if not tables_parser.has_section("table"):
        raise ConfigurationError(f"tables.ini benötigt Abschnitt [table]: {tables_file}")
    # Bestehende Konfigurationen migrieren, ohne den echten Namen zu verlieren.
    # Die GF-SQL-Konfiguration versteht alte {{table.students_view}}-Tokens
    # weiterhin; physisch wird nur students_table gepflegt.
    if (tables_parser.has_option("table", "students_view") and
            not tables_parser.has_option("table", "students_table")):
        original = tables_file.read_text(encoding="utf-8-sig")
        lines = original.splitlines(keepends=True)
        in_table = False
        for i, line in enumerate(lines):
            if line.lstrip().startswith("["):
                in_table = line.strip().lower() == "[table]"
            if in_table and re.match(r"^\s*students_view\s*=", line):
                lines[i] = re.sub(r"^(\s*)students_view(\s*=)", r"\1students_table\2", line, count=1)
                break
        else:
            raise ConfigurationError("tables.ini: students_view konnte nicht umbenannt werden")
        tables_file.write_text("".join(lines), encoding="utf-8")
        tables_parser.set("table", "students_table", tables_parser.get("table", "students_view"))
        tables_parser.remove_option("table", "students_view")
    missing = {
        "students_table": "DEMO_CURRENT_STUDENTS",
        "current_groups_view": "DEMO_CURRENT_GROUP_VIEW",
    }
    missing = {key: value for key, value in missing.items() if not tables_parser.has_option("table", key)}
    if missing:
        content = tables_file.read_text(encoding="utf-8-sig")
        lines = content.splitlines(keepends=True)
        section_index = next(i for i, line in enumerate(lines)
                             if line.strip().lower() == "[table]")
        insert_at = next((i for i in range(section_index + 1, len(lines))
                          if lines[i].lstrip().startswith("[")), len(lines))
        addition = "\n# Ergänzte GF-Importdatenquellen (reale Namen privat anpassen)\n" + "".join(
            f"{key} = {value}\n" for key, value in missing.items())
        if insert_at > 0 and not lines[insert_at - 1].endswith(("\n", "\r")):
            addition = "\n" + addition
        lines.insert(insert_at, addition)
        tables_file.write_text("".join(lines), encoding="utf-8")
    # Die kopierbaren Vorlagen dienen nur als paketinterne Quellen. Im privaten
    # Konfigurationsordner werden sofort die benutzbaren Dateinamen angelegt.
    # Bestehende Dateien niemals überschreiben oder löschen.
    private_defaults = {
        "db.config": ("# Private MySQL-Zugangsdaten; niemals in Git veröffentlichen.\n"
                      "host=localhost\nport=3306\nuser=\npassword=\ndatabase=\n"),
        "gf.config": ("# Private Gravity-Forms-Zugangsdaten; niemals in Git veröffentlichen.\n"
                      "[oauth1]\napi_base=https://example.org/wp-json/gf/v2\n"
                      "consumer_key=\nconsumer_secret=\n"),
    }
    for filename, default_content in private_defaults.items():
        destination = config_dir / filename
        if not destination.exists():
            old_example = config_dir / (filename + ".example")
            if old_example.is_file():
                shutil.copyfile(old_example, destination)
            else:
                destination.write_text(default_content, encoding="utf-8")
            created.append(destination)
    gf_import = config_dir / "gf-import.yaml"
    if not gf_import.exists():
        old_example = config_dir / "gf-import.yaml.example"
        # Unveränderte alte DEMO-Vorlagen durch die neue Platzhalter-Vorlage
        # ersetzen; bereits individuell angepasste Vorlagen aber übernehmen.
        template = defaults_dir / "gf-import.yaml.example"
        if old_example.is_file() and "DEMO_" not in old_example.read_text(encoding="utf-8"):
            template = old_example
        shutil.copyfile(template, gf_import)
        created.append(gf_import)
    return created


@dataclass(frozen=True)
class WorkspaceSelection:
    directory: Path
    year: str | None
    mao_number: int | None


def _registered_year_for(settings: dict[str, Any], target: Path) -> str | None:
    """Jahrgang, dessen registrierter Ordner ``target`` ist oder enthält.

    Ohne diese Zuordnung würde ein per ``--workspace`` angegebener Ordner eines
    archivierten Jahrgangs als unregistriert gelten und die Archivsperre umgehen.
    """
    wanted = os.path.normcase(str(target))
    best: tuple[int, str] | None = None
    for name, item in settings["workspaces"].items():
        root = os.path.normcase(str(Path(item["root"]).resolve()))
        if wanted == root or wanted.startswith(root.rstrip(os.sep) + os.sep):
            # Bei verschachtelten Ordnern gewinnt der genaueste (längste) Treffer.
            if best is None or len(root) > best[0]:
                best = (len(root), name)
    return best[1] if best else None


def resolve_workspace(settings: dict[str, Any], *, year: str | None = None,
                      workspace: Path | None = None, allow_unregistered: bool = False,
                      cwd: Path | None = None) -> WorkspaceSelection:
    if year is None and workspace is None:
        # Wer in einem registrierten Jahrgangsordner arbeitet, meint diesen Jahrgang.
        here = _registered_year_for(settings, (cwd or Path.cwd()).resolve())
        if here is not None:
            item = settings["workspaces"][here]
            return WorkspaceSelection(Path(item["root"]), here, item.get("mao"))
    selected = year or settings["current_year"]
    if year is not None:
        check_year(year)
        if year not in settings["workspaces"]:
            raise ConfigurationError(f"Jahrgang {year} nicht registriert. Mit 'mao workspace add' anlegen.")
    entry = settings["workspaces"].get(selected) if selected else None
    if workspace is not None:
        target = workspace.expanduser().resolve()
        # Wird year explizit angegeben, darf kein anderer Pfad benutzt werden.
        if year is not None and entry and target != Path(entry["root"]).resolve():
            raise ConfigurationError("--year und --workspace verweisen auf verschiedene Ordner")
        if year is None:
            # Ein registrierter Ordner behält seinen Jahrgang, damit die
            # Sperre für archivierte Jahrgänge auch bei --workspace greift.
            owner = _registered_year_for(settings, target)
            if owner is not None:
                return WorkspaceSelection(target, owner, settings["workspaces"][owner].get("mao"))
            return WorkspaceSelection(target, None, None)
        return WorkspaceSelection(target, selected, entry.get("mao") if entry else None)
    if entry:
        return WorkspaceSelection(Path(entry["root"]), selected, entry.get("mao"))
    if allow_unregistered:
        return WorkspaceSelection(Path.cwd().resolve(), None, None)
    raise ConfigurationError("Kein aktueller Workspace konfiguriert. 'mao jahrgang new' (oder 'mao jahrgang use') ausführen")


def write_wrapper(config_dir: Path, year: str, root: Path) -> list[Path]:
    """Erzeugt reine CLI-Wrapper; kein Python-Code und keine Geheimnisse."""
    check_year(year)
    config_dir = config_dir.resolve()
    root = root.resolve()
    for value in (str(config_dir), year):
        if any(c in value for c in ('"', '%', '\r', '\n', '!', '\x00')):
            raise ConfigurationError("Pfad enthält für Windows-CMD nicht unterstützte Sonderzeichen")
    root.mkdir(parents=True, exist_ok=True)
    bat = root / "mao-hier.cmd"
    sh = root / "mao-hier.sh"
    if bat.exists() or sh.exists():
        raise ConfigurationError("Wrapper existiert bereits; nichts überschrieben")
    bat.write_text(f'@echo off\nmao --config-dir "{config_dir}" --year "{year}" %*\n', encoding="utf-8", newline="\r\n")
    import shlex
    sh.write_text("#!/bin/sh\nexec mao --config-dir " + shlex.quote(str(config_dir)) + " --year "
                  + shlex.quote(year) + ' "$@"\n', encoding="utf-8", newline="\n")
    sh.chmod(sh.stat().st_mode | 0o111)
    return [bat, sh]
