"""Aufgabenzuweisung an Korrektor:innen: Nummern, Kandidaten, Kind-Prüfung, Menü und Schreiben (Testcursor)."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from mao_cli import korrektoren_aufgaben as a
from mao_cli.korrektoren import KorrektorenError

TABLES = {"korrektoren_table": "korr", "korrektoren_prefs_table": "prefs", "korrektoren_angebote_table": "ang",
          "korrektoren_angehoerige_table": "angeh", "korrektoren_zuordnung_table": "zu", "students_table": "stud",
          "jahrgaenge_table": "jg", "schuljahre_table": "sj"}

# id, titel, vorname, nachname, aktiv
KORR = [(1, None, "Anna", "Adler", 1), (2, "Dr.", "Bernd", "Bauer", 1), (3, None, "Cora", "Claus", 1), (4, None, "Dirk", "Dorn", 0)]
PREFS = {1: {"kl_5": 1}, 2: {"kl_5": 1, "kl_7": 1}, 3: {"kl_7": 1}, 4: {"kl_5": 1}}
ANGEBOTE = [(7, 1), (6, 1), (5, 1), (7, 2), (7, 3), (6, 3), (7, 4)]  # (jahrgang_id, korrektor_id); aktuell 7
KINDER = [(2, 1001)]
STUDENTS = {1001: "5-1"}


class Cur:
    def __init__(self, zuordnungen=(), count_offset=0):
        self.zu = [list(z) for z in zuordnungen]  # id, korrektor_id, mao_nummer, bemerkung
        self.count_offset = count_offset
        self.sql, self.executed, self.rowcount, self.next = "", [], 1, 100

    def execute(self, sql, params=None):
        self.sql = sql
        self.executed.append((sql, params))
        if sql.startswith("INSERT INTO `zu`"):
            self.next += 1
            self.zu.append([self.next, *params])
        elif sql.startswith("DELETE FROM `zu`"):
            self.zu = [z for z in self.zu if z[0] != params[0]]
        elif sql.startswith("UPDATE `zu`"):
            for z in self.zu:
                if z[0] == params[1]:
                    z[3] = params[0]

    def fetchall(self):
        s = self.sql
        if "GET_LOCK" in s:
            return [(1,)]
        if "FROM `jg`" in s:
            return [(7, 66)]
        if "COUNT(*) FROM `zu`" in s:
            return [(len(self.zu) + self.count_offset,)]
        if "FROM `zu`" in s:
            return [tuple(z) for z in self.zu]
        if "FROM `korr`" in s:
            return KORR
        if "FROM `prefs`" in s:
            return [(k, *(p.get(c, 0) for c in a.STUFEN.values())) for k, p in PREFS.items()]
        if "FROM `ang`" in s:
            return ANGEBOTE
        if "FROM `angeh`" in s:
            return KINDER
        if "FROM `stud`" in s:
            return list(STUDENTS.items())
        if "SHOW COLUMNS" in s:
            return [("bemerkung", "varchar(20)")]
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


def session(cur=None):
    cur = cur or Cur()
    return a.AufgabenSession(SimpleNamespace(conn=Conn(cur)), TABLES), cur


def test_number_parsing_and_validation():
    assert a.make_nummer(66, 5, 1) == 660521 and a.make_nummer(66, 12, 4) == 661224
    assert a.split_nummer(660521) == (66, 5, 2, 1)
    assert a.parse_nummer("660521", 66) == a.parse_nummer("0521", 66) == a.parse_nummer("5-1", 66) == 660521
    assert a.parse_nummer("12-4", 66) == 661224
    for bad in ("abc", "650521", "661121", "660525", "660511"):
        with pytest.raises(KorrektorenError):
            a.parse_nummer(bad, 66)


def test_kind_class_matching():
    assert a.kind_matches(5, "5-1") and a.kind_matches(5, "05b") and not a.kind_matches(5, "6-1")
    assert a.kind_matches(12, "11") and a.kind_matches(12, "12-3") and not a.kind_matches(12, "10-1")
    assert not a.kind_matches(5, None)


def test_candidates_show_offers_takeovers_and_child_flag():
    s, _ = session(Cur([(1, 1, 650521, None), (2, 3, 640721, None)]))
    cands = {c.korrektor_id: c for c in s.work.candidates(660521)}
    assert set(cands) == {1, 2}  # 3 bietet Stufe 5 nicht an, 4 ist inaktiv
    assert cands[1].angebote == 3 and cands[1].uebernommen == 1
    assert cands[2].kinder_in_stufe == ["5-1"] and cands[1].kinder_in_stufe == []
    only7 = {c.korrektor_id for c in s.work.candidates(660721)}
    assert only7 == {2, 3}
    assert cands[2].uebernommen == 0 and {c.korrektor_id: c.angebote for c in s.work.candidates(660721)}[3] == 2
    assert 3 in {c.korrektor_id for c in s.work.candidates(660521, alle=True)}


def test_menu_edit_warns_for_child_and_dry_run_writes_nothing():
    s, cur = session()
    answers = iter(["3", "5-1", "1 2", "n", "", "4", "q"])  # Kandidat 2 hat Kind -> Nein
    out: list[str] = []
    prompts: list[str] = []

    def ask(prompt):
        prompts.append(prompt)
        return next(answers)

    a.run_menu(s, apply=False, ask=ask, out=out.append)
    assert s.work.assigned(660521) == [1]
    assert any("Kind in Klassenstufe 5 (5-1)" in p for p in prompts)
    assert any("DRY-RUN" in line for line in out)
    assert not [q for q, _ in cur.executed if q.startswith(("INSERT", "DELETE", "UPDATE"))]


def test_menu_apply_writes_in_transaction_with_remark():
    s, cur = session()
    answers = iter(["3", "5-1", "1 2", "j", "b 1 Überblick", "", "4", "j", "q"])
    out: list[str] = []
    written = a.run_menu(s, apply=True, ask=lambda _p: next(answers), out=out.append)
    assert written == {"neu": 2, "entfernt": 0, "bemerkung": 0}
    assert sorted((z[1], z[2], z[3]) for z in cur.zu) == [(1, 660521, "Überblick"), (2, 660521, None)]
    assert s.db.conn.commits == 1 and s.db.conn.rollbacks == 0
    assert any("GET_LOCK" in q for q, _ in cur.executed) and any("RELEASE_LOCK" in q for q, _ in cur.executed)


def test_remove_and_remark_change_are_written():
    s, cur = session(Cur([(1, 1, 660521, "alt"), (2, 2, 660521, None)]))
    s.work.set(2, 660521, False)
    s.work.set_remark(1, 660521, "neu")
    assert s.work.diff() == {"neu": [], "entfernt": [(2, 660521)], "bemerkung": [(1, 660521)]}
    assert s.write() == {"neu": 0, "entfernt": 1, "bemerkung": 1}
    assert [(z[1], z[3]) for z in cur.zu] == [(1, "neu")]
    assert s.work.pending() == 0


def test_remark_too_long_and_without_assignment_refused():
    s, _ = session()
    with pytest.raises(KorrektorenError):
        s.work.set_remark(1, 660521, "x")
    s.work.set(1, 660521, True)
    with pytest.raises(KorrektorenError):
        s.work.set_remark(1, 660521, "x" * 21)


def test_count_mismatch_rolls_back():
    s, cur = session(Cur(count_offset=0))
    s.work.set(1, 660521, True)
    cur.count_offset = 5  # fremde Änderung: Zählung weicht ab
    with pytest.raises(KorrektorenError):
        s.write()
    assert s.db.conn.rollbacks == 1 and s.db.conn.commits == 0


def test_other_years_are_not_touched_and_demo_table_refused():
    s, cur = session(Cur([(1, 1, 650521, None)]))
    assert s.work.diff() == {"neu": [], "entfernt": [], "bemerkung": []}
    assert s.work.rows == {}
    with pytest.raises(KorrektorenError):
        a.AufgabenSession(SimpleNamespace(conn=Conn(Cur())), {**TABLES, "korrektoren_zuordnung_table": "DEMO_X"})


def test_overview_and_no_menu_output_are_read_only():
    s, cur = session(Cur([(1, 1, 660521, None)]))
    lines = a.overview_lines(s.work)
    assert any("660521" in x and "Adler, A." in x for x in lines)
    out: list[str] = []
    assert a.run_korrektoren_aufgaben(SimpleNamespace(conn=Conn(cur)), TABLES, apply=True, menu=False, out=out.append) == {}
    assert any("Angebote" in x and "Übernommen" in x for x in out)
    assert not [q for q, _ in cur.executed if q.startswith(("INSERT", "DELETE", "UPDATE"))]


def keys(*names):
    it = iter(names)
    return lambda: next(it)


def test_interactive_table_arrow_keys_assign_and_child_confirmation():
    s, _ = session()
    screens: list[list[str]] = []
    # Aufgabe 5-1 öffnen, Kandidat 1 zuordnen, runter, Kandidat 2 (Kind) mit n ablehnen, dann mit j zuordnen, zurück, beenden
    k = keys("enter", "space", "down", "space", "n", "space", "j", "q", "q")
    assert a.interactive_table(s.work, key=k, draw=lambda lines, row: screens.append(lines)) is False
    assert s.work.assigned(660521) == [1, 2]
    assert any("Kind in Klassenstufe 5 (5-1)" in x for lines in screens for x in lines)
    assert screens[0][0].startswith("Aufgabenzuweisung Jahrgang 66")


def test_interactive_navigation_and_switch_to_write():
    s, _ = session()
    drawn: list[tuple[list[str], int | None]] = []
    k = keys("down", "down", "right", "left", "up", "enter", "right", "q", "s")
    assert a.interactive_table(s.work, key=k, draw=lambda lines, row: drawn.append((lines, row))) is True
    assert drawn[1][1] == 5 and drawn[2][1] == 6  # Cursor wandert mit
    assert any("Aufgabe 660522" in x or "Aufgabe 660521" in x for lines, _ in drawn for x in lines)


def test_interactive_remark_and_error_message():
    s, _ = session()
    screens: list[list[str]] = []
    answers = iter(["zu lang " * 5, "Kurz"])
    k = keys("enter", "space", "b", "b", "q", "q")
    a.interactive_table(s.work, key=k, draw=lambda lines, row: screens.append(lines), ask=lambda _p: next(answers))
    assert any("zu lang" in x for lines in screens for x in lines if x.startswith("Nicht möglich"))
    assert s.work.want[(1, 660521)] == "Kurz"


def test_menu_item_one_opens_table_and_s_leads_to_write():
    s, cur = session()
    answers = iter(["1", "j", "q"])
    out: list[str] = []
    k = keys("enter", "space", "q", "s")
    written = a.run_menu(s, apply=True, ask=lambda _p: next(answers), out=out.append, key=k, draw=lambda *_: None)
    assert written == {"neu": 1, "entfernt": 0, "bemerkung": 0} and [z[1:3] for z in cur.zu] == [[1, 660521]]


def test_offers_must_be_from_current_jahrgang_and_expected_nr_is_checked():
    s, _ = session()
    # Korrektor 3 hat nur für Jahrgang 6 (alt) und 7 Angebote; Korrektor 4 ist inaktiv; Korrektor ohne aktuelles Angebot erscheint nie
    assert 5 not in {c.korrektor_id for c in s.work.candidates(660521, alle=True)}
    with pytest.raises(KorrektorenError):
        a.AufgabenSession(SimpleNamespace(conn=Conn(Cur())), TABLES, expected_nr=65)
    assert a.AufgabenSession(SimpleNamespace(conn=Conn(Cur())), TABLES, expected_nr=66).work.state.jahrgang_nr == 66
    # nur frühere Angebote -> kein Kandidat; zugeordnet ohne aktuelles Angebot wird markiert


def test_assigned_without_current_offer_is_flagged(monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "ANGEBOTE", [(6, 1), (7, 2)])
    s, _ = session(Cur([(1, 1, 660521, None)]))
    cand = {c.korrektor_id: c for c in s.work.candidates(660521)}
    assert cand[1].angebot_aktuell is False and cand[1].angebote == 1
    assert any("KEIN Angebot 66" in x for x in a.candidate_lines(s.work, 660521))
    assert 3 not in cand  # nur frühere Angebote -> kein Kandidat


def many(monkeypatch, n=14):
    mod = sys.modules[__name__]
    monkeypatch.setattr(mod, "KORR", [(i, None, f"V{i}", f"Name{i:02d}", 1) for i in range(1, n + 1)])
    monkeypatch.setattr(mod, "PREFS", {i: {"kl_5": 1} for i in range(1, n + 1)})
    monkeypatch.setattr(mod, "ANGEBOTE", [(7, i) for i in range(1, n + 1)])
    monkeypatch.setattr(mod, "KINDER", [(3, 1001)])


def test_long_list_uses_two_columns_depending_on_terminal_height(monkeypatch):
    many(monkeypatch)
    s, _ = session()
    lines, cursor, count = a.task_screen(s.work, 660521, 0, False, (100, 14))  # 6 Zeilen je Spalte
    assert count == 14 and cursor == -1
    body = lines[3:]
    assert len(body) == 6 and all(len(x.replace(a.INVERSE, "").replace(a.RESET, "")) > a.CELL_WIDTH for x in body)
    assert a.INVERSE in body[0] and "K:5-1" in "\n".join(body) and "Seite 1/2" in lines[2]
    lines2, _, _ = a.task_screen(s.work, 660521, 13, False, (100, 14))  # zweite Seite
    assert "Seite 2/2" in lines2[2] and a.INVERSE in "\n".join(lines2)
    tall, cursor, _ = a.task_screen(s.work, 660521, 2, False, (100, 40))  # hoch genug: eine Spalte
    assert cursor == 6 and a.INVERSE not in "\n".join(tall)
    narrow, cursor, _ = a.task_screen(s.work, 660521, 0, False, (60, 14))  # schmal: eine Spalte mit Seiten
    assert cursor == 4 and "Seite 1/3" in narrow[2]


def test_interactive_cursor_walks_through_columns_and_pages(monkeypatch):
    many(monkeypatch)
    s, _ = session()
    k = keys("enter", *["down"] * 7, "space", "q", "q")  # 8. Kandidat (zweite Spalte)
    a.interactive_table(s.work, key=k, draw=lambda *_: None, size=lambda: (100, 14))
    assert s.work.assigned(660521) == [8]


def test_kind_filter_toggle_hides_but_keeps_assigned_and_confirmation(monkeypatch):
    many(monkeypatch)
    s, _ = session()
    assert len(s.work.candidates(660521)) == 14
    shown = s.work.candidates(660521, ohne_kinder=True)
    assert len(shown) == 13 and 3 not in {c.korrektor_id for c in shown}
    s.work.set(3, 660521, True)  # Zugeordnete mit Kind bleiben sichtbar
    assert 3 in {c.korrektor_id for c in s.work.candidates(660521, ohne_kinder=True)}
    s.work.set(3, 660521, False)
    lines, _, count = a.task_screen(s.work, 660521, 0, False, (100, 40), ohne_kinder=True)
    assert count == 13 and any("1 mit Kind in der Stufe ausgeblendet" in x for x in lines)
    # Taste k blendet aus; Nachfrage bleibt, wenn wieder eingeblendet
    prompts: list[list[str]] = []
    k = keys("enter", "k", "k", "down", "down", "space", "j", "q", "q")  # dritter Kandidat = Korrektor 3 (Kind)
    a.interactive_table(s.work, key=k, draw=lambda lines, row: prompts.append(lines), size=lambda: (100, 40))
    assert s.work.assigned(660521) == [3]
    assert any("Trotzdem zuordnen" in x for lines in prompts for x in lines)


def test_sorting_by_first_or_last_name_with_toggle_key(monkeypatch):
    mod = sys.modules[__name__]
    monkeypatch.setattr(mod, "KORR", [(1, None, "Zoe", "Adler", 1), (2, None, "Anna", "Zorn", 1), (3, None, "Max", "Meier", 1)])
    monkeypatch.setattr(mod, "PREFS", {1: {"kl_5": 1}, 2: {"kl_5": 1}, 3: {"kl_5": 1}})
    monkeypatch.setattr(mod, "ANGEBOTE", [(7, 1), (7, 2), (7, 3)])
    s, _ = session()
    assert [c.korrektor_id for c in s.work.candidates(660521)] == [1, 3, 2]
    assert [c.korrektor_id for c in s.work.candidates(660521, sortierung="vorname")] == [2, 3, 1]
    lines, _, _ = a.task_screen(s.work, 660521, 0, False, (100, 40), sortierung="vorname")
    assert "sortiert nach Vorname" in lines[2] and "Anna Zorn" in lines[4] and "Zoe Adler" in lines[6]
    lines, _, _ = a.task_screen(s.work, 660521, 0, False, (100, 40))
    assert "sortiert nach Nachname" in lines[2] and "Adler, Zoe" in lines[4]
    # Taste n: Cursor bleibt auf derselben Person (Zoe Adler: Platz 1 -> Platz 3), Zuordnung trifft sie
    k = keys("enter", "n", "space", "q", "q")
    a.interactive_table(s.work, key=k, draw=lambda *_: None, size=lambda: (100, 40))
    assert s.work.assigned(660521) == [1]
    k = keys("enter", "down", "n", "space", "q", "q")  # Max Meier bleibt markiert
    s.work.reset()
    a.interactive_table(s.work, key=k, draw=lambda *_: None, size=lambda: (100, 40))
    assert s.work.assigned(660521) == [3]


def test_persons_with_other_task_are_hidden_by_default_and_v_shows_them(monkeypatch):
    many(monkeypatch, 4)
    s, _ = session(Cur([(1, 1, 660522, None), (2, 2, 660521, None)]))
    assert [c.korrektor_id for c in s.work.candidates(660521)] == [1, 2, 3, 4]
    shown = [c.korrektor_id for c in s.work.candidates(660521, ohne_vergebene=True)]
    assert shown == [2, 3, 4]  # 1 hat Aufgabe 660522; 2 ist hier zugeordnet und bleibt
    lines, _, count = a.task_screen(s.work, 660521, 0, False, (100, 40), ohne_vergebene=True)
    assert count == 3 and any("1 mit anderer Aufgabe ausgeblendet" in x for x in lines)
    # Standard in der interaktiven Tabelle: ausgeblendet; v blendet ein
    screens: list[list[str]] = []
    a.interactive_table(s.work, key=keys("enter", "v", "q", "q"), draw=lambda lines, row: screens.append(lines),
                        size=lambda: (100, 40))
    assert not any("V1 Name01" in x or "Name01, V1" in x for x in screens[1])
    assert any("Name01, V1" in x for x in screens[2])
    # Nach dem Zuordnen in der Aufgabe verschwindet die Person aus anderen Aufgaben-Listen
    s.work.set(3, 660521, True)
    assert 3 not in {c.korrektor_id for c in s.work.candidates(660522, ohne_vergebene=True)}


def test_draft_roundtrip_contains_only_ids_and_loads_into_new_session(tmp_path):
    s, _ = session()
    s.work.set(1, 660521, True)
    s.work.set(2, 660522, True)
    s.work.set_remark(2, 660522, "Überblick")
    draft = a.Draft(tmp_path)
    path = draft.save(s.work)
    text = path.read_text(encoding="utf-8")
    assert "Adler" not in text and "Bauer" not in text and "Anna" not in text and "@" not in text
    assert "jahrgang: 66" in text and "korrektor: 1" in text and 'bemerkung: "Überblick"' in text
    s2, _ = session()
    note = a.Draft(tmp_path).load_into(s2.work)
    assert "2 Zuordnungen" in note and "2 neu" in note
    assert s2.work.want == {(1, 660521): None, (2, 660522): "Überblick"}
    assert not draft.path.with_name(draft.path.name + ".tmp").exists()


def test_invalid_drafts_are_refused(tmp_path):
    s, _ = session()
    draft = a.Draft(tmp_path)
    for bad in ("jahrgang: 65\nzuordnungen: {}\n", 'jahrgang: 66\nzuordnungen:\n  "660521":\n    - {korrektor: 99}\n',
                'jahrgang: 66\nzuordnungen:\n  "660921x":\n    - {korrektor: 1}\n',
                'jahrgang: 66\nzuordnungen:\n  "660521":\n    - {korrektor: 1}\n    - {korrektor: 1}\n',
                'jahrgang: 66\nzuordnungen:\n  "660521":\n    - {korrektor: 1, bemerkung: "' + "x" * 21 + '"}\n', "[1, 2"):
        draft.path.write_text(bad, encoding="utf-8")
        with pytest.raises(KorrektorenError):
            draft.load_into(s.work)
    with pytest.raises(KorrektorenError):
        a.Draft(tmp_path, name="../plan.yaml")
    with pytest.raises(KorrektorenError):
        a.Draft(tmp_path, name="plan.txt")
    with pytest.raises(KorrektorenError):
        a.Draft(tmp_path, tmp_path, "plan.yaml")


def test_menu_saves_draft_on_quit_loads_it_next_time_and_apply_clears_it(tmp_path):
    s, cur = session()
    draft = a.Draft(tmp_path)
    answers = iter(["3", "5-1", "1", "", "q", ""])  # zuordnen, beenden, Entwurf speichern (Enter = ja)
    a.run_menu(s, apply=False, ask=lambda _p: next(answers), out=lambda _x: None, draft=draft)
    assert draft.exists() and not [q for q, _ in cur.executed if q.startswith(("INSERT", "DELETE", "UPDATE"))]
    # nächster Start: Entwurf wird geladen; Schreiben mit --apply löscht ihn
    s2, cur2 = session()
    out: list[str] = []
    answers = iter(["4", "j", "q"])
    written = a.run_menu(s2, apply=True, ask=lambda _p: next(answers), out=out.append, draft=a.Draft(tmp_path))
    assert any("Entwurf" in x and "geladen" in x for x in out) and written == {"neu": 1, "entfernt": 0, "bemerkung": 0}
    assert not draft.exists() and [z[1:3] for z in cur2.zu] == [[1, 660521]]


def test_dry_run_write_keeps_draft_and_neu_ignores_it(tmp_path):
    s, _ = session()
    draft = a.Draft(tmp_path)
    s.work.set(1, 660521, True)
    answers = iter(["4", "q"])
    out: list[str] = []
    a.run_menu(s, apply=False, ask=lambda _p: next(answers), out=out.append, draft=draft)
    assert draft.exists() and any("Entwurf gespeichert" in x for x in out)
    s2, _ = session()
    a.run_menu(s2, apply=False, ask=lambda _p: "q", out=lambda _x: None, draft=a.Draft(tmp_path), use_draft=False)
    assert s2.work.want == {}
    # unlesbarer Entwurf: Meldung, Stand der Datenbank, Menü läuft weiter
    draft.path.write_text("jahrgang: 1\n", encoding="utf-8")
    out2: list[str] = []
    s3, _ = session()
    a.run_menu(s3, apply=False, ask=lambda _p: "q", out=out2.append, draft=a.Draft(tmp_path))
    assert any("Entwurf nicht übernommen" in x for x in out2) and s3.work.want == {}


def test_w_key_saves_draft_in_interactive_table(tmp_path):
    s, _ = session()
    draft = a.Draft(tmp_path)
    screens: list[list[str]] = []
    a.interactive_table(s.work, key=keys("enter", "space", "w", "q", "w", "q"), draw=lambda lines, row: screens.append(lines),
                        size=lambda: (100, 40), draft=draft)
    assert draft.exists() and "korrektor: 1" in draft.path.read_text(encoding="utf-8")
    assert any("Entwurf gespeichert" in x for lines in screens for x in lines)


def test_takeover_overview_counts_earlier_years_and_includes_planned_changes():
    s, _ = session(Cur([(1, 1, 650521, None), (2, 1, 640721, None), (3, 1, 660521, None), (4, 3, 660522, None)]))
    s.work.set(2, 660721, True)  # geplant, noch nicht in der Datenbank
    lines = a.takeover_lines(s.work)
    text = "\n".join(lines)
    adler = next(x for x in lines if x.startswith("Adler, Anna"))
    assert "660521" in adler and adler.split()[-3:] == ["2", "3", "3"]  # früher 2 (65, 64), gesamt 3, Angebote 3
    bauer = next(x for x in lines if x.startswith("Bauer, Bernd"))
    assert "660721" in bauer and bauer.split()[-3:] == ["0", "1", "1"]
    assert "Claus, Cora" in text and "davon erstmals: 2" in lines[-1] and "3 Korrektor:innen" in lines[-1]
    assert "Vorname" not in text
    assert [x[:5] for x in lines[2:-1]] == ["Adler", "Claus", "Bauer"]  # nach Aufgabennummer: 660521, 660522, 660721
    assert [x[:5] for x in a.takeover_lines(s.work, "nachname")[2:-1]] == ["Adler", "Bauer", "Claus"]
    by_first = a.takeover_lines(s.work, "vorname")
    assert by_first[2].startswith("Anna Adler") and by_first[3].startswith("Bernd Bauer")  # alphabetisch nach Vorname
    assert "Noch keine" in a.takeover_lines(session()[0].work)[-1]


def test_takeover_overview_in_menu_grid_and_without_menu():
    s, _ = session(Cur([(1, 1, 660521, None)]))
    out: list[str] = []
    a.run_menu(s, apply=False, ask=keys_ask("7", "q"), out=out.append)
    assert any("Adler, Anna" in x for x in out)
    shown: list[list[str]] = []
    a.interactive_table(s.work, key=keys("u", "x", "q"), draw=lambda lines, row: shown.append(lines), size=lambda: (100, 40))
    assert any(x.startswith("Übernommene Korrekturen") for lines in shown for x in lines)
    out2: list[str] = []
    a.run_korrektoren_aufgaben(SimpleNamespace(conn=Conn(Cur([(1, 1, 660521, None)]))), TABLES, apply=False, menu=False, out=out2.append)
    assert any(x.startswith("Übernommene Korrekturen") for x in out2)


def keys_ask(*answers):
    it = iter(answers)
    return lambda _p: next(it)
