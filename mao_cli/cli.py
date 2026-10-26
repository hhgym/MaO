"""Kommandozeile der MaO-Pipeline (neuer Befehlssatz vor Version 2)."""
from __future__ import annotations

import argparse
import logging
import sys
import time
import traceback
from pathlib import Path
from typing import Optional, Sequence

import yaml

from . import __version__
from .common import LOGGER, can_reach, ensure_parent, write_text
from .config import (
    ConfigurationError, apply_profile, load_config, load_profiles,
    load_tables, resolve_table_placeholders, validate_config, validate_profiles,
)
from .db import DB, active_jahrgang_db, current_MaO_number_db, current_MaO_number_fallback, load_db_config
from .exports import ExportPlan, load_datasets, render_plan, run_exports
from .latex import run_latex_jobs
from .updater import update_hint, UpdateError
from .workspaces import (resolve_config_dir, write_pointer, CONFIG_FOLDER, TEX_FOLDER, JAHRGANG_FOLDER, jahrgang_folder_name, normalize_config_dir, load_settings, save_settings, init_config,
                         resolve_workspace, write_wrapper, check_year)
from .year_ops import preliminary_plan, database_preflight
from .gf_import import import_participants, ImportErrorGF, write_import_report
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
    gf.add_argument("--no-mandatory", action="store_true", help="Pflichtteilnahmen ausnahmsweise nicht ergänzen")
    gf.add_argument("--report", type=Path, metavar="DATEI.json|DATEI.csv",
                    help="Prüfbericht im Workspace speichern (ohne vorhandene Datei zu überschreiben)")

    init_year = commands.add_parser("init-jahrgang", help="Jahrgangswechsel sicher vorprüfen (read-only)")
    global_overrides(init_year)
    init_year.add_argument("--offline", action="store_true", help="Nur geplante Prüfschritte anzeigen")
    init_year.add_argument("--dry-run", action="store_true", default=True,
                           help="Immer ohne Datenänderung (Standard)")

    update = commands.add_parser("update", help="Hinweis zur Aktualisierung mit uv (MaO ersetzt sich nicht selbst)")
    update.add_argument("legacy", nargs="?", choices=("check", "download", "install", "status", "rollback"),
                        help=argparse.SUPPRESS)
    update.add_argument("--channel", choices=("stable", "rc", "develop"), default="stable")
    update.add_argument("--version", metavar="X.Y.Z", help="Bestimmte Version statt der neuesten")
    update.add_argument("--repository", metavar="OWNER/REPO", help="GitHub-Repository (alternativ MAO_GITHUB_REPO)")
    for ignored in ("--yes", "--home"):
        update.add_argument(ignored, nargs="?", const=True, default=None, help=argparse.SUPPRESS)
    update.add_argument("--output", type=Path, help=argparse.SUPPRESS)

    args = parser.parse_args(argv)
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
    doc = load_settings(config_dir)
    if make_current:
        _make_current(doc, year)
        save_settings(config_dir, doc)
    print(f"{'Vorhanden' if existed else 'Angelegt'}: {target}")
    print(f"Aktueller Jahrgang: {doc['current_year'] or '-'}" + ("" if make_current else " (unverändert; --current setzt ihn)"))
    return 0


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


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.command == "update":
        try:
            if args.legacy:
                print("Hinweis: Die Unterbefehle von 'mao update' entfallen; MaO wird jetzt mit uv aktualisiert.")
            for line in update_hint(args.channel, args.repository, args.version):
                print(line)
            return 0
        except UpdateError as exc:
            print(f"Updatefehler: {exc}", file=sys.stderr)
            return 5
    config_dir, config_source = resolve_config_dir(getattr(args, "config_dir", None))
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
                                                no_mandatory=args.no_mandatory, report=report)
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
                        "Kurs fehlt (11/12): %s | geschrieben: %s | Modus: %s",
                        stats["gf_entries"], stats["inactive_entries"], stats["empty_reports"], stats["mandatory_added"],
                        stats["participants"], stats["missing_teacher"], stats["missing_course"],
                        stats["inserted"], "APPLY" if args.apply else "DRY-RUN")
            for item in report["classes"]:
                LOGGER.info("  Klasse %-5s | GF: %s | Leermeldungen: %s | Pflicht ergänzt: %s | Gesamt: %s",
                            item["klasse"], item["gf"], item.get("empty_reports", 0), item["mandatory"], item["participants"])
            if args.report:
                saved = write_import_report(workspace, config_dir, args.report, report)
                LOGGER.info("GF-Prüfbericht: %s", saved)
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
