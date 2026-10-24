"""Offline-Prüfungen des Gravity-Forms-Erstimports; keine realen Tabellen oder Schlüssel."""
from pathlib import Path
from types import SimpleNamespace
import pytest

from mao_cli import gf_import as g
from mao_cli import cli


@pytest.fixture()
def opts():
    return g.ImportSettings(16, 200, 60, ("5-*", "7-3"),
                            {"klasse": "1", "kurs": "5", "lehrkraft": "3", "teilnehmende": "7"}, {})


def test_entry_checkbox_and_mandatory(opts):
    students = [
        {"matrikelnummer": 12, "vorname": "Ada", "nachname": "X", "geschlecht": "w", "klasse": "11"},
        {"matrikelnummer": 34, "vorname": "Ben", "nachname": "Y", "geschlecht": "m", "klasse": "5-1"},
    ]
    entries = [{"1": "11", "3": "Mu", "5": "LK A", "7.1": "12: Ada", "status": "active"},
               {"1": "5-1", "3": "Mu", "7.1": "34", "status": "spam"}]
    mandatory = [dict(students[1], lehrkraft_token="Mu")]
    items, stat = g.build_participants(entries, students, mandatory, {"mu": 7}, {"lk a": 83}, opts)
    assert [r["matrikelnummer"] for r in items] == [12, 34]
    assert [r["tnr"] for r in items] == [1, 2]
    assert items[0]["kurs_id"] == 83 and items[0]["lehrkraft_id"] == 7
    assert items[1]["kurs_id"] is None and items[1]["stufe"] == 5
    assert stat == {"gf_entries": 1, "inactive_entries": 1, "mandatory_added": 1}


def test_duplicate_matrikel_fails(opts):
    students = [{"matrikelnummer": 12, "vorname": "Ada", "nachname": "X", "geschlecht": "w", "klasse": "11"}]
    entries = [{"1": "11", "7.1": "12"}, {"1": "11", "7.1": "12"}]
    with pytest.raises(g.ImportErrorGF, match="mehrfach"):
        g.build_participants(entries, students, [], {}, {}, opts)


def test_unknown_student_fails(opts):
    with pytest.raises(g.ImportErrorGF, match="nicht im Schülerverzeichnis"):
        g.build_participants([{"1": "11", "7.1": "42"}], [], [], {}, {}, opts)


def test_ambiguous_mapping_and_unresolved_text(opts):
    with pytest.raises(g.ImportErrorGF, match="Mehrdeutige"):
        g._mapping([{"id": 1, "token": "LK A"}, {"id": 2, "token": "lk a"}], "Kurse")
    students = [{"matrikelnummer": 12, "vorname": "Ada", "nachname": "X", "geschlecht": "w", "klasse": "11"}]
    with pytest.raises(g.ImportErrorGF, match="Keine eindeutige"):
        g.build_participants([{"1": "11", "3": "unknown", "7.1": "12"}], students, [], {}, {}, opts)


def test_credential_validation(tmp_path):
    f = tmp_path / "gf.config"
    f.write_text("[oauth1]\napi_base=http://example.com/gf/v2\nconsumer_key=x\nconsumer_secret=y\n")
    with pytest.raises(Exception, match="HTTPS"):
        g.load_gf_credentials(f)
    f.write_text(f.read_text().replace("http://", "https://"))
    c = g.load_gf_credentials(f)
    assert c["api_base"] == "https://example.com/gf/v2"


def test_private_import_config(tmp_path):
    from mao_cli.workspaces import init_config
    init_config(tmp_path, cli.DEFAULTS)
    with pytest.raises(Exception, match="fehlt"):
        g.load_import_settings(tmp_path / "gf-import.yaml")
    example = tmp_path / "gf-import.yaml.example"
    example.rename(tmp_path / "gf-import.yaml")
    with pytest.raises(Exception, match="angepasst"):
        g.load_import_settings(tmp_path / "gf-import.yaml")


def test_cli_mode_and_no_import_in_version():
    assert cli.parse_args(["import-gf"]).apply is False
    assert cli.parse_args(["import-gf", "--apply"]).apply is True
    with pytest.raises(SystemExit):
        cli.parse_args(["import-gf", "--apply", "--dry-run"])


class FakeCursor:
    def __init__(self, present=0):
        self.present = present
        self.closed = False
        self.last = ""
        self.executed = []
        self.inserted = 0
    def execute(self, sql, params=None):
        self.last = sql
        self.executed.append((sql, params))
    def fetchall(self):
        if "SHOW COLUMNS" in self.last:
            return [(x,) for x in g.WRITE_COLUMNS]
        return []
    def fetchone(self):
        if "GET_LOCK" in self.last or "RELEASE_LOCK" in self.last:
            return (1,)
        return (self.present + self.inserted,)
    def executemany(self, sql, params):
        self.inserted = len(params)
        self.executed.append((sql, params))
    def close(self):
        self.closed = True


class FakeConn:
    def __init__(self, present=0):
        self.cursor_obj = FakeCursor(present)
        self.in_transaction = False
        self.commits = 0
        self.rollbacks = 0
    def cursor(self):
        return self.cursor_obj
    def start_transaction(self):
        self.in_transaction = True
    def commit(self):
        self.commits += 1
        self.in_transaction = False
    def rollback(self):
        self.rollbacks += 1
        self.in_transaction = False


def test_persist_dry_and_apply_transaction():
    participants = [{x: 1 for x in g.WRITE_COLUMNS}]
    cnx = FakeConn()
    assert g.persist_import(SimpleNamespace(conn=cnx), "SAMPLE_TABLE", participants, dry_run=True) == 0
    assert cnx.commits == 0 and not any("INSERT" in sql for sql, _ in cnx.cursor_obj.executed)
    assert g.persist_import(SimpleNamespace(conn=cnx), "SAMPLE_TABLE", participants, dry_run=False) == 1
    assert cnx.commits == 1
    assert any("GET_LOCK" in sql for sql, _ in cnx.cursor_obj.executed)
    assert any("RELEASE_LOCK" in sql for sql, _ in cnx.cursor_obj.executed)


def test_persist_never_replaces_existing_rows():
    cnx = FakeConn(present=5)
    with pytest.raises(g.ImportErrorGF, match="bereits 5"):
        g.persist_import(SimpleNamespace(conn=cnx), "SAMPLE_TABLE", [{x: 1 for x in g.WRITE_COLUMNS}], dry_run=False)
    assert cnx.commits == 0
    assert not any("INSERT" in sql for sql, _ in cnx.cursor_obj.executed)
