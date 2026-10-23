"""MaO-CLI: latex."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List
from .common import LOGGER, step, log_info, write_text, atomic_write, _as_list
from .config import ConfigurationError, safe_output_path


def run_latex_jobs(cfg: Dict[str, Any], base_dir: Path, mao_number: int, *,
                   only_latex: bool = False, no_latex: bool = False,
                   dry_run: bool = False) -> dict[str, int]:
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
        tex_path = safe_output_path(workdir, tex)
        if not tex_path.is_file():
            LOGGER.warning("LaTeX-Job '%s' übersprungen: Quelldatei fehlt: %s", jname, tex_path)
            stats["skipped"] += 1
            continue
        jobname = str(job.get("jobname", Path(tex).stem)).strip()
        if not re.fullmatch(r"[\w.\-]+", jobname) or jobname in (".", ".."):
            raise ConfigurationError(f"Ungültiger LaTeX-jobname: {jobname}")
        if dry_run:
            LOGGER.info("[DRY-RUN] LaTeX-Job '%s': %s (Läufe: %s)", jname, tex_path, runs)
            continue
        extra_defs = _as_list(job.get("extra_defs"))
        define_mao = bool(job.get("define_mao", True))
        defs: List[str] = []
        if define_mao:
            defs.append(f"\\def\\MaO{{{mao_number}}}")
        defs.extend([str(x) for x in extra_defs if str(x).strip()])
        wrapper = workdir / f".__mao_cli_wrap__{jobname}.tex"
        wrapper_content = "\n".join(defs) + "\n" + f"\\input{{{tex}}}\n"
        cmd_base = [engine, "-interaction=nonstopmode", "-halt-on-error", f"-jobname={jobname}", wrapper.name]
        src_pdf = workdir / f"{jobname}.pdf"
        cp = job.get("copy_pdf")
        if isinstance(cp, dict):
            src_pdf = safe_output_path(workdir, str(cp.get("from", f"{jobname}.pdf")))
        with step(f"LaTeX: {jname}"):
            try:
                # Generated wrappers never contain user-written text; remove them on failure too.
                write_text(wrapper, wrapper_content)
                previous_mtime = src_pdf.stat().st_mtime_ns if src_pdf.exists() else None
                for i in range(runs):
                    log_info(f"Lauf {i + 1}/{runs}", indent=1)
                    try:
                        result = subprocess.run(cmd_base, cwd=str(workdir), capture_output=True,
                                                text=True, errors="replace", check=False)
                    except FileNotFoundError as exc:
                        raise RuntimeError(f"LaTeX-Engine '{engine}' wurde nicht gefunden") from exc
                    if result.returncode:
                        LOGGER.error("LaTeX fehlgeschlagen (%s), Exit-Code %s. stdout:\n%s", jname, result.returncode,
                                     "\n".join(result.stdout.splitlines()[-25:]))
                        LOGGER.error("stderr:\n%s", "\n".join(result.stderr.splitlines()[-25:]))
                        raise RuntimeError(f"LaTeX fehlgeschlagen: {jname} (Log: {workdir / (jobname + '.log')})")
                if not src_pdf.is_file():
                    raise RuntimeError(f"LaTeX-Job '{jname}': Ausgabedatei fehlt: {src_pdf}")
                if previous_mtime is not None and src_pdf.stat().st_mtime_ns <= previous_mtime:
                    raise RuntimeError(f"LaTeX-Job '{jname}': PDF wurde nicht neu erzeugt: {src_pdf}")
                if isinstance(cp, dict):
                    dst_name = str(cp.get("to", f"{jobname}.pdf")).format(mao=mao_number)
                    dst_pdf = safe_output_path(workdir, dst_name)
                    if src_pdf != dst_pdf:
                        atomic_write(dst_pdf, src_pdf.read_bytes())
                    log_info(f"PDF: {dst_pdf.as_posix()}", indent=1)
                stats["compiled"] += 1
            finally:
                wrapper.unlink(missing_ok=True)
    return stats
