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
from .db import DB, current_MaO_number_db, current_MaO_number_fallback, load_db_config
from .exports import ExportPlan, load_datasets, render_plan, run_exports
from .latex import run_latex_jobs
from .updater import update_action, UpdateError

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
    commands = parser.add_subparsers(dest="command", required=True)

    def common(sub: argparse.ArgumentParser, *, profile: bool = False) -> None:
        sub.add_argument("--workspace", metavar="ORDNER", type=Path, default=Path.cwd(),
                         help="Arbeitsordner (Standard: aktuelles Verzeichnis)")
        if profile:
            sub.add_argument("--profile", default=DEFAULT_PROFILE, metavar="NAME",
                             help=f"Ausführungsprofil (Standard: {DEFAULT_PROFILE})")
        sub.add_argument("--verbose", action="store_true", help="Ausführliche Meldungen")
        sub.add_argument("--log-file", type=Path, metavar="DATEI", help="Zusätzliche Logdatei")

    run = commands.add_parser("run", help="Datenexporte und optional LaTeX ausführen")
    common(run, profile=True)
    run.add_argument("--no-latex", action="store_true", help="LaTeX-Jobs überspringen")
    run.add_argument("--mao", type=int, help="MaO-Nummer für Dokumente (nicht Datenquelle!)")

    plan = commands.add_parser("plan", help="Exportplan ermitteln (kein CSV/PDF schreiben)")
    common(plan, profile=True)
    plan.add_argument("--output", type=Path, metavar="MARKDOWN", help="Plan als Markdown speichern")
    plan.add_argument("--no-latex", action="store_true", help="LaTeX-Jobs im Plan ausblenden")
    plan.add_argument("--mao", type=int, help="MaO-Nummer für Dokumente (nicht Datenquelle!)")

    latex = commands.add_parser("latex", help="Nur LaTeX mit vorhandenen Dateien ausführen")
    common(latex, profile=True)
    latex.add_argument("--mao", type=int, help="MaO-Nummer für Dokumente (nicht Datenquelle!)")

    validate = commands.add_parser("validate", help="Konfiguration ohne Datenbank prüfen")
    common(validate, profile=True)

    profiles = commands.add_parser("profiles", help="Verfügbare Ausführungsprofile anzeigen")
    common(profiles)

    update = commands.add_parser("update", help="GitHub-Release oder develop-Checkout prüfen/aktualisieren")
    sub = update.add_subparsers(dest="update_command", required=True)
    for action in ("check", "download", "install", "status", "rollback"):
        cmd = sub.add_parser(action, help={"check": "Updates nur prüfen", "download": "Releasewheel geprüft herunterladen", "install": "Update ausdrücklich installieren", "status": "Aktive Version und Rollback anzeigen", "rollback": "Vorherige Version wieder aktivieren"}[action])
        cmd.add_argument("--channel", choices=("stable", "rc", "develop"), default="stable")
        cmd.add_argument("--repository", metavar="OWNER/REPO", help="GitHub-Repository (alternativ MAO_GITHUB_REPO)")
        if action == "download":
            cmd.add_argument("--output", type=Path, required=True, help="Downloadordner")
        if action in ("install", "rollback"):
            cmd.add_argument("--yes", action="store_true", help="Aktion ausdrücklich bestätigen")
        cmd.add_argument("--home", type=Path, help="Verzeichnis für verwaltete Versionen (alternativ MAO_UPDATE_HOME)")

    args = parser.parse_args(argv)
    if getattr(args, "mao", None) is not None and args.mao < 1:
        parser.error("--mao muss positiv sein")
    return args


def resolve_config_file(workspace: Path, filename: str) -> Path:
    """Arbeitsordner/ config/ überschreibt die ausgelieferten Standarddateien."""
    local = workspace / "config" / filename
    return local if local.is_file() else DEFAULTS / filename


def load_pipeline(workspace: Path, profile: Optional[str]):
    files = {name: resolve_config_file(workspace, name) for name in FILES}
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


def _checked_plan_path(workspace: Path, dest: Path, protected: set[Path]) -> Path:
    target = (workspace / dest).resolve() if not dest.is_absolute() else dest.resolve()
    if target in protected:
        raise ConfigurationError(f"Exportplan darf Datei nicht überschreiben: {target}")
    return target


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.command == "update":
        try:
            result = update_action(args.update_command, args.channel, args.repository,
                                   yes=getattr(args, "yes", False),
                                   dest=getattr(args, "output", None), home=getattr(args, "home", None))
            print(result)
            return 0
        except UpdateError as exc:
            print(f"Updatefehler: {exc}", file=sys.stderr)
            return 5
    workspace = args.workspace.expanduser().resolve()
    logfile = args.log_file
    if logfile is not None and not logfile.is_absolute():
        logfile = workspace / logfile
    try:
        logger = setup_logging(args.verbose, logfile)
    except OSError as exc:
        print(f"Logdatei nicht erreichbar: {exc}", file=sys.stderr)
        return 2
    started = time.monotonic()
    try:
        profile_name = getattr(args, "profile", None)
        cfg, profiles, config_files, tables = load_pipeline(workspace, profile_name)
        LOGGER.info("Arbeitsordner: %s", workspace)
        if args.command == "profiles":
            for name, profile in profiles.items():
                LOGGER.info("%-20s %s", name, profile["description"])
            return 0
        if args.command == "validate":
            if any(value.startswith("DEMO_") for value in tables.values()):
                LOGGER.warning("Nur DEMO-Tabellennamen konfiguriert: Für Datenbankläufe lokale config/tables.ini anlegen")
            LOGGER.info("Konfiguration gültig | Profil: %s | Datasets: %d | Exporte: %d | LaTeX-Jobs: %d",
                        args.profile, len(cfg["datasets"]),
                        sum(ex.get("enabled", True) for ex in cfg["exports"]),
                        sum(job.get("enabled", True) for job in cfg["latex"].get("jobs", [])))
            return 0
        LOGGER.info("Profil: %s", args.profile)

        if args.command == "latex":
            mao = args.mao or current_MaO_number_fallback()
            LOGGER.info("LaTeX-Jahrgang: %d (keine Datenbankabfrage)", mao)
            latex_stats = run_latex_jobs(cfg, workspace, mao)
            LOGGER.info("ABSCHLUSS | PDF-Jobs: %d | übersprungen: %d | Warnungen: %d | Dauer: %.2f s",
                        latex_stats["compiled"], latex_stats["skipped"], logger.warnings,
                        time.monotonic() - started)
            return 0

        placeholders = sorted(key for key, value in tables.items() if value.startswith("DEMO_"))
        if placeholders:
            raise ConfigurationError(
                "Die Standard-tables.ini enthält Demo-Namen. "
                "Lege im Arbeitsordner config/tables.ini mit allen echten SQL-Objektnamen an. "
                f"Fehlende Einträge: {', '.join(placeholders)}"
            )
        try:
            db_config = load_db_config(workspace)
        except (OSError, ValueError) as exc:
            raise ConnectionError(f"Datenbankkonfiguration: {exc}") from exc
        timeout = float(((cfg.get("db") or {}).get("connectivity_check") or {}).get("timeout_sec", 2.0))
        if not can_reach(db_config.host, db_config.port, timeout=timeout):
            raise ConnectionError(f"Datenbank nicht erreichbar: {db_config.host}:{db_config.port}")
        with DB(db_config) as db:
            db_mao = current_MaO_number_db(db, tables)
            mao = args.mao or db_mao or current_MaO_number_fallback()
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
        protected.add((workspace / "db.config").resolve())
        protected.add(planner.manifest.resolve())
        for filename in planner.outputs:
            if planner._absolute(filename) in protected:
                raise ConfigurationError(f"Export kollidiert mit Systemdatei: {filename}")
        actions = planner.actions()
        job_names = [] if args.no_latex else [job["name"] for job in cfg["latex"].get("jobs", [])
                                                   if job.get("enabled", True)]
        report = render_plan(actions, job_names, mao, source="SQL aus pipeline.yaml")
        if args.command == "plan":
            LOGGER.info("PLAN | %d Dateiaktionen, %d LaTeX-Jobs", len(actions), len(job_names))
            print("\n" + report)
            if args.output:
                target = _checked_plan_path(workspace, args.output,
                                             protected | {planner._absolute(name) for name in planner.outputs}
                                             | {planner._absolute(name) for name in planner.previous})
                write_text(target, report)
                LOGGER.info("Plan gespeichert: %s", target)
            return 0

        changes = planner.commit()
        latex_stats = run_latex_jobs(cfg, workspace, mao, no_latex=args.no_latex)
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
    except (ConfigurationError, yaml.YAMLError, ValueError, TypeError, OSError) as exc:
        LOGGER.error("Konfigurations- oder Dateifehler: %s", exc)
        if args.verbose:
            LOGGER.debug("Traceback:\n%s", traceback.format_exc())
        return 2
    except Exception as exc:
        LOGGER.error("Verarbeitung fehlgeschlagen: %s (%s)", exc, type(exc).__name__)
        if args.verbose:
            LOGGER.debug("Traceback:\n%s", traceback.format_exc())
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
