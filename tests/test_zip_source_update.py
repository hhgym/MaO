"""Regressionstests für aus GitHub-Branch-ZIP installierte Entwicklungsstände."""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from mao_cli import updater as u, update_install as install


SHA = "a" * 40
VERSION = "0.6.0.dev0+g" + SHA[:12]
NAME = f"mao_cli-{VERSION}-py3-none-any.whl"
BUILD = u.DevelopBuild("hhgym/MaO", SHA, 23, NAME, "https://github.com/a.whl", "https://github.com/a.whl.sha256")


def _wheel(tmp_path: Path, *, changed: bool = False):
    package = tmp_path / "local" / "mao_cli"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.6.0.dev0"\n', encoding="utf-8")
    (package / "cli.py").write_text("print('same code')\n", encoding="utf-8")
    (package / "defaults").mkdir()
    (package / "defaults" / "profiles.ini").write_text("[global]\n", encoding="utf-8")
    filename = tmp_path / NAME
    with zipfile.ZipFile(filename, "w") as zipf:
        zipf.writestr("mao_cli/__init__.py", f'__version__ = "{VERSION}"\n')
        zipf.writestr("mao_cli/cli.py", "print('modified code' if True else '')\n" if changed else "print('same code')\n")
        zipf.writestr("mao_cli/defaults/profiles.ini", "[global]\n")
    return package, filename


def test_branch_zip_and_ci_wheel_identical_despite_commit_stamp(monkeypatch, tmp_path):
    pkg, wheel = _wheel(tmp_path)
    monkeypatch.setattr(u, "__file__", str(pkg / "updater.py"))
    assert u._packaged_source_matches(wheel)


def test_crlf_line_endings_are_not_treated_as_different_code(monkeypatch, tmp_path):
    pkg, wheel = _wheel(tmp_path)
    for candidate in pkg.rglob("*"):
        if candidate.is_file():
            candidate.write_bytes(candidate.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    monkeypatch.setattr(u, "__file__", str(pkg / "updater.py"))
    assert u._packaged_source_matches(wheel)


def test_branch_zip_differs_in_one_module(monkeypatch, tmp_path):
    pkg, wheel = _wheel(tmp_path, changed=True)
    monkeypatch.setattr(u, "__file__", str(pkg / "updater.py"))
    assert not u._packaged_source_matches(wheel)


def test_develop_check_and_install_noop_for_same_zip(monkeypatch, tmp_path):
    pkg, wheel = _wheel(tmp_path)
    monkeypatch.setattr(u, "__file__", str(pkg / "updater.py"))
    monkeypatch.setattr(u, "find_develop", lambda repo: BUILD)
    monkeypatch.setattr(u, "stage_develop", lambda build, directory: (wheel, VERSION))
    monkeypatch.setattr(u, "_installed_version", lambda home: "0.6.0.dev0")
    monkeypatch.setattr(u, "install_managed", lambda *args, **kwargs: pytest.fail("No install for same source"))
    assert "ist aktuell" in u.update_action("check", "develop", None, home=tmp_path)
    assert "ist aktuell" in u.update_action("install", "develop", None, yes=True, home=tmp_path)


def test_isolated_python_selfcheck_does_not_inherit_source_path(monkeypatch, tmp_path):
    observed = []
    env = tmp_path / "new-venv"
    env.mkdir()
    monkeypatch.setattr(install, "python_in", lambda path: tmp_path / "python")
    def fake_run(args, **kwargs):
        observed.extend(args)
        return SimpleNamespace(returncode=0, stdout="mao " + VERSION + "\n", stderr="")
    monkeypatch.setattr(install.subprocess, "run", fake_run)
    install._verify_environment(env, VERSION)
    assert observed[1:4] == ["-I", "-m", "mao_cli"]


def test_selfcheck_reports_actual_error_without_activating(monkeypatch, tmp_path):
    monkeypatch.setattr(install, "python_in", lambda path: tmp_path / "python")
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **kw:
        SimpleNamespace(returncode=1, stdout="", stderr="Traceback\nModuleNotFoundError: No module named 'mysql'\n"))
    with pytest.raises(install.InstallError, match="ModuleNotFoundError.*mysql"):
        install._verify_environment(tmp_path, VERSION)
