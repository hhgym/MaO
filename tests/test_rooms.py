"""Raumzuteilung: Regeln, Planer (rein), Datenbankschicht mit Testcursor."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mao_cli import rooms as r
from mao_cli.config import ConfigurationError

DEFAULTS = Path(r.__file__).parent / "defaults"


def rules(**kw):
    base = dict(grade_rooms={"5": ["A", "B"], "6": ["A", "B"], "7": ["C", "D"], "11": ["Aula"], "12": ["Aula"]},
                reserve="R", top15_by_grade={"7": "T7"}, top15_oberstufe="TO")
    base.update(kw)
    return r.RoomRules(**base)


def person(m, stufe, klasse, raum=None):
    return {"matrikelnummer": m, "stufe": stufe, "klasse": klasse, "raum": raum}


def test_example_rules_load():
    loaded = r.load_room_rules(DEFAULTS / "raumregeln.yaml.example")
    assert loaded.grade_rooms["11"] == ["Aula"] and loaded.max_per_room == 15
    assert loaded.top15_by_grade["7"] and loaded.top15_oberstufe


def test_rules_reject_bad_input(tmp_path):
    path = tmp_path / "raumregeln.yaml"
    with pytest.raises(ConfigurationError, match="fehlen"):
        r.load_room_rules(path)
    path.write_text("rooms:\n  grade_rooms:\n    '4': [A]\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Klassenstufe"):
        r.load_room_rules(path)
    path.write_text("rooms:\n  grade_rooms:\n    '5': [A]\nmax_per_room: 0\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="max_per_room"):
        r.load_room_rules(path)


def test_points_only_count_numbers():
    assert r.parse_points("17") == 17 and r.parse_points(0) == 0
    assert r.parse_points("keine Teilnahme") is None and r.parse_points(None) is None
    assert r.parse_points("gemeldet, aber war abwesend") is None


def test_top_selection_groups_oberstufe_and_reports_ties():
    pts = [(1, 30, 11), (2, 29, 12), (3, 29, 11), (4, 5, 12), (5, 20, 7), (6, 20, 7)]
    top, notes = r.select_top(pts, 2)
    assert top["11/12"] == [1, 2] and top["7"] == [5, 6]
    assert any("gleichstand" in n and "11/12" in n for n in notes)


def test_top15_go_to_their_rooms_and_rest_is_spread():
    people = [person(i, 7, "7-1") for i in range(1, 41)]
    pts = [(i, 100 - i, 7) for i in range(1, 41)]
    plan = r.plan_rooms(people, {}, rules(), pts, seed=1)
    assert sorted(m for m, room in plan.assignments.items() if room == "T7") == list(range(1, 16))
    assert all(plan.per_room[room] <= 15 for room in plan.per_room)
    assert len(plan.assignments) == 40 and not plan.unassigned


def test_oberstufe_top_and_aula_use_database_capacity():
    people = [person(i, 11 if i % 2 else 12, "11") for i in range(1, 31)]
    pts = [(i, 50 - i, 11 if i % 2 else 12) for i in range(1, 31)]
    plan = r.plan_rooms(people, {"Aula": 40}, rules(), pts, seed=3)
    assert sum(1 for room in plan.assignments.values() if room == "TO") == 15
    assert sum(1 for room in plan.assignments.values() if room == "Aula") == 15


def test_stufen_5_und_6_share_rooms_15_plus_15():
    people = [person(i, 5, "5-1") for i in range(1, 16)] + [person(100 + i, 6, "6-1") for i in range(1, 16)]
    plan = r.plan_rooms(people, {}, rules(), [], seed=2)
    assert plan.paired_rooms == ["A", "B"]
    assert plan.per_room_grade["A"] == {"5": 15, "6": 15} or plan.per_room_grade["B"] == {"5": 15, "6": 15}
    assert len(plan.assignments) == 30


def test_overflow_goes_to_reserve_then_unassigned():
    people = [person(i, 8, "8-1") for i in range(1, 21)]
    plan = r.plan_rooms(people, {}, rules(grade_rooms={"8": ["X"]}, top15_by_grade={}), [], seed=1)
    assert plan.per_room == {"X": 15, "R": 5} and not plan.unassigned
    plan = r.plan_rooms(people, {}, rules(grade_rooms={"8": ["X"]}, reserve=None, top15_by_grade={}), [], seed=1)
    assert len(plan.unassigned) == 5


def test_stufe_without_rooms_warns_and_existing_assignments_are_protected():
    people = [person(1, 9, "9-1"), person(2, 7, "7-1", raum="C"), person(3, 7, "7-1")]
    plan = r.plan_rooms(people, {}, rules(reserve=None, top15_by_grade={}), [], seed=1)
    assert plan.kept == 1 and 2 not in plan.assignments and plan.assignments[3] in ("C", "D")
    assert any("Stufe 9" in w for w in plan.warnings) and [p["matrikelnummer"] for p in plan.unassigned] == [1]


def test_existing_rooms_count_towards_capacity():
    people = [person(i, 7, "7-1", raum="C") for i in range(1, 16)] + [person(99, 7, "7-1")]
    plan = r.plan_rooms(people, {}, rules(grade_rooms={"7": ["C"]}, reserve=None, top15_by_grade={}), [], seed=1)
    assert [p["matrikelnummer"] for p in plan.unassigned] == [99]


def test_same_seed_same_plan():
    people = [person(i, 7, f"7-{i % 3 + 1}") for i in range(1, 60)]
    a = r.plan_rooms(people, {}, rules(top15_by_grade={}), [], seed=7).assignments
    b = r.plan_rooms(people, {}, rules(top15_by_grade={}), [], seed=7).assignments
    c = r.plan_rooms(people, {}, rules(top15_by_grade={}), [], seed=8).assignments
    assert a == b and a != c


class FakeCursor:
    def __init__(self, data):
        self.data, self.executed, self.last = data, [], ""
    def execute(self, sql, params=None):
        self.last = sql
        self.executed.append((sql, params))
    def executemany(self, sql, params):
        self.executed.append((sql, list(params)))
    def fetchall(self):
        if "GET_LOCK" in self.last or "RELEASE_LOCK" in self.last:
            return [(1,)]
        if "COUNT(*)" in self.last:
            return [(self.data["count"],)]
        for key in ("raeume", "orga", "punkte"):
            if f"`{key}`" in self.last:
                return self.data[key]
        return []
    def close(self):
        pass


class FakeConn:
    def __init__(self, data):
        self.cur, self.in_transaction, self.commits, self.rollbacks = FakeCursor(data), False, 0, 0
    def cursor(self):
        return self.cur
    def start_transaction(self):
        self.in_transaction = True
    def commit(self):
        self.commits += 1
        self.in_transaction = False
    def rollback(self):
        self.rollbacks += 1
        self.in_transaction = False


TABLES = {"orga_table": "orga", "rooms_table": "raeume", "points_last_year_view": "punkte"}


def setup(tmp_path, count):
    (tmp_path / "raumregeln.yaml").write_text(
        "rooms:\n  grade_rooms:\n    '7': [C, D]\n  reserve: R\n", encoding="utf-8")
    data = {"raeume": [(1, "C", 15, 1), (2, "D", 15, 1), (3, "R", 15, 1)],
            "orga": [(1, 7, "7-1", None), (2, 7, "7-1", None), (3, 7, "7-2", None)],
            "punkte": [(1, "7-1", "12"), (2, "7-1", "keine Teilnahme")], "count": count}
    return FakeConn(data)


def test_dry_run_writes_nothing(tmp_path):
    cnx = setup(tmp_path, 3)
    report = {}
    stats = r.assign_rooms(SimpleNamespace(conn=cnx), tmp_path, TABLES, dry_run=True, seed=1, report=report)
    assert stats["assigned"] == 3 and stats["written"] == 0 and cnx.commits == 0
    assert not any(sql.lstrip().upper().startswith("UPDATE") for sql, _ in cnx.cur.executed)
    assert report["status"] == "ready" and "matrikelnummer" not in str(report["rooms"])


def test_apply_writes_in_transaction_with_count_check(tmp_path):
    cnx = setup(tmp_path, 3)
    r.assign_rooms(SimpleNamespace(conn=cnx), tmp_path, TABLES, dry_run=False, seed=1)
    updates = [(sql, p) for sql, p in cnx.cur.executed if sql.startswith("UPDATE")]
    assert cnx.commits == 1 and len(updates[0][1]) == 3 and "raum_id IS NULL" in updates[0][0]
    assert any("RELEASE_LOCK" in sql for sql, _ in cnx.cur.executed)


def test_apply_rolls_back_on_count_mismatch(tmp_path):
    cnx = setup(tmp_path, 1)
    with pytest.raises(r.RoomError, match="Zählabgleich"):
        r.assign_rooms(SimpleNamespace(conn=cnx), tmp_path, TABLES, dry_run=False, seed=1)
    assert cnx.commits == 0 and cnx.rollbacks == 1


def test_overwrite_clears_first(tmp_path):
    cnx = setup(tmp_path, 3)
    r.assign_rooms(SimpleNamespace(conn=cnx), tmp_path, TABLES, dry_run=False, seed=1, overwrite=True)
    sqls = [sql for sql, _ in cnx.cur.executed if sql.startswith("UPDATE")]
    assert sqls[0].endswith("SET raum_id = NULL") and "raum_id IS NULL" not in sqls[1]


def test_unknown_room_code_blocks(tmp_path):
    cnx = setup(tmp_path, 3)
    cnx.cur.data["raeume"] = [(1, "C", 15, 1)]
    with pytest.raises(r.RoomError, match="fehlen in der Raumtabelle"):
        r.assign_rooms(SimpleNamespace(conn=cnx), tmp_path, TABLES, dry_run=False, seed=1)
    assert cnx.commits == 0


def test_demo_tables_are_refused(tmp_path):
    cnx = setup(tmp_path, 3)
    with pytest.raises(r.RoomError, match="points_last_year_view"):
        r.assign_rooms(SimpleNamespace(conn=cnx), tmp_path, {**TABLES, "points_last_year_view": "DEMO_X"},
                       dry_run=True, seed=1)


def test_report_files_stay_in_workspace(tmp_path):
    ws, cfg = tmp_path / "ws", tmp_path / "cfg"
    ws.mkdir(); cfg.mkdir()
    rep = {"rooms": [{"raum": "C", "teilnehmende": 2, "kapazitaet": 15, "stufen": {"7": 2}}], "unassigned": []}
    assert r.write_room_report(ws, cfg, Path("raum.csv"), rep).read_text(encoding="utf-8").startswith("raum,")
    with pytest.raises(ConfigurationError, match="überschrieben"):
        r.write_room_report(ws, cfg, Path("raum.csv"), rep)
    with pytest.raises(ConfigurationError):
        r.write_room_report(ws, cfg, Path("../x.json"), rep)
