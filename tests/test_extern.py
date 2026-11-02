"""Externe Teilnehmende: Formularauswertung, Planung und Datenbankschicht mit Testcursor."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from mao_cli import extern as e
from mao_cli.config import ConfigurationError

FIELDS = {"vorname": "3.3", "nachname": "3.6", "klassenstufe": "5", "schule": "6", "geschlecht": "9"}
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
            return [("id",), ("vorname",), ("nachname",), ("geschlecht",)]
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
        'form_id: 57\nfields: {vorname: "3.3", nachname: "3.6", klassenstufe: "5", schule: "6", geschlecht: "9"}\n', encoding="utf-8")
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
    e.import_extern(SimpleNamespace(conn=cnx), config_dir, TABLES, dry_run=False, entries=[entry(2), entry(1, "Ben", "Muster", **{"9": "W"})])
    inserts = [(q, p) for q, p in cnx.c.executed if q.startswith("INSERT")]
    assert cnx.commits == 1 and len(inserts) == 4
    assert inserts[0][1] == ("Ben", "Muster", "w") and inserts[1][1][1] == 101 and inserts[1][1][4] == 1  # nach GF-ID sortiert
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


def test_gender_is_optional_and_validated():
    rows, issues, _ = e.parse_entries([entry(1, **{"9": "d"}), entry(2, **{"9": "x"}), entry(3)], FIELDS)
    assert [r["geschlecht"] for r in rows] == ["d", None, None] and len(issues) == 1
    rows, issues, _ = e.parse_entries([entry(1)], {k: v for k, v in FIELDS.items() if k != "geschlecht"})
    assert rows[0]["geschlecht"] is None and not issues


# ------------------------------------------------------------------ Raumzuteilung

from mao_cli import rooms as r  # noqa: E402


def rules_file(tmp_path, extern="rooms: [X1, X2]"):
    (tmp_path / "raumregeln.yaml").write_text(
        'rooms:\n  grade_rooms: {"5": [R1]}\n  reserve: R2\nextern:\n  ' + extern + "\n", encoding="utf-8")
    return tmp_path


def test_rules_reject_shared_or_malformed_extern_rooms(tmp_path):
    assert r.load_room_rules(rules_file(tmp_path) / "raumregeln.yaml").extern_rooms == ("X1", "X2")
    with pytest.raises(ConfigurationError, match="eigene Räume"):
        r.load_room_rules(rules_file(tmp_path, "rooms: [R2]") / "raumregeln.yaml")
    with pytest.raises(ConfigurationError, match="extern"):
        r.load_room_rules(rules_file(tmp_path, "rooms: [X1, X1]") / "raumregeln.yaml")
    with pytest.raises(ConfigurationError, match="extern"):
        r.load_room_rules(rules_file(tmp_path, "zimmer: [X1]") / "raumregeln.yaml")


ROOMS = {"X1": {"id": 1, "capacity": 15, "active": True}, "X2": {"id": 2, "capacity": 15, "active": True},
         "R9": {"id": 9, "capacity": 30, "active": True}}


def people(n, raum=None):
    return [{"id": 100 + i, "nr": i, "klassenstufe": 4, "raum": raum} for i in range(1, n + 1)]


def test_extern_rooms_fill_one_by_one_and_overflow_is_reported(tmp_path):
    rules = r.load_room_rules(rules_file(tmp_path, "rooms: [X1]") / "raumregeln.yaml")
    plan = e.plan_extern_rooms(people(17), ROOMS, rules)
    assert plan.per_room == {"X1": 15} and len(plan.unassigned) == 2 and plan.assignments[101] == "X1"
    plan = e.plan_extern_rooms(people(17), ROOMS, r.load_room_rules(rules_file(tmp_path) / "raumregeln.yaml"))
    assert plan.per_room == {"X1": 15, "X2": 2} and not plan.unassigned


def test_extern_rooms_keep_existing_and_overwrite(tmp_path):
    rules = r.load_room_rules(rules_file(tmp_path) / "raumregeln.yaml")
    mixed = people(3)
    mixed[2]["raum"] = "X2"
    plan = e.plan_extern_rooms(mixed, ROOMS, rules)
    assert plan.kept == 1 and set(plan.assignments.values()) == {"X1"}
    plan = e.plan_extern_rooms(mixed, ROOMS, rules, overwrite=True)
    assert plan.kept == 0 and len(plan.assignments) == 3


class RoomCur:
    def __init__(self, rows, total):
        self.rows, self.total, self.sql, self.executed = rows, total, "", []
    def execute(self, sql, params=None):
        self.sql = sql
        self.executed.append((sql, params))
    def executemany(self, sql, params):
        self.executed.append((sql, list(params)))
    def fetchall(self):
        if "GET_LOCK" in self.sql:
            return [(1,)]
        if "FROM `jg`" in self.sql:
            return [(7,)]
        if "FROM `raeume`" in self.sql:
            return [(1, "X1", 15, 1), (2, "X2", 15, 1)]
        if "COUNT(*)" in self.sql:
            return [(self.total,)]
        return self.rows
    def close(self):
        pass


RTABLES = {**TABLES, "rooms_table": "raeume"}


def test_assign_extern_apply_is_guarded_and_checked(tmp_path):
    rules_file(tmp_path)
    cnx = Conn(RoomCur([(1, 1, 4, None), (2, 2, 4, None)], total=2))
    report: dict = {}
    stats = e.assign_extern_rooms(SimpleNamespace(conn=cnx), tmp_path, RTABLES, dry_run=True, report=report)
    assert stats["assigned"] == 2 and cnx.commits == 0 and report["status"] == "ready"
    cnx = Conn(RoomCur([(1, 1, 4, None), (2, 2, 4, None)], total=2))
    e.assign_extern_rooms(SimpleNamespace(conn=cnx), tmp_path, RTABLES, dry_run=False, plan_id=stats["plan_id"])
    assert cnx.commits == 1 and any("raum_id IS NULL" in q for q, _ in cnx.c.executed if q.startswith("UPDATE"))
    bad = Conn(RoomCur([(1, 1, 4, None)], total=0))
    with pytest.raises(r.RoomError, match="Zählabgleich"):
        e.assign_extern_rooms(SimpleNamespace(conn=bad), tmp_path, RTABLES, dry_run=False)
    assert bad.commits == 0 and bad.rollbacks == 1
    with pytest.raises(r.RoomError, match="Plan-ID"):
        e.assign_extern_rooms(SimpleNamespace(conn=Conn(RoomCur([(1, 1, 4, None)], total=1))), tmp_path, RTABLES,
                              dry_run=False, plan_id="falsch")


def test_assign_extern_needs_section_participants_and_known_rooms(tmp_path):
    (tmp_path / "raumregeln.yaml").write_text('rooms:\n  grade_rooms: {"5": [R1]}\n', encoding="utf-8")
    with pytest.raises(r.RoomError, match="Abschnitt extern"):
        e.assign_extern_rooms(SimpleNamespace(conn=Conn(RoomCur([], 0))), tmp_path, RTABLES, dry_run=True)
    rules_file(tmp_path)
    with pytest.raises(r.RoomError, match="Keine externen"):
        e.assign_extern_rooms(SimpleNamespace(conn=Conn(RoomCur([], 0))), tmp_path, RTABLES, dry_run=True)
    rules_file(tmp_path, "rooms: [Y9]")
    with pytest.raises(r.RoomError, match="fehlt in der Raumtabelle"):
        e.assign_extern_rooms(SimpleNamespace(conn=Conn(RoomCur([(1, 1, 4, None)], 0))), tmp_path, RTABLES, dry_run=True)
