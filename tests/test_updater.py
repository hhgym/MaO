"""Offline-Tests für GitHub-Releases, Kanäle, Sicherheitsprüfungen und CLI."""
from __future__ import annotations
import hashlib
from pathlib import Path

import pytest
from packaging.version import Version
from mao_cli import cli
from mao_cli import updater as u


def entry(version, *, prerelease=False, draft=False, checksum=True):
    wheel = f'mao_cli-{version}-py3-none-any.whl'
    assets = [{'name': wheel, 'browser_download_url': 'https://github.com/test/releases/download/v'+version+'/'+wheel}]
    if checksum:
        assets.append({'name': wheel+'.sha256', 'browser_download_url': assets[0]['browser_download_url']+'.sha256'})
    return {'tag_name':'v'+version, 'prerelease':prerelease, 'draft':draft, 'assets':assets}


def test_release_channels_choose_latest_eligible(monkeypatch):
    monkeypatch.setattr(u, '_github_json', lambda path: [
        entry('2.0.0'), entry('2.1.0rc1', prerelease=True), entry('2.2.0.dev1', prerelease=True),
        entry('2.3.0', draft=True), entry('2.4.0', checksum=False),
    ])
    assert str(u.find_release('hhgym/mao-cli', 'stable').version) == '2.0.0'
    assert str(u.find_release('hhgym/mao-cli', 'rc').version) == '2.1.0rc1'


def test_requires_repository_and_check_only(monkeypatch):
    monkeypatch.delenv('MAO_GITHUB_REPO', raising=False)
    assert u._repo(None) == 'hhgym/MaO'
    monkeypatch.setattr(u, 'find_release', lambda *args: u.Release(Version('2.0.0'), 'v2.0.0', 'a', 'b', 'test.whl'))
    assert 'Update verfügbar' in u.update_action('check','stable','hhgym/mao-cli')
    with pytest.raises(u.UpdateError, match='--yes'):
        u.update_action('install','stable','hhgym/mao-cli')


def test_bad_checksum_prevents_publication(monkeypatch, tmp_path):
    release = u.Release(Version('2.0.0'),'v2.0.0','https://github.com/a.whl','https://github.com/a.sha256','mao_cli-2.0.0-py3-none-any.whl')
    monkeypatch.setattr(u,'_download',lambda url,limit=40000000: b'broken' if url.endswith('.whl') else b'0'*64)
    with pytest.raises(u.UpdateError, match='SHA-256'):
        u.stage_release(release,tmp_path)
    assert not list(tmp_path.iterdir())


def test_valid_checksum_and_no_overwrite(monkeypatch,tmp_path):
    contents = b'example-wheel-bytes'
    filename = 'mao_cli-2.0.0-py3-none-any.whl'
    release = u.Release(Version('2.0.0'),'v2.0.0','https://github.com/a.whl','https://github.com/a.sha256',filename)
    monkeypatch.setattr(u,'_download',lambda url,limit=40000000: contents if url.endswith('.whl') else (hashlib.sha256(contents).hexdigest()+'  '+filename).encode())
    path = u.stage_release(release,tmp_path)
    assert path.read_bytes()==contents
    with pytest.raises(u.UpdateError, match="nicht gespeichert"):
        u.stage_release(release,tmp_path)


def test_develop_checks_git_dirty_and_branch(monkeypatch,tmp_path):
    cmds=[]
    def fakegit(*args,cwd):
        cmds.append(args)
        if args[0]=='branch': return 'develop'
        if args[0]=='status': return ' M README.md'
        return 'abc'
    monkeypatch.setattr(u,'_git',fakegit)
    with pytest.raises(u.UpdateError, match='nicht sauber'):
        u.develop_status(tmp_path)
    assert ('status','--porcelain') in cmds


def test_cli_update_check_bypasses_pipeline(monkeypatch):
    monkeypatch.setattr(cli,'update_action',lambda *args,**kwargs: 'bereits aktuell')
    assert cli.main(['update','check','--channel','stable','--repository','hhgym/mao-cli'])==0
    assert cli.parse_args(['update','install','--channel','rc','--yes']).yes
    assert cli.main(['update','download','--output','/not/existing','--channel','develop'])==0


def test_install_without_yes_will_not_call_pip(monkeypatch):
    monkeypatch.setattr(u, 'find_release', lambda *args: u.Release(Version('2.0.0'),'v2.0.0','a','b','a.whl'))
    monkeypatch.setattr(u.subprocess,'run',lambda *args,**kwargs: pytest.fail('pip must not run'))
    with pytest.raises(u.UpdateError, match='--yes'):
        u.update_action('install','rc','hhgym/mao-cli')

def test_develop_needs_successful_ci(monkeypatch,tmp_path):
    monkeypatch.setattr(u,'develop_status',lambda root: ('a'*40, 'b'*40))
    monkeypatch.setattr(u,'_repository_from_origin',lambda root: 'school/mao-cli')
    monkeypatch.setattr(u,'_ci_success',lambda repo,sha: False)
    with pytest.raises(u.UpdateError, match='CI-Lauf'):
        u.update_action('install','develop','school/mao-cli',yes=True,root=tmp_path)


def test_check_release_tag_matches_version():
    from scripts.check_release import main
    assert main(['v0.5.0.dev0']) == 0
    assert main(['v0.5.0']) == 2
