"""Gemeinsame Testeinstellungen: der Versionshinweis darf im Test nie das Netz benutzen."""
import pytest


@pytest.fixture(autouse=True)
def _no_update_notice(monkeypatch):
    monkeypatch.setenv("MAO_NO_UPDATE_CHECK", "1")


@pytest.fixture
def tex(tmp_path):
    base = tmp_path / "tex"
    (base / "Teil").mkdir(parents=True)
    (base / "Raum").mkdir()
    (base / "Fest").mkdir()
    (base / "base").mkdir()
    (base / "Teil" / "Teil.tex").write_text("\\input{template.tex}\n\\input{../db/Include/Tabellen-Teilnehmende-Klasse.tex}\n", encoding="utf-8")
    (base / "base" / "template.tex").write_text("% Basis/Raeume.csv steht nur im Kommentar\n\\DTLloaddb{r}{\\MaOdb Basis/Raeume.csv}\n", encoding="utf-8")
    (base / "Raum" / "Raum.tex").write_text("\\input{\\MaOroot db/Include/Tabellen-Raeume.tex}\n", encoding="utf-8")
    (base / "Fest" / "Fest.tex").write_text("kein Bezug\n", encoding="utf-8")
    return base
