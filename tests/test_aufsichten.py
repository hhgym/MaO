"""Aufsichtenprüfung: reine Prüflogik und Datenbankschicht mit Testcursor."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from mao_cli import aufsichten as a
from mao_cli import rooms as r
from mao_cli.config import ConfigurationError

TEACHERS = {1: {"kuerzel": "AB", "aktiv": 1, "austritt": None}, 2: {"kuerzel": "CD", "aktiv": 1, "austritt": None},
            3: {"kuerzel": "EF", "aktiv": 0, "austritt": None}, 4: {"kuerzel": "GH", "aktiv": 1, "austritt": date(2020, 1, 1)}}
RULES = r.RoomRules(grade_rooms={"5": ["R1"]})


def sup(raum, lk, rolle="Standard"):
    return {"raum": raum, "lehrkraft_id": lk, "rolle": rolle}


def kinds(findings):
    return [f["art"] for f in findings]


def test_complete_plan_has_no_findings():
    rows, findings = a.check_supervision({"R1": 15, "R2": 10}, [sup("R1", 1), sup("R2", 2), sup(None, 2, "Fachaufsicht")], TEACHERS, RULES, date(2026, 11, 11))
    assert not findings and rows[0]["lehrkraefte"] == ["AB"]


def test_missing_surplus_and_open_slots():
    rows, findings = a.check_supervision({"R1": 15, "R2": 10, "R3": 5}, [sup("R1", 1), sup("R1", 2), sup("R2", None)], TEACHERS, RULES)
    texts = " ".join(f["text"] for f in findings)
    assert kinds(findings).count("fehlt") == 2 and "R3" in texts and "ohne Lehrkraft" in texts and "2 Aufsichten" in texts


def test_reserve_does_not_count_and_roles_are_configurable():
    _, findings = a.check_supervision({"R1": 5}, [sup("R1", 1, "Reserve")], TEACHERS, RULES)
    assert "fehlt" in kinds(findings)
    both = r.RoomRules(grade_rooms={"5": ["R1"]}, supervisor_roles=("Standard", "Fachaufsicht"))
    _, findings = a.check_supervision({"R1": 5}, [sup("R1", 1, "Fachaufsicht")], TEACHERS, both)
    assert "fehlt" not in kinds(findings)


def test_double_booking_inactive_unknown_and_orphans():
    _, findings = a.check_supervision({"R1": 5, "R2": 5}, [sup("R1", 1), sup("R2", 1), sup("R1", 3), sup("R2", 4), sup("R2", 99),
                                                           sup("R9", 2), sup(None, 2, "Standard"), sup(None, 2, "Reserve")],
                                      TEACHERS, RULES, date(2026, 11, 11))
    texts = [f["text"] for f in findings]
    assert any("mehreren Räumen" in t and "AB" in t for t in texts)
    assert any("EF" in t and "nicht mehr aktiv" in t for t in texts) and any("GH" in t and "nicht mehr aktiv" in t for t in texts)
    assert any("99" in t and "existiert nicht" in t for t in texts)
    assert any("niemand schreibt" in t for t in texts) and any("ohne Raum" in t for t in texts)
    assert sum("ohne Raum" in t for t in texts) == 1  # Reserve ohne Raum ist normal


def test_fachaufsicht_is_the_roomless_competition_management():
    _, findings = a.check_supervision({"R1": 5}, [sup("R1", 1), sup(None, 2, "Fachaufsicht")], TEACHERS, RULES)
    assert not findings
    _, findings = a.check_supervision({"R1": 5}, [sup("R1", 1)], TEACHERS, RULES)
    assert [f["text"] for f in findings] == ["Keine Fachaufsicht (Wettkampfleitung) eingetragen"]
    _, findings = a.check_supervision({"R1": 5}, [sup("R1", 1), sup("R1", 2, "Fachaufsicht")], TEACHERS, RULES)
    assert any("gehört keinem Raum an" in f["text"] for f in findings) and not any(f["art"] == "fehler" for f in findings)


def test_rules_section_is_validated(tmp_path):
    def load(extra):
        (tmp_path / "raumregeln.yaml").write_text('rooms:\n  grade_rooms: {"5": [R1]}\n' + extra, encoding="utf-8")
        return r.load_room_rules(tmp_path / "raumregeln.yaml")
    loaded = load("aufsichten:\n  lehrkraefte_pro_raum: 2\n  zaehlende_rollen: [Standard, Fachaufsicht]\n")
    assert loaded.supervisors_per_room == 2 and loaded.supervisor_roles == ("Standard", "Fachaufsicht")
    assert load("").supervisors_per_room == 1
    for bad in ("aufsichten: {lehrkraefte_pro_raum: 0}\n", "aufsichten: {zaehlende_rollen: [Boss]}\n", "aufsichten: {x: 1}\n"):
        with pytest.raises(ConfigurationError, match="aufsichten"):
            load(bad)


class Cur:
    def __init__(self, orga=((1, 15),), extern=(), sups=((1, 1, "Standard"),)):
        self.orga, self.extern, self.sups, self.sql, self.writes = list(orga), list(extern), list(sups), "", []
    def execute(self, sql, params=None):
        self.sql = sql
        if sql.startswith(("INSERT", "UPDATE", "DELETE")):
            self.writes.append(sql)
    def fetchall(self):
        s = self.sql
        if "FROM `jg`" in s:
            return [(7,)]
        if "FROM `raeume`" in s:
            return [(1, "R1", 15, 1), (2, "X1", 15, 1)]
        if "FROM `orga`" in s:
            return self.orga
        if "FROM `teiln`" in s:
            return self.extern
        if "FROM `auf`" in s:
            return self.sups
        if "FROM `lk`" in s:
            return [(1, "AB", "Alpha", 1, None), (2, "CD", "Beta", 1, None)]
        return []
    def close(self):
        pass


TABLES = {"orga_table": "orga", "extern_teilnahmen_table": "teiln", "aufsichten_table": "auf", "teachers_table": "lk",
          "rooms_table": "raeume", "jahrgaenge_table": "jg", "schuljahre_table": "sj"}


def run(tmp_path, cur, report=None):
    (tmp_path / "raumregeln.yaml").write_text('rooms:\n  grade_rooms: {"5": [R1]}\n', encoding="utf-8")
    return a.check_aufsichten(SimpleNamespace(conn=SimpleNamespace(cursor=lambda: cur)), tmp_path, TABLES, report=report)


def test_database_check_includes_externals_and_never_writes(tmp_path):
    cur = Cur(extern=((2, 12),))
    report: dict = {}
    stats = run(tmp_path, cur, report)
    assert stats["raeume"] == 2 and stats["probleme"] == 1 and report["status"] == "probleme"
    assert any(f["raum"] == "X1" and f["art"] == "fehlt" for f in report["findings"]) and not cur.writes


def test_database_check_requires_assigned_rooms(tmp_path):
    with pytest.raises(r.RoomError, match="raumzuteilung"):
        run(tmp_path, Cur(orga=()))
    with pytest.raises(r.RoomError, match="aufsichten_table"):
        a.check_aufsichten(SimpleNamespace(conn=SimpleNamespace(cursor=lambda: Cur())), tmp_path,
                           {**TABLES, "aufsichten_table": "DEMO_AUFSICHTEN"})
