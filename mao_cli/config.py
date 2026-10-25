"""MaO-CLI: config."""
from __future__ import annotations

import configparser
import copy
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from .common import _as_list


def load_config(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class ConfigurationError(ValueError):
    """Error in a pipeline definition rather than in database content."""


def safe_output_path(base: Path, relative: str) -> Path:
    if not relative or not isinstance(relative, str):
        raise ConfigurationError("Ausgabepfad fehlt")
    dest = (base / relative).resolve()
    if dest == base.resolve() or not dest.is_relative_to(base.resolve()):
        raise ConfigurationError(f"Ausgabepfad außerhalb des Projektverzeichnisses: {relative}")
    return dest


def validate_config(cfg: Dict[str, Any], base_dir: Optional[Path] = None) -> List[str]:
    """Static YAML validation; schema/columns are additionally checked against live SQL results."""
    errors: List[str] = []
    warnings: List[str] = []
    if not isinstance(cfg, dict):
        raise ConfigurationError("Wurzel der YAML-Konfiguration muss ein Mapping sein")
    for required in ("datasets", "exports", "latex"):
        if required not in cfg:
            errors.append(f"Abschnitt '{required}' fehlt")
    for required in ("datasets", "exports"):
        if required in cfg and not isinstance(cfg[required], list):
            errors.append(f"Abschnitt '{required}' muss Liste sein")
    if errors:
        raise ConfigurationError("Konfiguration ungültig:\n - " + "\n - ".join(errors))
    ds_names: set[str] = set()
    available: set[str] = set()
    for i, ds in enumerate(cfg.get("datasets", [])):
        if not isinstance(ds, dict):
            errors.append(f"datasets[{i}]: Mapping erwartet"); continue
        name = ds.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"datasets[{i}]: name fehlt"); continue
        if name in ds_names:
            errors.append(f"Dataset '{name}' ist mehrfach definiert")
        ds_names.add(name)
        if ds.get("enabled", True):
            available.add(name)
            if not isinstance(ds.get("sql"), str) or not ds["sql"].strip():
                errors.append(f"Dataset '{name}': SQL fehlt")
            if ds.get("cast") is not None and not isinstance(ds["cast"], dict):
                errors.append(f"Dataset '{name}': cast muss Mapping sein")
    enabled_ex = 0
    export_ids: set[str] = set()
    static_paths: dict[str, str] = {}
    template_paths: set[str] = set()
    for i, ex in enumerate(cfg.get("exports", [])):
        if not isinstance(ex, dict):
            errors.append(f"exports[{i}]: Mapping erwartet"); continue
        eid = ex.get("id")
        if not isinstance(eid, str) or not eid.strip() or eid in export_ids:
            errors.append(f"exports[{i}]: id fehlt, ist ungültig oder doppelt: {eid!r}")
        else:
            export_ids.add(eid)
        if not ex.get("enabled", True):
            continue
        enabled_ex += 1
        name = ex.get("name") or f"exports[{i}]"
        ref = ex.get("from_dataset")
        if ref not in available:
            errors.append(f"Export '{name}': Dataset '{ref}' fehlt oder ist deaktiviert")
        out = ex.get("output") or {}
        if not isinstance(out, dict):
            errors.append(f"Export '{name}': output muss Mapping sein"); continue
        cols = out.get("columns", "*")
        if cols not in (None, "*") and not isinstance(cols, (list, str)):
            errors.append(f"Export '{name}': columns muss Liste, '*' oder Spaltenname sein")
        if not isinstance(out.get("sep", ";"), str) or not out.get("sep", ";"):
            errors.append(f"Export '{name}': ungültiger Trenner")
        f = ex.get("filter", [])
        for fidx, fl in enumerate(_as_list(f)):
            if not isinstance(fl, dict) or not fl.get("column") or fl.get("op", "==") not in {"==","!=",">",">=","<","<=","in","notin","contains","isnull","notnull"}:
                errors.append(f"Export '{name}': filter[{fidx}] ungültig")
        sort = _as_list(ex.get("sort_by"))
        if sort and not (all(isinstance(x, str) and x for x in sort) or all(isinstance(x, dict) and x.get("column") and str(x.get("order", "asc")).lower() in ("asc", "desc") for x in sort)):
            errors.append(f"Export '{name}': sort_by ungültig")
        if ex.get("rename") is not None and not isinstance(ex["rename"], dict):
            errors.append(f"Export '{name}': rename muss Mapping sein")
        if ex.get("cast") is not None and not isinstance(ex["cast"], dict):
            errors.append(f"Export '{name}': cast muss Mapping sein")
        if ex.get("macro") is not None and not isinstance(ex.get("macro"), dict):
            errors.append(f"Export '{name}': macro muss Mapping sein")
        splits = ex.get("splits")
        if splits is None:
            splits = [ex["split"]] if ex.get("split") else []
        if not isinstance(splits, list):
            errors.append(f"Export '{name}': splits muss Liste sein"); continue
        for j, sp in enumerate(splits):
            if not isinstance(sp, dict):
                errors.append(f"Export '{name}': splits[{j}] muss Mapping sein"); continue
            if not sp.get("enabled", True):
                continue
            templ = sp.get("filename_template")
            if not sp.get("by") or not isinstance(templ, str) or "{key}" not in templ:
                errors.append(f"Export '{name}': splits[{j}] benötigt by und filename_template mit {{key}}")
            else:
                template_paths.add(templ)
                try:
                    if base_dir:
                        safe_output_path(base_dir, templ.format(key="Test"))
                    templ.format(key="Test")
                except (KeyError, ValueError, ConfigurationError) as exc:
                    errors.append(f"Export '{name}': ungültiges Dateinamen-Template: {exc}")
            macro = sp.get("macro") if isinstance(sp.get("macro"), dict) else ex.get("macro")
            if isinstance(macro, dict) and macro.get("enabled", True) and macro.get("tex_path"):
                path = macro["tex_path"]
                if path in static_paths:
                    errors.append(f"Ausgabepfad '{path}' mehrfach verwendet: {static_paths[path]} / {name}")
                static_paths[path] = str(name)
        for value in (out.get("path"), (ex.get("macro") or {}).get("tex_path") if not splits and isinstance(ex.get("macro"), dict) and ex["macro"].get("enabled",True) else None):
            if value:
                if value in static_paths:
                    errors.append(f"Ausgabepfad '{value}' mehrfach verwendet: {static_paths[value]} / {name}")
                static_paths[value] = str(name)
                if base_dir:
                    try:
                        safe_output_path(base_dir, value)
                    except ConfigurationError as exc:
                        errors.append(str(exc))
    latex = cfg.get("latex", {})
    if not isinstance(latex, dict):
        errors.append("latex muss Mapping sein")
    else:
        try:
            if int(latex.get("runs", 2)) < 1:
                errors.append("latex.runs muss >= 1 sein")
        except (TypeError, ValueError):
            errors.append("latex.runs muss ganze Zahl sein")
        if not isinstance(latex.get("jobs", []), list):
            errors.append("latex.jobs muss Liste sein")
        else:
            job_ids: set[str] = set()
            for j, job in enumerate(latex["jobs"]):
                if not isinstance(job, dict):
                    errors.append(f"latex.jobs[{j}] muss Mapping sein")
                else:
                    jid = job.get("id")
                    if not isinstance(jid, str) or not jid.strip() or jid in job_ids:
                        errors.append(f"latex.jobs[{j}]: id fehlt, ist ungültig oder doppelt: {jid!r}")
                    else:
                        job_ids.add(jid)
                    if job.get("enabled", True) and not job.get("tex"):
                        errors.append(f"latex.jobs[{j}] benötigt tex")
    if errors:
        raise ConfigurationError("Konfiguration ungültig:\n - " + "\n - ".join(errors))
    if enabled_ex == 0:
        warnings.append("Keine Exporte aktiviert")
    if base_dir:
        for templ in template_paths:
            example = Path(templ.format(key="Test"))
            # First use of manifest: detect existing files that we cannot prove we own.
            if not (base_dir / ".mao-cli" / "exports-manifest.json").exists():
                matches = list((base_dir / example.parent).glob(example.name.replace("Test", "*"))) if (base_dir / example.parent).exists() else []
                if matches:
                    warnings.append(f"Historische Splitdateien für '{templ}' sind noch nicht manifestgeführt ({len(matches)}). Sie werden beim ersten Lauf vorsorglich nicht gelöscht.")
    return warnings


# ---------------------------------------------------------------------------
# Optionale Profile und austauschbare Tabellennamen
# ---------------------------------------------------------------------------

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TABLE_TOKEN_RE = re.compile(r"\{\{\s*table\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def load_tables(path: Path) -> dict[str, str]:
    """[table] aus tables.ini, SQL-Namen werden nur als sichere Identifier eingesetzt."""
    ini = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8-sig") as file:
            ini.read_file(file)
    except (configparser.Error, UnicodeError) as exc:
        raise ConfigurationError(f"Tabellenkonfiguration '{path}': {exc}") from exc
    if not ini.has_section("table"):
        raise ConfigurationError(f"Tabellenkonfiguration '{path}': [table] fehlt")
    values = dict(ini.items("table"))
    for key, identifier in values.items():
        if not IDENTIFIER_RE.fullmatch(identifier):
            raise ConfigurationError(f"Ungültiger Tabellen-/Viewname in tables.ini: {key}={identifier!r}")
    return values


def resolve_sql_table_tokens(sql: str, tables: dict[str, str], *, label: str) -> str:
    """Ersetzt ausschließlich SQL-Identifier aus der zentralen tables.ini.

    Die Zielwerte werden bereits von load_tables() gegen IDENTIFIER_RE geprüft.
    Andere SQL-Parameter sind niemals über diese Tokens einzusetzen.
    """
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in tables:
            raise ConfigurationError(f"{label}: Unbekannte Tabellenkennung '{key}' in tables.ini")
        return tables[key]

    resolved = TABLE_TOKEN_RE.sub(replace, sql)
    if "{{" in resolved or "}}" in resolved:
        raise ConfigurationError(f"{label}: Ungültiger Tabellenplatzhalter")
    return resolved


def resolve_table_placeholders(cfg: Dict[str, Any], tables: dict[str, str]) -> Dict[str, Any]:
    """Gleiche sichere {{table.*}}-Auflösung für Pipeline und GF-Import."""
    changed = copy.deepcopy(cfg)
    for dataset in changed.get("datasets", []):
        sql = dataset.get("sql", "")
        if isinstance(sql, str):
            dataset["sql"] = resolve_sql_table_tokens(sql, tables, label=f"Dataset '{dataset.get('name')}'")
    return changed


def _profile_list(ini: configparser.ConfigParser, section: str, key: str) -> list[str]:
    raw: str = ini.get(section, key, fallback="enabled")
    return [part.strip() for part in raw.split(",") if part.strip()]


def load_profiles(path: Path) -> dict[str, dict[str, Any]]:
    """Profile mit eindeutigen Export- und LaTeX-Job-IDs."""
    ini = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8-sig") as file:
            ini.read_file(file)
    except (configparser.Error, UnicodeError) as exc:
        raise ConfigurationError(f"Profile '{path}': {exc}") from exc
    result: dict[str, dict[str, Any]] = {}
    for section in ini.sections():
        unknown = set(ini.options(section)) - {"exports", "latex_jobs", "description"}
        if unknown:
            raise ConfigurationError(f"Profil '{section}': Unbekannte Optionen {sorted(unknown)}. "
                                     "Nur exports, latex_jobs und description sind erlaubt.")
        result[section] = {"exports": _profile_list(ini, section, "exports"),
                           "latex_jobs": _profile_list(ini, section, "latex_jobs"),
                           "description": ini.get(section, "description", fallback="")}
    return result


def _profile_mask(entries: list[dict], selectors: list[str], kind: str) -> list[bool]:
    ids = [entry["id"] for entry in entries]
    selection = set(selectors)
    wildcards = selection.intersection({"enabled", "all", "none"})
    if wildcards and len(selection) > 1:
        raise ConfigurationError(f"{kind}: 'enabled', 'all' oder 'none' nicht mit IDs kombinieren")
    unknown = selection - set(ids) - {"enabled", "all", "none"}
    if unknown:
        raise ConfigurationError(f"{kind}: Unbekannte IDs {sorted(unknown)}")
    if selection == {"enabled"}:
        return [bool(e.get("enabled", True)) for e in entries]
    if selection == {"all"}:
        return [True] * len(entries)
    if not selection or selection == {"none"}:
        return [False] * len(entries)
    return [entry_id in selection for entry_id in ids]


def apply_profile(cfg: Dict[str, Any], profile: dict[str, Any]) -> Dict[str, Any]:
    """Temporäre Auswahl, die pipeline.yaml selbst wird nie geändert."""
    changed = copy.deepcopy(cfg)
    for section, selectors in (("exports", profile["exports"]), ("latex_jobs", profile["latex_jobs"])):
        entries = changed["latex"].get("jobs", []) if section == "latex_jobs" else changed["exports"]
        mask = _profile_mask(entries, selectors, section)
        for e, active in zip(entries, mask):
            e["enabled"] = active
    needed = {e.get("from_dataset") for e in changed.get("exports", []) if e.get("enabled", True)}
    for dataset in changed.get("datasets", []):
        if dataset.get("name") in needed:
            dataset["enabled"] = True
    return changed


def validate_profiles(cfg: Dict[str, Any], profiles: dict[str, dict[str, Any]]) -> None:
    for name, profile in profiles.items():
        try:
            apply_profile(cfg, profile)
        except ConfigurationError as exc:
            raise ConfigurationError(f"Profil '{name}': {exc}") from exc
