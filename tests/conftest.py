"""Gemeinsame Testeinstellungen: der Versionshinweis darf im Test nie das Netz benutzen."""
import pytest


@pytest.fixture(autouse=True)
def _no_update_notice(monkeypatch):
    monkeypatch.setenv("MAO_NO_UPDATE_CHECK", "1")
