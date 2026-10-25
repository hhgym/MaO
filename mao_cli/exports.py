"""MaO-CLI: exports."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
import pandas as pd
import re
from .common import (LOGGER, step, log_info, atomic_write, sanitize_key_for_template, _as_list, write_text)
from .config import ConfigurationError, safe_output_path
from .transforms import _apply_cast, _apply_filters, _apply_rename, _apply_sort, _apply_columns, csv_bytes

if TYPE_CHECKING:  # nur für Typprüfung; zur Laufzeit kein Datenbanktreiber nötig
    from .db import DB


_DIGIT_WORDS = {
    "0": "Null",
    "1": "Eins",
    "2": "Zwei",
    "3": "Drei",
    "4": "Vier",
    "5": "Fuenf",
    "6": "Sechs",
    "7": "Sieben",
    "8": "Acht",
    "9": "Neun",
}

_UMLAUT_MAP = str.maketrans({
    "ä": "ae", "ö": "oe", "ü": "ue",
    "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
    "ß": "ss",
})

_RX_NOT_LETTERS = re.compile(r"[^A-Za-z-]+")


def replace_digits_with_words(s: str) -> str:
    out = []
    for ch in s:
        out.append(_DIGIT_WORDS.get(ch, ch))
    return "".join(out)


def latex_db_identifier(s: str, prefix: str = "") -> str:
    s = (s or "").strip()
    s = s.translate(_UMLAUT_MAP)
    s = replace_digits_with_words(s)
    s = s.replace("_", "-")
    s = _RX_NOT_LETTERS.sub("", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        s = "NA"
    if prefix:
        p = (prefix or "").strip().translate(_UMLAUT_MAP)
        p = replace_digits_with_words(p).replace("_", "-")
        p = (_RX_NOT_LETTERS.sub("", p) or "DB")
        p = re.sub(r"-{2,}", "-", p).strip("-")
        s = p + ("-" if (p and s and not p.endswith("-") and not s.startswith("-")) else "") + s
    return s


def relpath_from(from_dir: Path, to_path: Path) -> str:
    rel = os.path.relpath(to_path.resolve(), from_dir.resolve())
    return Path(rel).as_posix()


def write_export_macros(
    *,
    tex_path: Path,
    loads: List[Tuple[str, Path]],
    list_macro: Optional[str] = None,
    entries: Optional[List[str]] = None,
) -> None:
    """Write TeX file containing \\DTLloaddb lines + (optional) one list macro."""
    text = render_export_macros(tex_path=tex_path, loads=loads, list_macro=list_macro, entries=entries)
    write_text(tex_path, text)


def render_export_macros(*, tex_path: Path, loads: List[Tuple[str, Path]],
                         list_macro: Optional[str] = None,
                         entries: Optional[List[str]] = None) -> str:
    """Render macro file without touching the filesystem (needed for dry-run)."""
    tex_dir = tex_path.parent

    # Deduplicate loads/entries (preserve order). Prevents duplicate \\DTLloaddb lines,
    # e.g. when Gesamt is added via multiple paths or multiple splits share one macro file.
    dedup_loads: List[Tuple[str, Path]] = []
    seen_db: set[str] = set()
    for db_name, csv_abs in loads:
        if db_name in seen_db:
            previous = next(path for name, path in dedup_loads if name == db_name)
            if previous != csv_abs:
                raise ValueError(f"LaTeX-Datenbankkennung '{db_name}' ist nicht eindeutig: {previous} / {csv_abs}")
            continue
        seen_db.add(db_name)
        dedup_loads.append((db_name, csv_abs))

    dedup_entries: Optional[List[str]] = None
    if entries is not None:
        dedup_entries = []
        seen_e: set[str] = set()
        for e in entries:
            if e in seen_e:
                continue
            seen_e.add(e)
            dedup_entries.append(e)

    lines: List[str] = []
    for db_name, csv_abs in dedup_loads:
        # Pfadauflösung für \DTLloaddb:
        # Makrodateien liegen unter Listen/db/..., werden aber aus TeX-Dateien unter Listen/<Dokument>/ eingebunden.
        # Daher sollen CSV-Pfade als ../db/... geschrieben werden (relativ zu Listen/<Dokument>/).
        tex_posix = tex_path.as_posix().replace("\\", "/")
        if "/Listen/db/" in tex_posix or tex_posix.startswith("Listen/db/"):
            listen_root: Optional[Path] = None
            for p in tex_path.parents:
                if p.name == "Listen":
                    listen_root = p
                    break
            if listen_root is not None:
                csv_rel = "../" + relpath_from(listen_root, csv_abs)
            else:
                csv_rel = relpath_from(tex_dir, csv_abs)
        else:
            csv_rel = relpath_from(tex_dir, csv_abs)
        lines.append(f"\\DTLloaddb{{{db_name}}}{{{csv_rel}}}")

    lines.append("")

    if list_macro and entries is not None:
        payload = ",".join(dedup_entries or [])
        lines.append(f"\\newcommand{{\\{list_macro}}}{{{payload}}}")

    lines.append("")
    return "\n".join(lines)


class ExportPlan:
    """Stage outputs, calculate changes and manage only files recorded in our manifest."""
    def __init__(self, base_dir: Path, *, active_owners: Optional[set[str]] = None):
        # Nur Dateien der selektierten Export-IDs werden als veraltet betrachtet.
        self.active_owners = active_owners
        self.base_dir = base_dir.resolve()
        self.outputs: dict[str, tuple[bytes, str, str]] = {}
        self.rows = 0
        self.manifest = self.base_dir / ".mao-cli" / "exports-manifest.json"
        self.previous: dict[str, str] = {}
        self.previous_owners: dict[str, Optional[str]] = {}
        if self.manifest.exists():
            try:
                payload = json.loads(self.manifest.read_text(encoding="utf-8"))
                files = payload.get("files")
                if payload.get("schema") != 2 or not isinstance(files, dict):
                    raise ValueError("Manifest muss Schema 2 verwenden; alten Workspace neu aufsetzen")
                for path, item in files.items():
                    if not isinstance(item, dict):
                        raise ValueError(f"Ungültiger Manifesteintrag: {path}")
                    digest, owner = item.get("sha256"), item.get("owner")
                    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                        raise ValueError(f"Ungültiger Datei-Hash: {path}")
                    if not isinstance(owner, str) or not owner:
                        raise ValueError(f"Ausgabe ohne Besitzer-ID: {path}")
                    self._absolute(path)
                    self.previous[path] = digest
                    self.previous_owners[path] = owner
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise ConfigurationError(f"Ungültiges Exportmanifest: {self.manifest} ({exc})") from exc

    def _absolute(self, name: str) -> Path:
        return safe_output_path(self.base_dir, name)

    def add(self, path: Path, data: bytes, kind: str, owner: str) -> None:
        absolute = self._absolute(os.path.relpath(path.resolve(), self.base_dir))
        name = absolute.relative_to(self.base_dir).as_posix()
        if name in self.outputs:
            raise ConfigurationError(f"Ausgabepfad doppelt vergeben: {name} (Export '{owner}' / '{self.outputs[name][2]}')")
        if absolute == self.manifest:
            raise ConfigurationError("Ausgabepfad kollidiert mit Exportmanifest")
        self.outputs[name] = (data, kind, owner)

    def actions(self) -> list[tuple[str, str, str]]:
        results = []
        for name, (content, kind, owner) in self.outputs.items():
            path = self._absolute(name)
            if not path.exists():
                status = "NEU"
            elif path.read_bytes() == content:
                status = "UNVERÄNDERT"
            else:
                status = "AKTUALISIEREN"
            results.append((status, name, kind))
        for name, previous_hash in self.previous.items():
            # Nur Dateien ausgewählter Exporte bereinigen.
            if name not in self.outputs and (self.active_owners is None or
                                             self.previous_owners.get(name) in self.active_owners):
                path = self._absolute(name)
                if not path.exists():
                    results.append(("BEREITS ENTFERNT", name, "alt"))
                elif hashlib.sha256(path.read_bytes()).hexdigest() == previous_hash:
                    results.append(("ENTFERNEN", name, "alt"))
                else:
                    results.append(("SCHÜTZEN (geändert)", name, "alt"))
        return results

    def commit(self) -> dict[str, int]:
        actions = self.actions()
        result: dict[str, int] = {}
        for action, name, _ in actions:
            result[action] = result.get(action, 0) + 1
        new_files: dict[str, dict[str, Optional[str]]] = {}
        # Einträge anderer Profile bleiben im Manifest.
        if self.active_owners is not None:
            new_files = {name: {"sha256": digest, "owner": self.previous_owners.get(name)}
                         for name, digest in self.previous.items()}
        new_files.update({name: {"sha256": hashlib.sha256(contents).hexdigest(), "owner": owner}
                          for name, (contents, _, owner) in self.outputs.items()})
        for action, name, _ in actions:
            if action in ("ENTFERNEN", "BEREITS ENTFERNT"):
                new_files.pop(name, None)
            elif action == "SCHÜTZEN (geändert)":
                new_files[name] = {"sha256": self.previous[name], "owner": self.previous_owners.get(name)}
        manifest_bytes = json.dumps({
            "schema": 2,
            "files": new_files,
        }, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"

        # Snapshot all touched managed files so that a failed publication can be rolled back.
        # This is a filesystem recovery mechanism, not a substitute for a full DB transaction.
        changed = [(action, self._absolute(name), name) for action, name, _ in actions
                   if action in ("NEU", "AKTUALISIEREN", "ENTFERNEN")]
        touched = [path for _, path, _ in changed] + [self.manifest]
        before: dict[Path, Optional[bytes]] = {
            path: path.read_bytes() if path.is_file() else None for path in touched
        }
        try:
            for action, path, name in changed:
                if action != "ENTFERNEN":
                    atomic_write(path, self.outputs[name][0])
            for action, path, name in changed:
                if action == "ENTFERNEN":
                    path.unlink(missing_ok=True)
            atomic_write(self.manifest, manifest_bytes)
        except Exception:
            LOGGER.exception("Exportveröffentlichung fehlgeschlagen; versuche Wiederherstellung")
            rollback_errors = []
            for path, original in before.items():
                try:
                    if original is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic_write(path, original)
                except OSError as restore_exc:
                    rollback_errors.append(f"{path}: {restore_exc}")
            if rollback_errors:
                LOGGER.critical("Wiederherstellung unvollständig: %s", "; ".join(rollback_errors))
            raise
        for action, name, _ in actions:
            if action == "SCHÜTZEN (geändert)":
                LOGGER.warning("Veraltete Datei von Hand verändert, wird nicht gelöscht: %s", name)
        return result


def render_plan(actions: list[tuple[str, str, str]], latex_jobs: list[str], mao: int,
                *, source: str = "") -> str:
    lines = ["# MaO Exportplan", "", f"- MaO-Jahrgang: **{mao}**", f"- Datenquelle: **{source or 'offline/ohne DB'}**", "",
             "| Aktion | Typ | Datei |", "|---|---|---|"]
    for action, name, kind in actions:
        lines.append(f"| {action} | {kind} | `{name.replace('|', '/')}` |")
    if not actions:
        lines.append("| – | – | Keine CSV-/TeX-Ausgaben berechnet |")
    lines += ["", "## LaTeX-Jobs (Vorschau)", ""]
    for job in latex_jobs:
        lines.append(f"- {job}")
    if not latex_jobs:
        lines.append("- Keine aktiviert")
    lines.append("")
    return "\n".join(lines)


def load_datasets(db: DB, cfg: Dict[str, Any]) -> Dict[str, pd.DataFrame]:
    datasets: Dict[str, pd.DataFrame] = {}
    required = {str(e["from_dataset"]) for e in cfg.get("exports", []) if e.get("enabled", True)}
    for ds in _as_list(cfg.get("datasets")):
        name = str(ds.get("name", "")).strip()
        if name not in required:
            LOGGER.debug("[SKIP] Dataset '%s': kein aktiver Export benötigt es", name)
            continue
        if not name:
            raise ValueError("Dataset ohne name")
        if not bool(ds.get("enabled", True)):
            log_info(f"[SKIP] Dataset '{name}' disabled")
            raise ValueError(f"Dataset '{name}' ist deaktiviert, wird aber benötigt")
            continue
        sql = str(ds.get("sql", "")).strip()
        if not sql:
            raise ValueError(f"Dataset '{name}' ohne sql")

        with step(f"{name} laden", indent=1):
            try:
                df = db.query_df(sql)
                df = _apply_cast(df, ds.get("cast"))
            except Exception as exc:
                raise RuntimeError(f"Dataset '{name}': {exc}") from exc
            datasets[name] = df
            log_info(f"Zeilen: {len(df)} | Spalten: {len(df.columns)}", indent=2)
    return datasets


def run_exports(datasets: Dict[str, pd.DataFrame], cfg: Dict[str, Any], base_dir: Path, planner: "ExportPlan") -> None:
    """
    Exports:
      - write CSVs per export spec
      - supports:
          * split (single)
          * splits (list of splits)
      - optional: write TeX macro files per export.macro or split.macro
        (supports entry_template with {db}, {key}, and dataset columns).
    """
    for ex in _as_list(cfg.get("exports")):
        name = str(ex.get("name", "")).strip() or "export"
        export_id = str(ex.get("id") or name)
        if not bool(ex.get("enabled", True)):
            LOGGER.info("[SKIP] Export '%s' disabled", name)
            continue

        ds_name = str(ex.get("from_dataset", "")).strip()
        if not ds_name:
            raise ValueError(f"Export '{name}' benötigt 'from_dataset'.")
        df0 = datasets.get(ds_name)
        if df0 is None:
            raise ValueError(f"Export '{name}': Dataset '{ds_name}' wurde nicht geladen")
        if df0.empty:
            LOGGER.info("[LEER] Export '%s' – Dataset '%s' ohne Daten (Gesamt/Makros werden aktualisiert)", name, ds_name)

        with step(f"Generiere: {name}"):
            df = df0.copy()
            df = _apply_filters(df, _as_list(ex.get("filter")))
            df = _apply_rename(df, ex.get("rename"))
            df = _apply_cast(df, ex.get("cast"))

            sort_by = _as_list(ex.get("sort_by"))
            if sort_by:
                df = _apply_sort(df, sort_by)

            out_spec = ex.get("output", {}) or {}
            sep = out_spec.get("sep", ";")
            float_format = out_spec.get("float_format")
            columns_spec = out_spec.get("columns", "*")

            base_macro = ex.get("macro")  # optional export-level macro

            # Normalize splits
            splits = ex.get("splits")
            if splits is None:
                # Back-compat: single 'split'
                split_one = ex.get("split")
                splits = [split_one] if split_one else []
            if splits and not isinstance(splits, list):
                raise TypeError(f"Export '{name}': 'splits' muss eine Liste sein.")

            # Helper for entry formatting
            def _entry_from_template(tmpl: str, row: Dict[str, Any]) -> str:
                try:
                    return str(tmpl).format(**row)
                except KeyError as e:
                    raise KeyError(
                        f"entry_template/label_template verwendet unbekannte Spalte/Platzhalter: {e}. "
                        f"Verfügbar: {sorted(row.keys())}"
                    )

            # Macro accumulator per tex_path
            # We allow multiple splits to write different macro files.
            macro_acc: Dict[Path, Dict[str, Any]] = {}

            def _accumulate_macro(macro_spec: Dict[str, Any], loads: List[Tuple[str, Path]], entries: List[str]) -> None:
                tex_rel = str(macro_spec.get("tex_path", "")).strip()
                if not tex_rel:
                    return
                tex_path = (base_dir / tex_rel).resolve()
                acc = macro_acc.setdefault(tex_path, {"loads": [], "entries": [], "list_macro": None})
                acc["loads"].extend(loads)
                acc["entries"].extend(entries)
                lm = macro_spec.get("list_macro")
                if lm:
                    acc["list_macro"] = str(lm).strip()

            # 1) Write optional overall output.path (Gesamt)
            overall_path = str(out_spec.get("path", "")).strip()
            overall_out_path: Optional[Path] = None
            if overall_path:
                overall_out_path = (base_dir / overall_path)
                df_out = _apply_columns(df.copy(), columns_spec)
                planner.add(overall_out_path, csv_bytes(df_out, sep=sep, float_format=float_format), "csv", export_id)
                planner.rows += len(df_out)
                log_info(f"Gesamt: {overall_out_path.as_posix()} | Zeilen: {len(df_out)}", indent=1)

                # If export-level macro wants Gesamt included and has tex_path, add it now.
                if (not splits) and isinstance(base_macro, dict) and bool(base_macro.get("enabled", True)) and bool(base_macro.get("include_gesamt", False)):
                    g_db = (str(base_macro.get("gesamt_db_name", "")).strip() or str(base_macro.get("db_name", "")).strip())
                    if not g_db:
                        p = str(base_macro.get("db_prefix", "")).strip()
                        g_db = latex_db_identifier("Gesamt", prefix=p) if p else latex_db_identifier("Gesamt")
                    _accumulate_macro(
                        base_macro,
                        loads=[(g_db, overall_out_path)],
                        entries=[f"Gesamt/{g_db}"] if base_macro.get("list_macro") else [],
                    )


            # Wenn es KEINE splits gibt: Macro-Datei für die Gesamtdatei erzeugen (export-level macro),
            # unabhängig von include_gesamt.
            if (not splits) and overall_out_path is not None and isinstance(base_macro, dict) and bool(base_macro.get("enabled", True)) and bool(base_macro.get("tex_path")):
                db_prefix = str(base_macro.get("db_prefix", "")).strip()
                db_name = str(base_macro.get("db_name", "")).strip()
                if not db_name:
                    db_name = latex_db_identifier(name, prefix=db_prefix) if db_prefix else latex_db_identifier(name)
                _accumulate_macro(
                    base_macro,
                    loads=[(db_name, overall_out_path)],
                    entries=[],
                )

            # 2) Handle splits (0..n)
            if splits:
                for sp in splits:
                    if not isinstance(sp, dict):
                        raise TypeError(f"Export '{name}': jeder split muss ein Mapping sein.")
                    if not bool(sp.get("enabled", True)):
                        continue

                    by = str(sp.get("by", "")).strip()
                    templ = str(sp.get("filename_template", "")).strip()
                    if not by or not templ:
                        raise ValueError(f"Export '{name}': split benötigt 'by' und 'filename_template'.")
                    if by not in df.columns:
                        raise KeyError(f"Export '{name}': split.by-Spalte fehlt: {by}")

                    sp_macro = sp.get("macro")
                    # Macro resolution: prefer split.macro, else export.macro
                    macro_spec = sp_macro if isinstance(sp_macro, dict) else base_macro
                    macro_enabled = isinstance(macro_spec, dict) and bool(macro_spec.get("enabled", True)) and bool(macro_spec.get("tex_path"))

                    loads: List[Tuple[str, Path]] = []
                    entries: List[str] = []

                    n_files = 0
                    for key, grp0 in df.groupby(by, sort=False):
                        if grp0.empty:
                            continue
                        key_str = str(key).strip()
                        if not key_str:
                            continue

                        out_path = base_dir / templ.format(key=sanitize_key_for_template(key_str))
                        grp = _apply_columns(grp0.copy(), columns_spec)
                        planner.add(out_path, csv_bytes(grp, sep=sep, float_format=float_format), "csv", export_id)
                        planner.rows += len(grp)
                        n_files += 1

                        if macro_enabled:
                            db_prefix = str(macro_spec.get("db_prefix", "")).strip()
                            db_name = latex_db_identifier(key_str, prefix=db_prefix) if db_prefix else latex_db_identifier(key_str)
                            loads.append((db_name, out_path))

                            row = grp0.iloc[0].to_dict()
                            row["key"] = key_str
                            row["db"] = db_name

                            if macro_spec.get("entry_template"):
                                entry = _entry_from_template(str(macro_spec["entry_template"]), row)
                            else:
                                label_tmpl = str(macro_spec.get("label_template") or "{key}")
                                label = _entry_from_template(label_tmpl, row)
                                entry = f"{label}/{db_name}"
                            if macro_spec.get("list_macro"):
                                entries.append(entry)

                    log_info(f"Split: {str(sp.get('name', by))} ({by}) | Dateien: {n_files}", indent=1)

                    if macro_enabled:
                        # split-level include_gesamt can add the export's overall file (if any)
                        if overall_out_path is not None and bool(macro_spec.get("include_gesamt", False)):
                            g_db = (str(macro_spec.get("gesamt_db_name", "")).strip() or str(macro_spec.get("db_name", "")).strip())
                            if not g_db:
                                p = str(macro_spec.get("db_prefix", "")).strip()
                                g_db = latex_db_identifier("Gesamt", prefix=p) if p else latex_db_identifier("Gesamt")
                            loads.insert(0, (g_db, overall_out_path))
                            if macro_spec.get("list_macro"):
                                entries.insert(0, f"Gesamt/{g_db}")

                        _accumulate_macro(macro_spec, loads=loads, entries=entries)

            # 3) Write all macro files accumulated in this export
            for tex_path, acc in macro_acc.items():
                list_macro = acc.get("list_macro")
                text = render_export_macros(
                    tex_path=tex_path,
                    loads=acc["loads"],
                    list_macro=list_macro,
                    entries=acc["entries"] if list_macro else None,
                )
                planner.add(tex_path, text.encode("utf-8"), "tex", export_id)
                log_info(f"TeX-Makros: {tex_path.as_posix()}", indent=1)
