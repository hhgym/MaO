"""Offline-Prüfungen des Gravity-Forms-Erstimports; keine realen Tabellen oder Schlüssel."""
from pathlib import Path
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
    assert items[0]["kurs_id"] == 83 and items[0]["lehrkraft_id"] == 7
    assert items[1]["kurs_id"] == 84 and items[1]["stufe"] == 5
    assert stat == {"gf_entries": 1, "inactive_entries": 1, "mandatory_added": 1,
                    "mandatory_classes_present": 1, "optional_classes_present": 0}


def test_duplicate_matrikel_fails(opts):
    students = [{"matrikelnummer": 12, "vorname": "Ada", "nachname": "X", "geschlecht": "w", "klasse": "11"}]
    entries = [{"1": "11", "7.1": "12"}, {"1": "11", "7.1": "12"}]
    with pytest.raises(g.ImportErrorGF, match="mehrfach"):
        g.build_participants(entries, students, [], {}, {}, opts)


def test_unknown_student_fails(opts):
    with pytest.raises(g.ImportErrorGF, match="nicht im Schülerverzeichnis"):
        g.build_participants([{"1": "11", "7.1": "42"}], [], [], set(), {}, opts)


def test_course_ids_are_not_assumed_identical(opts):
    assert g._course_links([{"lerngruppe_schuljahr_id": 124, "lehrkraft_id": 7, "lerngruppen_einsatz_id": 920}]) == {(124, 7): 920}
    with pytest.raises(g.ImportErrorGF, match="Mehrdeutige"):
        g._course_links([{"lerngruppe_schuljahr_id": 124, "lehrkraft_id": 7, "lerngruppen_einsatz_id": 920},
                         {"lerngruppe_schuljahr_id": 124, "lehrkraft_id": 7, "lerngruppen_einsatz_id": 921}])
    with pytest.raises(g.ImportErrorGF, match="Kein eindeutiger Einsatz"):
        g._resolve_course("124", 7, {(200, 7): 920})


def test_same_group_different_teachers_have_distinct_einsatz_ids(opts):
    links = g._course_links([
        {"lerngruppe_schuljahr_id": 100, "lehrkraft_id": 7, "lerngruppen_einsatz_id": 900},
        {"lerngruppe_schuljahr_id": 100, "lehrkraft_id": 8, "lerngruppen_einsatz_id": 901},
    ])
    assert g._resolve_course("100", 7, links) == 900
    assert g._resolve_course("100", 8, links) == 901
    with pytest.raises(g.ImportErrorGF, match="Kein eindeutiger Einsatz"):
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
    assert {row["kurs_id"] for row in rows} == {900, 901}


def test_mandatory_required_missing_and_ambiguous_teacher(opts):
    students = [{"matrikelnummer": 12, "vorname": "Ada", "nachname": "X", "geschlecht": "w", "klasse": "5-1"}]
    with pytest.raises(g.ImportErrorGF, match="Erforderliche Pflichtklasse"):
        g.build_participants([], students, [], {7}, {}, opts)
    with pytest.raises(g.ImportErrorGF, match="Mehrdeutige Mathe-Lerngruppe/Lehrkraft"):
        g.build_participants([], students, [{"klasse": "5-1", "lehrkraft_id": 7, "lerngruppe_schuljahr_id": 100},
                                           {"klasse": "5-1", "lehrkraft_id": 8, "lerngruppe_schuljahr_id": 100}], {7, 8}, {(100, 7): 900, (100, 8): 901}, opts)


def test_course_id_mapped_for_non_oberstufe(opts):
    students = [{"matrikelnummer": 12, "vorname": "Ada", "nachname": "X", "geschlecht": "w", "klasse": "5-1"}]
    entries = [{"1": "5-1", "3": "7", "5": "100", "7.1": "12"}]
    result, _ = g.build_participants(entries, students,
                                      [{"klasse": "5-1", "lehrkraft_id": 7, "lerngruppe_schuljahr_id": 100}], {7}, {(100, 7): 900}, opts)
    assert result[0]["kurs_id"] == 900


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
    text = text.replace("students_view = DEMO_CURRENT_STUDENTS", "students_view = STUDENT_CURRENT")
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
    tables_path.write_text(text.replace("teachers_table = DEMO_TEACHERS\n", "").replace("students_view = DEMO_CURRENT_STUDENTS", "students_view = STUDENT_CURRENT").replace("current_groups_view = DEMO_CURRENT_GROUP_VIEW", "current_groups_view = GROUP_CURRENT"), encoding="utf-8")
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
    def execute(self, sql, params=None):
        self.last = sql
        self.executed.append((sql, params))
    def fetchall(self):
        if "SHOW COLUMNS" in self.last:
            return [(x,) for x in g.WRITE_COLUMNS]
        return []
    def fetchone(self):
        if "GET_LOCK" in self.last or "RELEASE_LOCK" in self.last:
            return (1,)
        return (self.present + self.inserted,)
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
    assert "students_view = DEMO_CURRENT_STUDENTS" in text
    assert "current_groups_view = DEMO_CURRENT_GROUP_VIEW" in text
    assert "{{table.teachers_table}}" in (tmp_path / "gf-import.yaml").read_text(encoding="utf-8")
    init_config(tmp_path, cli.DEFAULTS)
    assert (tmp_path / "tables.ini").read_text(encoding="utf-8") == text
