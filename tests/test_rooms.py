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
    pts = {i: 100 - i for i in range(1, 41)}
    plan = r.plan_rooms(people, {}, rules(), pts, seed=1)
    assert sorted(m for m, room in plan.assignments.items() if room == "T7") == list(range(1, 16))
    assert all(plan.per_room[room] <= 15 for room in plan.per_room)
    assert len(plan.assignments) == 40 and not plan.unassigned


def test_oberstufe_top_and_aula_use_database_capacity():
    people = [person(i, 11 if i % 2 else 12, "11") for i in range(1, 31)]
    pts = {i: 50 - i for i in range(1, 31)}
    plan = r.plan_rooms(people, {"Aula": 40}, rules(), pts, seed=3)
    assert sum(1 for room in plan.assignments.values() if room == "TO") == 15
    assert sum(1 for room in plan.assignments.values() if room == "Aula") == 15


def test_stufen_5_und_6_share_rooms_15_plus_15():
    people = [person(i, 5, "5-1") for i in range(1, 16)] + [person(100 + i, 6, "6-1") for i in range(1, 16)]
    plan = r.plan_rooms(people, {}, rules(), {}, seed=2)
    assert plan.paired_rooms == ["A", "B"]
    assert plan.per_room_grade["A"] == {"5": 15, "6": 15} or plan.per_room_grade["B"] == {"5": 15, "6": 15}
    assert len(plan.assignments) == 30


def test_overflow_goes_to_reserve_then_unassigned():
    people = [person(i, 8, "8-1") for i in range(1, 21)]
    plan = r.plan_rooms(people, {}, rules(grade_rooms={"8": ["X"]}, top15_by_grade={}), {}, seed=1)
    assert plan.per_room == {"X": 15, "R": 5} and not plan.unassigned
    plan = r.plan_rooms(people, {}, rules(grade_rooms={"8": ["X"]}, reserve=None, top15_by_grade={}), {}, seed=1)
    assert len(plan.unassigned) == 5


def test_stufe_without_rooms_warns_and_existing_assignments_are_protected():
    people = [person(1, 9, "9-1"), person(2, 7, "7-1", raum="C"), person(3, 7, "7-1")]
    plan = r.plan_rooms(people, {}, rules(reserve=None, top15_by_grade={}), {}, seed=1)
    assert plan.kept == 1 and 2 not in plan.assignments and plan.assignments[3] in ("C", "D")
    assert any("Stufe 9" in w for w in plan.warnings) and [p["matrikelnummer"] for p in plan.unassigned] == [1]


def test_existing_rooms_count_towards_capacity():
    people = [person(i, 7, "7-1", raum="C") for i in range(1, 16)] + [person(99, 7, "7-1")]
    plan = r.plan_rooms(people, {}, rules(grade_rooms={"7": ["C"]}, reserve=None, top15_by_grade={}), {}, seed=1)
    assert [p["matrikelnummer"] for p in plan.unassigned] == [99]


def test_same_seed_same_plan():
    people = [person(i, 7, f"7-{i % 3 + 1}") for i in range(1, 60)]
    a = r.plan_rooms(people, {}, rules(top15_by_grade={}), {}, seed=7).assignments
    b = r.plan_rooms(people, {}, rules(top15_by_grade={}), {}, seed=7).assignments
    c = r.plan_rooms(people, {}, rules(top15_by_grade={}), {}, seed=8).assignments
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


def test_fill_full_packs_rooms_even_spreads():
    people = [person(i, 7, f"7-{i % 2 + 1}") for i in range(1, 31)]
    base = dict(grade_rooms={"7": ["C", "D", "E"]}, reserve=None, top15_by_grade={})
    full = r.plan_rooms(people, {}, rules(**base), {}, seed=1)
    assert sorted(full.per_room.values()) == [15, 15] and full.per_room["C"] == 15
    even = r.plan_rooms(people, {}, rules(**base, fill="even"), {}, seed=1)
    assert sorted(even.per_room.values()) == [10, 10, 10]


def test_fill_full_mixes_classes_in_each_room():
    people = [person(i, 7, "7-1" if i <= 15 else "7-2") for i in range(1, 31)]
    plan = r.plan_rooms(people, {}, rules(grade_rooms={"7": ["C", "D"]}, reserve=None, top15_by_grade={}), {}, seed=4)
    in_c = [m for m, room in plan.assignments.items() if room == "C"]
    assert 0 < sum(m <= 15 for m in in_c) < 15


def test_top15_are_the_best_of_the_current_participants():
    people = [person(i, 7, "7-1") for i in range(11, 41)]  # 1..10 nehmen nicht teil
    pts = {i: 100 - i for i in range(1, 41)}
    plan = r.plan_rooms(people, {}, rules(), pts, seed=1)
    assert sorted(m for m, room in plan.assignments.items() if room == "T7") == list(range(11, 26))


def test_capacity_check_reports_shortage():
    people = [person(i, 9, "9-1") for i in range(1, 31)]
    plan = r.plan_rooms(people, {}, rules(grade_rooms={"9": ["X"]}, reserve=None, top15_by_grade={}), {}, seed=1)
    assert plan.capacity_check == [{"stufe": "9", "teilnehmende": 30, "freie_plaetze": 15}]
    assert any("Engpass Stufe 9" in w for w in plan.warnings)


def test_plan_id_is_stable_and_guards_apply(tmp_path):
    dry = setup(tmp_path, 3)
    stats = r.assign_rooms(SimpleNamespace(conn=dry), tmp_path, TABLES, dry_run=True, seed=1)
    cnx = setup(tmp_path, 3)
    r.assign_rooms(SimpleNamespace(conn=cnx), tmp_path, TABLES, dry_run=False, seed=1, plan_id=stats["plan_id"])
    assert cnx.commits == 1
    other = setup(tmp_path, 3)
    with pytest.raises(r.RoomError, match="Plan-ID"):
        r.assign_rooms(SimpleNamespace(conn=other), tmp_path, TABLES, dry_run=False, seed=1, plan_id="000000000000")
    assert other.commits == 0


def test_inactive_room_in_rules_blocks(tmp_path):
    cnx = setup(tmp_path, 3)
    cnx.cur.data["raeume"] = [(1, "C", 15, 1), (2, "D", 15, 0), (3, "R", 15, 1)]
    with pytest.raises(r.RoomError, match="Inaktive Räume"):
        r.assign_rooms(SimpleNamespace(conn=cnx), tmp_path, TABLES, dry_run=True, seed=1)


def test_fill_value_is_validated(tmp_path):
    path = tmp_path / "raumregeln.yaml"
    path.write_text("rooms:\n  grade_rooms:\n    '5': [A]\nfill: halb\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="fill"):
        r.load_room_rules(path)


def test_year_rules_take_precedence_over_central_template(tmp_path):
    central, year = tmp_path / "config", tmp_path / "Jahrgang" / "66_2026-27"
    central.mkdir()
    year.mkdir(parents=True)
    (central / "raumregeln.yaml").write_text("rooms: {}\n", encoding="utf-8")
    assert r.resolve_rules_file(year, central) == central / "raumregeln.yaml"
    created = r.create_year_rules(year, central)
    assert created == year / "raumregeln.yaml" and r.resolve_rules_file(year, central) == created
    with pytest.raises(Exception, match="existiert bereits"):
        r.create_year_rules(year, central)
    with pytest.raises(Exception, match="Vorlage fehlt"):
        r.create_year_rules(tmp_path, tmp_path / "leer")


def test_new_year_folder_inherits_rules_of_latest_year(tmp_path):
    from mao_cli.cli import _seed_year_rules
    base, config = tmp_path / "Jahrgang", tmp_path / "config"
    config.mkdir()
    (config / "raumregeln.yaml").write_text("# zentral\n", encoding="utf-8")
    for name, text in (("9_2019-20", "# alt\n"), ("10_2020-21", "# neuer\n")):
        (base / name).mkdir(parents=True)
        (base / name / "raumregeln.yaml").write_text(text, encoding="utf-8")
    new = base / "11_2021-22"
    new.mkdir()
    _seed_year_rules(new, config)
    assert (new / "raumregeln.yaml").read_text(encoding="utf-8") == "# neuer\n"
    first = tmp_path / "x" / "1_2000-01"
    first.mkdir(parents=True)
    _seed_year_rules(first, config)
    assert (first / "raumregeln.yaml").read_text(encoding="utf-8") == "# zentral\n"


# ------------------------------------------------------------------ Sammelräume

def test_flex_uses_fewest_rooms_and_avoids_mixing():
    layout, left = r.plan_flex({7: 15, 8: 9, 9: 6}, [("S1", 15), ("S2", 15), ("S3", 15)])
    assert not left and [code for code, _ in layout] == ["S1", "S2"]
    assert sorted(sum(seg.values()) for _, seg in layout) == [15, 15]
    assert any(seg == {7: 15} for _, seg in layout)  # Stufe 7 füllt einen Raum allein
    assert any(seg == {8: 9, 9: 6} for _, seg in layout)  # der Rest bildet den Sammelraum


def test_flex_keeps_mixing_to_neighbouring_levels():
    # 3 × 10 in 2 × 15: beide Räume müssen gemischt sein, aber nur mit benachbarten Stufen (nie 5 mit 7)
    layout, left = r.plan_flex({5: 10, 6: 10, 7: 10}, [("S1", 15), ("S2", 15)])
    assert not left and all(max(seg) - min(seg) == 1 for _, seg in layout)
    # Eine Stufe mit genau einem Raum voll bleibt rein; nur der Rest mischt
    layout, _ = r.plan_flex({5: 15, 8: 9, 9: 6}, [("S1", 15), ("S2", 15)])
    assert sorted(len(seg) for _, seg in layout) == [1, 2]


def test_flex_prefers_large_rooms_respects_existing_and_reports_overflow():
    layout, left = r.plan_flex({7: 20, 8: 5}, [("klein", 10), ("gross", 40)])
    assert layout == [("gross", {7: 20, 8: 5})] and not left
    layout, left = r.plan_flex({7: 20, 8: 20}, [("a", 15), ("b", 15)])
    assert sum(sum(seg.values()) for _, seg in layout) == 30 and sum(left.values()) == 10
    layout, _ = r.plan_flex({8: 5, 9: 5}, [("a", 15), ("b", 15)], existing={"a": {9}, "b": {8}})
    assert len(layout) == 1


def test_rules_parse_sammel_rooms_and_reject_conflicts(tmp_path):
    def load(extra):
        path = tmp_path / "raumregeln.yaml"
        path.write_text('rooms:\n  grade_rooms: {"5": [R1]}\n  reserve: R2\n' + extra, encoding="utf-8")
        return r.load_room_rules(path)
    assert load("  sammel: [S1, S2]\n").flex_rooms == ("S1", "S2")
    assert load("").flex_rooms == ()
    with pytest.raises(ConfigurationError, match="Sammelraum"):
        load("  sammel: [R1]\n")
    with pytest.raises(ConfigurationError, match="sammel"):
        load("  sammel: [S1, S1]\n")


def test_plan_rooms_sends_the_rest_into_sammelraeume():
    people = ([person(i, 5, "5-1") for i in range(100, 120)] + [person(i, 8, "8-1") for i in range(200, 209)]
              + [person(i, 9, "9-1") for i in range(300, 306)])
    rule = r.RoomRules(grade_rooms={"5": ["F5"]}, flex_rooms=("S1", "S2", "S3"), reserve="RES")
    plan = r.plan_rooms(people, {}, rule, {}, seed=1)
    assert not plan.unassigned and plan.per_room.get("F5") == 15 and "RES" not in plan.per_room
    assert {s for item in plan.flex for s in item["stufen"]} == {"5", "8", "9"}
    used = [code for code in plan.per_room if code.startswith("S")]
    assert len(used) == 2  # 5 + 9 + 6 = 20 Rest passt in zwei Räume
    assert sum(plan.per_room.values()) == len(people)
    again = r.plan_rooms(people, {}, rule, {}, seed=1)
    assert again.assignments == plan.assignments


def test_without_sammel_rooms_behaviour_is_unchanged():
    people = [person(i, 9, "9-1") for i in range(10)]
    plan = r.plan_rooms(people, {}, r.RoomRules(grade_rooms={"5": ["A"]}), {}, seed=1)
    assert len(plan.unassigned) == 10 and any("keine Zielräume" in w for w in plan.warnings)


def test_empty_grade_rooms_need_sammel_rooms(tmp_path):
    path = tmp_path / "raumregeln.yaml"
    path.write_text('rooms:\n  grade_rooms: {"5": [R1], "8": [], "9": []}\n  sammel: [S1]\n', encoding="utf-8")
    assert r.load_room_rules(path).grade_rooms["8"] == []
    path.write_text('rooms:\n  grade_rooms: {"5": [R1], "8": []}\n', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="leer"):
        r.load_room_rules(path)
    path.write_text('rooms:\n  grade_rooms: {"5": "R1"}\n', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Liste"):
        r.load_room_rules(path)


def test_capacity_check_with_sammel_rooms_only_warns_when_pool_is_too_small():
    people = [person(i, 8, "8-1") for i in range(40)]
    rule = r.RoomRules(grade_rooms={"5": ["A"], "8": []}, flex_rooms=("S1", "S2", "S3"))
    assert not r.plan_rooms(people, {}, rule, {}, seed=1).warnings
    small = r.RoomRules(grade_rooms={"5": ["A"], "8": []}, flex_rooms=("S1",))
    plan = r.plan_rooms(people, {}, small, {}, seed=1)
    assert any("Engpass" in w for w in plan.warnings) and len(plan.unassigned) == 25


def test_pure_first_fills_whole_rooms_per_level_and_collects_remainders():
    # Zahlen aus der Praxis: 32 + 32 + 23 + 3 = 90 Rest in 6 Räumen zu 15
    rooms_ = [(f"S{i}", 15) for i in range(8)]
    layout, left = r.plan_flex({7: 32, 8: 32, 9: 23, 10: 3}, rooms_)
    assert not left and len(layout) == 6
    mixed = [seg for _, seg in layout if len(seg) > 1]
    assert len(mixed) == 1 and mixed[0] == {7: 2, 8: 2, 9: 8, 10: 3}
    assert sum(1 for _, seg in layout if len(seg) == 1 and sum(seg.values()) == 15) == 5
    # Stil „nachbarn“: gleiche Raumzahl, aber mehrere Räume mit je zwei Nachbarstufen
    layout, _ = r.plan_flex({7: 32, 8: 32, 9: 23, 10: 3}, rooms_, pure_first=False)
    assert len(layout) == 6 and sum(1 for _, seg in layout if len(seg) > 1) == 3


def test_pure_first_respects_existing_levels_and_big_rooms():
    layout, _ = r.plan_flex({7: 15, 8: 15}, [("a", 15), ("b", 15)], existing={"a": {8}})
    assert dict(layout) == {"a": {8: 15}, "b": {7: 15}}
    layout, left = r.plan_flex({7: 40, 8: 10}, [("aula", 50), ("k", 15)])
    assert dict(layout) == {"aula": {7: 40, 8: 10}} and not left


def test_remainder_larger_than_a_room_is_split_between_neighbours():
    layout, left = r.plan_flex({5: 10, 6: 10, 7: 10}, [("a", 15), ("b", 15)])
    assert not left and all(max(seg) - min(seg) == 1 for _, seg in layout)


def test_sammel_style_is_validated(tmp_path):
    path = tmp_path / "raumregeln.yaml"
    path.write_text('rooms:\n  grade_rooms: {"5": [R1]}\n  sammel: [S1]\n  sammel_stil: nachbarn\n', encoding="utf-8")
    assert r.load_room_rules(path).flex_style == "nachbarn"
    path.write_text('rooms:\n  grade_rooms: {"5": [R1]}\n  sammel: [S1]\n  sammel_stil: bunt\n', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="sammel_stil"):
        r.load_room_rules(path)
    path.write_text('rooms:\n  grade_rooms: {"5": [R1]}\n', encoding="utf-8")
    assert r.load_room_rules(path).flex_style == "rein_zuerst"


def test_max_span_keeps_pure_rooms_and_mixes_only_neighbours():
    rooms_ = [(f"S{i}", 15) for i in range(8)]
    counts = {7: 32, 8: 32, 9: 23, 10: 3}
    layout, left = r.plan_flex(counts, rooms_, max_span=1)
    assert not left and len(layout) == 6
    assert all(max(seg) - min(seg) <= 1 for _, seg in layout)
    assert sum(1 for _, seg in layout if len(seg) == 1) >= 3 and sum(sum(seg.values()) for _, seg in layout) == 90
    free, _ = r.plan_flex(counts, rooms_)
    assert sum(1 for _, seg in free if len(seg) > 1) == 1
    wide, _ = r.plan_flex(counts, rooms_, max_span=3)
    assert sorted(map(str, (s for _, s in wide))) == sorted(map(str, (s for _, s in free)))


def test_max_span_with_neighbours_available():
    layout, left = r.plan_flex({6: 16, 7: 14}, [("a", 15), ("b", 15)], max_span=1)
    assert not left and sorted(len(seg) for _, seg in layout) == [1, 2]


def test_max_span_is_validated_and_warns_when_not_achievable(tmp_path):
    path = tmp_path / "raumregeln.yaml"
    path.write_text('rooms:\n  grade_rooms: {"5": [R1]}\n  sammel: [S1]\n  sammel_max_spanne: 1\n', encoding="utf-8")
    assert r.load_room_rules(path).flex_max_span == 1
    path.write_text('rooms:\n  grade_rooms: {"5": [R1]}\n  sammel: [S1]\n  sammel_max_spanne: -1\n', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="sammel_max_spanne"):
        r.load_room_rules(path)
    people = [person(i, 8, "8-1") for i in range(5)] + [person(100 + i, 10, "10-1") for i in range(5)]
    rule = r.RoomRules(grade_rooms={"5": ["A"]}, flex_rooms=("S1",), flex_max_span=1)
    plan = r.plan_rooms(people, {}, rule, {}, seed=1)
    assert any("erlaubte Spanne" in w for w in plan.warnings) and not plan.unassigned


# ------------------------------------------------------------------ Räume je Stufe / Raum von Hand

def test_rooms_by_stufe_lists_rooms_in_order_with_counts():
    plan = r.RoomPlan()
    plan.per_room_grade = {"204": {"8": 15}, "105": {"7": 2, "8": 13}, "003": {"7": 15}}
    result = r.rooms_by_stufe(plan)
    assert list(result) == ["7", "8"] and result["7"] == [("003", 15), ("105", 2)] and result["8"] == [("105", 13), ("204", 15)]


def test_plan_report_has_rooms_per_stufe():
    people = [person(i, 7, "7-1") for i in range(20)]
    plan = r.plan_rooms(people, {}, r.RoomRules(grade_rooms={"7": ["A", "B"]}), {}, seed=1)
    assert r.rooms_by_stufe(plan)["7"] == [("A", 15), ("B", 5)]


class SetCur:
    def __init__(self, people, in_room=(0, 0), taken_room=None):
        self.people, self.in_room, self.taken, self.sql, self.writes = people, in_room, taken_room, "", []
    def execute(self, sql, params=None):
        self.sql, self.params = sql, params
        if sql.startswith("UPDATE"):
            self.writes.append((sql, params))
    def fetchall(self):
        s = self.sql
        if "GET_LOCK" in s:
            return [(1,)]
        if "FROM `raeume`" in s:
            return [(1, "105", 15, 1), (2, "205", 15, 1), (3, "OFF", 15, 0)]
        if "SELECT matrikelnummer, stufe" in s:
            return [(m, 8, "8-1", self.taken) for m in self.people]
        if "SUM(" in s:
            return [self.in_room]
        if "COUNT(*)" in s:
            return [(len(self.people),)]
        return []
    def close(self):
        pass


TABLES2 = {"orga_table": "orga", "rooms_table": "raeume"}


def run_set(cur, matrikels, **kw):
    cnx = SimpleNamespace(cursor=lambda: cur, in_transaction=False, start_transaction=lambda: None,
                          commit=lambda: kw.setdefault("_c", None), rollback=lambda: None)
    kw.pop("_c", None)
    return r.set_room(SimpleNamespace(conn=cnx), TABLES2, kw.pop("room", "105"), matrikels, **kw)


def test_set_room_dry_run_and_apply_are_guarded():
    cur = SetCur([111, 222])
    stats = run_set(cur, [111, 222], dry_run=True)
    assert stats["im_raum_danach"] == 2 and not cur.writes
    cur = SetCur([111, 222])
    run_set(cur, [111, 222], dry_run=False)
    sql, params = cur.writes[0]
    assert "raum_id IS NULL" in sql and params[0] == 1 and params[1:] == (111, 222)


def test_set_room_refuses_unknown_inactive_taken_and_overfull():
    with pytest.raises(r.RoomError, match="fehlt in der Raumtabelle"):
        run_set(SetCur([1]), [1], dry_run=True, room="XXX")
    with pytest.raises(r.RoomError, match="inaktiv"):
        run_set(SetCur([1]), [1], dry_run=True, room="OFF")
    with pytest.raises(r.RoomError, match="anderen Raum"):
        run_set(SetCur([1], taken_room=2), [1], dry_run=True)
    cur = SetCur([1], taken_room=2)
    run_set(cur, [1], dry_run=False, overwrite=True)
    assert "raum_id IS NULL" not in cur.writes[0][0]
    assert run_set(SetCur([1, 2], in_room=(15, 0)), [1, 2], dry_run=True)["im_raum_danach"] == 17
    assert run_set(SetCur([1, 2], in_room=(15, 0)), [1, 2], dry_run=True, allow_over_capacity=True)["im_raum_danach"] == 17
    with pytest.raises(r.RoomError, match="doppelt"):
        run_set(SetCur([1]), [1, 1], dry_run=True)


def _rooms3():
    return {"A": {"id": 1, "capacity": 2, "active": True}, "B": {"id": 2, "capacity": 15, "active": True},
            "C": {"id": 3, "capacity": 15, "active": False}}


def test_suggest_rooms_prefers_same_level_and_lists_full_last_and_skips_inactive():
    rooms = _rooms3()
    assert r.suggest_rooms(7, rooms, {"A": {7: 1}, "B": {8: 3}}, 15) == ["A", "B"]
    assert r.suggest_rooms(7, rooms, {"A": {8: 2}, "B": {7: 1}}, 15) == ["B", "A"]


class IaCur:
    def __init__(self):
        self.last, self.writes = "", []
    def execute(self, sql, params=None):
        self.last = sql
        if sql.startswith("UPDATE"):
            self.writes.append((sql, params))
    def fetchall(self):
        if "GET_LOCK" in self.last or "RELEASE_LOCK" in self.last:
            return [(1,)]
        if "COUNT(*), COALESCE" in self.last:
            return [(0, 0)]
        if "COUNT(*)" in self.last:
            return [(len(self.writes[-1][1]) - 1,)]
        if "`raeume`" in self.last:
            return [(1, "A", 2, 1), (2, "B", 15, 1)]
        if "`orga`" in self.last and "matrikelnummer IN" in self.last:
            return [(1, 7, "7-1", None), (2, 7, "7-2", None), (3, 8, "8-1", None)]
        if "`orga`" in self.last:
            return [(1, 7, "7-1", None), (2, 7, "7-2", None), (3, 8, "8-1", None)]
        return []
    def close(self):
        pass


def _db(cur):
    cnx = SimpleNamespace(cursor=lambda: cur, in_transaction=False, start_transaction=lambda: None,
                          commit=lambda: None, rollback=lambda: None)
    return SimpleNamespace(conn=cnx)


def _run_ia(inputs, apply):
    cur, lines, answers = IaCur(), [], iter(inputs)
    result = r.interactive_assign(_db(cur), TABLES2 | {"points_last_year_view": "punkte"}, apply=apply,
                                  ask=lambda _p: next(answers), out=lines.append)
    return cur, result, lines


def test_interactive_dry_run_writes_nothing():
    cur, result, _ = _run_ia(["1", "1", "A"], apply=False)
    assert result["zugeteilt"] == 3 and not cur.writes


def test_interactive_skip_undo_and_quit():
    cur, result, _ = _run_ia(["1", "", "u", "q"], apply=False)
    assert result["zugeteilt"] == 1 and result["uebersprungen"] == 0
    cur, result, _ = _run_ia(["", "q"], apply=False)
    assert result["zugeteilt"] == 0 and result["uebersprungen"] == 1


def test_interactive_apply_needs_confirmation():
    cur, result, _ = _run_ia(["A", "A", "B", "n"], apply=True)
    assert not cur.writes and result["zugeteilt"] == 0
    cur, result, _ = _run_ia(["A", "A", "B", "j"], apply=True)
    assert len(cur.writes) == 2


def test_interactive_refuses_full_and_unknown_room():
    _cur, result, lines = _run_ia(["A", "A", "XX", "A"], apply=False)
    assert any("über der Kapazität" in x for x in lines) and any("Unbekannter" in x for x in lines)
    assert result["zugeteilt"] == 3


def test_suggest_rooms_offers_all_active_rooms_including_full_ones():
    rooms = {c: {"active": True, "capacity": 2} for c in "ABCDEFGH"}
    rooms["I"] = {"active": False, "capacity": 2}
    occupancy = {"A": {7: 2}, "B": {7: 2}}                      # A und B sind voll
    offered = r.suggest_rooms(7, rooms, occupancy, 15)
    assert sorted(offered) == list("ABCDEFGH") and offered[-2:] == ["A", "B"]
    assert len(r.suggest_rooms(7, rooms, occupancy, 15, limit=3)) == 3
