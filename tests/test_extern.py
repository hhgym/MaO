"""Externe Teilnehmende: Formularauswertung, Planung und Datenbankschicht mit Testcursor."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from mao_cli import extern as e
from mao_cli.config import ConfigurationError

FIELDS = {"vorname": "3.3", "nachname": "3.6", "klassenstufe": "5", "schule": "6"}
TABLES = {"schools_table": "sch", "extern_personen_table": "pers", "extern_teilnahmen_table": "teiln",
          "jahrgaenge_table": "jg", "schuljahre_table": "sj"}


def entry(eid, first="Anna", last="Beispiel", stufe="4", school="01G01", **extra):
    return {"id": str(eid), "status": "active", "3.3": first, "3.6": last, "5": stufe, "6": school, **extra}


def test_parse_cleans_names_and_reports_problems():
    rows, issues, inactive = e.parse_entries(
        [entry(1, "  Anna  Maria ", "Beispiel"), entry(2, "", "X"), entry(3, stufe="7"), entry(4, school=""),
         entry(5, stufe="x"), entry(6, status="spam"), {"status": "active"}, entry(7, school="01g02")], FIELDS)
    assert [r["gf_eintrag_id"] for r in rows] == [1, 7]
    assert rows[0]["vorname"] == "Anna Maria" and rows[1]["schulnummer"] == "01G02"
    assert len(issues) == 5 and inactive == 1


SCHOOLS = {"01G01": ("Erste Schule", True), "01G02": ("Zweite Schule", True), "01G03": ("Alte Schule", False)}


def rows(*items):
    return [{"gf_eintrag_id": i, "vorname": v, "nachname": n, "klassenstufe": 4, "schulnummer": s} for i, v, n, s in items]


def test_plan_new_already_imported_and_unknown_school():
    plan = e.plan_import(rows((1, "A", "B", "01G01"), (2, "C", "D", "99X99"), (3, "E", "F", "01G03")), SCHOOLS,
                         {10: ("E", "F")}, [{"jahrgang_id": 1, "person_id": 10, "schulnummer": "01G03",
                                              "klassenstufe": 4, "gf_eintrag_id": 3}], 1)
    assert [r["gf_eintrag_id"] for r in plan.new] == [1] and plan.already == 1
    assert len(plan.skipped) == 1 and "99X99" in plan.skipped[0]


def test_plan_skips_double_submission_but_keeps_same_name_other_school():
    plan = e.plan_import(rows((1, "Anna", "Beispiel", "01G01"), (2, "anna", "BEISPIEL", "01G01"),
                              (3, "Anna", "Beispiel", "01G02")), SCHOOLS, {}, [], 1)
    assert [r["gf_eintrag_id"] for r in plan.new] == [1, 3]
    assert "Doppelanmeldung" in plan.skipped[0] and plan.warnings and "andere Schule" in plan.warnings[0]


def test_plan_hints_returning_child_without_merging():
    old = [{"jahrgang_id": 1, "person_id": 10, "schulnummer": "01G01", "klassenstufe": 3, "gf_eintrag_id": 5}]
    plan = e.plan_import(rows((9, "Anna", "Beispiel", "01G01")), SCHOOLS, {10: ("Anna", "Beispiel")}, old, 2)
    assert len(plan.new) == 1 and plan.hints[0]["moegliche_person_ids"] == [10]
    assert plan.hints[0]["fruehere_teilnahmen"][0]["klassenstufe"] == 3


def test_plan_warns_for_school_no_longer_listed():
    plan = e.plan_import(rows((1, "A", "B", "01G03")), SCHOOLS, {}, [], 1)
    assert len(plan.new) == 1 and "nicht mehr" in plan.warnings[0]


class Cur:
    def __init__(self, persons=(), parts=(), schools=None, counts=None):
        self.persons, self.parts = list(persons), list(parts)
        self.schools = schools if schools is not None else [(n, v[0], int(v[1])) for n, v in SCHOOLS.items()]
        self.sql, self.executed, self.lastrowid, self.next = "", [], 0, 100
        self.counts = counts or {}
    def execute(self, sql, params=None):
        self.sql = sql
        self.executed.append((sql, params))
        if sql.startswith("INSERT INTO `pers`"):
            self.next += 1
            self.lastrowid = self.next
    def fetchall(self):
        s = self.sql
        if "GET_LOCK" in s:
            return [(1,)]
        if "SHOW COLUMNS FROM `pers`" in s:
            return [("id",), ("vorname",), ("nachname",)]
        if "SHOW COLUMNS" in s:
            return [(c,) for c in e.WRITE_PARTICIPATION]
        if "FROM `jg`" in s:
            return [(7,)]
        if "FROM `sch`" in s:
            return self.schools
        if "SELECT id, vorname" in s:
            return self.persons
        if "FROM `teiln`" in s and "COUNT" not in s:
            return self.parts
        if "COUNT(*) FROM `pers`" in s:
            return [(self.counts.get("pers", len(self.persons) + sum(1 for q, _ in self.executed if q.startswith("INSERT INTO `pers`"))),)]
        if "COUNT(*) FROM `teiln`" in s:
            return [(self.counts.get("teiln", len(self.parts) + sum(1 for q, _ in self.executed if q.startswith("INSERT INTO `teiln`"))),)]
        return []
    def close(self):
        pass


class Conn:
    def __init__(self, cur):
        self.c, self.in_transaction, self.commits, self.rollbacks = cur, False, 0, 0
    def cursor(self):
        return self.c
    def start_transaction(self):
        self.in_transaction = True
    def commit(self):
        self.commits += 1
    def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "gf-extern.yaml").write_text(
        'form_id: 57\nfields: {vorname: "3.3", nachname: "3.6", klassenstufe: "5", schule: "6"}\n', encoding="utf-8")
    return tmp_path


def test_dry_run_reports_without_writing(config_dir):
    cnx = Conn(Cur())
    report: dict = {}
    stats = e.import_extern(SimpleNamespace(conn=cnx), config_dir, TABLES, dry_run=True, report=report,
                            entries=[entry(1), entry(2, "Ben", "Muster", "5", "01G02")])
    assert stats["neu"] == 2 and cnx.commits == 0 and report["status"] == "ready"
    assert not any(q.startswith("INSERT") for q, _ in cnx.c.executed)
    assert report["neu"][1]["schule"] == "Zweite Schule"


def test_apply_inserts_person_then_participation_in_one_transaction(config_dir):
    cnx = Conn(Cur())
    e.import_extern(SimpleNamespace(conn=cnx), config_dir, TABLES, dry_run=False, entries=[entry(2), entry(1, "Ben", "Muster")])
    inserts = [(q, p) for q, p in cnx.c.executed if q.startswith("INSERT")]
    assert cnx.commits == 1 and len(inserts) == 4
    assert inserts[0][1] == ("Ben", "Muster") and inserts[1][1][1] == 101 and inserts[1][1][4] == 1  # nach GF-ID sortiert
    assert inserts[1][1][0] == 7


def test_apply_rolls_back_on_count_mismatch(config_dir):
    cnx = Conn(Cur(counts={"teiln": 0}))
    with pytest.raises(e.ExternError, match="Zählabgleich"):
        e.import_extern(SimpleNamespace(conn=cnx), config_dir, TABLES, dry_run=False, entries=[entry(1)])
    assert cnx.commits == 0 and cnx.rollbacks == 1


def test_rerun_is_idempotent(config_dir):
    parts = [{"jahrgang_id": 7, "person_id": 1, "schulnummer": "01G01", "klassenstufe": 4, "gf_eintrag_id": 1}]
    cur = Cur(persons=[(1, "Anna", "Beispiel")], parts=[(7, 1, "01G01", 4, 1)])
    cnx = Conn(cur)
    stats = e.import_extern(SimpleNamespace(conn=cnx), config_dir, TABLES, dry_run=False, entries=[entry(1)])
    assert stats["neu"] == 0 and stats["bereits_importiert"] == 1 and cnx.commits == 0
    assert parts and not any(q.startswith("INSERT") for q, _ in cur.executed)


def test_demo_tables_and_bad_config_are_refused(config_dir, tmp_path):
    with pytest.raises(e.ExternError, match="extern_personen_table"):
        e.import_extern(SimpleNamespace(conn=Conn(Cur())), config_dir,
                        {**TABLES, "extern_personen_table": "DEMO_EXTERN_PERSONEN"}, dry_run=True, entries=[])
    (tmp_path / "bad.yaml").write_text("fields: {vorname: x}\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        e.load_extern_settings(tmp_path / "bad.yaml")
    with pytest.raises(ConfigurationError, match="fehlt"):
        e.load_extern_settings(tmp_path / "gibt-es-nicht.yaml")
