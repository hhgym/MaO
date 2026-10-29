"""Schulabgleich: GeoJSON-Auswertung, Vergleich und Datenbankschicht mit Testcursor."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from mao_cli import schools as s

TABLES = {"schools_table": "schulen"}


def feature(number, name, **extra):
    props = {"bsn": number, "schulname": name, "schulart": "Grundschule", "bezirk": "Mitte", "plz": "10115", **extra}
    return {"type": "Feature", "id": f"schulen.{number}", "properties": props, "geometry": {"type": "Point"}}


def source(count=120, **kw):
    features = [feature(f"01G{i:02d}"[:5] if i < 100 else f"02G{i - 100:02d}", f"Schule {i}") for i in range(count)]
    return json.dumps({"type": "FeatureCollection", "features": features, **kw}).encode()


def test_parse_reads_properties_and_trims():
    raw = json.dumps({"features": [feature("01g01", " Erste Schule ", telefon="", internet="https://x.de")]})
    schools, notes = s.parse_schools(raw)
    assert schools[0]["schulnummer"] == "01G01" and schools[0]["name"] == "Erste Schule"
    assert schools[0]["telefon"] is None and schools[0]["internet"] == "https://x.de" and not notes


def test_parse_rejects_duplicates_and_incomplete_sources():
    with pytest.raises(s.SchoolError, match="mehrfach"):
        s.parse_schools(json.dumps({"features": [feature("01G01", "A"), feature("01G01", "B")]}))
    with pytest.raises(s.SchoolError, match="unvollständig"):
        s.parse_schools(json.dumps({"numberMatched": 5, "features": [feature("01G01", "A")]}))
    with pytest.raises(s.SchoolError, match="JSON"):
        s.parse_schools("<html>")
    with pytest.raises(s.SchoolError, match="features"):
        s.parse_schools("{}")


def test_parse_skips_entries_and_shortens_long_values():
    raw = json.dumps({"features": [feature("", "ohne Nummer"), feature("01G02", "Lang", plz="1" * 20), {"x": 1}]})
    schools, notes = s.parse_schools(raw)
    assert [x["schulnummer"] for x in schools] == ["01G02"] and len(schools[0]["plz"]) == 10
    assert len(notes) == 3


def test_plan_detects_new_changed_removed_and_reactivated():
    new = [{"schulnummer": "A", **{c: "x" for c in s.FIELDS}}, {"schulnummer": "B", **{c: "neu" for c in s.FIELDS}},
           {"schulnummer": "D", **{c: "x" for c in s.FIELDS}}, {"schulnummer": "E", **{c: "x" for c in s.FIELDS}}]
    old = {"A": {**{c: "x" for c in s.FIELDS}, "aktiv": 1}, "B": {**{c: "x" for c in s.FIELDS}, "aktiv": 1},
           "C": {**{c: "x" for c in s.FIELDS}, "aktiv": 1}, "D": {**{c: "x" for c in s.FIELDS}, "aktiv": 0}}
    plan = s.plan_sync(old, new)
    assert plan.unchanged == 1 and [r["schulnummer"] for r in plan.inserts] == ["E"]
    assert sorted(r["schulnummer"] for r, _ in plan.updates) == ["B", "D"] and plan.deactivate == ["C"]


class FakeCursor:
    def __init__(self, rows, count):
        self.rows, self.count, self.executed, self.last = rows, count, [], ""
    def execute(self, sql, params=None):
        self.last = sql
        self.executed.append((sql, params))
    def executemany(self, sql, params):
        self.executed.append((sql, list(params)))
    def fetchall(self):
        if "LOCK" in self.last:
            return [(1,)]
        if "COUNT(*)" in self.last:
            return [(self.count,)]
        return self.rows
    def close(self):
        pass


class FakeConn:
    def __init__(self, rows=(), count=0):
        self.cur, self.in_transaction, self.commits, self.rollbacks = FakeCursor(list(rows), count), False, 0, 0
    def cursor(self):
        return self.cur
    def start_transaction(self):
        self.in_transaction = True
    def commit(self):
        self.commits += 1
    def rollback(self):
        self.rollbacks += 1


def existing_rows(n):
    return [("01G%02d" % i if i < 100 else "02G%02d" % (i - 100), *(f"Schule {i}" if c == "name" else None for c in s.FIELDS), 1)
            for i in range(n)]


def test_dry_run_reports_without_writing():
    cnx = FakeConn()
    report: dict = {}
    stats = s.sync_schools(SimpleNamespace(conn=cnx), TABLES, dry_run=True, fetcher=lambda url: source(120), report=report)
    assert stats["neu"] == 120 and cnx.commits == 0 and report["status"] == "ready"
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE")) for sql, _ in cnx.cur.executed)


def test_apply_inserts_in_transaction_and_checks_count():
    cnx = FakeConn(count=120)
    s.sync_schools(SimpleNamespace(conn=cnx), TABLES, dry_run=False, fetcher=lambda url: source(120))
    assert cnx.commits == 1 and any(sql.startswith("INSERT") for sql, _ in cnx.cur.executed)
    bad = FakeConn(count=7)
    with pytest.raises(s.SchoolError, match="Zählabgleich"):
        s.sync_schools(SimpleNamespace(conn=bad), TABLES, dry_run=False, fetcher=lambda url: source(120))
    assert bad.commits == 0 and bad.rollbacks == 1


def test_deactivates_instead_of_deleting():
    cnx = FakeConn(existing_rows(130), count=130)
    stats = s.sync_schools(SimpleNamespace(conn=cnx), TABLES, dry_run=False, fetcher=lambda url: source(120))
    assert stats["weggefallen"] == 10
    sqls = [sql for sql, _ in cnx.cur.executed]
    assert any("`aktiv` = 0" in q for q in sqls) and not any(q.upper().startswith("DELETE") for q in sqls)
    assert not any("zweite_stufe" in q for q in sqls)


def test_implausibly_small_source_is_refused():
    cnx = FakeConn(existing_rows(130), count=130)
    with pytest.raises(s.SchoolError, match="unvollständig"):
        s.sync_schools(SimpleNamespace(conn=cnx), TABLES, dry_run=False, fetcher=lambda url: source(30))
    assert cnx.commits == 0


def test_demo_table_and_http_are_refused():
    with pytest.raises(s.SchoolError, match="schools_table"):
        s.sync_schools(SimpleNamespace(conn=FakeConn()), {"schools_table": "DEMO_SCHOOLS"}, fetcher=lambda url: b"")
    with pytest.raises(s.SchoolError, match="https"):
        s.fetch_bytes("http://example.org/x.json")
