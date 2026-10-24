"""Sicherheits-, Rollback-, Privat-Repo- und plattformübergreifende Update-Tests."""
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from packaging.version import Version

from mao_cli import cli, updater as u, update_install as ins


def test_private_release_asset_ids(monkeypatch):
    def release(name, asset_id):
        return {'name': name, 'id': asset_id, 'browser_download_url': 'https://github.com/example'}
    data = [{'tag_name': 'v0.5.0-rc.1', 'prerelease': True, 'draft': False, 'assets': [
        release('mao_cli-0.5.0rc1-py3-none-any.whl', 300), release('mao_cli-0.5.0rc1-py3-none-any.whl.sha256', 301)]}]
    monkeypatch.setattr(u, '_github_json', lambda path: data)
    v = u.find_release('hhgym/MaO', 'rc')
    assert v.version == Version('0.5.0rc1')
    assert v.wheel_url == 'https://api.github.com/repos/hhgym/MaO/releases/assets/300'
    assert u.find_release('hhgym/MaO', 'stable') is None


def test_token_priority_and_no_leak(monkeypatch):
    monkeypatch.setenv('GITHUB_TOKEN', 'secret-a')
    monkeypatch.setenv('MAO_GITHUB_TOKEN', 'secret-b')
    assert u._headers()['Authorization'] == 'Bearer secret-b'
    req = u.urllib.request.Request('https://api.github.com/repos/hhgym/MaO/releases/assets/4',
                                   headers=u._headers(binary=True))
    handler = u._RestrictedRedirect()
    forwarded = handler.redirect_request(req, None, 302, 'Found', {}, 'https://objects.githubusercontent.com/asset')
    assert 'authorization' not in {k.lower() for k in forwarded.headers}
    assert 'authorization' not in {k.lower() for k in forwarded.unredirected_hdrs}
    with pytest.raises(u.UpdateError, match='Redirectziel'):
        handler.redirect_request(req, None, 302, 'Found', {}, 'http://example.com/token')
    with pytest.raises(u.UpdateError, match='Redirectziel'):
        handler.redirect_request(req, None, 302, 'Found', {}, 'https://evil.example/asset')


def test_sha_mismatch_blocks_install(monkeypatch, tmp_path):
    release = u.Release(Version('0.6.0rc1'), 'v0.6.0-rc.1', 'https://api.github.com/1',
                        'https://api.github.com/2', 'mao_cli-0.5.0rc1-py3-none-any.whl')
    monkeypatch.setattr(u, 'find_release', lambda *a: release)
    monkeypatch.setattr(u, '_download', lambda url, limit=40_000_000: b'bad-wheel' if url.endswith('1') else b'0'*64)
    monkeypatch.setattr(u, 'install_managed', lambda *a, **kw: pytest.fail('must not install'))
    with pytest.raises(u.UpdateError, match='SHA-256'):
        u.update_action('install', 'rc', None, yes=True, home=tmp_path)
    assert not (tmp_path/'active.json').exists()


def test_atomic_activation_and_rollback(monkeypatch, tmp_path):
    wheel = tmp_path / 'release.whl'
    wheel.write_bytes(b'fake')
    def build(env, wheel, version):
        ins.python_in(env).parent.mkdir(parents=True)
        ins.python_in(env).write_bytes(b'fake')
        (env/'built').write_text(version)
    monkeypatch.setattr(ins, '_install_wheel', build)
    monkeypatch.setattr(ins, '_verify_environment', lambda env,version: None if (env/'built').read_text()==version else pytest.fail('invalid env'))
    home=tmp_path/'managed'
    launcher, previous = ins.install_managed(wheel, '0.4.0', home)
    assert launcher.exists() and previous is None
    first = ins.read_state(home)
    assert first['active']=='0.4.0' and first['previous'] is None
    launcher, previous = ins.install_managed(wheel, '0.5.0rc1', home)
    assert previous == '0.4.0'
    assert ins.read_state(home)=={'active':'0.5.0rc1','previous':'0.4.0'}
    assert ins.rollback_managed(home)=='0.4.0'
    assert ins.read_state(home)=={'active':'0.4.0','previous':'0.5.0rc1'}
    assert (home/'versions'/'0.5.0rc1'/'built').is_file()


def test_failed_install_does_not_change_active(monkeypatch,tmp_path):
    wheel=tmp_path/'new.whl';wheel.write_bytes(b'a')
    home=tmp_path/'managed'
    home.mkdir()
    ins._write_state(home,'0.4.0',None)
    def broken(env,wheel,version):
        (env/'partial').write_text('bad')
        raise ins.InstallError('failure')
    monkeypatch.setattr(ins, '_install_wheel',broken)
    with pytest.raises(ins.InstallError, match='failure'):
        ins.install_managed(wheel,'0.5.0rc1',home)
    assert ins.read_state(home)['active']=='0.4.0'
    assert not list((home/'versions').glob('.staging-*'))


def test_state_path_traversal_and_lock(tmp_path):
    home=tmp_path/'managed';home.mkdir()
    (home/'active.json').write_text('{"schema":1,"active":"../outside","previous":null}')
    with pytest.raises(ins.InstallError,match='Versionskennung'):
        ins.read_state(home)
    (home/'active.json').unlink()
    with ins._update_lock(home):
        with pytest.raises(ins.InstallError,match='Update läuft'):
            with ins._update_lock(home):
                pass
    assert not (home/'.update.lock').exists()


def test_rollback_requires_confirmation(tmp_path):
    with pytest.raises(u.UpdateError,match='--yes'):
        u.update_action('rollback','stable',None,home=tmp_path)
    assert 'noch keine' in u.update_action('status','stable',None,home=tmp_path)
    assert cli.parse_args(['update','rollback','--yes']).yes
    assert cli.parse_args(['update','status','--home',str(tmp_path)]).home == tmp_path


def test_develop_install_failure_keeps_managed_state(monkeypatch, tmp_path):
    from mao_cli import update_install as ins
    build = u.DevelopBuild("hhgym/MaO", "b"*40, 21, 90)
    monkeypatch.setattr(u, "find_develop", lambda repository: build)
    monkeypatch.setattr(u, "_installed_version", lambda home: "0.6.0.dev0")
    def fake_stage(build, directory):
        wheel = directory / "mao_cli-0.6.0.dev0+gbbbbbbbbbbbb-py3-none-any.whl"
        wheel.write_bytes(b"dummy")
        return wheel, "0.6.0.dev0+gbbbbbbbbbbbb"
    monkeypatch.setattr(u, "stage_develop", fake_stage)
    monkeypatch.setattr(u, "install_managed", lambda *args: (_ for _ in ()).throw(ins.InstallError("pip kaputt")))
    with pytest.raises(u.UpdateError, match="pip kaputt"):
        u.update_action("install", "develop", None, yes=True, home=tmp_path)
    assert not (tmp_path / "active.json").exists()


def test_updated_help():
    assert cli.main(['update','status','--home','/tmp/mao-fake-never-used'])==0


def test_managed_install_accepts_develop_local_versions(monkeypatch, tmp_path):
    wheel=tmp_path/'develop.whl'; wheel.write_bytes(b'a')
    def mock_install(env, wheel, version):
        ins.python_in(env).parent.mkdir(parents=True, exist_ok=True)
        ins.python_in(env).write_bytes(b'fake')
    monkeypatch.setattr(ins, '_install_wheel', mock_install)
    home=tmp_path/'maohome'
    version='0.6.0.dev0+gabcdef123456'
    launcher, previous = ins.install_managed(wheel, version, home)
    assert launcher.is_file() and previous is None
    assert ins.read_state(home)['active'] == version
    assert "0123456789_.+-" in ins.LAUNCHER
    with pytest.raises(ins.InstallError, match='Ungültige'):
        ins.install_managed(wheel, '../bad', home)
