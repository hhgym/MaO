"""Aufgabenzuweisung an Korrektor:innen: Nummern, Kandidaten, Kind-Prüfung, Menü und Schreiben (Testcursor)."""
from __future__ import annotations

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
    answers = iter(["2", "5-1", "1 2", "n", "", "3", "q"])  # Kandidat 2 hat Kind -> Nein
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
    answers = iter(["2", "5-1", "1 2", "j", "b 1 Überblick", "", "3", "j", "q"])
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
