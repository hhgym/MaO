"""Einsatz der Oberstufe: Verteilung, Entwurf, Konsolensitzung und Schreiben."""
from __future__ import annotations

from pathlib import Path

import pytest

from mao_cli import einsatz as e

TABLES = {"students_table": "STUD", "orga_table": "ORGA", "oberstufe_einsaetze_table": "EINS",
          "oberstufe_rollen_table": "ROLLEN", "rooms_table": "RAUM", "extern_teilnahmen_table": "EXT",
          "jahrgaenge_table": "JG", "schuljahre_table": "SJ"}


def person(n: int, stufe: int = 11, participates: bool = False, wish: str | None = None) -> e.Person:
    return e.Person(n, f"Nach{n}", f"Vor{n}", str(stufe), stufe, participates, wish)


def free_people(count: int, wish: str | None = None, start: int = 100) -> list[e.Person]:
    return [person(start + i, wish=wish) for i in range(count)]


def test_roles_for_participants_and_grade_12():
    persons = [person(1, 11, True), person(2, 12, True), person(3, 12), person(4, 12)]
    assign = e.build_assignment(persons, ["101"], e.Settings())
    assert assign == {1: "teilnahme", 2: "teilnahme", 3: "5pk", 4: "5pk"}


def test_wishes_are_met_when_capacity_allows():
    persons = free_people(2, "Aufsicht", 100) + free_people(3, "Hof", 200)
    settings = e.Settings(aufsichten_pro_raum=1, reserve=0, hof_gruppen=2, seed=3)
    assign = e.build_assignment(persons, ["101", "102"], settings)
    assert {assign[100], assign[101]} == {"aufsicht:101", "aufsicht:102"}
    assert all(assign[m].startswith("hof:") for m in (200, 201, 202))
    sizes = sorted([sum(1 for t in assign.values() if t == f"hof:{n}") for n in (1, 2)])
    assert sizes == [1, 2]                      # gleichmäßig auf die Gruppen verteilt


def test_surplus_wishes_are_drawn_and_rest_goes_on():
    persons = free_people(5, "Aufsicht")
    settings = e.Settings(aufsichten_pro_raum=1, reserve=1, hof_gruppen=1, hof_pro_gruppe=1, seed=7)
    assign = e.build_assignment(persons, ["101", "102"], settings)
    targets = sorted(assign.values())
    assert targets.count("aufsicht:101") == targets.count("aufsicht:102") == targets.count("reserve") == 1
    assert targets.count("hof:1") == 2 and "5pk" not in targets   # Überhang: Hof trotz Grenze, nie 5. PK


def test_no_wish_fills_open_slots_then_hof_never_5pk_for_stufe_11():
    persons = free_people(6)
    settings = e.Settings(aufsichten_pro_raum=1, reserve=1, hof_gruppen=2, hof_pro_gruppe=1, seed=1)
    assign = e.build_assignment(persons, ["101"], settings)
    assert sorted(assign.values()) == ["aufsicht:101", "hof:1", "hof:1", "hof:2", "hof:2", "reserve"]


def test_seed_makes_the_draw_reproducible_and_reroll_changes_it():
    persons = free_people(12, "Aufsicht")
    settings = e.Settings(aufsichten_pro_raum=1, reserve=0, hof_gruppen=1, seed=5)
    first = e.build_assignment(persons, ["101", "102", "103"], settings)
    assert first == e.build_assignment(persons, ["101", "102", "103"], settings)
    model = e.EinsatzModel(persons, ["101", "102", "103"], settings)
    model.reroll()
    assert model.settings.seed == 6 and model.undo() and model.assign == first


def test_locked_entries_stay_and_use_up_slots():
    persons = free_people(3)
    settings = e.Settings(aufsichten_pro_raum=1, reserve=0, hof_gruppen=0, seed=2)
    with pytest.raises(e.EinsatzError, match="Stufe 12"):
        e.build_assignment(persons, ["101", "102"], settings, locked={100: "aufsicht:101"})
    settings = e.Settings(aufsichten_pro_raum=1, reserve=0, hof_gruppen=1, seed=2)
    assign = e.build_assignment(persons, ["101", "102"], settings, locked={100: "aufsicht:101"})
    assert assign[100] == "aufsicht:101"
    assert sorted(assign[m] for m in (101, 102)) == ["aufsicht:102", "hof:1"]


def test_wishes_from_entries_accepts_id_code_and_label_and_skips_trash():
    roles = {2: "Aufsicht", 3: "Hof", 4: "5PK"}
    entries = [{"1": "100", "4": "2"}, {"1": "101", "4": "hof"}, {"1": "102", "4": "5PK"},
               {"1": "103", "4": "3", "status": "trash"}, {"1": "x", "4": "2"}, {"1": "104", "4": ""}]
    assert e.wishes_from_entries(entries, roles) == {100: "Aufsicht", 101: "Hof"}


def test_draft_roundtrip_and_unknown_person(tmp_path):
    persons = free_people(4, "Hof") + [person(1, 11, True)]
    model = e.EinsatzModel(persons, ["101"], e.Settings(reserve=1, hof_gruppen=2, seed=9))
    draft = e.Draft(tmp_path, tmp_path / "cfg", Path("einsatz-plan.yaml"))
    draft.save(model)
    text = draft.path.read_text(encoding="utf-8")
    assert "seed: 9" in text and "Wunsch Hof" in text
    other = e.EinsatzModel(persons, ["101"], e.Settings(reserve=1, hof_gruppen=2, seed=9), assign={p.matrikel: "5pk" for p in persons})
    assert draft.load_into(other).startswith("Entwurf")
    assert other.assign[100] == model.assign[100] and other.assign[1] == "5pk"   # Teilnahme bleibt fest
    draft.path.write_text(text.replace("100  Nach100", "999  Nach999"), encoding="utf-8")
    with pytest.raises(e.EinsatzError, match="999"):
        draft.load_into(other)


def test_console_session_move_swap_undo_and_draft(tmp_path):
    persons = free_people(4)
    model = e.EinsatzModel(persons, ["101", "102"], e.Settings(reserve=0, hof_gruppen=1, seed=1))
    draft = e.Draft(tmp_path, tmp_path / "cfg")
    answers = iter(["v 100 hof1", "t Nach101 Nach102", "v 100 nirgends", "u", "w", "d", "q"])
    lines: list[str] = []
    result = e.interactive_einsatz(model, None, None, apply=False, draft=draft, ask=lambda _p: next(answers), out=lines.append)  # type: ignore[arg-type]
    assert result["geschrieben"] == 0
    assert model.assign[100] == "hof:1"      # "u" nahm nur den Tausch zurück, die Verschiebung blieb
    assert any("Unbekanntes Ziel" in line for line in lines) and draft.exists()


def test_5pk_only_for_stufe_12():
    twelve = person(7, 12)
    model = e.EinsatzModel([person(2), twelve], ["101"], e.Settings(hof_gruppen=1))
    assert model.assign[7] == "5pk" and model.assign[2] != "5pk"
    with pytest.raises(e.EinsatzError, match="Stufe 12"):
        model.move(2, "5pk")
    with pytest.raises(e.EinsatzError, match="Stufe 12"):
        model.swap(2, 7)


def test_fixed_entries_cannot_be_moved():
    model = e.EinsatzModel([person(1, 11, True), person(2)], ["101"], e.Settings())
    with pytest.raises(e.EinsatzError):
        model.move(1, "5pk")


class Cur:
    def __init__(self, db):
        self.db, self.rows = db, []

    def execute(self, sql, params=None):
        self.db.executed.append((sql, params))
        s = " ".join(sql.split())
        if "FROM `JG`" in s:
            self.rows = [(4,)]
        elif "FROM `ROLLEN`" in s:
            self.rows = [(1, "Teilnahme"), (2, "Aufsicht"), (3, "Hof"), (4, "5PK"), (5, "Korrektur")]
        elif s.startswith("SELECT DISTINCT matrikelnummer FROM `ORGA`"):
            self.rows = [(1,)]
        elif "FROM `STUD`" in s:
            self.rows = [(1, "A", "A", "11"), (2, "B", "B", "11"), (3, "C", "C", "11"), (4, "D", "D", "12"), (5, "E", "E", "11")]
        elif "FROM `RAUM`" in s:
            self.rows = [(10, "101", 30, 1), (11, "102", 30, 1)]
        elif s.startswith("SELECT raum_id, COUNT(*) FROM `ORGA`"):
            self.rows = [(10, 20), (11, 15)]
        elif s.startswith("SELECT raum_id, COUNT(*) FROM `EXT`"):
            self.rows = []
        elif "FROM `EINS`" in s:
            self.rows = [(2, 3, "Gruppe", 1, None, "AUTO"), (3, 2, "Standard", None, 10, "MANUAL"), (5, 5, "Standard", None, None, "AUTO")]
        elif s.startswith("SELECT GET_LOCK"):
            self.rows = [(1,)]
        else:
            self.rows = []

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class Cnx:
    in_transaction = False

    def __init__(self, db):
        self.db = db

    def cursor(self):
        return Cur(self.db)

    def start_transaction(self):
        self.db.executed.append(("START", None))

    def commit(self):
        self.db.executed.append(("COMMIT", None))

    def rollback(self):
        self.db.executed.append(("ROLLBACK", None))


class FakeDB:
    def __init__(self):
        self.executed: list = []
        self.conn = Cnx(self)


def test_context_diff_and_write_respect_manual_and_foreign_rows(monkeypatch):
    monkeypatch.setattr(e, "_gf_wishes", lambda *a, **k: {2: "Hof", 99: "Hof"})
    db = FakeDB()
    settings = e.Settings(aufsichten_pro_raum=1, reserve=0, hof_gruppen=1, seed=1)
    ctx = e.load_context(db, Path("."), TABLES, settings, form_id=59)
    assert ctx.rooms == ["101", "102"] and ctx.locked == {3: "aufsicht:101", 5: "sonst:Korrektur"}
    by = {p.matrikel: p for p in ctx.persons}
    assert by[1].participates and by[2].wish == "Hof" and by[4].stufe == 12
    model = e.EinsatzModel(ctx.persons, ctx.rooms, settings, ctx.locked)
    assert model.assign[1] == "teilnahme" and model.assign[4] == "5pk" and model.assign[2] == "hof:1"
    assert model.assign[3] == "aufsicht:101" and model.assign[5] == "sonst:Korrektur"
    changes = e.diff(model, ctx)
    assert [m for m, _ in changes["neu"]] == [1, 4] and changes["geaendert"] == []   # 2 ist schon Hof Gruppe 1
    assert e.write_plan(db, ctx, changes) == 2
    inserts = [x for x in db.executed if x[0].startswith("INSERT")]
    assert len(inserts) == 2 and "ON DUPLICATE KEY UPDATE" in inserts[0][0] and ("COMMIT", None) in db.executed
    assert inserts[0][1] == (4, 1, 1, "Standard", None, None)


def test_run_without_apply_saves_draft_and_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(e, "_gf_wishes", lambda *a, **k: {})
    db = FakeDB()
    (tmp_path / "cfg").mkdir()
    result = e.run_einsatz(db, tmp_path / "cfg", TABLES, tmp_path, settings=e.Settings(hof_gruppen=1), apply=False,
                           interactive=False, fresh=False, plan=None)
    assert result["geschrieben"] == 0 and (tmp_path / "einsatz-plan.yaml").is_file()
    assert not [x for x in db.executed if x[0].startswith("INSERT")]


def test_raumregeln_einsatz_section_is_loaded_and_validated(tmp_path):
    from mao_cli.config import ConfigurationError
    from mao_cli.rooms import load_room_rules
    base = (Path(__file__).resolve().parents[1] / "mao_cli" / "defaults" / "raumregeln.yaml.example").read_text(encoding="utf-8")
    path = tmp_path / "raumregeln.yaml"
    path.write_text(base, encoding="utf-8")
    assert load_room_rules(path).einsatz == {"aufsichten_pro_raum": 1, "reserve": 3, "hof_gruppen": 2}
    path.write_text(base.replace("  reserve: 3 ", "  reserve: 5 ").replace("  # ohne_raum:", "  ohne_raum:").replace("# hof_pro_gruppe", "hof_pro_gruppe"), encoding="utf-8")
    loaded = load_room_rules(path).einsatz
    assert loaded["reserve"] == 5 and loaded["hof_pro_gruppe"] == 10 and loaded["ohne_raum"] == ["Aula"]
    for bad in ("  reserve: -1 ", "  unbekannt: 1\n  reserve: 3 ", "  reserve: abc "):
        path.write_text(base.replace("  reserve: 3 ", bad), encoding="utf-8")
        with pytest.raises(ConfigurationError, match="einsatz"):
            load_room_rules(path)


def test_unassigned_persons_are_reported():
    persons = [person(1), person(2)]
    model = e.EinsatzModel(persons, ["101"], e.Settings(hof_gruppen=1))
    assert not any("Nicht verplant" in w for w in model.warnings())
    del model.assign[2]
    assert any("Nicht verplant (1)" in w for w in model.warnings())
