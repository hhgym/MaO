"""Aufsichtenprüfung: reine Prüflogik und Datenbankschicht mit Testcursor."""
from __future__ import annotations

from datetime import date
from pathlib import Path
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


class SCur(Cur):
    def fetchall(self):
        s = self.sql
        if "GET_LOCK" in s or "RELEASE_LOCK" in s:
            return [(1,)]
        if "SELECT id, kuerzel, aktiv" in s:
            return [(1, "AB", 1), (2, "CD", 1), (3, "EF", 0)]
        if "MAX(COALESCE" in s:
            return getattr(self, "short", [(1, 5)])
        if "SELECT a.lehrkraft_id, j.jahrgang_nr" in s:
            return getattr(self, "hist", [(1, 7)])
        if "SELECT l.kuerzel" in s:
            return [("AB", "Anna", "Alpha", 1), ("CD", "Carl", "Beta", 0)]
        if "SELECT kuerzel, vorname" in s:
            return [("AB", "Anna", "Alpha"), ("CD", "Carl", "Beta")]
        if "SELECT raum_id, rolle" in s:
            return [(r_, ro) for r_, _lk, ro in self.sups]
        return super().fetchall()
    def execute(self, sql, params=None):
        super().execute(sql, params)
        if sql.startswith(("INSERT", "DELETE")):
            self.writes[-1] = (sql, params)


def sdb(cur):
    return SimpleNamespace(conn=SimpleNamespace(cursor=lambda: cur, in_transaction=False, start_transaction=lambda: None,
                                                commit=lambda: None, rollback=lambda: None))


def test_set_supervision_dry_run_apply_and_guards():
    cur = SCur(sups=())
    stats = a.set_supervision(sdb(cur), TABLES, room="R1", kuerzel=["ab", "cd"])
    assert stats["betroffen"] == ["AB", "CD"] and not cur.writes
    cur = SCur(sups=())
    a.set_supervision(sdb(cur), TABLES, room="R1", kuerzel=["AB"], dry_run=False)
    assert cur.writes[0][1] == (7, 1, 1, "Standard")
    cur = SCur(sups=((1, 1, "Standard"),))
    stats = a.set_supervision(sdb(cur), TABLES, room="R1", kuerzel=["AB"], dry_run=False)
    assert stats["bereits_so"] == ["AB"] and not cur.writes
    with pytest.raises(r.RoomError, match="Unbekannte"):
        a.set_supervision(sdb(SCur()), TABLES, room="R1", kuerzel=["ZZ"])
    with pytest.raises(r.RoomError, match="nicht aktiv"):
        a.set_supervision(sdb(SCur()), TABLES, room="R1", kuerzel=["EF"])
    with pytest.raises(r.RoomError, match="Raum"):
        a.set_supervision(sdb(SCur()), TABLES, room="NOPE", kuerzel=["AB"])
    with pytest.raises(r.RoomError, match="keinem Raum"):
        a.set_supervision(sdb(SCur()), TABLES, room="R1", kuerzel=["AB"], role="Reserve")
    with pytest.raises(r.RoomError, match="Raum nötig"):
        a.set_supervision(sdb(SCur()), TABLES, room=None, kuerzel=["AB"])


def test_set_supervision_roomless_remove_and_double_booking_warning():
    cur = SCur(sups=())
    a.set_supervision(sdb(cur), TABLES, room=None, kuerzel=["CD"], role="Fachaufsicht", dry_run=False)
    assert cur.writes[0][1] == (7, None, 2, "Fachaufsicht")
    cur = SCur(sups=((1, 1, "Standard"),))
    stats = a.set_supervision(sdb(cur), TABLES, room="X1", kuerzel=["AB"])
    assert stats["hinweise"] and "R1" in stats["hinweise"][0]
    cur = SCur(sups=((1, 1, "Standard"),))
    a.set_supervision(sdb(cur), TABLES, room="R1", kuerzel=["AB"], remove=True, dry_run=False)
    assert cur.writes[0][0].startswith("DELETE") and cur.writes[0][1] == (7, 1, "Standard", 1)
    with pytest.raises(r.RoomError, match="Nichts zu entfernen"):
        a.set_supervision(sdb(SCur(sups=())), TABLES, room="R1", kuerzel=["AB"], remove=True)


def pdb(cur):
    return sdb(cur)


def test_export_plan_writes_editable_file_and_never_overwrites(tmp_path):
    cur = SCur(extern=((2, 12),), sups=((1, 1, "Standard"), (None, 2, "Fachaufsicht")))
    path = a.export_plan(pdb(cur), tmp_path / "cfg", TABLES, tmp_path)
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["raeume"] == {"R1": ["AB"], "X1": []} and data["fachaufsicht"] == ["CD"] and data["reserve"] == []
    with pytest.raises(r.RoomError, match="existiert bereits"):
        a.export_plan(pdb(cur), tmp_path / "cfg", TABLES, tmp_path)
    with pytest.raises(r.RoomError, match="Endung"):
        a.export_plan(pdb(cur), tmp_path / "cfg", TABLES, tmp_path, Path("x.txt"))
    with pytest.raises(r.RoomError, match="Workspace"):
        a.export_plan(pdb(cur), tmp_path / "cfg", TABLES, tmp_path, Path("../x.yaml"))


def write_plan(tmp_path, text):
    (tmp_path / "plan.yaml").write_text(text, encoding="utf-8")
    return Path("plan.yaml")


def test_apply_plan_diff_swap_and_single_transaction(tmp_path):
    plan = write_plan(tmp_path, 'raeume:\n  R1: [CD]\n  X1: [AB]\nfachaufsicht: [AB]\n')
    cur = SCur(sups=((1, 1, "Standard"),))
    stats = a.apply_plan(pdb(cur), tmp_path / "cfg", TABLES, tmp_path, plan, dry_run=True)
    assert stats["hinzu"] == ["Fachaufsicht ohne Raum: AB", "Standard R1: CD", "Standard X1: AB"]
    assert stats["entfernt"] == ["Standard R1: AB"] and not cur.writes
    cur = SCur(sups=((1, 1, "Standard"),))
    a.apply_plan(pdb(cur), tmp_path / "cfg", TABLES, tmp_path, plan, dry_run=False)
    kinds_ = [w[0].split()[0] for w in cur.writes]
    assert kinds_ == ["DELETE", "INSERT", "INSERT", "INSERT"]


def test_apply_plan_scope_validation_and_warnings(tmp_path):
    plan = write_plan(tmp_path, "raeume:\n  R1: [AB]\n")
    cur = SCur(sups=((2, 2, "Standard"), (None, 1, "Reserve")))
    stats = a.apply_plan(pdb(cur), tmp_path / "cfg", TABLES, tmp_path, plan, dry_run=True)
    assert stats["entfernt"] == [] and stats["hinzu"] == ["Standard R1: AB"]  # X1 und Reserve nicht im Plan: unverändert
    for text, msg in (("raeume:\n  ZZ: [AB]\n", "Raum ZZ"), ("raeume:\n  R1: [QQ]\n", "Unbekanntes"), ("raeume:\n  R1: [EF]\n", "nicht aktiv"),
                      ("raeume:\n  R1: [AB, ab]\n", "doppelt"), ("foo: 1\n", "erlaubt"), ("raeume: 5\n", "Zuordnung")):
        with pytest.raises(r.RoomError, match=msg):
            a.apply_plan(pdb(SCur()), tmp_path / "cfg", TABLES, tmp_path, write_plan(tmp_path, text), dry_run=True)
    stats = a.apply_plan(pdb(SCur(sups=())), tmp_path / "cfg", TABLES, tmp_path,
                         write_plan(tmp_path, "raeume:\n  R1: [AB]\n  X1: [AB]\n"), dry_run=True)
    assert stats["hinweise"] and "mehreren Räumen" in stats["hinweise"][0]


def test_rotation_proposal_swaps_least_recent_into_short_room():
    rooms = {"R1": ["AB"], "R2": ["CD"], "R3": ["EF"]}
    prop = a.rotation_proposal(["R1"], rooms, {"AB": [5, 7], "EF": [3]})
    assert prop["rang"] == ["CD", "EF", "AB"]
    assert prop["raeume"] == {"R1": ["CD"], "R2": ["AB"], "R3": ["EF"]}
    assert prop["wechsel"] == ["CD: R2 → Kurzraum, AB: Kurzraum → R2"]
    keep = a.rotation_proposal(["R1"], {"R1": ["CD"], "R2": ["AB"]}, {"AB": [7]})
    assert keep["raeume"] == {"R1": ["CD"], "R2": ["AB"]} and "bleibt" in keep["wechsel"][0]
    assert a.rotation_proposal(["R1", "R2"], {"R1": ["AB"]}, {})["hinweise"]


TABLES_IA = {**TABLES, "archive_table": "arch", "teacher_subjects_table": "lf", "subjects_table": "fa"}


def session(tmp_path, inputs, apply=False, cur=None, tables=None):
    (tmp_path / "raumregeln.yaml").write_text('rooms:\n  grade_rooms: {"5": [R1]}\n', encoding="utf-8")
    cur = cur or SCur(extern=((2, 12),), sups=((1, 1, "Standard"), (2, 2, "Standard")))
    lines, answers = [], iter(inputs)
    result = a.interactive_supervision(sdb(cur), tmp_path, tables or TABLES_IA, apply=apply, workspace=tmp_path,
                                       ask=lambda _p: next(answers), out=lines.append)
    return cur, result, "\n".join(lines)


def test_session_shows_table_with_short_room_and_math_list(tmp_path):
    _cur, result, text = session(tmp_path, ["l", "la", "q"])
    assert any(x.startswith("R1") and " K " in x for x in text.splitlines()) and "X1" in text and "Fachaufsicht: -" in text and "Alpha" in text and result["eintraege"] == 0


def test_session_kurz_proposal_and_apply_in_one_go(tmp_path):
    cur, result, text = session(tmp_path, ["kurz", "kurz ok", "fertig"])
    assert "noch nie" in text and result["eintraege"] == 4 and not cur.writes
    cur, result, _ = session(tmp_path, ["kurz ok", "fertig", "n", "q"], apply=True)
    assert not cur.writes
    cur, result, _ = session(tmp_path, ["kurz ok", "fertig", "j"], apply=True)
    assert [w[0].split()[0] for w in cur.writes] == ["DELETE", "DELETE", "INSERT", "INSERT"]


def test_session_edit_swap_undo_and_errors(tmp_path):
    _cur, result, text = session(tmp_path, ["R1 AB CD", "R1 -CD", "tausch R1 X1", "tausch AB CD", "u", "u", "u",
                                           "X1 ZZ", "fach EF", "fach MEI", "qqq", "fertig", "q"])
    assert "Unbekannte Kürzel: ZZ" in text and "Unbekannter Befehl" in text and "Kürzel doppelt" not in text
    assert "ist auch in einem anderen Raum" in text
    assert "nicht aktiv" in text  # EF ist inaktiv: fertig meldet es, nichts wird geschrieben
    assert result["eintraege"] == 0


def test_session_without_rooms_or_history_still_works(tmp_path):
    cur = SCur(sups=())
    cur.short, cur.hist = [], []
    _cur, result, text = session(tmp_path, ["kurz", "R1 AB", "fach CD", "reserve AB", "fertig"], cur=cur,
                                 tables={**TABLES, "archive_table": "DEMO_ARCHIVE"})
    assert "Keine Kurzräume" in text and result["eintraege"] == 3
