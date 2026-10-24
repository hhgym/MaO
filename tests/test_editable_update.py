"""Offline-Tests für updates direkt in einem pip-install-e-Quellordner."""
from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from mao_cli import updater as u
from mao_cli import editable_update as e
from mao_cli.update_install import InstallError

OLD = '0.6.0.dev0'
NEW = '0.6.0.dev0+g' + 'a' * 12
OLD_PROJECT = b'''[build-system]\nrequires = ["setuptools>=68", "wheel"]\nbuild-backend = "setuptools.build_meta"\n[project]\nname = "mao-cli"\nversion = "0.6.0.dev0"\nrequires-python = ">=3.10"\ndependencies = ["packaging>=23"]\n[project.scripts]\nmao = "mao_cli.cli:main"\n'''


def _source(tmp_path):
    root = tmp_path / 'MaO'
    root.mkdir()
    (root / 'pyproject.toml').write_bytes(OLD_PROJECT)
    pkg = root / 'mao_cli'
    pkg.mkdir()
    (pkg / '__init__.py').write_text(f'__version__ = "{OLD}"\n')
    (pkg / 'cli.py').write_text('def parse_args(argv):\n    print("mao " + __import__("mao_cli").__version__)\n')
    (pkg / 'updater.py').write_text('# old\n')
    (root / 'custom-notes.txt').write_text('Must not change')
    return e.EditableSource(root)


def _wheel(tmp_path):
    wheel = tmp_path / ('mao_cli-' + NEW + '-py3-none-any.whl')
    with zipfile.ZipFile(wheel, 'w') as z:
        z.writestr('mao_cli/__init__.py', f'__version__ = "{NEW}"\n')
        z.writestr('mao_cli/cli.py', 'def parse_args(argv):\n    print("mao " + __import__("mao_cli").__version__)\n')
        z.writestr('mao_cli/updater.py', '# new\n')
        z.writestr('mao_cli/defaults/profiles.ini', '[x]\ny = yes\n')
    return wheel


def test_detect_editable_requires_current_source_match(monkeypatch, tmp_path):
    source = _source(tmp_path)
    package_path = source.package / 'editable_update.py'
    monkeypatch.setattr(e, '__file__', str(package_path))
    uri = source.root.as_uri()
    monkeypatch.setattr(e.importlib.metadata, 'distribution', lambda name:
                        SimpleNamespace(read_text=lambda key: json.dumps({'url': uri, 'dir_info': {'editable': True}})))
    assert e.detect_editable() == source
    monkeypatch.setattr(e, '__file__', str(tmp_path / 'other' / 'editable_update.py'))
    with pytest.raises(InstallError, match='tatsächlich gestarteten'):
        e.detect_editable()
    monkeypatch.setattr(e.importlib.metadata, 'distribution', lambda name:
                        SimpleNamespace(read_text=lambda key: json.dumps({'url': uri, 'dir_info': {'editable': False}})))
    assert e.detect_editable() is None


def test_install_editable_updates_original_source_and_rollback(monkeypatch, tmp_path):
    source, wheel = _source(tmp_path), _wheel(tmp_path)
    assert 'version = "' + NEW + '"' in e.prepare_pyproject(source, OLD_PROJECT, NEW).decode()
    backup = e.install_editable(source, wheel, OLD_PROJECT, NEW)
    assert (source.package / 'updater.py').read_text() == '# new\n'
    assert (source.package / 'defaults' / 'profiles.ini').is_file()
    assert f'__version__ = "{NEW}"' in (source.package / '__init__.py').read_text()
    assert f'version = "{NEW}"' in (source.root / 'pyproject.toml').read_text()
    assert backup.joinpath('mao_cli/updater.py').read_text() == '# old\n'
    assert (source.root / 'custom-notes.txt').read_text() == 'Must not change'
    assert e._read_state(source)['active'] == NEW
    assert e.rollback_editable(source) == OLD
    assert (source.package / 'updater.py').read_text() == '# old\n'
    assert f'version = "{OLD}"' in (source.root / 'pyproject.toml').read_text()
    assert e._read_state(source)['active'] == OLD


def test_editable_rejects_local_modification_since_last_update(tmp_path):
    source, wheel = _source(tmp_path), _wheel(tmp_path)
    e.install_editable(source, wheel, OLD_PROJECT, NEW)
    (source.package / 'updater.py').write_text('my local change!')
    with pytest.raises(InstallError, match='seit dem Update verändert'):
        e.install_editable(source, wheel, OLD_PROJECT, NEW)
    with pytest.raises(InstallError, match='seit dem Update verändert'):
        e.rollback_editable(source)
    assert (source.package / 'updater.py').read_text() == 'my local change!'


def test_editable_failed_selfcheck_restores_old_sources(monkeypatch, tmp_path):
    source, wheel = _source(tmp_path), _wheel(tmp_path)
    original = e._smoke
    def broken(root, version):
        if root == source.root:
            raise InstallError('fake broken module')
        return original(root, version)
    monkeypatch.setattr(e, '_smoke', broken)
    with pytest.raises(InstallError, match='fake broken'):
        e.install_editable(source, wheel, OLD_PROJECT, NEW)
    assert (source.package / 'updater.py').read_text() == '# old\n'
    assert (source.root / 'pyproject.toml').read_bytes() == OLD_PROJECT
    assert e._read_state(source) is None


def test_changed_dependencies_or_entrypoint_blocks_update(tmp_path):
    source = _source(tmp_path)
    for fragment in (b'"packaging>=24"', b'"mao_cli.other:main"'):
        new = OLD_PROJECT.replace(b'"packaging>=23"', fragment) if b'packaging' in fragment else OLD_PROJECT.replace(b'"mao_cli.cli:main"', fragment)
        with pytest.raises(InstallError, match='geändert'):
            e.prepare_pyproject(source, new, NEW)


def test_wheel_zip_traversal_and_symlink_blocked(tmp_path):
    wheel = tmp_path / 'bad.whl'
    with zipfile.ZipFile(wheel, 'w') as z:
        z.writestr('mao_cli/../malicious.py', 'attack')
    with pytest.raises(InstallError, match='Unsicherer'):
        e._read_wheel_package(wheel)


def test_public_pyproject_fetch_sha_reference(monkeypatch):
    captured = []
    def fake(path):
        captured.append(path)
        import base64
        return {'name': 'pyproject.toml', 'encoding': 'base64',
                'content': base64.b64encode(OLD_PROJECT).decode()}
    monkeypatch.setattr(u, '_github_json', fake)
    assert u._repository_pyproject(None, 'a' * 40) == OLD_PROJECT
    assert captured == ['/repos/hhgym/MaO/contents/pyproject.toml?ref=' + 'a' * 40]


def test_updater_prefers_editable_to_active_managed_install(monkeypatch, tmp_path):
    from mao_cli.update_install import _write_state
    source = _source(tmp_path)
    home = tmp_path / 'managed'
    _write_state(home, '0.6.0.dev0+g' + 'b'*12, None)
    monkeypatch.setattr(u, 'detect_editable', lambda: source)
    assert u._installed_version(home) == OLD
    assert 'Editable-Installation' in u.update_action('status','develop',None,home=home)
    assert 'MaO' in u.update_action('status','develop',None,home=home)


def test_update_action_installs_editable_not_managed(monkeypatch, tmp_path):
    source, wheel = _source(tmp_path), _wheel(tmp_path)
    build = u.DevelopBuild('hhgym/MaO', 'a'*40, 7, wheel.name, 'https://github.com/x', 'https://github.com/y')
    monkeypatch.setattr(u, 'detect_editable', lambda: source)
    monkeypatch.setattr(u, 'find_develop', lambda repo: build)
    monkeypatch.setattr(u, 'stage_develop', lambda build, dest: (wheel, NEW))
    monkeypatch.setattr(u, '_packaged_source_matches', lambda p: False)
    monkeypatch.setattr(u, '_repository_pyproject', lambda *a: OLD_PROJECT)
    monkeypatch.setattr(u, 'install_managed', lambda *a,**kw: pytest.fail('not managed'))
    assert 'direkt in' in u.update_action('install', 'develop', None, yes=True, home=tmp_path/'unused')
    assert (source.package/'updater.py').read_text() == '# new\n'
