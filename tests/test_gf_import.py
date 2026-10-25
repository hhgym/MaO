"""Offline-Prüfungen des Gravity-Forms-Erstimports; keine realen Tabellen oder Schlüssel."""
from types import SimpleNamespace
import pytest

from mao_cli import gf_import as g
from mao_cli import cli


@pytest.fixture()
def opts():
    return g.ImportSettings(16, 200, 60, ("5-1",), ("7-4",),
                            {"klasse": "1", "kurs": "5", "lehrkraft": "3", "teilnehmende": "7"}, {})


def test_entry_checkbox_and_mandatory(opts):
    students = [
        {"matrikelnummer": 12, "vorname": "Ada", "nachname": "X", "geschlecht": "w", "klasse": "11"},
        {"matrikelnummer": 34, "vorname": "Ben", "nachname": "Y", "geschlecht": "m", "klasse": "5-1"},
    ]
    entries = [{"1": "11", "3": "7", "5": "100", "7.1": "12: Ada", "status": "active"},
               {"1": "5-1", "3": "7", "7.1": "34", "status": "spam"}]
    mandatory = [{"klasse": "5-1", "lehrkraft_id": 7, "lerngruppe_schuljahr_id": 200}]
    items, stat = g.build_participants(entries, students, mandatory, {7}, {(100, 7): 83, (200, 7): 84}, opts)
    assert [r["matrikelnummer"] for r in items] == [12, 34]
    assert [r["tnr"] for r in items] == [1, 2]
    assert items[0]["kurs_id"] == 100 and items[0]["lehrkraft_id"] == 7
    assert items[1]["kurs_id"] is None and items[1]["stufe"] == 5
    assert stat == {"gf_entries": 1, "inactive_entries": 1, "empty_reports": 0, "mandatory_added": 1,
                    "mandatory_classes_present": 1, "optional_classes_present": 0,
                    "class_mismatch": 0, "unknown_gender": 0}


def test_duplicate_matrikel_fails(opts):
    students = [{"matrikelnummer": 12, "vorname": "Ada", "nachname": "X", "geschlecht": "w", "klasse": "11"}]
    entries = [{"1": "11", "7.1": "12"}, {"1": "11", "7.1": "12"}]
    with pytest.raises(g.ImportErrorGF, match="mehrfach"):
        g.build_participants(entries, students, [], {}, {}, opts)


def test_unknown_student_fails(opts):
    with pytest.raises(g.ImportErrorGF, match="nicht im Schülerverzeichnis"):
        g.build_participants([{"1": "11", "7.1": "42"}], [], [], set(), {}, opts)



def test_course_ids_preserved_from_gravity_forms(opts):
    links = g._course_links([
        {"lerngruppe_schuljahr_id": 124, "lehrkraft_id": 7},
        {"lerngruppe_schuljahr_id": 124, "lehrkraft_id": 7},
    ])
    assert links == {(124, 7): 124}
    assert g._resolve_course("124", 7, links) == 124
    with pytest.raises(g.ImportErrorGF, match="Keine gültige Lerngruppen"):
        g._resolve_course("124", 8, links)


def test_same_group_multiple_teachers_keep_group_id(opts):
    links = g._course_links([
        {"lerngruppe_schuljahr_id": 100, "lehrkraft_id": 7},
        {"lerngruppe_schuljahr_id": 100, "lehrkraft_id": 8},
    ])
    assert g._resolve_course("100", 7, links) == 100
    assert g._resolve_course("100", 8, links) == 100
    with pytest.raises(g.ImportErrorGF, match="Keine gültige Lerngruppen"):
        g._resolve_course("100", 9, links)
    with pytest.raises(g.ImportErrorGF, match="fehlt die Lehrkraft-ID"):
        g._resolve_course("100", None, links)

def test_numeric_teacher_id_validation():
    assert g._resolve_teacher("7", {7}, "Lehrkraft") == 7
    with pytest.raises(g.ImportErrorGF, match="existiert nicht"):
        g._resolve_teacher("9", {7}, "Lehrkraft")
    with pytest.raises(g.ImportErrorGF, match="positive numerische ID"):
        g._resolve_teacher("MU", {7}, "Lehrkraft")
    assert g._resolve_teacher("  ", {7}, "Lehrkraft") is None


def test_mandatory_groups_and_optional_7_4(opts):
    students = [
        {"matrikelnummer": 1, "vorname": "A", "nachname": "A", "geschlecht": "w", "klasse": "5-1"},
        {"matrikelnummer": 2, "vorname": "B", "nachname": "B", "geschlecht": "m", "klasse": "5-1"},
        {"matrikelnummer": 3, "vorname": "C", "nachname": "C", "geschlecht": "w", "klasse": "7-4"},
    ]
    groups = [{"klasse": "5-1", "lehrkraft_id": 7, "lerngruppe_schuljahr_id": 100}]
    rows, stats = g.build_participants([{"1": "5-1", "3": "7", "7.1": "1"}], students[:2],
                                      groups, {7}, {(100, 7): 900}, opts)
    assert {r["matrikelnummer"] for r in rows} == {1, 2}
    assert stats["mandatory_added"] == 1 and stats["optional_classes_present"] == 0
    with pytest.raises(g.ImportErrorGF, match="Optionale Klasse vorhanden"):
        g.build_participants([], students, groups, {7}, {(100, 7): 900}, opts)
    groups.append({"klasse": "7-4", "lehrkraft_id": 11, "lerngruppe_schuljahr_id": 200})
    rows, stats = g.build_participants([], students, groups, {7, 11}, {(100, 7): 900, (200, 11): 901}, opts)
    assert {r["matrikelnummer"] for r in rows} == {1, 2, 3}
    assert stats["mandatory_added"] == 3 and stats["optional_classes_present"] == 1
    assert {row["kurs_id"] for row in rows} == {None}


def test_mandatory_required_missing_and_ambiguous_teacher(opts):
    students = [{"matrikelnummer": 12, "vorname": "Ada", "nachname": "X", "geschlecht": "w", "klasse": "5-1"}]
    with pytest.raises(g.ImportErrorGF, match="Erforderliche Pflichtklasse"):
        g.build_participants([], students, [], {7}, {}, opts)
    with pytest.raises(g.ImportErrorGF, match="Mehrdeutige Mathe-Lerngruppe/Lehrkraft"):
        g.build_participants([], students, [{"klasse": "5-1", "lehrkraft_id": 7, "lerngruppe_schuljahr_id": 100},
                                           {"klasse": "5-1", "lehrkraft_id": 8, "lerngruppe_schuljahr_id": 100}], {7, 8}, {(100, 7): 900, (100, 8): 901}, opts)



def test_non_oberstufe_course_is_null_even_with_field_5(opts):
    students = [{"matrikelnummer": 12, "vorname": "Ada", "nachname": "X", "geschlecht": "w", "klasse": "5-1"}]
    entries = [{"1": "5-1", "3": "7", "5": "100", "7.1": "12"}]
    result, _ = g.build_participants(entries, students,
                                      [{"klasse": "5-1", "lehrkraft_id": 7, "lerngruppe_schuljahr_id": 100}],
                                      {7}, {(100, 7): 100}, opts)
    assert result[0]["kurs_id"] is None


def test_oberstufe_uses_original_lerngruppe_schuljahr_id(opts):
    options = g.ImportSettings(opts.form_id, opts.page_size, opts.timeout, (), (), opts.fields, opts.queries)
    students = [
        {"matrikelnummer": 11, "vorname": "A", "nachname": "X", "geschlecht": "w", "klasse": "11"},
        {"matrikelnummer": 12, "vorname": "B", "nachname": "Y", "geschlecht": "m", "klasse": "12"},
        {"matrikelnummer": 10, "vorname": "C", "nachname": "Z", "geschlecht": "m", "klasse": "10-1"},
    ]
    entries = [
        {"1": "11", "3": "7", "5": "100", "7.1": "11"},
        {"1": "12", "3": "8", "5": "200", "7.1": "12"},
        {"1": "10-1", "3": "7", "5": "999", "7.1": "10"},
    ]
    rows, _ = g.build_participants(entries, students, [], {7, 8},
                                   {(100, 7): 100, (200, 8): 200}, options)
    by_student = {r["matrikelnummer"]: r for r in rows}
    assert by_student[11]["kurs_id"] == 100
    assert by_student[12]["kurs_id"] == 200
    assert by_student[10]["kurs_id"] is None


def test_oberstufe_rejects_wrong_teacher_for_group(opts):
    options = g.ImportSettings(opts.form_id, opts.page_size, opts.timeout, (), (), opts.fields, opts.queries)
    students = [{"matrikelnummer": 12, "vorname": "A", "nachname": "X", "geschlecht": "w", "klasse": "11"}]
    with pytest.raises(g.ImportErrorGF, match="Keine gültige Lerngruppen"):
        g.build_participants([{"1": "11", "3": "7", "5": "100", "7.1": "12"}],
                             students, [], {7}, {(100, 8): 100}, options)

def test_credential_validation(tmp_path):
    f = tmp_path / "gf.config"
    f.write_text("[oauth1]\napi_base=http://example.com/gf/v2\nconsumer_key=x\nconsumer_secret=y\n")
    with pytest.raises(Exception, match="HTTPS"):
        g.load_gf_credentials(f)
    f.write_text(f.read_text().replace("http://", "https://"))
    c = g.load_gf_credentials(f)
    assert c["api_base"] == "https://example.com/gf/v2"


def test_private_import_config(tmp_path):
    from mao_cli.workspaces import init_config
    init_config(tmp_path, cli.DEFAULTS)
    assert (tmp_path / "gf-import.yaml").is_file()
    with pytest.raises(Exception, match="DEMO_"):
        g.load_import_settings(tmp_path / "gf-import.yaml")


def test_import_resolves_identifiers_from_central_tables(tmp_path):
    from mao_cli.workspaces import init_config
    init_config(tmp_path, cli.DEFAULTS)
    tables_path = tmp_path / "tables.ini"
    text = tables_path.read_text(encoding="utf-8")
    text = text.replace("students_table = DEMO_CURRENT_STUDENTS", "students_table = STUDENT_CURRENT")
    text = text.replace("current_groups_view = DEMO_CURRENT_GROUP_VIEW", "current_groups_view = GROUPS_CURRENT")
    text = text.replace("teachers_table = DEMO_TEACHERS", "teachers_table = TEACHER_MASTER")
    tables_path.write_text(text, encoding="utf-8")
    settings = g.load_import_settings(tmp_path / "gf-import.yaml")
    assert settings.queries["students"] == "SELECT matrikelnummer, vorname, nachname, geschlecht, klasse FROM STUDENT_CURRENT"
    assert settings.queries["teachers"] == "SELECT id FROM TEACHER_MASTER"
    assert "FROM GROUPS_CURRENT WHERE" in settings.queries["courses"]
    assert "GROUPS_CURRENT WHERE" in settings.queries["mandatory"]
    assert "fach_id" not in settings.queries["mandatory"]  # Aktuell nur Mathematik


def test_import_rejects_invalid_and_unknown_table_names(tmp_path):
    from mao_cli.workspaces import init_config
    init_config(tmp_path, cli.DEFAULTS)
    tables_path = tmp_path / "tables.ini"
    text = tables_path.read_text(encoding="utf-8")
    tables_path.write_text(text.replace("DEMO_TEACHERS", "TEACHERS; DROP TABLE USERS"), encoding="utf-8")
    with pytest.raises(Exception, match="Ungültiger Tabellen-/Viewname"):
        g.load_import_settings(tmp_path / "gf-import.yaml")
    tables_path.write_text(text.replace("teachers_table = DEMO_TEACHERS\n", "").replace("students_table = DEMO_CURRENT_STUDENTS", "students_table = STUDENT_CURRENT").replace("current_groups_view = DEMO_CURRENT_GROUP_VIEW", "current_groups_view = GROUP_CURRENT"), encoding="utf-8")
    with pytest.raises(Exception, match="Unbekannte Tabellenkennung"):
        g.load_import_settings(tmp_path / "gf-import.yaml")


def test_init_does_not_overwrite_private_existing_configs(tmp_path):
    from mao_cli.workspaces import init_config
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "db.config").write_text("secret_database", encoding="utf-8")
    (tmp_path / "gf.config").write_text("secret_gravity", encoding="utf-8")
    (tmp_path / "gf-import.yaml").write_text("special: custom", encoding="utf-8")
    assert init_config(tmp_path, cli.DEFAULTS)
    assert init_config(tmp_path, cli.DEFAULTS) == []
    assert (tmp_path / "db.config").read_text() == "secret_database"
    assert (tmp_path / "gf.config").read_text() == "secret_gravity"
    assert (tmp_path / "gf-import.yaml").read_text() == "special: custom"


def test_init_copies_legacy_examples_without_overwriting(tmp_path):
    from mao_cli.workspaces import init_config
    (tmp_path / "gf.config.example").write_text("legacy_GF", encoding="utf-8")
    (tmp_path / "db.config.example").write_text("legacy_DB", encoding="utf-8")
    (tmp_path / "gf-import.yaml.example").write_text("legacy_SQL", encoding="utf-8")
    init_config(tmp_path, cli.DEFAULTS)
    assert (tmp_path / "gf.config").read_text() == "legacy_GF"
    assert (tmp_path / "db.config").read_text() == "legacy_DB"
    assert (tmp_path / "gf-import.yaml").read_text() == "legacy_SQL"
    assert (tmp_path / "gf.config.example").read_text() == "legacy_GF"


def test_cli_mode_and_no_import_in_version():
    assert cli.parse_args(["import-gf"]).apply is False
    assert cli.parse_args(["import-gf", "--apply"]).apply is True
    with pytest.raises(SystemExit):
        cli.parse_args(["import-gf", "--apply", "--dry-run"])


class FakeCursor:
    def __init__(self, present=0):
        self.present = present
        self.closed = False
        self.last = ""
        self.executed = []
        self.inserted = 0
        self.unread = False
    def execute(self, sql, params=None):
        # Wie der echte MySQL-Cursor: fetchone() ohne Rest-Abruf sperrt die nächste Abfrage.
        if self.unread:
            raise RuntimeError("Unread result found")
        self.last = sql
        self.executed.append((sql, params))
        self.unread = sql.lstrip().upper().startswith(("SELECT", "SHOW"))
    def fetchall(self):
        self.unread = False
        if "SHOW COLUMNS" in self.last:
            return [(x,) for x in g.WRITE_COLUMNS]
        if "GET_LOCK" in self.last or "RELEASE_LOCK" in self.last:
            return [(1,)]
        if "COUNT(*)" in self.last:
            return [(self.present + self.inserted,)]
        return []
    def fetchone(self):
        self.unread = True
        rows = self.fetchall()
        self.unread = True
        return rows[0]
    def executemany(self, sql, params):
        self.inserted = len(params)
        self.executed.append((sql, params))
    def close(self):
        self.closed = True


class FakeConn:
    def __init__(self, present=0):
        self.cursor_obj = FakeCursor(present)
        self.in_transaction = False
        self.commits = 0
        self.rollbacks = 0
    def cursor(self):
        return self.cursor_obj
    def start_transaction(self):
        self.in_transaction = True
    def commit(self):
        self.commits += 1
        self.in_transaction = False
    def rollback(self):
        self.rollbacks += 1
        self.in_transaction = False


def test_persist_dry_and_apply_transaction():
    participants = [{x: 1 for x in g.WRITE_COLUMNS}]
    cnx = FakeConn()
    assert g.persist_import(SimpleNamespace(conn=cnx), "SAMPLE_TABLE", participants, dry_run=True) == 0
    assert cnx.commits == 0 and not any("INSERT" in sql for sql, _ in cnx.cursor_obj.executed)
    assert g.persist_import(SimpleNamespace(conn=cnx), "SAMPLE_TABLE", participants, dry_run=False) == 1
    assert cnx.commits == 1
    assert any("GET_LOCK" in sql for sql, _ in cnx.cursor_obj.executed)
    assert any("RELEASE_LOCK" in sql for sql, _ in cnx.cursor_obj.executed)


def test_persist_never_replaces_existing_rows():
    cnx = FakeConn(present=5)
    with pytest.raises(g.ImportErrorGF, match="bereits 5"):
        g.persist_import(SimpleNamespace(conn=cnx), "SAMPLE_TABLE", [{x: 1 for x in g.WRITE_COLUMNS}], dry_run=False)
    assert cnx.commits == 0
    assert not any("INSERT" in sql for sql, _ in cnx.cursor_obj.executed)


def test_init_adds_missing_table_names_to_existing_config_without_overwrite(tmp_path):
    from mao_cli.workspaces import init_config
    (tmp_path / "tables.ini").write_text("[table]\nteachers_table = SCHOOL_TEACHERS\n# bewusst behalten\n", encoding="utf-8")
    (tmp_path / "gf-import.yaml.example").write_text("queries: DEMO_TEACHERS", encoding="utf-8")
    init_config(tmp_path, cli.DEFAULTS)
    text = (tmp_path / "tables.ini").read_text(encoding="utf-8")
    assert "teachers_table = SCHOOL_TEACHERS" in text
    assert "# bewusst behalten" in text
    assert "students_table = DEMO_CURRENT_STUDENTS" in text
    assert "current_groups_view = DEMO_CURRENT_GROUP_VIEW" in text
    assert "{{table.teachers_table}}" in (tmp_path / "gf-import.yaml").read_text(encoding="utf-8")
    init_config(tmp_path, cli.DEFAULTS)
    assert (tmp_path / "tables.ini").read_text(encoding="utf-8") == text


class FKCursor(FakeCursor):
    """Liefert zusätzlich Fremdschlüssel aus information_schema."""
    def __init__(self, fk_rows, present=0):
        super().__init__(present)
        self.fk_rows = fk_rows
    def fetchall(self):
        if "KEY_COLUMN_USAGE" in self.last:
            self.unread = False
            return self.fk_rows
        return super().fetchall()


def test_persist_blocks_unexpected_foreign_key_before_writing():
    participants = [{x: 1 for x in g.WRITE_COLUMNS}]
    cnx = FakeConn()
    # Treiber liefern information_schema-Werte teils als bytes.
    cnx.cursor_obj = FKCursor([("kurs_id", b"assignment_table", "id"), ("lehrkraft_id", "TEACHERS", "id")])
    report = {}
    with pytest.raises(g.ImportErrorGF, match="kurs_id verweist auf assignment_table.id, erwartet GROUP_YEAR"):
        g.persist_import(SimpleNamespace(conn=cnx), "SAMPLE_TABLE", participants, dry_run=False,
                         foreign_keys={"kurs_id": "GROUP_YEAR", "lehrkraft_id": "teachers"}, report=report)
    assert not any("INSERT" in sql for sql, _ in cnx.cursor_obj.executed)
    assert any("RELEASE_LOCK" in sql for sql, _ in cnx.cursor_obj.executed)
    assert cnx.commits == 0
    assert report["schema"]["foreign_keys"] == {"kurs_id": "assignment_table.id", "lehrkraft_id": "TEACHERS.id"}


def test_persist_accepts_matching_foreign_key_case_insensitive():
    cnx = FakeConn()
    cnx.cursor_obj = FKCursor([("kurs_id", "group_year", "id")])
    report = {}
    assert g.persist_import(SimpleNamespace(conn=cnx), "SAMPLE_TABLE", [{x: 1 for x in g.WRITE_COLUMNS}],
                            dry_run=True, foreign_keys={"kurs_id": "GROUP_YEAR"}, report=report) == 0
    notes = report["schema"]["notes"]
    assert any("kurs_id → group_year.id (wie erwartet)" in line for line in notes)
    assert any("lehrkraft_id → keiner" in line for line in notes)


def test_review_foreign_keys_lines_and_problems():
    lines, problems = g.review_foreign_keys({"kurs_id": None, "lehrkraft_id": "T.id"},
                                            {"kurs_id": "GROUP_YEAR", "lehrkraft_id": "T"})
    assert problems == []
    assert lines[0].startswith("WARNUNG: kurs_id hat keinen Fremdschlüssel")
    assert "wie erwartet" in lines[1]


def test_foreign_key_expectations_from_import_config(tmp_path):
    (tmp_path / "tables.ini").write_text(
        "[table]\nlerngruppe_schuljahr_table = GROUP_YEAR\nteachers_table = DEMO_TEACHERS\n", encoding="utf-8")
    base = ('fields: {klasse: "1", kurs: "5", lehrkraft: "3", teilnehmende: "7"}\n'
            'queries: {students: "SELECT * FROM S", teachers: "SELECT * FROM T", courses: "SELECT * FROM C"}\n')
    config = tmp_path / "gf-import.yaml"
    config.write_text(base + 'foreign_keys:\n  kurs_id: "{{table.lerngruppe_schuljahr_table}}"\n'
                             '  lehrkraft_id: "{{table.teachers_table}}"\n', encoding="utf-8")
    # DEMO-Namen gelten als nicht eingerichtet und werden nur angezeigt, nicht erzwungen.
    assert dict(g.load_import_settings(config).foreign_keys) == {"kurs_id": "GROUP_YEAR"}
    config.write_text(base, encoding="utf-8")
    assert dict(g.load_import_settings(config).foreign_keys) == {}
    for bad in ('foreign_keys: {raum_id: X}\n', 'foreign_keys: {kurs_id: "A B"}\n', 'foreign_keys: [kurs_id]\n'):
        config.write_text(base + bad, encoding="utf-8")
        with pytest.raises(Exception, match="foreign_keys"):
            g.load_import_settings(config)


def test_class_mismatch_and_unknown_gender_are_reported_not_blocking(opts):
    options = g.ImportSettings(opts.form_id, opts.page_size, opts.timeout, (), (), opts.fields, opts.queries)
    students = [
        {"matrikelnummer": 1, "vorname": "A", "nachname": "A", "geschlecht": "w", "klasse": "5-2"},
        {"matrikelnummer": 2, "vorname": "B", "nachname": "B", "geschlecht": "?", "klasse": "5-1"},
        {"matrikelnummer": 3, "vorname": "C", "nachname": "C", "geschlecht": "", "klasse": "5-1"},
    ]
    entries = [{"1": "5-1", "3": "7", "7.1": "1", "7.2": "2", "7.3": "3"}]
    warnings = []
    rows, stats = g.build_participants(entries, students, [], {7}, {}, options, warnings=warnings)
    by_student = {r["matrikelnummer"]: r for r in rows}
    # Bisheriges Verhalten bleibt: die GF-Klasse wird übernommen, aber gemeldet.
    assert by_student[1]["klasse"] == "5-1"
    assert by_student[2]["geschlecht"] is None and by_student[3]["geschlecht"] is None
    assert stats["class_mismatch"] == 1 and stats["unknown_gender"] == 1
    assert any("Matrikelnummer 1" in w and "5-2" in w for w in warnings)
    assert any("Matrikelnummer 2" in w and "Geschlecht" in w for w in warnings)
    assert not any("Matrikelnummer 3" in w for w in warnings)  # leere Angabe ist kein Fehler
    # Ohne Warnliste bleibt die Funktion abwärtskompatibel.
    assert g.build_participants(entries, students, [], {7}, {}, options)[1]["class_mismatch"] == 1


class _Response:
    def __init__(self, payload):
        self.payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self.payload


def _fake_gf_api(monkeypatch, pages):
    """Simuliert die paginierte GF-API, ohne requests_oauthlib oder Netzwerk."""
    import sys
    calls = []
    class Session:
        def __init__(self, **_kwargs):
            pass
        def mount(self, *_args):
            pass
        def get(self, _url, params, **_kwargs):
            calls.append(params["paging[current_page]"])
            return _Response(pages[params["paging[current_page]"] - 1])
        def close(self):
            pass
    monkeypatch.setitem(sys.modules, "requests_oauthlib", SimpleNamespace(OAuth1Session=Session))
    return calls


CREDENTIALS = {"api_base": "https://example.org/gf/v2", "consumer_key": "k", "consumer_secret": "s"}


def test_gf_entries_complete_download(monkeypatch):
    calls = _fake_gf_api(monkeypatch, [{"total_count": 3, "entries": [{"id": "1"}, {"id": "2"}]},
                                       {"total_count": 3, "entries": [{"id": "3"}]}])
    assert [e["id"] for e in g.gf_entries(CREDENTIALS, 16, 2, 5)] == ["1", "2", "3"]
    assert calls == [1, 2]


def test_gf_entries_rejects_incomplete_download(monkeypatch):
    _fake_gf_api(monkeypatch, [{"total_count": 4, "entries": [{"id": "1"}, {"id": "2"}]},
                               {"total_count": 4, "entries": [{"id": "4"}]}])
    with pytest.raises(g.ImportErrorGF, match="Unvollständiger GF-Abruf: 3 von 4"):
        list(g.gf_entries(CREDENTIALS, 16, 2, 5))


def test_gf_entries_rejects_entries_delivered_twice(monkeypatch):
    # Neue Meldung während des Abrufs: Eintrag 2 rutscht auf die nächste Seite.
    _fake_gf_api(monkeypatch, [{"total_count": 4, "entries": [{"id": "1"}, {"id": "2"}]},
                               {"total_count": 4, "entries": [{"id": "2"}, {"id": "3"}]}])
    with pytest.raises(g.ImportErrorGF, match="GF-Eintrag 2 wurde doppelt geliefert"):
        list(g.gf_entries(CREDENTIALS, 16, 2, 5))
