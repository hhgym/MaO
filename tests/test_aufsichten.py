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
        if "SELECT DISTINCT raum_id, COALESCE" in s:
            return getattr(self, "levels", [(1, 5), (1, 6)])
        if "SELECT DISTINCT raum_id, klassenstufe" in s:
            return getattr(self, "ext_levels", [(2, 4)])
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
    assert any(x.startswith("R1") and " K " in x for x in text.splitlines()) and "X1" in text and "Fachaufsicht: -" in text and "schon verplant (2): AB (R1); CD (X1)" in text and result["eintraege"] == 0


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


from mao_cli import aufsichten_tui as tui  # noqa: E402


def make_model(tmp_path, **kw):
    (tmp_path / "raumregeln.yaml").write_text('rooms:\n  grade_rooms: {"5": [R1]}\n', encoding="utf-8")
    cur = kw.pop("cur", None) or SCur(extern=((2, 12),), sups=((1, 1, "Standard"), (2, 2, "Standard")))
    db = sdb(cur)
    return db, cur, a.load_supervision_model(db, tmp_path, TABLES_IA, tmp_path)


def drive(model, db, keys, apply=False):
    frames, seq = [], iter(keys)
    result = tui.run_tui(model, db, TABLES_IA, apply=apply, read=lambda: next(seq), write=frames.append, size=lambda: (30, 100))
    return result, frames


def typed(text):
    return ["space" if c == " " else c for c in text]


def test_model_edit_swap_undo_and_proposal(tmp_path):
    _db, _cur, m = make_model(tmp_path)
    assert m.rows() == ["R1", "X1", "fachaufsicht", "reserve"] and not m.changed
    assert m.edit("R1", ["zz"]).startswith("Unbekannte") and m.edit("R1", ["ab", "AB"]) == "Kürzel doppelt"
    assert m.edit("X1", ["+AB"]).startswith("Hinweis") and m.team("X1") == ["CD", "AB"]
    assert m.swap_rows("R1", "X1") == "" and m.team("R1") == ["CD", "AB"] and m.swap_rows("R1", "R1")
    assert m.swap_people("AB", "CD") == "" and m.edit("fachaufsicht", ["CD"]) == "" and m.changed
    assert m.swap_people("AB", "EF").startswith("Beide")
    while m.undo():
        pass
    assert not m.changed and m.history_text("AB").startswith("1×") and m.history_text("CD") == "noch nie"
    m.adopt(m.proposal())
    assert m.team("R1") == ["CD"] and m.team("X1") == ["AB"] and m.gap("R1") is False


def test_tui_cursor_edit_complete_and_dry_run(tmp_path):
    db, cur, m = make_model(tmp_path)
    keys = ["down", "enter"] + ["backspace"] * 2 + typed("a") + ["tab", "enter", "s", "q", "j"]
    result, frames = drive(m, db, keys)
    assert m.team("X1") == ["AB"] and not cur.writes and not result["geschrieben"]
    text = "".join(frames)
    assert "Änderungen: +1 −1" in text and "DRY-RUN" in text and "\x1b[7m" in text and "K = Kurzraum" in text


def test_tui_apply_swap_rotation_and_confirm(tmp_path):
    db, cur, m = make_model(tmp_path)
    keys = ["space", "down", "space", "s", "j"]  # R1 <-> X1 tauschen, schreiben
    result, _ = drive(m, db, keys, apply=True)
    assert result["geschrieben"] and [w[0].split()[0] for w in cur.writes] == ["DELETE", "DELETE", "INSERT", "INSERT"]
    db, cur, m = make_model(tmp_path)
    result, frames = drive(m, db, ["r", "n", "r", "j", "s", "n", "q", "j"], apply=True)
    assert m.team("R1") == ["CD"] and not cur.writes and not result["geschrieben"] and "noch nie" in "".join(frames)


def test_tui_quit_asks_when_changed_and_errors_stay_in_edit(tmp_path):
    db, cur, m = make_model(tmp_path)
    result, frames = drive(m, db, ["enter"] + typed(" zz") + ["enter", "esc", "q"])
    assert "Unbekannte Kürzel: ZZ" in "".join(frames) and not m.changed and result["eintraege"] == 0
    db, cur, m = make_model(tmp_path)
    drive(m, db, ["enter", "backspace", "backspace", "backspace", "enter", "q", "n", "l", "L", "u", "u", "q"])
    assert m.team("R1") == ["AB"]


def test_tui_scrolls_marks_and_unmarks(tmp_path):
    db, cur, m = make_model(tmp_path)
    for n in range(40):
        m.state["raeume"][f"Z{n:02d}"] = []
    result, frames = drive(m, db, ["end", "pgup", "home", "pgdn", "up", "space", "space", "q", "j"])
    assert "Reserve" in frames[1] and "\x1b[H" in frames[0]


@pytest.mark.skipif(__import__("os").name == "nt", reason="select() auf Pipes gibt es nur unter POSIX")
def test_read_key_posix_maps_escape_sequences():
    import os
    r, w = os.pipe()
    try:
        os.write(w, b"\x1b[A\x1b[3~\xc3\xa4a\x7f\r\x1b")
        got = [tui.read_key_posix(r) for _ in range(6)]
    finally:
        os.close(r)
        os.close(w)
    assert got == ["up", "delete", "ä", "a", "backspace", "enter"]


def test_tui_supervision_needs_terminal(tmp_path):
    db, cur, m = make_model(tmp_path)
    with pytest.raises(r.RoomError, match="Terminal"):
        tui.tui_supervision(m, db, TABLES_IA, apply=False)


def test_overview_shows_who_is_not_yet_placed_in_this_session(tmp_path):
    db, _cur, m = make_model(tmp_path)
    assert m.overview() == ([], ["AB (R1)"], "Mathematik-Lehrkräfte")
    m.edit("R1", ["-"])
    m.edit("fachaufsicht", ["CD"])
    assert m.overview() == (["AB Alpha"], [], "Mathematik-Lehrkräfte")
    assert m.overview(True) == (["AB Alpha"], ["CD (X1, Fach)"], "alle Lehrkräfte")
    _result, frames = drive(m, db, ["l", "L", "q", "j"])
    text = "".join(frames)
    assert "Noch nicht verplant" in text and "AB Alpha" in text and "CD (X1, Fach)" in text


def test_levels_are_shown_in_both_views(tmp_path):
    db, _cur, m = make_model(tmp_path)
    assert m.level_text("R1") == "5, 6" and m.level_text("X1") == "E4" and m.level_text("reserve") == ""
    _r, frames = drive(m, db, ["q"])
    assert any("Stufen" in f and "5, 6" in f and "E4" in f for f in frames)
    _c, _res, text = session(tmp_path, ["q"])
    assert "Stufen" in text and "5, 6" in text and "E4" in text


def test_draft_roundtrip_load_and_clear(tmp_path):
    db, _cur, m = make_model(tmp_path)
    draft = a.Draft(tmp_path, tmp_path / "cfg")
    assert draft.load_into(m) == "" and not m.changed
    m.edit("R1", ["CD"])
    m.edit("reserve", ["AB", "CD"])
    assert m.changed
    path = draft.save(m)
    assert path.name == "aufsichten-plan.yaml" and not m.changed and not list(tmp_path.glob("*.tmp"))
    text = path.read_text(encoding="utf-8")
    assert "# 15 Teilnehmende, Stufe 5, 6" in text and "reserve: [AB, CD]" in text
    _db2, _cur2, fresh = make_model(tmp_path)
    assert fresh.team("R1") == ["AB"]
    assert draft.load_into(fresh).startswith("Entwurf aufsichten-plan.yaml geladen")
    assert fresh.team("R1") == ["CD"] and fresh.state["reserve"] == ["AB", "CD"] and not fresh.changed
    path.write_text('raeume:\n  "R1": [ZZ]\n', encoding="utf-8")
    with pytest.raises(r.RoomError, match="unbekannte Kürzel ZZ"):
        draft.load_into(fresh)
    path.write_text('raeume:\n  "NOPE": [AB]\nfoo: 1\n', encoding="utf-8")
    with pytest.raises(r.RoomError, match="erlaubt sind nur"):
        draft.load_into(fresh)
    path.write_text('raeume:\n  "NOPE": [AB]\n', encoding="utf-8")
    assert "NOPE" in draft.load_into(fresh)
    assert draft.clear() and not draft.clear()


def test_tui_dry_run_saves_draft_and_apply_clears_it(tmp_path):
    db, cur, m = make_model(tmp_path)
    draft = a.Draft(tmp_path, tmp_path / "cfg")
    frames: list[str] = []
    seq = iter(["enter", "backspace", "backspace", "backspace", "enter", "s", "q"])
    tui.run_tui(m, db, TABLES_IA, apply=False, read=lambda: next(seq), write=frames.append, size=lambda: (30, 120), draft=draft)
    assert draft.path.exists() and "Entwurf gespeichert" in "".join(frames) and not cur.writes
    db2, cur2, m2 = make_model(tmp_path)
    draft.load_into(m2)
    assert m2.team("R1") == []
    seq = iter(["s", "j"])
    result = tui.run_tui(m2, db2, TABLES_IA, apply=True, read=lambda: next(seq), write=lambda _t: None, size=lambda: (30, 120), draft=draft)
    assert result["geschrieben"] and result["entwurf_geloescht"] and not draft.path.exists()


def test_tui_quit_offers_save_or_discard_of_draft(tmp_path):
    db, _cur, m = make_model(tmp_path)
    draft = a.Draft(tmp_path, tmp_path / "cfg")
    seq = iter(["enter", "backspace", "backspace", "backspace", "enter", "w", "q", "esc", "q", "v"])
    tui.run_tui(m, db, TABLES_IA, apply=False, read=lambda: next(seq), write=lambda _t: None, size=lambda: (30, 120), draft=draft)
    assert draft.path.exists()  # w hat gespeichert, q danach ohne neue Änderung beendet direkt
    db, _cur, m = make_model(tmp_path)
    draft2 = a.Draft(tmp_path, tmp_path / "cfg", Path("zwei.yaml"))
    seq = iter(["enter", "backspace", "backspace", "backspace", "enter", "q", "j"])
    result = tui.run_tui(m, db, TABLES_IA, apply=False, read=lambda: next(seq), write=lambda _t: None, size=lambda: (30, 120), draft=draft2)
    assert draft2.path.exists() and "Entwurf gespeichert" in result["entwurf"]
    db, _cur, m = make_model(tmp_path)
    draft3 = a.Draft(tmp_path, tmp_path / "cfg", Path("drei.yaml"))
    seq = iter(["enter", "backspace", "backspace", "backspace", "enter", "q", "v"])
    tui.run_tui(m, db, TABLES_IA, apply=False, read=lambda: next(seq), write=lambda _t: None, size=lambda: (30, 120), draft=draft3)
    assert not draft3.path.exists()


def test_line_mode_saves_draft_on_dry_run_fertig_and_w(tmp_path):
    (tmp_path / "raumregeln.yaml").write_text('rooms:\n  grade_rooms: {"5": [R1]}\n', encoding="utf-8")
    db, cur, m = make_model(tmp_path)
    draft = a.Draft(tmp_path, tmp_path / "cfg")
    lines, answers = [], iter(["R1 -", "fertig"])
    a.interactive_supervision(db, tmp_path, TABLES_IA, apply=False, workspace=tmp_path, ask=lambda _p: next(answers),
                              out=lines.append, draft=draft, model=m)
    assert draft.path.exists() and any("Entwurf gespeichert" in x for x in lines)
    draft.clear()
    db, cur, m = make_model(tmp_path)
    lines, answers = [], iter(["R1 -", "w", "R1 AB", "q", "n"])
    a.interactive_supervision(db, tmp_path, TABLES_IA, apply=False, workspace=tmp_path, ask=lambda _p: next(answers),
                              out=lines.append, draft=draft, model=m)
    assert draft.path.exists() and '"R1": []' in draft.path.read_text(encoding="utf-8")
