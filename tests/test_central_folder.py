"""Zentraler MaO-Ordner: Suche, Verweis, relative Jahrgangsordner."""
from __future__ import annotations

import pytest

from mao_cli import cli, workspaces as w
from mao_cli.config import ConfigurationError


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("MAO_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    # Quellordner-Regel ausschalten, damit nur die zu testende Reihenfolge wirkt.
    monkeypatch.setattr(w, "__file__", str(tmp_path / "nowhere" / "mao_cli" / "workspaces.py"))
    return tmp_path


def make_mao(root):
    mao = root / "MaO"
    w.init_config(mao / "config", cli.DEFAULTS)
    return mao


def test_init_creates_config_and_tex(home):
    mao = make_mao(home)
    assert (mao / "config" / "settings.yaml").is_file()
    assert (mao / "tex").is_dir()


def test_found_from_inside_the_mao_folder(home, monkeypatch):
    mao = make_mao(home)
    sub = mao / "66_2026-27" / "Listen"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert w.resolve_config_dir() == (mao / "config", "aktueller Ordner")


def test_cli_and_env_accept_mao_folder_itself(home, monkeypatch):
    mao = make_mao(home)
    assert w.config_dir_for(mao) == mao / "config"
    monkeypatch.setenv("MAO_CONFIG", str(mao))
    assert w.resolve_config_dir() == (mao / "config", "MAO_CONFIG")


def test_pointer_is_used_outside_and_ranks_after_search(home, monkeypatch):
    mao = make_mao(home)
    w.write_pointer(mao)
    outside = home / "elsewhere"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert w.resolve_config_dir() == (mao / "config", "Verweis (mao config use)")
    (home / "other").mkdir()
    other = make_mao(home / "other")
    monkeypatch.chdir(other)
    assert w.resolve_config_dir()[1] == "aktueller Ordner"


def test_pointer_requires_initialised_folder(home):
    with pytest.raises(ConfigurationError, match="settings.yaml"):
        w.write_pointer(home / "leer")


def test_default_when_nothing_known(home):
    assert w.resolve_config_dir() == (w.user_state_dir(), "Standardordner")


def test_roots_inside_mao_folder_are_stored_relative(home):
    mao = make_mao(home)
    config = mao / "config"
    doc = w.load_settings(config)
    doc["workspaces"]["2026-27"] = {"root": str(mao / "66_2026-27"), "status": "current", "mao": 66}
    doc["workspaces"]["2025-26"] = {"root": str(home / "extern" / "alt"), "status": "archived"}
    doc["current_year"] = "2026-27"
    w.save_settings(config, doc)
    text = (config / "settings.yaml").read_text(encoding="utf-8")
    assert "root: 66_2026-27" in text
    assert str(home) in text  # außerhalb bleibt absolut
    loaded = w.load_settings(config)
    assert loaded["workspaces"]["2026-27"]["root"] == str((mao / "66_2026-27").resolve())


def test_relative_roots_follow_the_folder_to_another_computer(home):
    mao = make_mao(home)
    doc = w.load_settings(mao / "config")
    doc["workspaces"]["2026-27"] = {"root": str(mao / "66_2026-27"), "status": "current"}
    doc["current_year"] = "2026-27"
    w.save_settings(mao / "config", doc)
    moved = home / "Rechner2" / "Nextcloud" / "MaO"
    moved.parent.mkdir(parents=True)
    mao.rename(moved)
    assert w.load_settings(moved / "config")["workspaces"]["2026-27"]["root"] == str((moved / "66_2026-27").resolve())


def test_workspace_detected_from_current_folder(home):
    mao = make_mao(home)
    doc = w.load_settings(mao / "config")
    doc["workspaces"]["2026-27"] = {"root": str(mao / "66_2026-27"), "status": "current"}
    doc["workspaces"]["2025-26"] = {"root": str(mao / "65_2025-26"), "status": "archived"}
    doc["current_year"] = "2026-27"
    w.save_settings(mao / "config", doc)
    settings = w.load_settings(mao / "config")
    inside = mao / "65_2025-26" / "Listen"
    inside.mkdir(parents=True)
    picked = w.resolve_workspace(settings, cwd=inside)
    assert picked.year == "2025-26" and picked.directory == (mao / "65_2025-26").resolve()
    assert w.resolve_workspace(settings, cwd=home).year == "2026-27"
    assert w.resolve_workspace(settings, year="2026-27", cwd=inside).year == "2026-27"


def test_cli_init_use_and_show(home, monkeypatch, capsys):
    mao = home / "MaO"
    assert cli.main(["config", "init", str(mao)]) == 0
    assert cli.main(["config", "use", str(mao)]) == 0
    monkeypatch.chdir(home)
    assert cli.main(["config", "show"]) == 0
    out = capsys.readouterr().out
    assert "Verweis (mao config use)" in out and str(mao / "config") in out


def test_workspace_add_central_has_no_wrapper_and_relative_root(home, monkeypatch):
    mao = make_mao(home)
    monkeypatch.chdir(mao)
    root = mao / "66_2026-27"
    assert cli.main(["workspace", "add", "2026-27", "--root", str(root)]) == 0
    assert not (root / "mao-hier.cmd").exists() and not (root / "mao-hier.sh").exists()
    assert "root: 66_2026-27" in (mao / "config" / "settings.yaml").read_text(encoding="utf-8")


def test_conflict_copies_are_reported_but_not_blocking(home, capsys):
    mao = make_mao(home)
    (mao / "config" / "settings (conflicted copy 2026-10-26 120000).yaml").write_text("x")
    (mao / "config" / "tables (Konflikt-Kopie 2026).ini").write_text("x")
    (mao / "tex" / "Doc").mkdir()
    (mao / "tex" / "Doc" / "Doc (conflicted copy PC2).tex").write_text("x")
    (mao / "config" / "normal (kopie).yaml").write_text("x")
    found = w.find_conflict_copies(mao / "config")
    assert sorted(p.name for p in found) == ["Doc (conflicted copy PC2).tex", "settings (conflicted copy 2026-10-26 120000).yaml",
                                             "tables (Konflikt-Kopie 2026).ini"]
    assert cli.main(["--config-dir", str(mao), "config", "show"]) == 0
    err = capsys.readouterr().err
    assert err.count("Konfliktkopie") == 3
