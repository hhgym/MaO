"""Vorlagen in <MaO>/tex: Suchpfad, Pfad-Makros, getrennte Orte für .tex und PDF."""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import mao_cli.latex as latex_module
from mao_cli.config import ConfigurationError
from mao_cli.latex import run_latex_jobs, texinputs_for, tex_path_value

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def layout(tmp_path: Path):
    mao = tmp_path / "Mein MaO"
    (mao / "tex" / "base").mkdir(parents=True)
    (mao / "tex" / "Doc").mkdir()
    year = mao / "Jahrgang" / "66_2026-27"
    (year / "db" / "T").mkdir(parents=True)
    (year / "Doc").mkdir()
    return mao, year


def config(**job):
    base = {"name": "doc", "id": "doc", "workdir": "Doc", "tex": "@tex/Doc/Doc.tex", "pdf_name": "MaO_{mao}_Doc"}
    return {"latex": {"runs": 1, "jobs": [{**base, **job}]}}


def test_texinputs_lists_template_folder_base_and_tex_then_defaults(tmp_path):
    mao, _ = layout(tmp_path)
    value = texinputs_for(mao / "tex" / "Doc" / "Doc.tex", mao / "tex", None)
    parts = value.split(os.pathsep)
    assert parts[:3] == [(mao / "tex" / "Doc").resolve().as_posix(), (mao / "tex" / "base").resolve().as_posix(),
                         (mao / "tex").resolve().as_posix()]
    assert value.endswith(os.pathsep)  # danach Standardpfade
    assert texinputs_for(mao / "x.tex", None, None) == ""


def test_unsafe_paths_are_rejected(tmp_path):
    with pytest.raises(ConfigurationError, match="nicht unterstützt"):
        tex_path_value(tmp_path / "100% sicher")
    assert tex_path_value(tmp_path / "mit Leerzeichen", directory=True).endswith("mit Leerzeichen/")


def test_at_tex_without_central_folder_is_an_error(tmp_path):
    with pytest.raises(ConfigurationError, match="zentralen MaO-Ordner"):
        run_latex_jobs(config(), tmp_path, 66)


def test_wrapper_defines_path_macros_and_sets_texinputs(tmp_path, monkeypatch):
    mao, year = layout(tmp_path)
    (mao / "tex" / "Doc" / "Doc.tex").write_text("x", encoding="utf-8")
    seen = {}

    def fake(args, cwd, env=None, **kw):
        wrapper = Path(cwd) / args[-1]
        seen["wrapper"] = wrapper.read_text(encoding="utf-8")
        seen["env"] = env
        seen["cwd"] = Path(cwd)
        (Path(cwd) / "MaO_66_Doc.pdf").write_bytes(b"PDF")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(latex_module.subprocess, "run", fake)
    stats = run_latex_jobs(config(), year, 66, tex_dir=mao / "tex", year="2026-27")
    assert stats["compiled"] == 1
    text = seen["wrapper"]
    assert "\\def\\MaO{66}" in text
    assert "\\edef\\MaOdb{\\detokenize{../db/}}" in text
    assert "\\edef\\MaOtex{\\detokenize{" + (mao / "tex").resolve().as_posix() + "/}}" in text
    assert "\\edef\\MaOjahrgang{\\detokenize{2026-27}}" in text
    assert "\\input{" + (mao / "tex" / "Doc" / "Doc.tex").resolve().as_posix() + "}" in text
    assert seen["cwd"].resolve() == (year / "Doc").resolve()
    assert (mao / "tex" / "base").resolve().as_posix() in seen["env"]["TEXINPUTS"]
    assert (year / "Doc" / "MaO_66_Doc.pdf").is_file()


def test_missing_template_is_skipped(tmp_path, caplog):
    mao, year = layout(tmp_path)
    stats = run_latex_jobs(config(), year, 66, tex_dir=mao / "tex")
    assert stats["skipped"] == 1 and "Quelldatei fehlt" in caplog.text


@pytest.mark.skipif(shutil.which("lualatex") is None, reason="lualatex nicht installiert")
def test_real_lualatex_template_data_and_pdf_in_different_folders(tmp_path):
    mao, year = layout(tmp_path)
    (mao / "tex" / "base" / "template.tex").write_text(
        "\\documentclass{article}\n\\usepackage{graphicx,datatool}\n\\DTLsetseparator{;}\n", encoding="utf-8")
    (mao / "tex" / "Doc" / "logo.png").write_bytes(PNG)
    (mao / "tex" / "Doc" / "Doc.tex").write_text(
        "\\input{template.tex}\n\\DTLloaddb{t}{\\MaOdb T/x.csv}\n\\begin{document}\n"
        "\\DTLforeach{t}{\\a=a,\\b=b}{\\a-\\b;}\n\\includegraphics[width=1cm]{logo.png}\n"
        "MaO \\MaO\\ Jahrgang \\MaOjahrgang\n\\end{document}\n", encoding="utf-8")
    (year / "db" / "T" / "x.csv").write_text("a;b\n1;2\n3;4\n", encoding="utf-8")
    stats = run_latex_jobs(config(), year, 66, tex_dir=mao / "tex", year="2026-27")
    assert stats["compiled"] == 1
    pdf = year / "Doc" / "MaO_66_Doc.pdf"
    assert pdf.read_bytes().startswith(b"%PDF")
    if shutil.which("pdftotext"):  # Daten wurden trotz Leerzeichen im MaO-Ordnernamen geladen
        text = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True).stdout
        assert "1-2;" in text.replace(" ", "") and "3-4;" in text.replace(" ", "")
    assert not list((mao / "tex").glob("**/*.pdf"))  # PDF landet nie im Vorlagenordner
    assert not list((year / "Doc").glob(".__mao_cli_wrap__*"))


def test_export_macros_use_maodb_relative_to_export_folder(tmp_path):
    from mao_cli.exports import render_export_macros
    db = tmp_path / "db"
    text = render_export_macros(
        tex_path=db / "Include" / "T.tex",
        loads=[("A", db / "Teilnehmende" / "Klasse" / "a.csv")], db_dir=db)
    assert "\\DTLloaddb{A}{\\MaOdb Teilnehmende/Klasse/a.csv}" in text
    assert "../" not in text
