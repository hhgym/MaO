"""Öffentliche GitHub-Releases für Develop: Bindung an SHA und Prüfsumme."""
import hashlib
import pytest
from mao_cli import updater as u

SHA = 'ab'*20
VERSION = '0.6.0.dev0+g' + SHA[:12]
NAME = f'mao_cli-{VERSION}-py3-none-any.whl'
RELEASE_URL = 'https://github.com/hhgym/MaO/releases/download/develop-build/'
BUILD = u.DevelopBuild('hhgym/MaO', SHA, 42, NAME, RELEASE_URL+NAME, RELEASE_URL+NAME+'.sha256')


def test_develop_stage_downloads_public_wheel(monkeypatch, tmp_path):
    content = b'wheel-contents'
    def fake(url, limit=40_000_000):
        return (hashlib.sha256(content).hexdigest()+'  '+NAME).encode() if url.endswith('.sha256') else content
    monkeypatch.setattr(u, '_download', fake)
    path, version = u.stage_develop(BUILD, tmp_path)
    assert path.read_bytes() == content
    assert version == VERSION
    with pytest.raises(u.UpdateError, match='gespeichert'):
        u.stage_develop(BUILD, tmp_path)


def test_develop_stage_rejects_checksum_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr(u, '_download', lambda url,limit=40_000_000: b'0'*64 if url.endswith('.sha256') else b'wheel-contents')
    with pytest.raises(u.UpdateError, match='SHA-256'):
        u.stage_develop(BUILD, tmp_path)
    assert not list(tmp_path.iterdir())


def test_develop_stage_rejects_wrong_commit(monkeypatch, tmp_path):
    wrong = u.DevelopBuild(BUILD.repository, 'cd'*20, BUILD.run_id, BUILD.wheel_name, BUILD.wheel_url, BUILD.checksum_url)
    monkeypatch.setattr(u, '_download', lambda *args,**kw: pytest.fail('No download on wrong commit'))
    with pytest.raises(u.UpdateError, match='Commit'):
        u.stage_develop(wrong, tmp_path)


def test_anonymous_headers_ignore_expired_environment_credentials(monkeypatch):
    for key in ('MAO_GITHUB_TOKEN','GH_TOKEN','GITHUB_TOKEN'):
        monkeypatch.setenv(key, 'expired-token')
    assert 'Authorization' not in u._headers()
    assert 'Authorization' not in u._headers(binary=True)


def test_public_asset_urls_reject_external_and_modified_link():
    assert u._public_asset_url(BUILD.wheel_url,BUILD.repository, NAME)==BUILD.wheel_url
    for url in ('https://evil.example/download',
                'https://github.com/unrelated/MaO/releases/download/develop-build/'+NAME,
                'https://github.com/hhgym/MaO/releases/download/another-tag/'+NAME,
                RELEASE_URL + 'other.whl', RELEASE_URL + NAME + '?query=yes'):
        with pytest.raises(u.UpdateError, match='Downloadadresse'):
            u._public_asset_url(url, BUILD.repository, NAME)


def test_restricted_redirect_drops_auth_for_download_cdn():
    req = u.urllib.request.Request('https://github.com/hhgym/MaO/releases/download/develop-build/file',
                                    headers={'Authorization':'Bearer fake'})
    redir = u._RestrictedRedirect().redirect_request(req,None,302,'Found',{},
                                                       'https://release-assets.githubusercontent.com/path?sig=placeholder')
    assert 'authorization' not in {k.lower() for k in redir.headers}
    with pytest.raises(u.UpdateError):
        u._RestrictedRedirect().redirect_request(req,None,302,'Found',{},
                                                  'https://danger.example/download')
