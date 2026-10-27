"""MaO-CLI: latex."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List
from .common import LOGGER, step, log_info, write_text, atomic_write, _as_list
from .config import ConfigurationError, safe_output_path


_SAFE_JOBNAME = re.compile(r"[A-Za-z0-9_.\-]+")
_FORBIDDEN_IN_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})
# Hilfsdateien, die ein LaTeX-Lauf neben dem PDF hinterlässt (nur auf Wunsch entfernt).
AUX_SUFFIXES = (".aux", ".log", ".out", ".toc", ".lof", ".lot", ".nav", ".snm", ".vrb", ".fls",
                ".fdb_latexmk", ".synctex.gz", ".bbl", ".blg", ".bcf", ".run.xml")


def _ascii_jobname(name: str) -> str:
    """ASCII-Name für den LaTeX-Aufruf (Sonderzeichen in -jobname sind unter Windows riskant)."""
    ascii_name = re.sub(r"[^A-Za-z0-9_.\-]+", "-", name.translate(_TRANSLIT)).strip("-.")
    return ascii_name or "mao-latex"


def target_pdf_name(job: Dict[str, Any], default_jobname: str, mao_number: int) -> str | None:
    """Endgültiger PDF-Name (ohne .pdf), wenn LaTeX das PDF direkt so benennen soll.

    Nur mit ``pdf_name``; ohne bleibt es beim bisherigen ``copy_pdf``-Verhalten.
    """
    pdf_name = job.get("pdf_name")
    copy = job.get("copy_pdf")
    if pdf_name is not None:
        if isinstance(copy, dict):
            raise ConfigurationError(f"LaTeX-Job '{job.get('name', '')}': pdf_name und copy_pdf schließen sich aus")
        try:
            name = str(pdf_name).format(mao=mao_number).strip()
        except (KeyError, IndexError, ValueError) as exc:
            raise ConfigurationError(f"LaTeX-Job '{job.get('name', '')}': ungültiges pdf_name: {exc}") from exc
        if name.lower().endswith(".pdf"):
            name = name[:-4]
        if not name or name in (".", "..") or _FORBIDDEN_IN_FILENAME.search(name):
            raise ConfigurationError(f"LaTeX-Job '{job.get('name', '')}': ungültiges pdf_name: {pdf_name!r}")
        return name
    return None


def remove_aux_files(workdir: Path, jobname: str) -> list[str]:
    removed = []
    for suffix in AUX_SUFFIXES:
        path = workdir / f"{jobname}{suffix}"
        if path.is_file():
            path.unlink()
            removed.append(path.name)
    return removed


TEX_PREFIX = "@tex/"
_TEX_UNSAFE = re.compile(r"[%#{}\\\r\n\x00]")


def tex_path_value(path: Path, *, directory: bool = False) -> str:
    """Pfad für TeX: Schrägstriche, optional abschließend ``/``; Zeichen, die TeX nicht übersteht, werden abgelehnt."""
    text = path.resolve().as_posix()
    if directory and not text.endswith("/"):
        text += "/"
    if _TEX_UNSAFE.search(text):
        raise ConfigurationError(f"Pfad enthält Zeichen, die in LaTeX-Aufrufen nicht unterstützt werden (% # {{ }} \\): {path}")
    return text


def relative_tex_dir(target: Path, start: Path) -> str:
    """Ordner ``target`` relativ zu ``start`` mit Schrägstrichen und abschließendem ``/``; sonst absolut."""
    try:
        rel = Path(os.path.relpath(target.resolve(), start.resolve())).as_posix()
    except ValueError:  # z. B. anderes Laufwerk unter Windows
        return tex_path_value(target, directory=True)
    text = "./" if rel == "." else rel.rstrip("/") + "/"
    if _TEX_UNSAFE.search(text):
        raise ConfigurationError(f"Pfad enthält Zeichen, die in LaTeX-Aufrufen nicht unterstützt werden (% # {{ }} \\): {target}")
    return text


def resolve_template(tex: str, workdir: Path, tex_dir: Path | None, job_name: str) -> Path:
    """``@tex/<Ordner>/<Datei>.tex`` liegt im Vorlagenordner, sonst relativ zum Job-Ordner."""
    if not tex.startswith(TEX_PREFIX):
        return safe_output_path(workdir, tex)
    if tex_dir is None:
        raise ConfigurationError(f"LaTeX-Job '{job_name}': '{tex}' benötigt den zentralen MaO-Ordner mit tex/ "
                                 "(mao config init <MaO-ORDNER>)")
    return safe_output_path(tex_dir, tex[len(TEX_PREFIX):])


def texinputs_for(tex_path: Path, tex_dir: Path | None, existing: str | None) -> str:
    """Suchpfad für ``\\input``/``\\includegraphics``: Vorlagenordner, ``base``, ``tex``; danach die Standardpfade."""
    parts: list[str] = []
    if tex_dir is not None:
        tex_dir = tex_dir.resolve()
        for folder in (tex_path.resolve().parent, tex_dir / "base", tex_dir):
            if folder.is_dir():
                text = folder.as_posix()
                if text not in parts:
                    parts.append(text)
    if existing:
        parts.append(existing.rstrip(os.pathsep))
    # Abschließendes Trennzeichen = danach die Standardpfade der TeX-Installation.
    return os.pathsep.join(parts) + os.pathsep if parts else (existing or "")


def run_latex_jobs(cfg: Dict[str, Any], base_dir: Path, mao_number: int, *,
                   only_latex: bool = False, no_latex: bool = False,
                   dry_run: bool = False, cleanup: bool = False,
                   tex_dir: Path | None = None, year: str | None = None) -> dict[str, int]:
    stats = {"compiled": 0, "skipped": 0}
    if no_latex:
        LOGGER.info("[SKIP] LaTeX deaktiviert")
        return stats

    latex = cfg.get("latex") or {}
    runs = int(latex.get("runs", 2))
    engine = str(latex.get("engine", "lualatex")).strip()
    jobs = _as_list(latex.get("jobs"))
    if not jobs:
        LOGGER.info("[SKIP] Keine LaTeX-Jobs definiert")
        return stats
    for job in jobs:
        jname = str(job.get("name", "")).strip() or "latex"
        if not bool(job.get("enabled", True)):
            stats["skipped"] += 1
            continue
        raw_workdir = str(job.get("workdir", "")).strip()
        workdir = base_dir if raw_workdir in ("", ".") else safe_output_path(base_dir, raw_workdir)
        tex = str(job.get("tex", "")).strip()
        if not tex:
            raise ConfigurationError(f"LaTeX-Job '{jname}' ohne tex")
        tex_path = resolve_template(tex, workdir, tex_dir, jname)
        if not tex_path.is_file():
            LOGGER.warning("LaTeX-Job '%s' übersprungen: Quelldatei fehlt: %s", jname, tex_path)
            stats["skipped"] += 1
            continue
        jobname = str(job.get("jobname", Path(tex).stem)).strip()
        if not re.fullmatch(r"[\w.\-]+", jobname) or jobname in (".", ".."):
            raise ConfigurationError(f"Ungültiger LaTeX-jobname: {jobname}")
        final_name = target_pdf_name(job, jobname, mao_number)
        if final_name is not None:
            # LaTeX schreibt das PDF gleich unter dem Zielnamen; Sonderzeichen nur im Zwischennamen vermeiden.
            jobname = final_name if _SAFE_JOBNAME.fullmatch(final_name) else _ascii_jobname(final_name)
        if dry_run:
            LOGGER.info("[DRY-RUN] LaTeX-Job '%s': %s (Läufe: %s)", jname, tex_path, runs)
            continue
        extra_defs = _as_list(job.get("extra_defs"))
        define_mao = bool(job.get("define_mao", True))
        defs: List[str] = []
        if define_mao:
            defs.append(f"\\def\\MaO{{{mao_number}}}")
            # Pfad-Makros für Vorlagen: \\MaOdb<Datei> (Exporte), \\MaOroot (Jahrgangsordner), \\MaOtex (Vorlagen).
            db_dir = safe_output_path(base_dir, str(latex.get("db_dir", "db")))
            # \\MaOdb relativ zum Job-Ordner (z. B. ../db/): \\DTLloaddb/\\input vertragen im Dateinamen
            # keine Leerzeichen, relative Pfade bleiben davon unabhängig.
            for macro, value in (("MaOroot", tex_path_value(base_dir, directory=True)),
                                 ("MaOdb", relative_tex_dir(db_dir, workdir))):
                defs.append(f"\\edef\\{macro}{{\\detokenize{{{value}}}}}")
            if tex_dir is not None:
                defs.append(f"\\edef\\MaOtex{{\\detokenize{{{tex_path_value(tex_dir, directory=True)}}}}}")
            if year:
                defs.append(f"\\edef\\MaOjahrgang{{\\detokenize{{{year}}}}}")
        defs.extend([str(x) for x in extra_defs if str(x).strip()])
        wrapper = workdir / f".__mao_cli_wrap__{jobname}.tex"
        input_name = tex_path_value(tex_path) if tex.startswith(TEX_PREFIX) else tex
        wrapper_content = "\n".join(defs) + "\n" + f"\\input{{{input_name}}}\n"
        run_env = {**os.environ, "TEXINPUTS": texinputs_for(tex_path, tex_dir, os.environ.get("TEXINPUTS"))} \
            if tex_dir is not None else None
        cmd_base = [engine, "-interaction=nonstopmode", "-halt-on-error", f"-jobname={jobname}", wrapper.name]
        src_pdf = workdir / f"{jobname}.pdf"
        cp = job.get("copy_pdf") if final_name is None else None
        if isinstance(cp, dict):
            src_pdf = safe_output_path(workdir, str(cp.get("from", f"{jobname}.pdf")))
        with step(f"LaTeX: {jname}"):
            try:
                # Generated wrappers never contain user-written text; remove them on failure too.
                write_text(wrapper, wrapper_content)
                previous_mtime = src_pdf.stat().st_mtime_ns if src_pdf.exists() else None
                empty_document = False
                for i in range(runs):
                    log_info(f"Lauf {i + 1}/{runs}", indent=1)
                    try:
                        result = subprocess.run(cmd_base, cwd=str(workdir), capture_output=True,
                                                text=True, errors="replace", check=False, env=run_env)
                    except FileNotFoundError as exc:
                        raise RuntimeError(f"LaTeX-Engine '{engine}' wurde nicht gefunden") from exc
                    if result.returncode and "no pages of output" in (result.stdout or ""):
                        # Kein LaTeX-Fehler: Schleifen über leere Listen erzeugen keine Seite.
                        empty_document = True
                        break
                    if result.returncode:
                        LOGGER.error("LaTeX fehlgeschlagen (%s), Exit-Code %s. stdout:\n%s", jname, result.returncode,
                                     "\n".join(result.stdout.splitlines()[-25:]))
                        LOGGER.error("stderr:\n%s", "\n".join(result.stderr.splitlines()[-25:]))
                        raise RuntimeError(f"LaTeX fehlgeschlagen: {jname} (Log: {workdir / (jobname + '.log')})")
                if empty_document:
                    LOGGER.warning("LaTeX-Job '%s' übersprungen: Das Dokument hat keine Seiten. Vermutlich fehlen Daten "
                                   "(z. B. keine Teilnehmenden mit anwesenheit = 1; leere Listen in db/Include/*.tex).", jname)
                    stats["skipped"] += 1
                    continue
                if not src_pdf.is_file():
                    raise RuntimeError(f"LaTeX-Job '{jname}': Ausgabedatei fehlt: {src_pdf}")
                if previous_mtime is not None and src_pdf.stat().st_mtime_ns <= previous_mtime:
                    raise RuntimeError(f"LaTeX-Job '{jname}': PDF wurde nicht neu erzeugt: {src_pdf}")
                if final_name is not None:
                    final_pdf = safe_output_path(workdir, f"{final_name}.pdf")
                    if src_pdf != final_pdf:
                        src_pdf.replace(final_pdf)
                    log_info(f"PDF: {final_pdf.as_posix()}", indent=1)
                elif isinstance(cp, dict):
                    dst_name = str(cp.get("to", f"{jobname}.pdf")).format(mao=mao_number)
                    dst_pdf = safe_output_path(workdir, dst_name)
                    if src_pdf != dst_pdf:
                        atomic_write(dst_pdf, src_pdf.read_bytes())
                    log_info(f"PDF: {dst_pdf.as_posix()}", indent=1)
                stats["compiled"] += 1
                if cleanup or bool(job.get("cleanup_aux", latex.get("cleanup_aux", False))):
                    removed = remove_aux_files(workdir, jobname)
                    if removed:
                        log_info(f"Aufgeräumt: {', '.join(removed)}", indent=1)
            finally:
                wrapper.unlink(missing_ok=True)
    return stats
