"""Korrekturangebote: Formularauswertung, Abgleich, Planung, Entscheidungen und Datenbankschicht (Testcursor)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from mao_cli import korrektoren as k
from mao_cli.config import ConfigurationError

FIELDS = {"uid": "40", "uid_neu": "39", "jahrgang_nr": "37", "schuljahr": "47", "name": "1", "geschlecht": "11", "email": "5",
          "kinder_bekannt": "46", "weitere_kinder": "29", "kinder": "22", "stufen": "15", "wuensche": "9", "erfahrung": "35",
          "erfahrung_text": "36", "uebergabe": "21", "anschrift": "20", "uebergabe_sonst": "23", "anmerkungen": "24"}
TABLES = {"korrektoren_table": "korr", "korrektoren_prefs_table": "prefs", "korrektoren_angebote_table": "ang",
          "korrektoren_adressen_table": "adr", "korrektoren_angehoerige_table": "angeh", "schueler_alle_table": "stud",
          "jahrgaenge_table": "jg", "schuljahre_table": "sj"}


def entry(eid, first="Anna", last="Muster", mail="anna@example.org", **extra):
    base = {"id": str(eid), "status": "active", "date_created": f"2026-10-{eid:02d} 10:00:00", "37": "66", "47": "12",
            "1.3": first, "1.6": last, "5": mail, "11": "weiblich", "15.1": "5. Klasse", "15.7": "11. / 12. Klasse",
            "35.2": "bereits als Korrektor:in tätig gewesen", "21": "Hertzkind", "39": f"new-{eid}"}
    base.update(extra)
    return base


def rows_of(*entries):
    rows, issues, inactive = k.parse_entries(entries, FIELDS)
    assert not issues
    return rows


def state(**kw):
    base = dict(jahrgang_id=7, jahrgang_nr=66, schuljahr_id=12, korrektoren=[], prefs={}, angebote=set(), adressen={},
                angehoerige=set(), students=[], limits={"korrektoren": {"vorname": 50, "nachname": 50, "email": 40, "titel": 30},
                                                        "prefs": {"wuensche": 65535}, "adressen": {"strasse": 150}})
    base.update(kw)
    return k.State(**base)


KNOWN = {"id": 5, "gf_uid": "abc", "titel": None, "vorname": "Anna", "nachname": "Muster", "geschlecht": "w",
         "email": "anna@example.org", "aktiv": 1}


def test_parse_entry_maps_form_fields():
    row = rows_of(entry(1, **{"1.2": "Dr.", "1.4": "Maria", "40": "abc", "9": "Klasse 7", "15.3": "7. Klasse"}))[0]
    assert row["vorname"] == "Anna Maria" and row["titel"] == "Dr." and row["geschlecht"] == "w"
    assert row["uid"] == "abc" and row["uid_bekannt"] is True
    assert row["stufen"]["kl_5"] == 1 and row["stufen"]["kl_7"] == 1 and row["stufen"]["kl_6"] == 0 and row["stufen"]["kl_11_12"] == 1
    assert row["erfahrung"] == {"keine_erfahrung": 0, "korrexp": 1, "selbst_teilgenommen": 0}
    new = rows_of(entry(2))[0]
    assert new["uid"] == "new-2" and new["uid_bekannt"] is False


def test_list_field_formats_and_php_serialized():
    php = 'a:2:{i:0;a:2:{s:4:"Name";s:11:"Max Müller";s:6:"Klasse";s:3:"8_1";}i:1;a:2:{s:4:"Name";s:3:"Eva";s:6:"Klasse";s:0:"";}}'
    assert k.parse_list_field(php) == [{"name": "Max Müller", "klasse": "8_1"}, {"name": "Eva", "klasse": ""}]
    assert k.parse_list_field('[{"Name": "A B", "Klasse": "9a"}]') == [{"name": "A B", "klasse": "9a"}]
    assert k.parse_list_field("") == [] and k.parse_list_field("kaputt") == []
    row = rows_of(entry(1, **{"29": "Ja", "22": php}))[0]
    assert len(row["kinder"]) == 2
    assert rows_of(entry(1, **{"29": "nein", "22": php}))[0]["kinder"] == []


def test_class_normalization():
    assert {k.norm_class(x) for x in ("8-1", "8_1", "8.1", "8a", "8 A", "08a")} == {"8-1"}
    assert k.norm_class("8b") == "8-2" and k.norm_class("10") == "10" and k.norm_class("Q1") is None


def test_year_filter_uses_number_and_school_year():
    rows = rows_of(entry(1, "A", "A", "a@example.org"), entry(2, "B", "B", "b@example.org", **{"37": "65", "47": "11"}),
                   entry(3, "C", "C", "c@example.org", **{"37": "", "47": "12"}),
                   entry(4, "D", "D", "d@example.org", **{"37": "", "47": ""}),
                   entry(5, "E", "E", "e@example.org", **{"37": "66", "47": "11"}))
    plan = k.plan_import(rows, state())
    assert [i.row["eintrag_id"] for i in plan.items] == [1, 3]
    assert plan.anderes_jahr == 2 and plan.jahr_unbekannt == 1


def test_uid_and_unique_email_match_automatically_and_new_is_created():
    rows = rows_of(entry(1, **{"40": "abc"}), entry(2, "Bea", "Neu", "bea@example.org"),
                   entry(3, "Anna", "Anders", "ANNA@example.org"))
    plan = k.plan_import(rows, state(korrektoren=[KNOWN]))
    by_id = {i.row["eintrag_id"]: i for i in plan.items}
    assert by_id[1].korrektor_id == 5 and by_id[1].treffer == "uid"
    assert by_id[2].korrektor_id is None and by_id[2].status == "ok" and by_id[2].treffer == "neu"
    assert by_id[3].treffer == "email" and by_id[3].aenderungen["nachname"] == ("Muster", "Anders")


def test_name_only_and_ambiguous_matches_are_unclear_and_decisions_resolve_them():
    other = {**KNOWN, "id": 6, "gf_uid": "def", "email": "andere@example.org"}
    rows = rows_of(entry(1, mail="neu@example.org"))
    plan = k.plan_import(rows, state(korrektoren=[KNOWN, other]))
    item = plan.items[0]
    assert item.status == "unklar" and [c["id"] for c in item.kandidaten] == [5, 6]
    assert k.plan_import(rows, state(korrektoren=[KNOWN, other]), {"1": {"korrektor": 6}}).items[0].korrektor_id == 6
    assert k.plan_import(rows, state(korrektoren=[KNOWN, other]), {"1": {"korrektor": "neu"}}).items[0].korrektor_id is None
    assert k.plan_import(rows, state(korrektoren=[KNOWN, other]), {"1": {"korrektor": "skip"}}).items[0].status == "uebersprungen"


def test_duplicate_entries_keep_the_newest_and_inactive_korrektor_is_reactivated():
    old = {**KNOWN, "aktiv": 0}
    rows = rows_of(entry(1, **{"40": "abc"}), entry(5, **{"40": "abc", "9": "neuer"}))
    plan = k.plan_import(rows, state(korrektoren=[old]))
    assert len(plan.items) == 1 and plan.items[0].row["eintrag_id"] == 5 and plan.doppelt == 1
    assert plan.items[0].reaktiviert is True


def test_too_long_values_are_refused_with_hint():
    rows = rows_of(entry(1, "A" * 51, "X", mail="x@example.org"), entry(2, mail="m" * 41 + "@example.org"))
    plan = k.plan_import(rows, state())
    assert [i.status for i in plan.items] == ["uebersprungen", "uebersprungen"]
    assert "verlängern" in plan.items[0].gruende[0] and "vorname" in plan.items[0].gruende[0]
    assert "email" in plan.items[1].gruende[0]


STUDENTS = [{"matrikelnummer": 1001, "vorname": "Max", "nachname": "Müller", "klasse": "8-1", "aktiv": 1},
            {"matrikelnummer": 1002, "vorname": "Max", "nachname": "Müller", "klasse": "9-2", "aktiv": 1},
            {"matrikelnummer": 1003, "vorname": "Mia", "nachname": "Meyer", "klasse": None, "aktiv": 0}]


def test_student_candidates_use_fuzzy_names_class_variants_and_include_inactive():
    c = k.student_candidates("Max Mueller", "8a", STUDENTS)
    assert c[0]["matrikelnummer"] == 1001 and c[0]["gleiche_klasse"] is True
    assert [x["matrikelnummer"] for x in k.student_candidates("Müller Max", "", STUDENTS)][:2] == [1001, 1002]
    assert k.student_candidates("Mia Meier", "", STUDENTS)[0]["matrikelnummer"] == 1003      # inaktiv, ohne Klasse
    assert k.student_candidates("Zzz Yyy", "", STUDENTS) == []


def test_children_are_only_written_after_a_decision_and_unknown_matrikel_is_refused():
    rows = rows_of(entry(1, **{"40": "abc", "29": "Ja", "22": '[{"Name": "Max Müller", "Klasse": "8a"}]'}))
    st = state(korrektoren=[KNOWN], students=STUDENTS)
    item = k.plan_import(rows, st).items[0]
    assert item.kinder_neu == [] and item.kinder_offen[0]["kandidaten"][0]["matrikelnummer"] == 1001
    item = k.plan_import(rows, st, {"1": {"kinder": {"0": 1001}}}).items[0]
    assert item.kinder_neu == [1001] and not item.kinder_offen
    assert k.plan_import(rows, st, {"1": {"kinder": {"0": "keins"}}}).items[0].kinder_neu == []
    bad = k.plan_import(rows, st, {"1": {"kinder": {"0": 9999}}})
    assert bad.items[0].kinder_neu == [] and "nicht in den Stammdaten" in bad.hinweise[0]
    st.angehoerige.add((5, 1001))
    assert k.plan_import(rows, st, {"1": {"kinder": {"0": 1001}}}).items[0].kinder_neu == []


def test_questions_store_ids_only_and_z_returns_to_menu(tmp_path):
    other = {**KNOWN, "id": 6, "gf_uid": "def", "email": "andere@example.org"}
    rows = rows_of(entry(1, mail="neu@example.org", **{"29": "Ja", "22": '[{"Name": "Max Müller", "Klasse": "8a"}]'}))
    st = state(korrektoren=[KNOWN, other], students=STUDENTS)
    plan = k.plan_import(rows, st)
    answers = iter(["x", "2"])
    shown = []
    decisions: dict = {}
    assert k.resolve_unclear(plan, decisions, ask=lambda _: next(answers), out=shown.append) is True
    assert decisions == {"1": {"korrektor": 6}} and any("Bitte eine Nummer" in s for s in shown)
    plan = k.plan_import(rows, st, decisions)
    assert k.assign_children(plan, decisions, ask=lambda _: "z", out=shown.append) is False
    answers = iter(["1"])
    k.assign_children(plan, decisions, ask=lambda _: next(answers), out=shown.append)
    assert decisions["1"]["kinder"] == {"0": 1001}
    path = tmp_path / "e.yaml"
    k.save_decisions(path, decisions)
    assert "Müller" not in path.read_text(encoding="utf-8") and k.load_decisions(path) == decisions


def test_settings_validation(tmp_path):
    good = tmp_path / "g.yaml"
    good.write_text("form_id: 3\nfields: " + str({key: val for key, val in FIELDS.items()}).replace("'", '"') + "\n", encoding="utf-8")
    assert k.load_korr_settings(good).form_id == 3
    bad = tmp_path / "b.yaml"
    bad.write_text("fields: {uid: x}\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        k.load_korr_settings(bad)
    with pytest.raises(ConfigurationError, match="fehlt"):
        k.load_korr_settings(tmp_path / "nein.yaml")


# ------------------------------------------------------------------ Datenbankschicht

class Cur:
    def __init__(self, korrektoren=(), count_offset=0):
        self.korrektoren = list(korrektoren)
        self.executed, self.sql, self.lastrowid, self.next = [], "", 0, 200
        self.count_offset = count_offset

    def execute(self, sql, params=None):
        self.sql = sql
        self.executed.append((sql, params))
        if sql.startswith("INSERT INTO `korr`"):
            self.next += 1
            self.lastrowid = self.next

    def fetchall(self):
        s = self.sql
        if "GET_LOCK" in s:
            return [(1,)]
        if "FROM `jg`" in s:
            return [(7, 66, 12)]
        if "FROM `korr`" in s and "COUNT" not in s:
            return [(x["id"], x["gf_uid"], x["titel"], x["vorname"], x["nachname"], x["geschlecht"], x["email"], x["aktiv"])
                    for x in self.korrektoren]
        if "COUNT(*) FROM `korr`" in s:
            return [(len(self.korrektoren) + self.count_offset + sum(1 for q, _ in self.executed if q.startswith("INSERT INTO `korr`")),)]
        if "FROM `stud`" in s:
            return [(x["matrikelnummer"], x["vorname"], x["nachname"], x["klasse"], x["aktiv"]) for x in STUDENTS]
        if "SHOW COLUMNS FROM `korr`" in s:
            return [("vorname", "varchar(50)"), ("email", "varchar(40)")]
        if "SHOW COLUMNS" in s:
            return [("wuensche", "text")]
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
    import yaml
    (tmp_path / "gf-korrektoren.yaml").write_text(yaml.safe_dump({"form_id": 3, "fields": FIELDS}), encoding="utf-8")
    return tmp_path


def run(config_dir, cur, entries, *, apply=False, menu=False, **kw):
    cnx = Conn(cur)
    report: dict = {}
    stats = k.run_korrektoren(SimpleNamespace(conn=cnx), config_dir, TABLES, workspace=config_dir, report=report,
                              entries=entries, apply=apply, menu=menu, **kw)
    return cnx, stats, report


def test_dry_run_writes_nothing_and_report_has_no_personal_data(config_dir):
    cnx, stats, report = run(config_dir, Cur([KNOWN]), [entry(1, **{"40": "abc"}), entry(2, "Bea", "Neu", "bea@example.org")],
                             apply=False)
    assert stats["neu"] == 1 and stats["bekannt"] == 1 and cnx.commits == 0 and report["status"] == "ready"
    assert stats["unveraendert"] == 0
    assert not any(q.startswith(("INSERT", "UPDATE")) for q, _ in cnx.c.executed)
    text = str(report)
    assert "Bea" not in text and "example.org" not in text


def test_apply_writes_korrektor_prefs_offer_and_address_in_one_transaction(config_dir):
    post = entry(2, "Bea", "Neu", "bea@example.org", **{"21": "Post", "20.1": "Weg 1", "20.3": "Berlin", "20.5": "10247", "20.6": "Deutschland"})
    cur = Cur([KNOWN])
    cnx, stats, report = run(config_dir, cur, [entry(1, **{"40": "abc"}), post], apply=True)
    writes = [(q, p) for q, p in cur.executed if q.startswith(("INSERT", "UPDATE"))]
    assert cnx.commits == 1 and stats["geschrieben_korrektoren_neu"] == 1 and report["status"] == "applied"
    tables = [q.split("`")[1] for q, _ in writes]
    assert tables == ["prefs", "ang", "korr", "prefs", "ang", "adr"]
    new_korr = next(p for q, p in writes if q.startswith("INSERT INTO `korr`"))
    assert new_korr[0] == "new-2" and new_korr[2] == "Neu" and new_korr[3] == "Bea" and new_korr[-1] == 1
    adr = next((q, p) for q, p in writes if q.startswith("INSERT INTO `adr`"))
    assert adr[1][0] == 201 and "Weg 1" in adr[1]
    ang = [p for q, p in writes if q.startswith("INSERT INTO `ang`")]
    assert ang == [(7, 5), (7, 201)]


class StateCur(Cur):
    """Testcursor mit vorhandenen Präferenzen, Angeboten und Anschriften."""
    def __init__(self, korrektoren=(), prefs=(), angebote=(), adressen=(), **kw):
        super().__init__(korrektoren, **kw)
        self.prefs, self.angebote, self.adressen = list(prefs), list(angebote), list(adressen)

    def fetchall(self):
        s = self.sql
        if "FROM `prefs`" in s:
            return self.prefs
        if "FROM `ang`" in s:
            return self.angebote
        if "FROM `adr`" in s:
            return self.adressen
        return super().fetchall()


def prefs_row(korrektor_id, row):
    values = k.prefs_values(row)
    return (korrektor_id, *(values[c] for c in k.PREFS_COLUMNS))


def test_unchanged_entry_writes_nothing_and_changed_prefs_are_updated(config_dir):
    e1 = entry(1, **{"40": "abc"})
    row = rows_of(e1)[0]
    cur = StateCur([KNOWN], prefs=[prefs_row(5, row)], angebote=[(7, 5)])
    cnx, stats, _ = run(config_dir, cur, [e1], apply=True)
    assert stats["unveraendert"] == 1 and cnx.commits == 0
    assert not any(q.startswith(("INSERT", "UPDATE")) for q, _ in cur.executed)
    cur = StateCur([KNOWN], prefs=[prefs_row(5, row)], angebote=[(7, 5)])
    cnx, stats, _ = run(config_dir, cur, [entry(1, **{"40": "abc", "9": "gern Geometrie"})], apply=True)
    qs = [q for q, _ in cur.executed if q.startswith(("INSERT", "UPDATE"))]
    assert cnx.commits == 1 and len(qs) == 1 and qs[0].startswith("UPDATE `prefs` SET")


def test_new_korrektor_is_remembered_so_a_rerun_does_not_create_it_twice(config_dir):
    cur = Cur([])
    run(config_dir, cur, [entry(1, "Bea", "Neu", "", **{"39": ""})], apply=True)
    assert k.load_decisions(config_dir / k.DECISIONS_FILE) == {"1": {"korrektor": 201}}
    new = {"id": 201, "gf_uid": None, "titel": None, "vorname": "Bea", "nachname": "Neu", "geschlecht": "w", "email": None, "aktiv": 1}
    cur = StateCur([new], angebote=[(7, 201)])
    _, stats, _ = run(config_dir, cur, [entry(1, "Bea", "Neu", "", **{"39": ""})], apply=True)
    assert stats["neu"] == 0 and stats["bekannt"] == 1
    assert not any(q.startswith("INSERT INTO `korr`") for q, _ in cur.executed)


def test_decision_neu_is_superseded_by_the_stored_form_uid(config_dir):
    new = {"id": 201, "gf_uid": "new-1", "titel": None, "vorname": "Anna", "nachname": "Muster", "geschlecht": "w",
           "email": "neu@example.org", "aktiv": 1}
    rows = rows_of(entry(1, mail="neu@example.org"))
    item = k.plan_import(rows, state(korrektoren=[KNOWN, new]), {"1": {"korrektor": "neu"}}).items[0]
    assert item.korrektor_id == 201 and item.treffer == "uid"


def test_apply_rolls_back_on_count_mismatch_and_skips_unclear(config_dir):
    cnx = Conn(Cur([KNOWN], count_offset=5))
    with pytest.raises(k.KorrektorenError, match="geändert|Zählabgleich"):
        k.run_korrektoren(SimpleNamespace(conn=cnx), config_dir, TABLES, workspace=config_dir, apply=True, menu=False,
                          entries=[entry(1, "Bea", "Neu", "bea@example.org")])
    assert cnx.commits == 0
    unclear = [entry(1, mail="neu@example.org")]            # Name wie KNOWN, andere E-Mail
    cur = Cur([KNOWN])
    cnx, stats, _ = run(config_dir, cur, unclear, apply=True)
    assert stats["unklar"] == 1 and cnx.commits == 0 and not any(q.startswith("INSERT") for q, _ in cur.executed)


def menu(config_dir, cur, entries, answers, *, apply):
    feed = iter(answers)
    shown: list[str] = []
    cnx = Conn(cur)
    stats = k.run_korrektoren(SimpleNamespace(conn=cnx), config_dir, TABLES, workspace=config_dir, entries=entries,
                              apply=apply, menu=True, ask=lambda _: next(feed), out=shown.append)
    return cnx, stats, "\n".join(shown)


def test_menu_resolves_unclear_and_writes_only_after_confirmation_with_apply(config_dir):
    kid = '[{"Name": "Max Müller", "Klasse": "8_1"}]'
    entries = [entry(1, mail="neu@example.org", **{"29": "Ja", "22": kid})]
    cnx, stats, text = menu(config_dir, Cur([KNOWN]), entries, ["1", "2", "n", "3", "1", "5", "q"], apply=False)
    assert "? GF     1" in text and "DRY-RUN" in text and cnx.commits == 0
    assert k.load_decisions(config_dir / k.DECISIONS_FILE) == {"1": {"korrektor": "neu", "kinder": {"0": 1001}}}
    cur = Cur([KNOWN])
    cnx, stats, text = menu(config_dir, cur, entries, ["5", "n", "5", "j", "q"], apply=True)
    assert cnx.commits == 1 and stats["geschrieben_korrektoren_neu"] == 1 and stats["geschrieben_angehoerige"] == 1
    assert "Geschrieben:" in text
    assert k.load_decisions(config_dir / k.DECISIONS_FILE)["1"]["korrektor"] == 201


def test_menu_reset_decision_and_invalid_choice(config_dir):
    (config_dir / k.DECISIONS_FILE).write_text("'1': {korrektor: skip}\n", encoding="utf-8")
    entries = [entry(1, mail="neu@example.org")]
    _, stats, text = menu(config_dir, Cur([KNOWN]), entries, ["9", "4", "1", "4", "2", "q"], apply=False)
    assert "Bitte 1 bis 6" in text and "zurückgesetzt" in text and "keine Entscheidung" in text
    assert stats["unklar"] == 1 and k.load_decisions(config_dir / k.DECISIONS_FILE) == {}


def test_demo_tables_are_refused(config_dir):
    with pytest.raises(k.KorrektorenError, match="korrektoren_table"):
        k.run_korrektoren(SimpleNamespace(conn=Conn(Cur())), config_dir, {**TABLES, "korrektoren_table": "DEMO_KORREKTOREN"},
                          workspace=config_dir, apply=False, menu=False, entries=[])


def test_two_entries_landing_at_the_same_korrektor_keep_the_newest():
    rows = rows_of(entry(1, mail="neu@example.org"), entry(3, "Dr", "X", "x@example.org", **{"40": "abc"}))
    plan = k.plan_import(rows, state(korrektoren=[KNOWN]), {"1": {"korrektor": 5}})
    assert [i.status for i in plan.items] == ["uebersprungen", "ok"] and "GF 3 gilt" in plan.items[0].gruende[0]
    assert plan.doppelt == 1


def test_children_of_unclear_entries_can_already_be_assigned():
    rows = rows_of(entry(1, mail="neu@example.org", **{"29": "Ja", "22": '[{"Name": "Max Müller", "Klasse": "8a"}]'}))
    plan = k.plan_import(rows, state(korrektoren=[KNOWN], students=STUDENTS))
    assert plan.items[0].status == "unklar" and k.plan_stats(plan)["kinder_offen"] == 1
