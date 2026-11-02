"""HTTPS-Einstellungen für Abrufe öffentlicher Daten (Schulverzeichnis, GitHub).

Windows-Rechner und Schulnetze scheitern oft mit "unable to get local issuer certificate":
Server liefern ihre Zwischenzertifikate nicht mit, oder ein Filter im Netz tauscht Zertifikate
aus, dessen Wurzel nur im Windows-Zertifikatspeicher steht. Browser lösen das über das
Betriebssystem, Pythons eigene Prüfung nicht. ``truststore`` nutzt die Prüfung des
Betriebssystems; ohne das Paket gilt der Python-Standard. Die Zertifikatsprüfung bleibt
immer eingeschaltet.
"""
from __future__ import annotations

import importlib
import os
import ssl
import urllib.error

CA_BUNDLE_ENV = "MAO_CA_BUNDLE"


def ssl_context() -> ssl.SSLContext:
    bundle = os.environ.get(CA_BUNDLE_ENV)
    if bundle:
        return ssl.create_default_context(cafile=bundle)
    try:
        truststore = importlib.import_module("truststore")
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        assert isinstance(context, ssl.SSLContext)
        return context
    except (ImportError, AttributeError, AssertionError):
        return ssl.create_default_context()


def is_certificate_error(exc: BaseException) -> bool:
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    return isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason)


CERTIFICATE_HINT = ("Zertifikatsprüfung fehlgeschlagen. Ein Update von MaO (uv tool install --force --reinstall …) "
                    "nutzt den Windows-Zertifikatspeicher. Alternativ in dieser Sitzung "
                    f"{CA_BUNDLE_ENV}=<Datei mit Zertifikaten (.pem)> setzen, oder die Daten im Browser speichern "
                    "und mit --file einlesen.")
