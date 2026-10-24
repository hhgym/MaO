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


def test_develop_find_build_requires_successful_exact_head_and_public_release(monkeypatch):
    sha = 'a'*40
    wheel = f'mao_cli-0.6.0.dev0+g{sha[:12]}-py3-none-any.whl'
    url = 'https://github.com/hhgym/MaO/releases/download/develop-build/'
    calls=[]
    def fakejson(path):
        calls.append(path)
        if path.endswith('/branches/develop'):
            return {'commit':{'sha':sha}}
        if '/ci.yml/runs?' in path:
            return {'workflow_runs':[{'id':43,'head_sha':sha,'event':'push','conclusion':'success'},
                                     {'id':44,'head_sha':'b'*40,'event':'push','conclusion':'success'}]}
        return {'assets':[{'name':wheel,'browser_download_url':url+wheel},
                          {'name':wheel+'.sha256','browser_download_url':url+wheel+'.sha256'}]}
    monkeypatch.setattr(u, '_github_json', fakejson)
    result = u.find_develop('hhgym/MaO')
    assert result == u.DevelopBuild('hhgym/MaO',sha,43,wheel,url+wheel,url+wheel+'.sha256')
    assert len(calls)==3
    assert calls[-1] == '/repos/hhgym/MaO/releases/tags/develop-build'


def test_develop_public_release_missing_checksum_fails_closed(monkeypatch):
    sha='a'*40
    name=f'mao_cli-0.6.0.dev0+g{sha[:12]}-py3-none-any.whl'
    def source(path):
        if path.endswith('/branches/develop'):return {'commit': {'sha': sha}}
        if '/ci.yml/runs?' in path:return {'workflow_runs':[{'id':3,'head_sha':sha,'event':'push','conclusion':'success'}]}
        return {'assets':[{'name':name,'browser_download_url':'https://github.com/hhgym/MaO/releases/download/develop-build/'+name}]}
    monkeypatch.setattr(u, '_github_json',source)
    with pytest.raises(u.UpdateError,match='Prüfdatei'):
        u.find_develop(None)


def test_develop_check_without_local_git(monkeypatch,tmp_path):
    build = u.DevelopBuild('hhgym/MaO','a'*40,43,'test.whl','https://github.com/x.whl','https://github.com/x.sha256')
    monkeypatch.setattr(u,'find_develop',lambda repository:build)
    monkeypatch.setattr(u,'_installed_version',lambda home:'0.6.0.dev0')
    monkeypatch.setattr(u,'git_checkout_root',lambda:pytest.fail('Git darf nicht aufgerufen werden'),raising=False)
    assert 'Neuer getesteter' in u.update_action('check','develop',None,home=tmp_path)
    assert '43' in u.update_action('check','develop',None,home=tmp_path)
    with pytest.raises(u.UpdateError,match='--yes'):
        u.update_action('install','develop',None,home=tmp_path)


def test_develop_already_current(monkeypatch,tmp_path):
    build = u.DevelopBuild('hhgym/MaO','a'*40,43,'test.whl','https://github.com/x.whl','https://github.com/x.sha256')
    monkeypatch.setattr(u,'find_develop',lambda repository:build)
    monkeypatch.setattr(u,'_installed_version',lambda home:'0.6.0.dev0+gaaaaaaaaaaaa')
    assert 'aktuell' in u.update_action('check','develop',None,home=tmp_path)

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

def test_develop_no_successful_ci(monkeypatch):
    monkeypatch.setattr(u, "_github_json", lambda path: {"commit": {"sha": "a"*40}} if path.endswith("/branches/develop")
                        else {"workflow_runs": []})
    with pytest.raises(u.UpdateError, match="noch nicht erfolgreich"):
        u.find_develop("hhgym/MaO")


def test_check_release_tag_matches_version():
    from scripts.check_release import main
    assert main(['v0.6.0.dev0']) == 0
    assert main(['v0.6.0']) == 2
