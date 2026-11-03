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


# ---------------------------------------------------------------- --run und Versionshinweis

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

UV_TOOL = u.Installation("uv-tool", "0.6.0", "url@abc", requested="v0.6.0", commit="a" * 40)


def target(kind="uv-tool", ref="v0.7.0"):
    inst = u.Installation(kind, "0.6.0")
    return u.UpdateTarget(["x"], "hhgym/MaO", ref, inst)


def test_run_on_posix_runs_uv_and_reports_failure():
    calls = []
    def ok(argv, check):
        calls.append(argv)
        return SimpleNamespace(returncode=0)
    msg = u.run_update(target(), platform="linux", which=lambda n: "/bin/uv", run=ok)
    assert calls == [["/bin/uv", "tool", "install", "--force", "--reinstall", "git+https://github.com/hhgym/MaO@v0.7.0"]]
    assert "aktualisiert" in msg[0]
    with pytest.raises(u.UpdateError, match="Code 2"):
        u.run_update(target(), platform="linux", which=lambda n: "/bin/uv", run=lambda a, check: SimpleNamespace(returncode=2))


def test_run_on_windows_spawns_detached_powershell_instead_of_running_uv():
    spawned = []
    def fake_spawn(argv, **kw):
        spawned.append((argv, kw))
    def no_run(*a, **k):
        raise AssertionError("uv darf unter Windows nicht im laufenden Prozess starten")
    msg = u.run_update(target(), platform="win32", pid=4242, which=lambda n: r"C:\\Tools\\uv.exe", run=no_run, spawn=fake_spawn)
    argv, kw = spawned[0]
    script = argv[-1]
    assert argv[0] == "powershell.exe" and "Wait-Process -Id 4242" in script
    assert "tool install --force --reinstall 'git+https://github.com/hhgym/MaO@v0.7.0'" in script
    assert kw["creationflags"] and "neuen Fenster" in msg[0]


def test_run_refuses_source_installs_missing_uv_and_current_version():
    with pytest.raises(u.UpdateError, match="uv tool install"):
        u.run_update(target(kind="editable"), which=lambda n: "/bin/uv")
    with pytest.raises(u.UpdateError, match="uv wurde nicht gefunden"):
        u.run_update(target(), which=lambda n: None)
    assert u.run_update(target(ref=None), which=lambda n: None) == []


def test_windows_script_escapes_quotes_and_rejects_bad_refs():
    assert "O''Brien" in u.windows_update_script(1, r"C:\\O'Brien\\uv.exe", "git+https://x@v1")
    with pytest.raises(u.UpdateError, match="Ungültige"):
        u.plan_update("stable", None, "1.0; calc")


def test_cli_update_run_executes_only_with_flag(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(u, "detect_installation", lambda: UV_TOOL)
    monkeypatch.setattr(u, "_fetch_tags", lambda repo: ["v0.7.0"])
    monkeypatch.setattr(cli, "run_update", lambda t: ran.append(t.ref) or ["ok"])
    assert cli.main(["update"]) == 0 and ran == []
    assert cli.main(["update", "--run"]) == 0 and ran == ["v0.7.0"]


def notice_env(monkeypatch, tmp_path, inst=UV_TOOL):
    monkeypatch.delenv(u.NOTICE_ENV, raising=False)
    monkeypatch.setattr(u, "detect_installation", lambda: inst)
    monkeypatch.setattr(u, "_state_file", lambda: tmp_path / "state.json")


def test_notice_reports_newer_tag_once_per_day(monkeypatch, tmp_path):
    notice_env(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(u, "_fetch_tags", lambda repo, timeout=10: calls.append(1) or ["v0.6.0", "v0.7.0"])
    now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    assert "v0.7.0" in u.update_notice(now) and "mao update --run" in u.update_notice(now)
    assert len(calls) == 1  # zweiter Aufruf aus dem Zwischenspeicher
    assert u.update_notice(now + timedelta(hours=25)) and len(calls) == 2


def test_notice_silent_when_current_offline_or_disabled(monkeypatch, tmp_path):
    notice_env(monkeypatch, tmp_path)
    monkeypatch.setattr(u, "_fetch_tags", lambda repo, timeout=10: ["v0.6.0"])
    assert u.update_notice() is None
    notice_env(monkeypatch, tmp_path / "x")
    def boom(repo, timeout=10):
        raise u.UpdateError("offline")
    monkeypatch.setattr(u, "_fetch_tags", boom)
    assert u.update_notice() is None and (tmp_path / "x" / "state.json").exists()
    monkeypatch.setenv(u.NOTICE_ENV, "1")
    assert u.update_notice() is None


def test_notice_for_branch_installs_and_dev_sources(monkeypatch, tmp_path):
    branch = u.Installation("uv-tool", "0.6.0.dev0", requested="develop", commit="a" * 40)
    notice_env(monkeypatch, tmp_path, branch)
    monkeypatch.setattr(u, "_fetch_head", lambda repo, ref, timeout=3: "b" * 40)
    assert "develop" in u.update_notice()
    notice_env(monkeypatch, tmp_path / "y", u.Installation("editable", "0.6.0"))
    assert u.update_notice() is None


def test_cli_prints_notice_only_in_terminals_and_not_for_update(monkeypatch, capsys):
    monkeypatch.setattr(cli, "update_notice", lambda: "NEUE VERSION")
    monkeypatch.setattr(u, "_fetch_tags", lambda repo: ["v1.0.0"])
    monkeypatch.setattr("sys.stderr.isatty", lambda: True, raising=False)
    cli.main(["profiles", "--config-dir", "/nonexistent"])
    assert "NEUE VERSION" in capsys.readouterr().err
    cli.main(["update", "--channel", "develop"])
    assert "NEUE VERSION" not in capsys.readouterr().err


def test_develop_channel_compares_commits(monkeypatch):
    branch = u.Installation("uv-tool", "0.6.0.dev0", requested="develop", commit="a" * 40)
    monkeypatch.setattr(u, "detect_installation", lambda: branch)
    monkeypatch.setattr(u, "_fetch_head", lambda repo, ref, timeout=3: "a" * 40)
    same = u.plan_update("develop", None, None)
    assert same.ref is None and any("Bereits aktuell" in line for line in same.lines)
    assert u.run_update(same) == []
    forced = u.plan_update("develop", None, None, force=True)
    assert forced.ref == "develop"
    monkeypatch.setattr(u, "_fetch_head", lambda repo, ref, timeout=3: "b" * 40)
    newer = u.plan_update("develop", None, None)
    assert newer.ref == "develop" and any("Neuer Stand" in line for line in newer.lines)


def test_develop_channel_installs_when_comparison_fails_or_not_a_branch_install(monkeypatch):
    branch = u.Installation("uv-tool", "0.6.0.dev0", requested="develop", commit="a" * 40)
    monkeypatch.setattr(u, "detect_installation", lambda: branch)
    def boom(repo, ref, timeout=3):
        raise OSError("offline")
    monkeypatch.setattr(u, "_fetch_head", boom)
    target = u.plan_update("develop", None, None)
    assert target.ref == "develop" and any("nicht möglich" in line for line in target.lines)
    tagged = u.Installation("uv-tool", "0.6.0", requested="v0.6.0", commit="c" * 40)
    monkeypatch.setattr(u, "detect_installation", lambda: tagged)
    monkeypatch.setattr(u, "_fetch_head", lambda *a, **k: (_ for _ in ()).throw(AssertionError("kein Abruf erwartet")))
    assert u.plan_update("develop", None, None).ref == "develop"
