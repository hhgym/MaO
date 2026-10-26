"""``mao update`` nennt nur den uv-Befehl; es installiert nichts selbst."""
from __future__ import annotations

import pytest

from mao_cli import cli, updater as u

TAGS = ["v0.5.0", "v0.6.0-rc.1", "v0.6.0rc2", "v0.6.0.dev0", "v0.5.1", "develop-build", "v0.6.0+g123", "vfoo", "v0.4.9"]


def test_latest_stable_ignores_prereleases_and_noise(monkeypatch):
    monkeypatch.setattr(u, "_fetch_tags", lambda repo: TAGS)
    assert u.latest_tag("hhgym/MaO", "stable") == "v0.5.1"


def test_latest_rc_includes_release_candidates_only(monkeypatch):
    monkeypatch.setattr(u, "_fetch_tags", lambda repo: TAGS)
    assert u.latest_tag("hhgym/MaO", "rc") == "v0.6.0rc2"


def test_no_matching_release_is_reported(monkeypatch):
    monkeypatch.setattr(u, "_fetch_tags", lambda repo: ["develop-build"])
    with pytest.raises(u.UpdateError, match="--version"):
        u.latest_tag("hhgym/MaO", "stable")


def test_hint_for_develop_needs_no_network(monkeypatch):
    monkeypatch.setattr(u, "_fetch_tags", lambda repo: (_ for _ in ()).throw(AssertionError("kein Netz")))
    lines = u.update_hint("develop", None, None)
    assert "uv tool install --force --reinstall git+https://github.com/hhgym/MaO@develop" in "\n".join(lines)


def test_explicit_version_gets_v_prefix():
    text = "\n".join(u.update_hint("stable", "me/Fork", "0.7.1"))
    assert "git+https://github.com/me/Fork@v0.7.1" in text


def test_up_to_date_message(monkeypatch):
    monkeypatch.setattr(u, "__version__", "1.0.0")
    monkeypatch.setattr(u, "detect_installation", lambda: u.Installation("uv-tool", "1.0.0"))
    monkeypatch.setattr(u, "_fetch_tags", lambda repo: ["v1.0.0"])
    assert "Bereits aktuell" in "\n".join(u.update_hint("stable", None, None))


def test_invalid_repository_and_channel():
    with pytest.raises(u.UpdateError):
        u.repository_name("evil/../x")
    with pytest.raises(u.UpdateError):
        u.update_hint("nightly", None, None)


def test_editable_install_gets_switch_instruction(monkeypatch):
    monkeypatch.setattr(u, "detect_installation", lambda: u.Installation("editable", "0.6.0.dev0", "file:///x"))
    assert "pip uninstall mao-cli" in "\n".join(u.update_hint("develop", None, None))


def test_cli_update_and_legacy_subcommand(monkeypatch, capsys):
    monkeypatch.setattr(u, "_fetch_tags", lambda repo: ["v1.0.0"])
    assert cli.main(["update", "--channel", "develop"]) == 0
    assert "@develop" in capsys.readouterr().out
    assert cli.main(["update", "install", "--channel", "develop", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "entfallen" in out and "@develop" in out


def test_cli_update_network_failure_is_exit_5(monkeypatch, capsys):
    def boom(repo):
        raise u.UpdateError("GitHub nicht erreichbar")
    monkeypatch.setattr(u, "_fetch_tags", boom)
    assert cli.main(["update"]) == 5
    assert "nicht erreichbar" in capsys.readouterr().err
