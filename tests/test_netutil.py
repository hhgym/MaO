"""HTTPS-Kontext: Betriebssystem-Zertifikate, eigenes Bündel, klare Fehlermeldung."""
from __future__ import annotations

import ssl
import sys
import types
import urllib.error

import pytest

from mao_cli import netutil, schools as s


def test_uses_truststore_when_available(monkeypatch):
    class FakeContext(ssl.SSLContext):
        pass
    fake = types.ModuleType("truststore")
    fake.SSLContext = FakeContext  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "truststore", fake)
    monkeypatch.delenv(netutil.CA_BUNDLE_ENV, raising=False)
    assert isinstance(netutil.ssl_context(), FakeContext)


def test_falls_back_to_python_default_without_truststore(monkeypatch):
    monkeypatch.setitem(sys.modules, "truststore", None)  # Import schlägt fehl
    monkeypatch.delenv(netutil.CA_BUNDLE_ENV, raising=False)
    context = netutil.ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_custom_bundle_is_used_and_must_exist(monkeypatch, tmp_path):
    monkeypatch.setenv(netutil.CA_BUNDLE_ENV, str(tmp_path / "fehlt.pem"))
    with pytest.raises(OSError):
        netutil.ssl_context()


def test_certificate_error_detection():
    err = urllib.error.URLError(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate"))
    assert netutil.is_certificate_error(err)
    assert not netutil.is_certificate_error(urllib.error.URLError("timed out"))


def test_schools_fetch_explains_certificate_problem(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"))
    monkeypatch.setattr(s, "urlopen", boom)
    with pytest.raises(s.SchoolError, match="--file"):
        s.fetch_bytes("https://example.org/x.json")


def test_read_file(tmp_path):
    path = tmp_path / "s.json"
    path.write_bytes(b"{}")
    assert s.read_file(str(path)) == b"{}"
    with pytest.raises(s.SchoolError, match="nicht lesbar"):
        s.read_file(str(tmp_path / "nein.json"))
