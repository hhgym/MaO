"""Jahrgangsordner unter <MaO>/Jahrgang/ werden ohne Registrierung erkannt."""
from __future__ import annotations

import pandas as pd
import pytest

from mao_cli import cli, db as dbmod, workspaces as w
from mao_cli.config import ConfigurationError


@pytest.fixture
def mao(tmp_path, monkeypatch):
    monkeypatch.delenv("MAO_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "state"))
    monkeypatch.setattr(w, "__file__", str(tmp_path / "nowhere" / "mao_cli" / "workspaces.py"))
    home = tmp_path / "MaO"
    w.init_config(home / "config", cli.DEFAULTS)
    monkeypatch.chdir(home)
    return home


def test_folders_are_discovered_with_number_and_status(mao):
    (mao / "Jahrgang" / "65_2025-26").mkdir(parents=True)
    (mao / "Jahrgang" / "66_2026-27").mkdir()
    (mao / "Jahrgang" / "Notizen").mkdir()
    (mao / "Jahrgang" / "66_2026-28").mkdir()  # ungültiges Schuljahr wird ignoriert
    doc = w.load_settings(mao / "config")
    assert set(doc["workspaces"]) == {"2025-26", "2026-27"}
    assert doc["workspaces"]["2026-27"]["mao"] == 66
    assert doc["current_year"] is None


def test_use_sets_current_and_marks_others_archived(mao, capsys):
    (mao / "Jahrgang" / "65_2025-26").mkdir(parents=True)
    (mao / "Jahrgang" / "66_2026-27").mkdir()
    assert cli.main(["jahrgang", "use", "66"]) == 0
    doc = w.load_settings(mao / "config")
    assert doc["current_year"] == "2026-27"
    assert doc["workspaces"]["2026-27"]["status"] == "current"
    assert doc["workspaces"]["2025-26"]["status"] == "archived"
    text = (mao / "config" / "settings.yaml").read_text(encoding="utf-8")
    assert "current_year: 2026-27" in text
    assert "workspaces: {}" in text or "65_2025-26" not in text  # erkannte Ordner werden nicht gespeichert
    assert cli.main(["jahrgang", "use", "2025-26"]) == 0
    assert w.load_settings(mao / "config")["current_year"] == "2025-26"
    assert cli.main(["jahrgang", "use", "99"]) == 2


def test_year_in_two_folders_is_an_error(mao):
    (mao / "Jahrgang" / "66_2026-27").mkdir(parents=True)
    (mao / "Jahrgang" / "67_2026-27").mkdir()
    with pytest.raises(ConfigurationError, match="doppelt"):
        w.load_settings(mao / "config")


def test_explicit_registration_wins_over_discovery(mao):
    (mao / "Jahrgang" / "66_2026-27").mkdir(parents=True)
    elsewhere = mao.parent / "extern"
    doc = w.load_settings(mao / "config")
    doc["workspaces"]["2026-27"] = {"root": str(elsewhere), "status": "current", "mao": 66}
    doc["current_year"] = "2026-27"
    w.save_settings(mao / "config", doc)
    loaded = w.load_settings(mao / "config")
    assert loaded["workspaces"]["2026-27"]["root"] == str(elsewhere.resolve())
    assert "auto" not in loaded["workspaces"]["2026-27"]


def test_new_explicit_creates_folder_and_keeps_current(mao, capsys):
    assert cli.main(["jahrgang", "new", "--nr", "66", "--year", "2026-27", "--current"]) == 0
    assert (mao / "Jahrgang" / "66_2026-27").is_dir()
    assert cli.main(["jahrgang", "new", "--nr", "67", "--year", "2027-28"]) == 0
    doc = w.load_settings(mao / "config")
    assert doc["current_year"] == "2026-27" and "2027-28" in doc["workspaces"]
    assert cli.main(["jahrgang", "new", "--nr", "67", "--year", "2027-28"]) == 0  # idempotent
    assert "Vorhanden" in capsys.readouterr().out
    assert cli.main(["jahrgang", "new", "--nr", "68"]) == 2  # --year fehlt


def test_new_refuses_other_number_for_registered_year(mao):
    assert cli.main(["jahrgang", "new", "--nr", "66", "--year", "2026-27"]) == 0
    assert cli.main(["jahrgang", "new", "--nr", "67", "--year", "2026-27"]) == 2


def test_current_workspace_found_from_inside_folder(mao, monkeypatch):
    inner = mao / "Jahrgang" / "65_2025-26" / "Listen"
    inner.mkdir(parents=True)
    (mao / "Jahrgang" / "66_2026-27").mkdir()
    assert cli.main(["jahrgang", "use", "66"]) == 0
    monkeypatch.chdir(inner)
    sel = w.resolve_workspace(w.load_settings(mao / "config"))
    assert (sel.year, sel.mao_number) == ("2025-26", 65)


class FakeDB:
    frames: dict[str, pd.DataFrame] = {}

    def __init__(self, cfg):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def query_df(self, sql):
        for key, frame in self.frames.items():
            if key in sql:
                return frame
        raise AssertionError(sql)


def patch_db(monkeypatch, mao, *, jahrgang_rows):
    tables = (mao / "config" / "tables.ini")
    text = tables.read_text(encoding="utf-8").replace("DEMO_JAHRGAENGE", "J_T").replace("DEMO_SCHULJAHRE", "S_T")
    tables.write_text(text, encoding="utf-8")
    FakeDB.frames = {
        "FROM `S_T`": pd.DataFrame({"id": [11], "beginn": [pd.Timestamp("2026-08-01")]}),
        "MAX(jahrgang_nr)": pd.DataFrame({"n": [65]}),
        "WHERE schuljahr_id": pd.DataFrame({"jahrgang_nr": jahrgang_rows}),
    }
    monkeypatch.setattr(cli, "DB", FakeDB)
    monkeypatch.setattr(cli, "can_reach", lambda *a, **k: True)
    monkeypatch.setattr(cli, "load_db_config", lambda d: type("C", (), {"host": "h", "port": 1})())


def test_new_from_database_uses_active_school_year(mao, monkeypatch):
    patch_db(monkeypatch, mao, jahrgang_rows=[66])
    assert cli.main(["jahrgang", "new"]) == 0
    assert (mao / "Jahrgang" / "66_2026-27").is_dir()
    assert w.load_settings(mao / "config")["current_year"] == "2026-27"


def test_new_from_database_without_jahrgang_gives_proposal(mao, monkeypatch, capsys):
    patch_db(monkeypatch, mao, jahrgang_rows=[])
    assert cli.main(["jahrgang", "new"]) == 2
    err = capsys.readouterr().err
    assert "--nr 66 --year 2026-27" in err
    assert not (mao / "Jahrgang").exists()


def test_school_year_label():
    assert dbmod.school_year_label("2026-08-01") == "2026-27"
    assert dbmod.school_year_label(pd.Timestamp("2099-08-01")) == "2099-00"


def test_active_jahrgang_requires_real_table_names():
    with pytest.raises(ValueError, match="echte Namen"):
        dbmod.active_jahrgang_db(object(), {"jahrgaenge_table": "DEMO_J", "schuljahre_table": "DEMO_S"})
