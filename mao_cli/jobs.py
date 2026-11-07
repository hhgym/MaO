"""MaO-CLI: Auswahl von LaTeX-Jobs und die dafür nötigen Exporte.

Ein Job braucht die Exporte, deren Dateien seine Vorlage (samt eingebundener Dateien) verwendet.
Die Zuordnung wird aus dem TeX-Quelltext erkannt (Pfad der CSV-/Makro-Datei) oder im Job mit
``exports: [id, ...]`` festgelegt. Kann sie nicht bestimmt werden, bleibt es beim bisherigen Verhalten
(alle aktivierten Exporte).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .common import LOGGER, _as_list
from .config import ConfigurationError
from .latex import resolve_template

DEFAULT_DB_DIR = "db"
_INCLUDE = re.compile(r"\\(?:input|include|InputIfFileExists|subfile)\s*\{([^{}]+)\}")
_MAX_FILES = 300


def export_id(export: Mapping[str, Any]) -> str:
    return str(export.get("id") or export.get("name") or "").strip()


def _strip_db(path: str, db_dir: str) -> str:
    text = path.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    prefix = db_dir.strip("/") + "/"
    return text[len(prefix):] if text.startswith(prefix) else text


def _static_prefix(template: str) -> str:
    """Fester Anfang eines Dateinamens mit Platzhaltern, z. B. ``Kopierliste-Raum-{key}.csv`` → ``Kopierliste-Raum-``."""
    return template.split("{", 1)[0]


def export_markers(export: Mapping[str, Any], db_dir: str = DEFAULT_DB_DIR) -> set[str]:
    """Textteile, an denen man erkennt, dass eine Vorlage die Ausgaben dieses Exports verwendet."""
    markers: set[str] = set()

    def add(path: Any) -> None:
        text = str(path or "").strip()
        if not text:
            return
        marker = _static_prefix(_strip_db(text, db_dir))
        if marker:
            markers.add(marker)

    out = export.get("output") or {}
    add(out.get("path"))
    macro = export.get("macro")
    if isinstance(macro, Mapping):
        add(macro.get("tex_path"))
    for split in _as_list(export.get("splits") or ([export["split"]] if export.get("split") else [])):
        if not isinstance(split, Mapping):
            continue
        add(split.get("filename_template") or split.get("path"))
        smacro = split.get("macro")
        if isinstance(smacro, Mapping):
            add(smacro.get("tex_path"))
    return markers


def collect_tex_text(tex_path: Path, search: Iterable[Path]) -> str:
    """Quelltext einer Vorlage samt der Dateien, die sie per ``\\input``/``\\include`` einbindet."""
    folders = [p for p in search if p and p.is_dir()]
    seen: set[Path] = set()
    parts: list[str] = []
    todo = [tex_path.resolve()]
    while todo and len(seen) < _MAX_FILES:
        current = todo.pop()
        if current in seen or not current.is_file():
            continue
        seen.add(current)
        try:
            text = current.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = re.sub(r"(?<!\\)%.*", "", text)  # TeX-Kommentare zählen nicht
        parts.append(text)
        for match in _INCLUDE.finditer(text):
            name = match.group(1).strip()
            if "\\" in name or not name:
                continue  # Pfad aus Makros (z. B. \MaOdb …) – steht als Text schon in den Treffern
            candidates = [name] if Path(name).suffix else [name + ".tex", name]
            for folder in [current.parent, *folders]:
                for candidate in candidates:
                    target = (folder / candidate)
                    if target.is_file():
                        todo.append(target.resolve())
                        break
    return "\n".join(parts)


def job_exports(cfg: Mapping[str, Any], job: Mapping[str, Any], workspace: Path, tex_dir: Path | None) -> set[str] | None:
    """Export-IDs, die ``job`` braucht; ``None``, wenn sich das nicht bestimmen lässt."""
    exports = {export_id(e): e for e in _as_list(cfg.get("exports"))}
    explicit = job.get("exports")
    if explicit is not None:
        wanted = {str(x).strip() for x in _as_list(explicit) if str(x).strip()}
        unknown = wanted - set(exports)
        if unknown:
            raise ConfigurationError(f"LaTeX-Job '{job.get('name')}': unbekannte Export-ID(s) in exports: {', '.join(sorted(unknown))}")
        return wanted
    latex = cfg.get("latex") or {}
    db_dir = str(latex.get("db_dir", DEFAULT_DB_DIR))
    raw_workdir = str(job.get("workdir", "")).strip()
    workdir = workspace if raw_workdir in ("", ".") else workspace / raw_workdir
    tex = str(job.get("tex", "")).strip()
    if not tex:
        return None
    try:
        tex_path = resolve_template(tex, workdir, tex_dir, str(job.get("name", "")))
    except ConfigurationError:
        return None
    if not tex_path.is_file():
        return None
    search = [tex_path.parent, workdir]
    if tex_dir is not None:
        search += [tex_dir / "base", tex_dir]
    text = collect_tex_text(tex_path, search).replace("\\", "/")
    found = {eid for eid, export in exports.items()
             if any(marker in text for marker in export_markers(export, db_dir))}
    return found or None


def restrict_exports(cfg: dict[str, Any], workspace: Path, tex_dir: Path | None) -> set[str] | None:
    """Nur die Exporte (und Datasets) aktivieren, die die aktiven LaTeX-Jobs brauchen.

    Gibt die benötigten Export-IDs zurück oder ``None``, wenn (für mindestens einen Job) keine
    Zuordnung möglich war – dann bleibt die Auswahl der Exporte unverändert.
    """
    jobs = [j for j in _as_list((cfg.get("latex") or {}).get("jobs")) if j.get("enabled", True)]
    needed: set[str] = set()
    for job in jobs:
        ids = job_exports(cfg, job, workspace, tex_dir)
        if ids is None:
            LOGGER.warning("LaTeX-Job '%s': benötigte Exporte nicht erkennbar – alle aktivierten Exporte werden erzeugt "
                           "(oder im Job mit 'exports: [id, …]' festlegen)", job.get("name"))
            return None
        needed |= ids
    for export in _as_list(cfg.get("exports")):
        export["enabled"] = export_id(export) in needed
    wanted_sets = {e.get("from_dataset") for e in _as_list(cfg.get("exports")) if e.get("enabled")}
    for dataset in _as_list(cfg.get("datasets")):
        if dataset.get("name") in wanted_sets:
            dataset["enabled"] = True
    return needed


def missing_outputs(cfg: Mapping[str, Any], workspace: Path, ids: Iterable[str]) -> list[str]:
    """Fehlende Gesamt-/Makrodateien der Exporte ``ids`` (für ``mao latex`` ohne Datenabruf)."""
    by_id = {export_id(e): e for e in _as_list(cfg.get("exports"))}
    missing: list[str] = []
    for eid in sorted(ids):
        export = by_id.get(eid) or {}
        paths = [(export.get("output") or {}).get("path")]
        if isinstance(export.get("macro"), Mapping):
            paths.append(export["macro"].get("tex_path"))
        for split in _as_list(export.get("splits")):
            if isinstance(split, Mapping) and isinstance(split.get("macro"), Mapping):
                paths.append(split["macro"].get("tex_path"))
        for rel in paths:
            if rel and not (workspace / str(rel)).is_file():
                missing.append(f"{eid}: {rel}")
    return missing


def _parse_numbers(text: str, count: int) -> set[int]:
    result: set[int] = set()
    for token in re.split(r"[,\s]+", text.strip()):
        if not token:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if not match:
            raise ValueError(token)
        lo, hi = int(match.group(1)), int(match.group(2) or match.group(1))
        if lo < 1 or hi > count or lo > hi:
            raise ValueError(token)
        result.update(range(lo, hi + 1))
    return result


def choose_jobs(cfg: Mapping[str, Any], workspace: Path, tex_dir: Path | None, *,
                ask: Callable[[str], str] = input, out: Callable[[str], None] = print,
                with_data: bool = True) -> list[str] | None:
    """Alle Jobs anzeigen und ankreuzen lassen. ``None`` = abgebrochen."""
    jobs = [j for j in _as_list((cfg.get("latex") or {}).get("jobs")) if j.get("name")]
    if not jobs:
        out("Keine LaTeX-Jobs definiert.")
        return []
    needs = {str(j["name"]): job_exports(cfg, j, workspace, tex_dir) for j in jobs}
    marked = [bool(j.get("enabled", True)) for j in jobs]
    width = max(len(str(j["name"])) for j in jobs)

    def show() -> None:
        out("\nLaTeX-Jobs ([x] = ausgewählt, Standard = in pipeline.yaml aktiviert):")
        for i, job in enumerate(jobs, 1):
            ids = needs[str(job["name"])]
            data = "Daten: ?" if ids is None else f"Daten: {len(ids)} Export(e)"
            off = "" if job.get("enabled", True) else "  (in pipeline.yaml aus)"
            out(f"  {i:>2} [{'x' if marked[i - 1] else ' '}] {str(job['name']):<{width}}  {data}{off}")
        out("Eingabe: Nummern/Bereiche umschalten (z. B. 1 4 6-8) · a alle · n keine · s Standard · Enter ausführen · q abbrechen")

    show()
    while True:
        answer = ask("Auswahl> ").strip()
        low = answer.lower()
        if low in ("q", "quit", "abbrechen"):
            return None
        if low == "":
            chosen = [str(j["name"]) for j, m in zip(jobs, marked) if m]
            if not chosen:
                out("Nichts ausgewählt (Enter ohne Auswahl bricht nicht ab; q = abbrechen).")
                continue
            return chosen
        if low == "a":
            marked = [True] * len(jobs)
        elif low == "n":
            marked = [False] * len(jobs)
        elif low == "s":
            marked = [bool(j.get("enabled", True)) for j in jobs]
        else:
            try:
                for number in _parse_numbers(answer, len(jobs)):
                    marked[number - 1] = not marked[number - 1]
            except ValueError as exc:
                out(f"Ungültige Eingabe: {exc}")
                continue
        show()


def summarize(cfg: Mapping[str, Any], names: list[str], needed: set[str] | None, out: Callable[[str], None] = print) -> None:
    out(f"Jobs: {', '.join(names)}")
    if needed is None:
        out("Exporte: alle aktivierten (Zuordnung nicht erkennbar)")
    else:
        datasets = {e.get("from_dataset") for e in _as_list(cfg.get("exports")) if export_id(e) in needed}
        out(f"Exporte: {len(needed)} ({', '.join(sorted(needed)) or '-'}) · Abfragen (Datasets): {len(datasets)}")
