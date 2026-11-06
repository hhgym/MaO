"""Kommandozeile der MaO-Pipeline (neuer Befehlssatz vor Version 2)."""
from __future__ import annotations

import argparse
import logging
import random
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Optional, Sequence

import yaml

from . import __version__
from .common import LOGGER, can_reach, ensure_parent, write_text
from .config import (
    ConfigurationError, apply_profile, load_config, load_profiles,
    load_tables, resolve_table_placeholders, select_latex_jobs, validate_config, validate_profiles,
)
from .db import DB, active_jahrgang_db, current_MaO_number_db, current_MaO_number_fallback, load_db_config
from .exports import ExportPlan, load_datasets, render_plan, run_exports
from .latex import run_latex_jobs
from .updater import UpdateError, plan_update, run_update, update_notice
from .workspaces import (resolve_config_dir, find_conflict_copies, write_pointer, CONFIG_FOLDER, TEX_FOLDER, JAHRGANG_FOLDER, jahrgang_folder_name, normalize_config_dir, load_settings, save_settings, init_config,
                         resolve_workspace, write_wrapper, check_year)
from .year_ops import preliminary_plan, database_preflight
from .aufsichten import (PLAN_FILE, Draft, apply_plan, check_aufsichten, export_plan, interactive_supervision, load_supervision_model,
                         set_supervision)
from .aufsichten_tui import tui_supervision
from .einsatz import DEFAULT_FORM_ID, PLAN_FILE as EINSATZ_PLAN_FILE, Settings as EinsatzSettings, run_einsatz
from .extern import ExternError, assign_extern_rooms, import_extern
from .gf_import import import_participants, ImportErrorGF, write_import_report
from .schools import DEFAULT_URL as DEFAULT_SCHOOLS_URL, SchoolError, read_file, sync_schools
from .rooms import (RoomError, assign_rooms, create_year_rules, interactive_assign, load_room_rules,
                    resolve_rules_file, set_room, write_room_report)
from .validation import validate_online

DEFAULTS = Path(__file__).resolve().parent / "defaults"
FILES = ("pipeline.yaml", "profiles.ini", "tables.ini")
DEFAULT_PROFILE = "vollpaket"


class LogCounter(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.warnings = 0
        self.errors = 0

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR:
            self.errors += 1
        elif record.levelno >= logging.WARNING:
            self.warnings += 1


def setup_logging(verbose: bool, log_file: Optional[Path] = None) -> LogCounter:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file is not None:
        ensure_parent(log_file)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    counter = LogCounter()
    handlers.append(counter)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S", handlers=handlers, force=True,
    )
    return counter


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="mao", description="MaO: Datenbankexporte und LaTeX-Dokumente")
    parser.add_argument("--version", action="version", version=f"mao {__version__}")
    # Die drei globalen Angaben werden vor UND nach dem Unterbefehl akzeptiert.
    parser.add_argument("--config-dir", type=Path, metavar="ORDNER", help="Zentrale Konfiguration (alternativ MAO_CONFIG)")
    parser.add_argument("--year", metavar="YYYY-YY", help="Registriertes Schuljahr")
    parser.add_argument("--workspace", type=Path, metavar="ORDNER", help="Expliziter Workspace")
    commands = parser.add_subparsers(dest="command", required=True)

    def global_overrides(sub: argparse.ArgumentParser) -> None:
        # SUPPRESS verhindert, dass ein leerer Subparser-Wert globale Optionen überschreibt.
        sub.add_argument("--config-dir", type=Path, default=argparse.SUPPRESS)
        sub.add_argument("--year", default=argparse.SUPPRESS)
        sub.add_argument("--workspace", type=Path, default=argparse.SUPPRESS)


    def common(sub: argparse.ArgumentParser, *, profile: bool = False) -> None:
        global_overrides(sub)
        if profile:
            sub.add_argument("--profile", default=None, metavar="NAME",
                             help="Ausführungsprofil (Standard aus settings.yaml)")
        sub.add_argument("--verbose", action="store_true", help="Ausführliche Meldungen")
        sub.add_argument("--log-file", type=Path, metavar="DATEI", help="Zusätzliche Logdatei")

    run = commands.add_parser("run", help="Datenexporte und optional LaTeX ausführen")
    common(run, profile=True)
    run.add_argument("--no-latex", action="store_true", help="LaTeX-Jobs überspringen")
    run.add_argument("--cleanup-latex", action="store_true",
                     help="LaTeX-Hilfsdateien (.aux, .log, ...) nach erfolgreichem Lauf löschen")
    run.add_argument("--mao", type=int, help="MaO-Nummer für Dokumente (nicht Datenquelle!)")
    run.add_argument("--job", action="append", metavar="NAME",
                       help="Nur diesen LaTeX-Job ausführen (auch wenn ausgeschaltet; mehrfach angebbar)")

    plan = commands.add_parser("plan", help="Exportplan ermitteln (kein CSV/PDF schreiben)")
    common(plan, profile=True)
    plan.add_argument("--output", type=Path, metavar="MARKDOWN", help="Plan als Markdown speichern")
    plan.add_argument("--no-latex", action="store_true", help="LaTeX-Jobs im Plan ausblenden")
    plan.add_argument("--mao", type=int, help="MaO-Nummer für Dokumente (nicht Datenquelle!)")

    latex = commands.add_parser("latex", help="Nur LaTeX mit vorhandenen Dateien ausführen")
    latex.add_argument("--cleanup-latex", action="store_true",
                       help="LaTeX-Hilfsdateien (.aux, .log, ...) nach erfolgreichem Lauf löschen")
    common(latex, profile=True)
    latex.add_argument("--mao", type=int, help="MaO-Nummer für Dokumente (nicht Datenquelle!)")
    latex.add_argument("--job", action="append", metavar="NAME",
                       help="Nur diesen LaTeX-Job ausführen (auch wenn ausgeschaltet; mehrfach angebbar)")

    validate = commands.add_parser("validate", help="Konfiguration prüfen; optional mit Datenbank und Gravity Forms")
    common(validate, profile=True)
    validate.add_argument("--online", action="store_true", help="Zusätzlich Datenbank, SQL-Quellen und Gravity-Forms-API lesen")

    profiles = commands.add_parser("profiles", help="Verfügbare Ausführungsprofile anzeigen")
    common(profiles)

    config_cmd = commands.add_parser("config", help="Zentrale Konfiguration verwalten")
    global_overrides(config_cmd)
    csub = config_cmd.add_subparsers(dest="config_command", required=True)
    for action in ("init", "show"):
        item = csub.add_parser(action)
        global_overrides(item)
        if action == "init":
            item.add_argument("folder", nargs="?", type=Path, metavar="MAO-ORDNER",
                              help="Zentraler MaO-Ordner: legt <ORDNER>/config und <ORDNER>/tex an")
    use = csub.add_parser("use", help="Zentralen MaO-Ordner auf diesem Rechner merken")
    global_overrides(use)
    use.add_argument("folder", type=Path, metavar="MAO-ORDNER")

    jahr = commands.add_parser("jahrgang", help="Jahrgangsordner unter <MaO>/Jahrgang/ anzeigen, anlegen, wählen")
    global_overrides(jahr)
    jsub = jahr.add_subparsers(dest="jahrgang_command", required=True)
    jlist = jsub.add_parser("list", help="Erkannte Jahrgänge anzeigen")
    global_overrides(jlist)
    jnew = jsub.add_parser("new", help="Jahrgangsordner anlegen (ohne Angaben: aktiver Jahrgang aus der Datenbank)")
    global_overrides(jnew)
    jnew.add_argument("--nr", type=int, metavar="MAO-NR", help="MaO-Nummer (zusammen mit --year)")
    jnew.add_argument("--current", action="store_true", help="Zum aktuellen Jahrgang machen (aus der Datenbank: immer)")
    juse = jsub.add_parser("use", help="Aktuellen Jahrgang festlegen (MaO-Nummer oder Schuljahr)")
    global_overrides(juse)
    juse.add_argument("which", metavar="MAO-NR|YYYY-YY")

    work = commands.add_parser("workspace", help="Sonderfälle: Jahrgangsordner außerhalb von <MaO>/Jahrgang/ registrieren")
    global_overrides(work)
    wsub = work.add_subparsers(dest="workspace_command", required=True)
    for action in ("list", "add", "select", "wrapper"):
        item = wsub.add_parser(action)
        global_overrides(item)
        if action != "list":
            item.add_argument("target_year", metavar="YYYY-YY")
        if action == "add":
            item.add_argument("--root", type=Path, required=True)
            item.add_argument("--no-wrapper", action="store_true", help=argparse.SUPPRESS)
            item.add_argument("--wrapper", action="store_true",
                              help="mao-hier.cmd/.sh mit festem Konfigurationspfad anlegen (selten nötig)")

    gf = commands.add_parser("import-gf", help="Gravity-Forms-Meldungen sicher in die aktuelle Organisationstabelle importieren")
    common(gf)
    gf_mode = gf.add_mutually_exclusive_group()
    gf_mode.add_argument("--apply", action="store_true", help="Erstbefüllung der LEEREN Organisationstabelle durchführen")
    gf_mode.add_argument("--dry-run", action="store_true", help="Nur Meldungen und Zuordnungen prüfen (Standard)")
    gf.add_argument("--abgleich", action="store_true",
                    help="Befüllte Organisationstabelle mit dem Formular abgleichen: neue Meldungen und nicht mehr "
                         "vorhandene zeigen; mit --apply nach Rückfrage nachtragen bzw. löschen")
    gf.add_argument("--no-mandatory", action="store_true", help="Pflichtteilnahmen ausnahmsweise nicht ergänzen")
    gf.add_argument("--report", type=Path, metavar="DATEI.json|DATEI.csv",
                    help="Prüfbericht im Workspace speichern (ohne vorhandene Datei zu überschreiben)")

    rooms = commands.add_parser("raumzuteilung", help="Räume (raum_id) nach raumregeln.yaml zuteilen")
    common(rooms)
    rooms_mode = rooms.add_mutually_exclusive_group()
    rooms_mode.add_argument("--apply", action="store_true", help="Zuteilung in die Datenbank schreiben")
    rooms_mode.add_argument("--dry-run", action="store_true", help="Nur planen und berichten (Standard)")
    rooms.add_argument("--seed", type=int, metavar="N",
                       help="Zufallswert für eine reproduzierbare Verteilung (wird im Log genannt)")
    rooms.add_argument("--plan-id", metavar="ID",
                       help="Mit --apply: nur schreiben, wenn der Plan der im Dry-Run genannten Plan-ID entspricht")
    rooms.add_argument("--ueberschreiben", action="store_true",
                       help="Vorhandene Raumzuordnungen verwerfen und alles neu verteilen (sonst bleiben sie erhalten)")
    rooms.add_argument("--extern", action="store_true",
                       help="Stattdessen die externen Teilnehmenden zuteilen (Räume aus raumregeln.yaml, Abschnitt extern)")
    rooms.add_argument("--regeln-anlegen", action="store_true",
                       help="raumregeln.yaml im Jahrgangsordner aus der zentralen Vorlage anlegen (überschreibt nie) und beenden")
    rooms.add_argument("--report", type=Path, metavar="DATEI.json|DATEI.csv",
                       help="Bericht im Workspace speichern (ohne vorhandene Datei zu überschreiben)")

    setroom = commands.add_parser("raum-setzen", help="Teilnehmenden von Hand einen Raum zuweisen (z. B. nach der Raumzuteilung)")
    common(setroom)
    set_mode = setroom.add_mutually_exclusive_group()
    set_mode.add_argument("--apply", action="store_true", help="Zuordnung in die Datenbank schreiben")
    set_mode.add_argument("--dry-run", action="store_true", help="Nur prüfen und berichten (Standard)")
    setroom.add_argument("--interaktiv", action="store_true", help="Teilnehmende ohne Raum Schritt für Schritt zuteilen (Terminal nötig)")
    setroom.add_argument("--raum", metavar="CODE", help="Raumcode (wie in der Raumtabelle)")
    setroom.add_argument("--matrikel", nargs="+", type=int, metavar="NUMMER", help="Matrikelnummer(n)")
    setroom.add_argument("--ueberschreiben", action="store_true", help="Auch Teilnehmende umsetzen, die schon einen anderen Raum haben")
    setroom.add_argument("--trotzdem", action="store_true", help="Ohne Wirkung (Handzuteilung ignoriert die Kapazität ohnehin; nur noch aus Kompatibilität)")

    sup = commands.add_parser("aufsichten", help="Aufsichten der Lehrkräfte prüfen (Standard) oder setzen (--lehrkraft / --interaktiv)")
    common(sup)
    sup_mode = sup.add_mutually_exclusive_group()
    sup_mode.add_argument("--apply", action="store_true", help="Beim Setzen in die Datenbank schreiben")
    sup_mode.add_argument("--dry-run", action="store_true", help="Beim Setzen nur prüfen und berichten (Standard)")
    sup.add_argument("--lehrkraft", nargs="+", metavar="KÜRZEL", help="Aufsicht(en) für diese Lehrkräfte setzen")
    sup.add_argument("--raum", metavar="CODE", help="Raumcode (nur Rolle Standard)")
    sup.add_argument("--rolle", default="Standard", choices=["Standard", "Reserve", "Fachaufsicht", "Abiturerstellung"],
                     help="Rolle; Reserve, Fachaufsicht und Abiturerstellung gehören zu keinem Raum")
    sup.add_argument("--entfernen", action="store_true", help="Die genannten Einträge entfernen statt eintragen")
    sup.add_argument("--plan-exportieren", nargs="?", const=Path("aufsichten-plan.yaml"), type=Path, metavar="DATEI.yaml",
                     help="Aktuellen Stand als Plandatei im Workspace anlegen (überschreibt nie); zum Bearbeiten aller Aufsichten auf einmal")
    sup.add_argument("--plan", type=Path, metavar="DATEI.yaml",
                     help="Plandatei mit der Datenbank abgleichen (Dry-Run, --apply schreibt alle Änderungen in einer Transaktion)")
    sup.add_argument("--interaktiv", action="store_true", help="Alle Aufsichten in einer Tabelle mit Cursor bearbeiten und gesammelt abschicken (Terminal nötig)")
    sup.add_argument("--neu", action="store_true",
                     help="Mit --interaktiv: vorhandenen Entwurf (aufsichten-plan.yaml bzw. --plan DATEI) ignorieren und aus der Datenbank starten")
    sup.add_argument("--einfach", action="store_true", help="Mit --interaktiv: zeilenweise Eingabe statt Tabellenansicht (z. B. in einfachen Konsolen)")
    sup.add_argument("--report", type=Path, metavar="DATEI.json", help="Bericht im Workspace speichern")

    einsatz = commands.add_parser("einsatz", help="Einsatz der Oberstufe (Stufe 11/12) planen: Aufsicht, Hof, Reserve, 5. PK")
    common(einsatz)
    einsatz_mode = einsatz.add_mutually_exclusive_group()
    einsatz_mode.add_argument("--apply", action="store_true", help="In die Einsatztabelle schreiben")
    einsatz_mode.add_argument("--dry-run", action="store_true", help="Nur planen und berichten (Standard); Zwischenstand landet in einsatz-plan.yaml")
    einsatz.add_argument("--aufsichten-pro-raum", type=int, metavar="N", help="Schüler-Aufsichten je Raum (Standard 1 oder raumregeln.yaml)")
    einsatz.add_argument("--reserve", type=int, metavar="N", help="Anzahl Reserve (Standard 3 oder raumregeln.yaml)")
    einsatz.add_argument("--hof-gruppen", type=int, metavar="N", help="Schichten/Gruppen für die Hofarbeit (Standard 2 oder raumregeln.yaml)")
    einsatz.add_argument("--hof-pro-gruppe", type=int, metavar="N", help="Höchstens so viele je Hofgruppe (Standard: unbegrenzt oder raumregeln.yaml)")
    einsatz.add_argument("--ohne-raum", nargs="+", default=None, metavar="CODE", help="Diese Räume bekommen keine Schüler-Aufsicht (sonst raumregeln.yaml)")
    einsatz.add_argument("--seed", type=int, metavar="ZAHL", help="Startwert der Auslosung (sonst zufällig; steht im Entwurf)")
    einsatz.add_argument("--formular", type=int, metavar="ID", help=f"Gravity-Forms-Formular der Wünsche (Standard {DEFAULT_FORM_ID} oder raumregeln.yaml)")
    einsatz.add_argument("--ohne-wuensche", action="store_true", help="Wünsche nicht abrufen (alle gelten als ohne Wunsch)")
    einsatz.add_argument("--interaktiv", action="store_true", help="Verteilung in einer Konsolensitzung prüfen und ändern")
    einsatz.add_argument("--neu", action="store_true", help="Vorhandenen Entwurf ignorieren und neu auslosen")
    einsatz.add_argument("--plan", type=Path, metavar="DATEI.yaml", help=f"Entwurfsdatei im Workspace (Standard {EINSATZ_PLAN_FILE})")
    einsatz.add_argument("--report", type=Path, metavar="DATEI.json", help="Bericht im Workspace speichern")

    extern = commands.add_parser("import-extern", help="Anmeldungen externer Teilnehmender (Grundschulen) übernehmen")
    common(extern)
    extern_mode = extern.add_mutually_exclusive_group()
    extern_mode.add_argument("--apply", action="store_true", help="Neue Anmeldungen in die Datenbank schreiben")
    extern_mode.add_argument("--dry-run", action="store_true", help="Nur prüfen und berichten (Standard)")
    extern.add_argument("--report", type=Path, metavar="DATEI.json", help="Bericht im Workspace speichern")

    schools = commands.add_parser("schulen-sync", help="Schulverzeichnis Berlin in die Schulen-Tabelle übernehmen")
    common(schools)
    schools_mode = schools.add_mutually_exclusive_group()
    schools_mode.add_argument("--apply", action="store_true", help="Änderungen in die Datenbank schreiben")
    schools_mode.add_argument("--dry-run", action="store_true", help="Nur vergleichen und berichten (Standard)")
    schools.add_argument("--url", default=DEFAULT_SCHOOLS_URL, help="Adresse des GeoJSON (Standard: Schulverzeichnis Berlin)")
    schools.add_argument("--file", type=Path, metavar="DATEI.json",
                         help="Lokal gespeichertes GeoJSON statt des Abrufs verwenden (z. B. wenn das Netz den Abruf blockiert)")
    schools.add_argument("--report", type=Path, metavar="DATEI.json", help="Bericht im Workspace speichern")

    init_year = commands.add_parser("init-jahrgang", help="Jahrgangswechsel sicher vorprüfen (read-only)")
    global_overrides(init_year)
    init_year.add_argument("--offline", action="store_true", help="Nur geplante Prüfschritte anzeigen")
    init_year.add_argument("--dry-run", action="store_true", default=True,
                           help="Immer ohne Datenänderung (Standard)")

    update = commands.add_parser("update", help="Aktualisierung mit uv: Befehl nennen, mit --run ausführen")
    update.add_argument("legacy", nargs="?", choices=("check", "download", "install", "status", "rollback"),
                        help=argparse.SUPPRESS)
    update.add_argument("--channel", choices=("stable", "rc", "develop"), default="stable")
    update.add_argument("--version", metavar="X.Y.Z", help="Bestimmte Version statt der neuesten")
    update.add_argument("--run", action="store_true",
                        help="Update selbst ausführen (uv tool install); unter Windows in einem neuen Fenster nach dem Beenden von mao")
    update.add_argument("--force", action="store_true",
                        help="Kanal develop auch bei unverändertem Stand neu installieren")
    update.add_argument("--repository", metavar="OWNER/REPO", help="GitHub-Repository (alternativ MAO_GITHUB_REPO)")
    for ignored in ("--yes", "--home"):
        update.add_argument(ignored, nargs="?", const=True, default=None, help=argparse.SUPPRESS)
    update.add_argument("--output", type=Path, help=argparse.SUPPRESS)

    args = parser.parse_args(argv)
    if args.command == "schulen-sync" and args.report and args.report.suffix.lower() != ".json":
        parser.error("--report benötigt für schulen-sync einen Dateinamen mit .json")
    if args.command in ("aufsichten", "einsatz") and args.report and args.report.suffix.lower() != ".json":
        parser.error("--report benötigt für aufsichten und einsatz einen Dateinamen mit .json")
    if args.command == "import-extern" and args.report and args.report.suffix.lower() != ".json":
        parser.error("--report benötigt für import-extern einen Dateinamen mit .json")
    if args.command == "raumzuteilung" and args.plan_id and not args.apply:
        parser.error("--plan-id gehört zu --apply")
    if args.command == "import-gf" and args.apply and args.report:
        parser.error("--report ist für den Import-Dry-Run vorgesehen; --apply ohne --report verwenden")
    if getattr(args, "year", None) is not None:
        try:
            check_year(args.year)
        except ConfigurationError as exc:
            parser.error(str(exc))
    if getattr(args, "mao", None) is not None and args.mao < 1:
        parser.error("--mao muss positiv sein")
    return args


def resolve_config_file(config_dir: Path, filename: str) -> Path:
    """Zentrale Nutzerdatei überschreibt paketinterne Dummy-Defaults."""
    local = config_dir / filename
    return local if local.is_file() else DEFAULTS / filename


def load_pipeline(config_dir: Path, workspace: Path, profile: Optional[str]):
    files = {name: resolve_config_file(config_dir, name) for name in FILES}
    if any(not path.is_file() for path in files.values()):
        raise ConfigurationError(f"Fehlende Konfiguration: {files}")
    tables = load_tables(files["tables.ini"])
    cfg = resolve_table_placeholders(load_config(files["pipeline.yaml"]), tables)
    warnings = validate_config(cfg, workspace)
    profiles = load_profiles(files["profiles.ini"])
    if not profiles:
        raise ConfigurationError(f"Keine Profile definiert: {files['profiles.ini']}")
    validate_profiles(cfg, profiles)
    for name, selection in profiles.items():
        validate_config(apply_profile(cfg, selection), workspace)
    for warning in warnings:
        LOGGER.warning("Konfiguration: %s", warning)
    if profile is not None:
        if profile not in profiles:
            raise ConfigurationError(f"Profil '{profile}' unbekannt. Verfügbar: {', '.join(profiles)}")
        cfg = apply_profile(cfg, profiles[profile])
        validate_config(cfg, workspace)
    return cfg, profiles, files, tables


def _print_workspaces(doc: dict) -> None:
    for year, item in sorted(doc["workspaces"].items()):
        current = "*" if year == doc["current_year"] else " "
        number = f"MaO {item['mao']}" if item.get("mao") else "MaO -"
        origin = f"{JAHRGANG_FOLDER}/" if item.get("auto") else "registriert"
        print(f"{current} {year}  {number:<7} {item['root']} ({origin}, {item['status']})")


def _make_current(doc: dict, year: str) -> None:
    for entry in doc["workspaces"].values():
        if entry["status"] == "current":
            entry["status"] = "archived"
    doc["workspaces"][year]["status"] = "current"
    doc["current_year"] = year


def jahrgang_action(args: argparse.Namespace, config_dir: Path) -> int:
    command = args.jahrgang_command
    doc = load_settings(config_dir)
    if command == "list":
        _print_workspaces(doc)
        return 0
    if command == "use":
        wanted = args.which.strip()
        if wanted.isdigit():
            matches = [y for y, e in doc["workspaces"].items() if e.get("mao") == int(wanted)]
            if not matches:
                raise ConfigurationError(f"Kein Jahrgang mit MaO-Nummer {wanted} gefunden")
            year = matches[0]
        else:
            year = check_year(wanted)
            if year not in doc["workspaces"]:
                raise ConfigurationError(f"Jahrgang {year} nicht gefunden. Mit 'mao jahrgang new' anlegen.")
        _make_current(doc, year)
        save_settings(config_dir, doc)
        print(f"Aktueller Jahrgang: {year}")
        return 0
    # new
    if config_dir.name != CONFIG_FOLDER:
        raise ConfigurationError(f"Jahrgangsordner unter {JAHRGANG_FOLDER}/ gibt es nur im zentralen MaO-Ordner "
                                 "(mao config init <MaO-ORDNER>). Sonst 'mao workspace add' verwenden.")
    year_arg = getattr(args, "year", None)
    if (args.nr is None) != (year_arg is None):
        raise ConfigurationError("--nr und --year nur zusammen angeben (oder beide weglassen: Angaben aus der Datenbank)")
    make_current = args.current
    if args.nr is not None and year_arg is not None:
        if args.nr < 1:
            raise ConfigurationError("--nr muss positiv sein")
        number, year = args.nr, check_year(year_arg)
    else:
        tables = load_tables(resolve_config_file(config_dir, "tables.ini"))
        db_config = load_db_config(config_dir)
        if not can_reach(db_config.host, db_config.port, timeout=3):
            raise ConnectionError("Datenbank nicht erreichbar; alternativ --nr und --year angeben")
        with DB(db_config) as db:
            info = active_jahrgang_db(db, tables)
        year = str(info["year"])
        if info["mao"] is None:
            last = info["last_mao"]
            hint = f" (Vorschlag: --nr {int(last) + 1} --year {year})" if isinstance(last, int) else ""
            raise ConfigurationError(f"In der Datenbank ist für das aktive Schuljahr {year} noch kein Jahrgang angelegt{hint}. "
                                     "Das Anlegen in der Datenbank gehört zum Jahrgangswechsel.")
        number = int(info["mao"])  # type: ignore[call-overload]
        make_current = True
    registered = doc["workspaces"].get(year)
    target = config_dir.parent / JAHRGANG_FOLDER / jahrgang_folder_name(number, year)
    if registered and Path(registered["root"]).resolve() != target.resolve():
        raise ConfigurationError(f"Schuljahr {year} ist bereits mit {registered['root']} registriert")
    existed = target.is_dir()
    target.mkdir(parents=True, exist_ok=True)
    _seed_year_rules(target, config_dir)
    doc = load_settings(config_dir)
    if make_current:
        _make_current(doc, year)
        save_settings(config_dir, doc)
    print(f"{'Vorhanden' if existed else 'Angelegt'}: {target}")
    print(f"Aktueller Jahrgang: {doc['current_year'] or '-'}" + ("" if make_current else " (unverändert; --current setzt ihn)"))
    return 0


def _seed_year_rules(target: Path, config_dir: Path) -> None:
    """Neuer Jahrgangsordner: Raumregeln des letzten vorhandenen Jahrgangs als Ausgangspunkt übernehmen (sonst zentrale Vorlage)."""
    if (target / "raumregeln.yaml").exists():
        return
    earlier = sorted((p for p in target.parent.iterdir() if p.is_dir() and p != target and (p / "raumregeln.yaml").is_file()),
                     key=lambda p: (int(p.name.split("_")[0]) if p.name.split("_")[0].isdigit() else -1, p.name))
    source = earlier[-1] / "raumregeln.yaml" if earlier else config_dir / "raumregeln.yaml"
    if source.is_file():
        (target / "raumregeln.yaml").write_text(source.read_text(encoding="utf-8-sig"), encoding="utf-8")
        print(f"raumregeln.yaml übernommen aus {source.parent.name if earlier else 'der zentralen Vorlage'}; bitte für den neuen Jahrgang anpassen.")


def management_action(args: argparse.Namespace, config_dir: Path, config_source: str = "") -> int:
    """Konfiguration und Workspaces verwalten, ohne DB/Export-Pipeline zu laden."""
    try:
        if args.command == "config":
            if args.config_command == "use":
                pointer = write_pointer(args.folder)
                print(f"MaO-Ordner für diesen Rechner gemerkt: {normalize_config_dir(args.folder).parent}\n({pointer})")
            elif args.config_command == "init":
                if args.folder is not None:
                    config_dir = args.folder.expanduser().resolve() / CONFIG_FOLDER
                for path in init_config(config_dir, DEFAULTS):
                    print(f"Erstellt: {path}")
                print(f"Konfigurationsverzeichnis: {config_dir}")
                if args.folder is not None:
                    print(f"Auf jedem Rechner einmal: mao config use \"{args.folder.expanduser().resolve()}\"")
            else:
                doc = load_settings(config_dir)
                print(f"Konfiguration: {config_dir} (gefunden über: {config_source})\nAktuelles Schuljahr: {doc['current_year'] or '-'}")
                print(f"Registrierte Workspaces: {len(doc['workspaces'])}")
                for name in (*FILES, "db.config", "settings.yaml"):
                    where = config_dir / name
                    print(f"  {name}: {'vorhanden' if where.exists() else 'nicht vorhanden'}")
            return 0
        if args.command == "jahrgang":
            return jahrgang_action(args, config_dir)
        if args.command == "workspace":
            doc = load_settings(config_dir)
            action = args.workspace_command
            if action == "list":
                _print_workspaces(doc)
                return 0
            year = check_year(args.target_year)
            if action == "add":
                if year in doc["workspaces"]:
                    raise ConfigurationError(f"Jahrgang {year} existiert bereits")
                root = args.root.expanduser().resolve()
                if any(root == Path(v["root"]) for v in doc["workspaces"].values()):
                    raise ConfigurationError(f"Workspace {root} ist bereits einem anderen Jahrgang zugeordnet")
                # Im zentralen MaO-Ordner erkennt MaO den Jahrgang am aktuellen Ordner;
                # Wrapper mit festem Pfad würden sonst auf andere Rechner synchronisiert.
                make_wrapper = args.wrapper and not args.no_wrapper
                if make_wrapper and any((root / name).exists() for name in ("mao-hier.cmd", "mao-hier.sh")):
                    raise ConfigurationError("Wrapper existiert bereits; --no-wrapper wählen oder Pfad ändern")
                doc["workspaces"][year] = {"root": str(root), "status": "planned"}
                if doc["current_year"] is None:
                    doc["current_year"] = year
                    doc["workspaces"][year]["status"] = "current"
                save_settings(config_dir, doc)
                print(f"Registriert: {year} → {root}")
                if make_wrapper:
                    for path in write_wrapper(config_dir, year, root):
                        print(f"Wrapper: {path}")
            elif action == "select":
                if year not in doc["workspaces"]:
                    raise ConfigurationError(f"Jahrgang {year} nicht registriert")
                for key, entry in doc["workspaces"].items():
                    if entry["status"] == "current":
                        entry["status"] = "archived" if key != year else "current"
                doc["workspaces"][year]["status"] = "current"
                doc["current_year"] = year
                save_settings(config_dir, doc)
                print(f"Aktueller Jahrgang: {year}")
            else:
                if year not in doc["workspaces"]:
                    raise ConfigurationError(f"Jahrgang {year} nicht registriert")
                for path in write_wrapper(config_dir, year, Path(doc["workspaces"][year]["root"])):
                    print(f"Wrapper: {path}")
            return 0
    except (ConfigurationError, OSError, ValueError, ConnectionError) as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return 2
    raise AssertionError("Nicht unterstützter Verwaltungsbefehl")


def _checked_plan_path(workspace: Path, dest: Path, protected: set[Path]) -> Path:
    target = (workspace / dest).resolve() if not dest.is_absolute() else dest.resolve()
    if target in protected:
        raise ConfigurationError(f"Exportplan darf Datei nicht überschreiben: {target}")
    return target


_LAST_COMMAND: Optional[str] = None


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Befehl ausführen; danach (nur im Terminal) ein Hinweis auf neuere Versionen."""
    global _LAST_COMMAND
    _LAST_COMMAND = None
    code = _main(argv)
    if _LAST_COMMAND not in (None, "update") and sys.stderr.isatty():
        try:
            notice = update_notice()
        except Exception:  # der Hinweis darf nie einen Befehl stören
            notice = None
        if notice:
            print(notice, file=sys.stderr)
    return code


def _main(argv: Optional[Sequence[str]] = None) -> int:
    global _LAST_COMMAND
    args = parse_args(argv)
    _LAST_COMMAND = args.command
    if args.command == "update":
        try:
            if args.legacy:
                print("Hinweis: Die Unterbefehle von 'mao update' entfallen; MaO wird jetzt mit uv aktualisiert.")
            update_target = plan_update(args.channel, args.repository, args.version, args.force)
            for line in update_target.lines:
                print(line)
            if args.run:
                for line in run_update(update_target):
                    print(line)
            return 0
        except UpdateError as exc:
            print(f"Updatefehler: {exc}", file=sys.stderr)
            return 5
    config_dir, config_source = resolve_config_dir(getattr(args, "config_dir", None))
    for conflict in find_conflict_copies(config_dir):
        print(f"Warnung: Konfliktkopie der Cloud-Synchronisation, bitte prüfen und eine Fassung löschen: {conflict}",
              file=sys.stderr)
    if args.command in ("config", "workspace", "jahrgang"):
        return management_action(args, config_dir, config_source)
    try:
        settings = load_settings(config_dir)
        selection = resolve_workspace(settings, year=getattr(args, "year", None),
                                      workspace=getattr(args, "workspace", None),
                                      allow_unregistered=(args.command in ("validate", "profiles")))
        workspace = selection.directory
    except ConfigurationError as exc:
        print(f"Workspace-Fehler: {exc}", file=sys.stderr)
        return 2
    logfile = getattr(args, "log_file", None)
    if logfile is not None and not logfile.is_absolute():
        logfile = workspace / logfile
    try:
        logger = setup_logging(getattr(args, "verbose", False), logfile)
    except OSError as exc:
        print(f"Logdatei nicht erreichbar: {exc}", file=sys.stderr)
        return 2
    started = time.monotonic()
    try:
        profile_name = getattr(args, "profile", None) or settings["defaults"]["profile"]
        tex_dir = config_dir.parent / TEX_FOLDER if config_dir.name == CONFIG_FOLDER else None
        if args.command == "import-gf":
            if selection.year is None or selection.year != settings["current_year"]:
                raise ConfigurationError("GF-Import nur für den aktuell registrierten Jahrgang zulässig")
            tables = load_tables(resolve_config_file(config_dir, "tables.ini"))
            target = tables.get("orga_table", "")
            if not target or target.startswith("DEMO_"):
                raise ConfigurationError("Für import-gf muss in der privaten tables.ini eine echte orga_table stehen")
            db_config = load_db_config(config_dir)
            if not can_reach(db_config.host, db_config.port, timeout=3):
                raise ConnectionError("Datenbank für GF-Import nicht erreichbar")
            report: dict = {"schema_version": 1, "status": "blocked", "mode": "apply" if args.apply else "dry-run",
                            "issues": [], "classes": [], "summary": {}}
            try:
                with DB(db_config) as db:
                    db_mao = current_MaO_number_db(db, tables)
                    if selection.mao_number is not None and db_mao is not None and selection.mao_number != db_mao:
                        raise ConfigurationError("Jahrgang aus der Datenbank passt nicht zur Workspace-Konfiguration")
                    stats = import_participants(db, config_dir, target, dry_run=not args.apply,
                                                no_mandatory=args.no_mandatory, report=report,
                                                sync=args.abgleich, ask=input if sys.stdin.isatty() else None)
            except Exception as exc:
                if str(exc) not in report["issues"] and not (
                    report["issues"] and "Einzelheiten im Prüfbericht" in str(exc)
                ):
                    report["issues"].append(str(exc))
                if args.report:
                    saved = write_import_report(workspace, config_dir, args.report, report)
                    LOGGER.info("Blockierter GF-Prüfbericht: %s", saved)
                raise
            LOGGER.info("GF-Import | Formular-Einträge: %s | inaktive Meldungen: %s | Leermeldungen: %s | "
                        "Pflichtteilnahmen ergänzt: %s | Teilnehmende: %s | Lehrkraft fehlt: %s | "
                        "Kurs fehlt (11/12): %s | geschrieben: %s | gelöscht: %s | Modus: %s",
                        stats["gf_entries"], stats["inactive_entries"], stats["empty_reports"], stats["mandatory_added"],
                        stats["participants"], stats["missing_teacher"], stats["missing_course"],
                        stats["inserted"], stats.get("removed", 0), "APPLY" if args.apply else "DRY-RUN")
            for item in report["classes"]:
                LOGGER.info("  Klasse %-5s | GF: %s | Leermeldungen: %s | Pflicht ergänzt: %s | Gesamt: %s",
                            item["klasse"], item["gf"], item.get("empty_reports", 0), item["mandatory"], item["participants"])
            if args.report:
                saved = write_import_report(workspace, config_dir, args.report, report)
                LOGGER.info("GF-Prüfbericht: %s", saved)
            if args.abgleich and args.apply and stats["inserted"] and sys.stdin.isatty():
                # Nachgetragene Teilnehmende haben keinen Raum; bestehende Zuteilungen bleiben unberührt.
                if input(f"{stats['inserted']} neue Teilnehmende ohne Raum. Jetzt von Hand Räumen zuteilen? [j/N] ").strip().lower() == "j":
                    rules_path = resolve_rules_file(workspace, config_dir)
                    with DB(db_config) as db:
                        interactive_assign(db, tables, apply=True, max_per_room=load_room_rules(rules_path).max_per_room)
            elif args.abgleich and args.apply and stats["removed"]:
                LOGGER.info("Gelöschte Teilnehmende haben ihre Raumzuordnung mit der Zeile verloren; andere Zuteilungen bleiben unverändert")
            return 0
        if args.command == "schulen-sync":
            tables = load_tables(resolve_config_file(config_dir, "tables.ini"))
            db_config = load_db_config(config_dir)
            if not can_reach(db_config.host, db_config.port, timeout=3):
                raise ConnectionError("Datenbank für den Schulabgleich nicht erreichbar")
            school_report: dict = {"schema_version": 1, "status": "blocked", "mode": "apply" if args.apply else "dry-run",
                                   "issues": [], "summary": {}}
            try:
                with DB(db_config) as db:
                    sync_schools(db, tables, url=args.url, dry_run=not args.apply, report=school_report,
                                 **({"fetcher": lambda _url: read_file(str(args.file))} if args.file else {}))
            except (SchoolError, OSError) as exc:
                school_report["issues"].append(str(exc))
                if args.report:
                    LOGGER.info("Blockierter Schulbericht: %s", write_import_report(workspace, config_dir, args.report, school_report))
                if isinstance(exc, SchoolError):
                    raise
                raise ConnectionError(f"Schulverzeichnis nicht abrufbar: {exc}") from exc
            if args.report:
                LOGGER.info("Schulbericht: %s", write_import_report(workspace, config_dir, args.report, school_report))
            return 0
        if args.command == "raum-setzen":
            if selection.year is None or selection.year != settings["current_year"]:
                raise ConfigurationError("raum-setzen nur für den aktuell registrierten Jahrgang zulässig")
            tables = load_tables(resolve_config_file(config_dir, "tables.ini"))
            db_config = load_db_config(config_dir)
            if not can_reach(db_config.host, db_config.port, timeout=3):
                raise ConnectionError("Datenbank nicht erreichbar")
            with DB(db_config) as db:
                db_mao = current_MaO_number_db(db, tables)
                if selection.mao_number is not None and db_mao is not None and selection.mao_number != db_mao:
                    raise ConfigurationError("Jahrgang aus der Datenbank passt nicht zur Workspace-Konfiguration")
                if args.interaktiv:
                    if not sys.stdin.isatty():
                        raise ConfigurationError("--interaktiv braucht ein Terminal")
                    rules_path = resolve_rules_file(workspace, config_dir)
                    interactive_assign(db, tables, apply=args.apply, max_per_room=load_room_rules(rules_path).max_per_room)
                    return 0
                if not args.raum or not args.matrikel:
                    raise ConfigurationError("raum-setzen braucht --raum und --matrikel (oder --interaktiv)")
                set_room(db, tables, args.raum, args.matrikel, dry_run=not args.apply, overwrite=args.ueberschreiben,
                         allow_over_capacity=args.trotzdem)
            if not args.apply:
                LOGGER.info("DRY-RUN: nichts geschrieben. Zuteilen mit --apply")
            return 0
        if args.command == "aufsichten":
            if selection.year is None or selection.year != settings["current_year"]:
                raise ConfigurationError("aufsichten nur für den aktuell registrierten Jahrgang zulässig")
            tables = load_tables(resolve_config_file(config_dir, "tables.ini"))
            db_config = load_db_config(config_dir)
            if not can_reach(db_config.host, db_config.port, timeout=3):
                raise ConnectionError("Datenbank für die Aufsichtenprüfung nicht erreichbar")
            if args.plan_exportieren or (args.plan and not args.interaktiv):
                if args.interaktiv or args.lehrkraft or (args.plan_exportieren and args.plan):
                    raise ConfigurationError("--plan/--plan-exportieren nicht mit --lehrkraft, --interaktiv oder untereinander kombinieren")
                with DB(db_config) as db:
                    db_mao = current_MaO_number_db(db, tables)
                    if selection.mao_number is not None and db_mao is not None and selection.mao_number != db_mao:
                        raise ConfigurationError("Jahrgang aus der Datenbank passt nicht zur Workspace-Konfiguration")
                    if args.plan_exportieren:
                        LOGGER.info("Plandatei angelegt: %s", export_plan(db, config_dir, tables, workspace, args.plan_exportieren))
                    else:
                        apply_plan(db, config_dir, tables, workspace, args.plan, dry_run=not args.apply)
                        if not args.apply:
                            LOGGER.info("DRY-RUN: nichts geschrieben. Übernehmen mit --apply")
                return 0
            if args.interaktiv or args.lehrkraft:
                with DB(db_config) as db:
                    db_mao = current_MaO_number_db(db, tables)
                    if selection.mao_number is not None and db_mao is not None and selection.mao_number != db_mao:
                        raise ConfigurationError("Jahrgang aus der Datenbank passt nicht zur Workspace-Konfiguration")
                    if args.interaktiv:
                        if not sys.stdin.isatty():
                            raise ConfigurationError("--interaktiv braucht ein Terminal")
                        draft = Draft(workspace, config_dir, args.plan or Path(PLAN_FILE))
                        model = load_supervision_model(db, config_dir, tables, workspace)
                        if not args.neu:
                            note = draft.load_into(model)
                            if note:
                                LOGGER.info("%s (ignorieren mit --neu)", note)
                        if args.einfach:
                            interactive_supervision(db, config_dir, tables, apply=args.apply, workspace=workspace,
                                                    draft=draft, model=model)
                        else:
                            outcome = tui_supervision(model, db, tables, apply=args.apply, draft=draft)
                            LOGGER.info("Aufsichten bearbeitet: %d Änderungen, %s", outcome["eintraege"],
                                        "geschrieben" if outcome["geschrieben"] else "nicht in die Datenbank geschrieben")
                    else:
                        set_supervision(db, tables, room=args.raum, kuerzel=args.lehrkraft, role=args.rolle,
                                        remove=args.entfernen, dry_run=not args.apply)
                        if not args.apply:
                            LOGGER.info("DRY-RUN: nichts geschrieben. Eintragen mit --apply")
                return 0
            if args.raum or args.entfernen:
                raise ConfigurationError("--raum/--entfernen gehören zu --lehrkraft")
            sup_report: dict = {"schema_version": 1, "status": "blocked", "mode": "check", "issues": [], "summary": {}}
            try:
                with DB(db_config) as db:
                    db_mao = current_MaO_number_db(db, tables)
                    if selection.mao_number is not None and db_mao is not None and selection.mao_number != db_mao:
                        raise ConfigurationError("Jahrgang aus der Datenbank passt nicht zur Workspace-Konfiguration")
                    check_aufsichten(db, config_dir, tables, workspace=workspace, report=sup_report)
            except (RoomError, ExternError, ConfigurationError) as exc:
                sup_report["issues"].append(str(exc))
                if args.report:
                    LOGGER.info("Blockierter Bericht: %s", write_import_report(workspace, config_dir, args.report, sup_report))
                raise
            if args.report:
                LOGGER.info("Aufsichtenbericht: %s", write_import_report(workspace, config_dir, args.report, sup_report))
            return 0
        if args.command == "einsatz":
            if selection.year is None or selection.year != settings["current_year"]:
                raise ConfigurationError("einsatz nur für den aktuell registrierten Jahrgang zulässig")
            tables = load_tables(resolve_config_file(config_dir, "tables.ini"))
            db_config = load_db_config(config_dir)
            if not can_reach(db_config.host, db_config.port, timeout=3):
                raise ConnectionError("Datenbank für den Einsatz nicht erreichbar")
            rules_file = resolve_rules_file(workspace, config_dir)
            preset = load_room_rules(rules_file).einsatz if rules_file.is_file() else {}
            if preset:
                LOGGER.info("Einsatz | Voreinstellungen aus %s: %s", rules_file.name, ", ".join(f"{k}={v}" for k, v in preset.items()))

            def pick(cli_value: Any, key: str, default: Any) -> Any:
                return cli_value if cli_value is not None else preset.get(key, default)

            einsatz_settings = EinsatzSettings(pick(args.aufsichten_pro_raum, "aufsichten_pro_raum", 1),
                                               pick(args.reserve, "reserve", 3), pick(args.hof_gruppen, "hof_gruppen", 2),
                                               pick(args.hof_pro_gruppe, "hof_pro_gruppe", None),
                                               args.seed if args.seed is not None else random.randint(1, 999999))
            einsatz_report: dict = {"schema_version": 1, "status": "blocked", "issues": []}
            try:
                with DB(db_config) as db:
                    db_mao = current_MaO_number_db(db, tables)
                    if selection.mao_number is not None and db_mao is not None and selection.mao_number != db_mao:
                        raise ConfigurationError("Jahrgang aus der Datenbank passt nicht zur Workspace-Konfiguration")
                    run_einsatz(db, config_dir, tables, workspace, settings=einsatz_settings, apply=args.apply,
                                interactive=args.interaktiv, fresh=args.neu, plan=args.plan, form_id=pick(args.formular, "formular", DEFAULT_FORM_ID),
                                skip_rooms=tuple(pick(args.ohne_raum, "ohne_raum", [])), use_wishes=not args.ohne_wuensche, report=einsatz_report)
            except (RoomError, ExternError, ConfigurationError) as exc:
                einsatz_report["issues"].append(str(exc))
                if args.report:
                    LOGGER.info("Blockierter Bericht: %s", write_import_report(workspace, config_dir, args.report, einsatz_report))
                raise
            if args.report:
                LOGGER.info("Einsatzbericht: %s", write_import_report(workspace, config_dir, args.report, einsatz_report))
            if not args.apply:
                LOGGER.info("DRY-RUN: nichts in die Datenbank geschrieben. Schreiben mit --apply")
            return 0
        if args.command == "import-extern":
            if selection.year is None or selection.year != settings["current_year"]:
                raise ConfigurationError("import-extern nur für den aktuell registrierten Jahrgang zulässig")
            tables = load_tables(resolve_config_file(config_dir, "tables.ini"))
            db_config = load_db_config(config_dir)
            if not can_reach(db_config.host, db_config.port, timeout=3):
                raise ConnectionError("Datenbank für den Import der Externen nicht erreichbar")
            extern_report: dict = {"schema_version": 1, "status": "blocked", "mode": "apply" if args.apply else "dry-run",
                                   "issues": [], "summary": {}}
            try:
                with DB(db_config) as db:
                    db_mao = current_MaO_number_db(db, tables)
                    if selection.mao_number is not None and db_mao is not None and selection.mao_number != db_mao:
                        raise ConfigurationError("Jahrgang aus der Datenbank passt nicht zur Workspace-Konfiguration")
                    import_extern(db, config_dir, tables, dry_run=not args.apply, report=extern_report)
            except (ExternError, ImportErrorGF, ConfigurationError) as exc:
                extern_report["issues"].append(str(exc))
                if args.report:
                    LOGGER.info("Blockierter Bericht: %s", write_import_report(workspace, config_dir, args.report, extern_report))
                raise
            if args.report:
                LOGGER.info("Bericht Externe: %s", write_import_report(workspace, config_dir, args.report, extern_report))
            if not args.apply:
                LOGGER.info("DRY-RUN: nichts geschrieben. Übernehmen mit: mao import-extern --apply")
            return 0
        if args.command == "raumzuteilung":
            if selection.year is None or selection.year != settings["current_year"]:
                raise ConfigurationError("Raumzuteilung nur für den aktuell registrierten Jahrgang zulässig")
            if args.regeln_anlegen:
                print(f"Angelegt: {create_year_rules(workspace, config_dir)}\nBitte die Raumcodes und Zuteilungen für diesen Jahrgang anpassen.")
                return 0
            tables = load_tables(resolve_config_file(config_dir, "tables.ini"))
            if not args.extern and tables.get("orga_table", "").startswith("DEMO_"):
                raise ConfigurationError("Für raumzuteilung muss in der privaten tables.ini eine echte orga_table stehen")
            db_config = load_db_config(config_dir)
            if not can_reach(db_config.host, db_config.port, timeout=3):
                raise ConnectionError("Datenbank für die Raumzuteilung nicht erreichbar")
            seed = args.seed if args.seed is not None else random.SystemRandom().randrange(1, 10 ** 6)
            room_report: dict = {"schema_version": 1, "status": "blocked", "mode": "apply" if args.apply else "dry-run",
                                 "issues": [], "summary": {}}
            try:
                with DB(db_config) as db:
                    db_mao = current_MaO_number_db(db, tables)
                    if selection.mao_number is not None and db_mao is not None and selection.mao_number != db_mao:
                        raise ConfigurationError("Jahrgang aus der Datenbank passt nicht zur Workspace-Konfiguration")
                    if args.extern:
                        assign_extern_rooms(db, config_dir, tables, dry_run=not args.apply, overwrite=args.ueberschreiben,
                                            plan_id=args.plan_id, report=room_report, workspace=workspace)
                    else:
                        assign_rooms(db, config_dir, tables, dry_run=not args.apply, seed=seed,
                                     overwrite=args.ueberschreiben, plan_id=args.plan_id, report=room_report,
                                     workspace=workspace)
            except (RoomError, ExternError, ConfigurationError) as exc:
                if str(exc) not in room_report["issues"]:
                    room_report["issues"].append(str(exc))
                if args.report:
                    LOGGER.info("Blockierter Raumbericht: %s", write_room_report(workspace, config_dir, args.report, room_report))
                raise
            if args.report:
                LOGGER.info("Raumbericht: %s", write_room_report(workspace, config_dir, args.report, room_report))
            if not args.apply:
                LOGGER.info("DRY-RUN: nichts geschrieben. Diese Verteilung übernehmen: mao raumzuteilung%s%s --apply --plan-id %s",
                            " --extern" if args.extern else "", "" if args.extern else f" --seed {seed}",
                            room_report["summary"].get("plan_id", "?"))
            return 0
        cfg, profiles, config_files, tables = load_pipeline(config_dir, workspace, profile_name if args.command not in ("profiles", "init-jahrgang") else None)
        LOGGER.info("Konfiguration: %s", config_dir)
        LOGGER.info("Arbeitsordner: %s", workspace)
        if args.command in ("run", "plan") and selection.year and selection.year != settings["current_year"]:
            raise ConfigurationError("SQL-Exporte für einen anderen als den aktuell ausgewählten Jahrgang "
                                     "sind gesperrt, damit keine aktuellen Daten historische Dateien überschreiben")
        if args.command == "profiles":
            for name, profile in profiles.items():
                LOGGER.info("%-20s %s", name, profile["description"])
            return 0
        if args.command == "init-jahrgang":
            if selection.year is None:
                raise ConfigurationError("init-jahrgang benötigt einen registrierten Jahrgang")
            for item in preliminary_plan(tables, selection.year):
                print(item)
            if not args.offline:
                db_config = load_db_config(config_dir)
                if not can_reach(db_config.host, db_config.port, timeout=3):
                    raise ConnectionError("Datenbank nicht erreichbar")
                with DB(db_config) as db:
                    for item in database_preflight(db, tables):
                        print(item)
            return 0
        if args.command == "validate":
            if any(value.startswith("DEMO_") for value in tables.values()):
                LOGGER.warning("Nur DEMO-Tabellennamen konfiguriert: Für Datenbankläufe zentrale tables.ini anlegen")
            LOGGER.info("Konfiguration gültig | Profil: %s | Datasets: %d | Exporte: %d | LaTeX-Jobs: %d",
                        profile_name, len(cfg["datasets"]),
                        sum(ex.get("enabled", True) for ex in cfg["exports"]),
                        sum(job.get("enabled", True) for job in cfg["latex"].get("jobs", [])))
            if args.online:
                db_config = load_db_config(config_dir)
                if not can_reach(db_config.host, db_config.port, timeout=3):
                    raise ConnectionError("Datenbank für Online-Validierung nicht erreichbar")
                with DB(db_config) as db:
                    for line in validate_online(db, config_dir, tables, cfg):
                        LOGGER.info("ONLINE | %s", line)
                LOGGER.info("Online-Validierung abgeschlossen (nur lesende Abfragen)")
            return 0
        LOGGER.info("Profil: %s", profile_name)
        if getattr(args, "job", None):
            select_latex_jobs(cfg, args.job)
            LOGGER.info("Nur LaTeX-Job(s): %s", ", ".join(args.job))

        if args.command == "latex":
            mao = args.mao or selection.mao_number or current_MaO_number_fallback()
            LOGGER.info("LaTeX-Jahrgang: %d (keine Datenbankabfrage)", mao)
            latex_stats = run_latex_jobs(cfg, workspace, mao, cleanup=args.cleanup_latex, tex_dir=tex_dir, year=selection.year)
            LOGGER.info("ABSCHLUSS | PDF-Jobs: %d | übersprungen: %d | Warnungen: %d | Dauer: %.2f s",
                        latex_stats["compiled"], latex_stats["skipped"], logger.warnings,
                        time.monotonic() - started)
            return 0

        placeholders = sorted(key for key, value in tables.items() if value.startswith("DEMO_"))
        if placeholders:
            raise ConfigurationError(
                "Die Standard-tables.ini enthält Demo-Namen. "
                "Lege im zentralen Konfigurationsordner tables.ini mit allen echten SQL-Objektnamen an. "
                f"Fehlende Einträge: {', '.join(placeholders)}"
            )
        try:
            db_config = load_db_config(config_dir)
        except (OSError, ValueError) as exc:
            raise ConnectionError(f"Datenbankkonfiguration: {exc}") from exc
        timeout = float(((cfg.get("db") or {}).get("connectivity_check") or {}).get("timeout_sec", 2.0))
        if not can_reach(db_config.host, db_config.port, timeout=timeout):
            raise ConnectionError(f"Datenbank nicht erreichbar: {db_config.host}:{db_config.port}")
        with DB(db_config) as db:
            db_mao = current_MaO_number_db(db, tables)
            if selection.mao_number is not None and db_mao is not None and selection.mao_number != db_mao:
                raise ConfigurationError("Der registrierte MaO-Jahrgang stimmt nicht mit der aktiven Datenbank überein")
            mao = args.mao or selection.mao_number or db_mao or current_MaO_number_fallback()
            if args.mao is not None and db_mao is not None and db_mao != args.mao:
                LOGGER.warning("--mao=%d weicht von DB-Jahrgang %d ab; SQL-Datenquelle bleibt unverändert",
                               args.mao, db_mao)
            LOGGER.info("MaO-Jahrgang für Dokumente: %d", mao)
            data = load_datasets(db, cfg)

        # Auch beim Vollprofil nur Eigentümer tatsächlich ausgewählter Exporte bereinigen.
        owners = {str(ex["id"]) for ex in cfg["exports"] if ex.get("enabled", True)}
        planner = ExportPlan(workspace, active_owners=owners)
        run_exports(data, cfg, workspace, planner)
        protected = {path.resolve() for path in config_files.values()}
        protected.add((config_dir / "db.config").resolve())
        protected.add((config_dir / "settings.yaml").resolve())
        protected.add(planner.manifest.resolve())
        for filename in planner.outputs:
            if planner._absolute(filename) in protected:
                raise ConfigurationError(f"Export kollidiert mit Systemdatei: {filename}")
        actions = planner.actions()
        job_names = [] if args.no_latex else [job["name"] for job in cfg["latex"].get("jobs", [])
                                                   if job.get("enabled", True)]
        plan_text = render_plan(actions, job_names, mao, source="SQL aus pipeline.yaml")
        if args.command == "plan":
            LOGGER.info("PLAN | %d Dateiaktionen, %d LaTeX-Jobs", len(actions), len(job_names))
            print("\n" + plan_text)
            if args.output:
                plan_file = _checked_plan_path(workspace, args.output,
                                             protected | {planner._absolute(name) for name in planner.outputs}
                                             | {planner._absolute(name) for name in planner.previous})
                write_text(plan_file, plan_text)
                LOGGER.info("Plan gespeichert: %s", plan_file)
            return 0

        changes = planner.commit()
        latex_stats = run_latex_jobs(cfg, workspace, mao, no_latex=args.no_latex, cleanup=args.cleanup_latex, tex_dir=tex_dir, year=selection.year)
        LOGGER.info("ABSCHLUSS | Datasets: %d | Exporte: %d | Zeilen: %d | Neu: %d | Aktualisiert: %d | "
                    "Unverändert: %d | Entfernt: %d | PDF-Jobs: %d | Warnungen: %d | Dauer: %.2f s",
                    len(data), len(planner.outputs), planner.rows,
                    changes.get("NEU", 0), changes.get("AKTUALISIEREN", 0), changes.get("UNVERÄNDERT", 0),
                    changes.get("ENTFERNEN", 0), latex_stats["compiled"], logger.warnings,
                    time.monotonic() - started)
        return 0
    except ConnectionError as exc:
        LOGGER.error("Datenbankfehler: %s", exc)
        return 3
    except (ConfigurationError, ImportErrorGF, yaml.YAMLError, ValueError, TypeError, OSError) as exc:
        LOGGER.error("Konfigurations- oder Dateifehler: %s", exc)
        if getattr(args, "verbose", False):
            LOGGER.debug("Traceback:\n%s", traceback.format_exc())
        return 2
    except Exception as exc:
        LOGGER.error("Verarbeitung fehlgeschlagen: %s (%s)", exc, type(exc).__name__)
        if getattr(args, "verbose", False):
            LOGGER.debug("Traceback:\n%s", traceback.format_exc())
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
