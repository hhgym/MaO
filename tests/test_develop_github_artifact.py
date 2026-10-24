"""GitHub-Develop-Wheels werden nach SHA und Integrität verifiziert."""
import io
import zipfile
import hashlib
import pytest
from mao_cli import updater as u

SHA = "ab" * 20
VERSION = "0.6.0.dev0+g" + SHA[:12]
NAME = f"mao_cli-{VERSION}-py3-none-any.whl"
BUILD = u.DevelopBuild("hhgym/MaO", SHA, 42, 1234)

def artifact_zip(content=b"wheel-contents", checksum=None, extra=None, filename=NAME):
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w") as z:
        z.writestr(filename, content)
        z.writestr(filename + ".sha256", (checksum or hashlib.sha256(content).hexdigest()) + "  " + filename)
        if extra: z.writestr(extra, b"unsafe")
    return mem.getvalue()

def test_develop_stage_checksum_and_version(monkeypatch, tmp_path):
    monkeypatch.setattr(u, "_download", lambda url: artifact_zip())
    path, version = u.stage_develop(BUILD, tmp_path)
    assert path.read_bytes() == b"wheel-contents"
    assert version == VERSION
    with pytest.raises(u.UpdateError, match="gespeichert"):
        u.stage_develop(BUILD, tmp_path)

def test_develop_stage_rejects_checksum_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr(u, "_download", lambda url: artifact_zip(checksum="0"*64))
    with pytest.raises(u.UpdateError, match="SHA-256"):
        u.stage_develop(BUILD, tmp_path)
    assert list(tmp_path.iterdir()) == []

def test_develop_stage_rejects_wrong_sha_and_zip_traversal(monkeypatch, tmp_path):
    wrong = NAME.replace(SHA[:12], "c"*12)
    monkeypatch.setattr(u, "_download", lambda url: artifact_zip(filename=wrong))
    with pytest.raises(u.UpdateError, match="Commit"):
        u.stage_develop(BUILD, tmp_path)
    monkeypatch.setattr(u, "_download", lambda url: artifact_zip(extra="../escape.txt"))
    with pytest.raises(u.UpdateError, match="unerwartete"):
        u.stage_develop(BUILD, tmp_path)
    assert not list(tmp_path.iterdir())

def test_develop_stage_requires_valid_archive(monkeypatch,tmp_path):
    monkeypatch.setattr(u,"_download",lambda url: b"not a zip")
    with pytest.raises(u.UpdateError,match="Ungültiges"):
        u.stage_develop(BUILD,tmp_path)

def test_restricted_redirect_drops_auth_for_azure_signed_url():
    req = u.urllib.request.Request("https://api.github.com/repos/x/actions/artifacts/1/zip",
                                    headers={"Authorization": "Bearer fake"})
    redir = u._RestrictedRedirect().redirect_request(req,None,302,"Found",{},
                                                       "https://results.blob.core.windows.net/download?sig=placeholder")
    assert "authorization" not in {k.lower() for k in redir.headers}
    with pytest.raises(u.UpdateError):
        u._RestrictedRedirect().redirect_request(req,None,302,"Found",{},
                                                  "https://danger.example/download")

