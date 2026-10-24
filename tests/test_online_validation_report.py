"""Offline-Regression: Onlineschalter und GF-Prüfberichte ohne produktive Daten."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mao_cli import cli
from mao_cli import gf_import as gf
from mao_cli import validation as v
from mao_cli.config import ConfigurationError
from mao_cli.workspaces import init_config, load_settings, save_settings


class Cursor:
    def __init__(self, source: dict[str, list[dict]], *, count: int = 0,
                 bad_columns: dict[str, set[str]] | None = None):
        self.source = source
        self.count = count
        self.bad_columns = bad_columns or {}
        self.executed: list[str] = []
        self.current = []
        self.column_names: tuple[str, ...] = ()
        self.sql = ''

    def execute(self, sql, params=None):
        assert sql.strip().upper().startswith(('SELECT ', 'SHOW COLUMNS'))
        self.sql = sql
        self.executed.append(sql)
        if 'SHOW COLUMNS' in sql:
            self.current = [(col,) for col in gf.WRITE_COLUMNS]
        elif 'COUNT(*)' in sql and 'FROM `ORG_TARGET`' in sql:
            self.current = [(self.count,)]
        else:
            key = next((key for key in self.source if key in sql), '')
            self.current = [tuple(item.values()) for item in self.source.get(key, [])]
            column_names = tuple(self.source[key][0].keys()) if key and self.source[key] else {
                'STUDENT_SQL': ('matrikelnummer', 'vorname', 'nachname', 'geschlecht', 'klasse'),
                'TEACHER_SQL': ('id',),
                'COURSE_SQL': ('lerngruppe_schuljahr_id', 'lehrkraft_id'),
                'MANDATORY_SQL': ('klasse', 'lehrkraft_id', 'lerngruppe_schuljahr_id'),
                'PIPELINE_SQL': ('x',),
            }.get(key, ())
            self.column_names = tuple(self.bad_columns.get(key, column_names))

    def fetchall(self):
        return self.current

    def fetchone(self):
        return self.current[0]

    def close(self):
        pass


@pytest.fixture
def config(tmp_path):
    path = tmp_path / 'central'
    init_config(path, cli.DEFAULTS)
    path.joinpath('tables.ini').write_text('[table]\norga_table = ORG_TARGET\n', encoding='utf-8')
    path.joinpath('gf-import.yaml').write_text('''form_id: 16
fields: {klasse: "1", kurs: "5", lehrkraft: "3", teilnehmende: "7"}
mandatory_classes: ["5-1"]
optional_mandatory_classes: []
queries:
  students: SELECT * FROM STUDENT_SQL
  teachers: SELECT * FROM TEACHER_SQL
  courses: SELECT * FROM COURSE_SQL
  mandatory: SELECT * FROM MANDATORY_SQL
''', encoding='utf-8')
    path.joinpath('gf.config').write_text('''[oauth1]
api_base=https://example.org/wp-json/gf/v2
consumer_key=test
consumer_secret=test
''', encoding='utf-8')
    return path


def sources():
    return {
        'STUDENT_SQL': [{'matrikelnummer': 1, 'vorname': 'Ada', 'nachname': 'B', 'geschlecht': 'w', 'klasse': '5-1'},
                        {'matrikelnummer': 2, 'vorname': 'Ben', 'nachname': 'C', 'geschlecht': 'm', 'klasse': '5-1'}],
        'TEACHER_SQL': [{'id': 7}],
        'COURSE_SQL': [{'lerngruppe_schuljahr_id': 10, 'lehrkraft_id': 7}],
        'MANDATORY_SQL': [{'klasse': '5-1', 'lehrkraft_id': 7, 'lerngruppe_schuljahr_id': 10}],
        'PIPELINE_SQL': [{'x': 1}],
    }


def test_validate_online_checks_schema_and_api_readonly(monkeypatch, config):
    cursor = Cursor(sources())
    monkeypatch.setattr(v, 'gf_entries', lambda *_: iter([{'id': 2}]))
    results = v.validate_online(SimpleNamespace(conn=SimpleNamespace(cursor=lambda: cursor)), config,
                                {'orga_table': 'ORG_TARGET'}, {'datasets': [{'name': 'test', 'sql': 'SELECT x FROM PIPELINE_SQL'}]})
    assert any('Pflichtklassen: 5-1' in line for line in results)
    assert any('Gravity Forms:' in line for line in results)
    assert any('bereit für Erstimport' in line for line in results)
    assert not any('INSERT' in sql or 'UPDATE' in sql or 'DELETE' in sql for sql in cursor.executed)


def test_validate_online_detects_missing_columns_or_bad_fk(monkeypatch, config):
    monkeypatch.setattr(v, 'gf_entries', lambda *_: pytest.fail('GF API must not be called on database errors'))
    cursor = Cursor(sources(), bad_columns={'COURSE_SQL': {'lerngruppe_schuljahr_id'}})
    with pytest.raises(gf.ImportErrorGF, match='fehlende Spalten'):
        v.validate_online(SimpleNamespace(conn=SimpleNamespace(cursor=lambda: cursor)), config,
                          {'orga_table': 'ORG_TARGET'}, {'datasets': []})
    bad_sources = sources()
    bad_sources['COURSE_SQL'][0]['lehrkraft_id'] = 9
    cursor = Cursor(bad_sources)
    with pytest.raises(gf.ImportErrorGF, match='unbekannte Lehrkraft'):
        v.validate_online(SimpleNamespace(conn=SimpleNamespace(cursor=lambda: cursor)), config,
                          {'orga_table': 'ORG_TARGET'}, {'datasets': []})


def test_validate_online_warns_on_nonempty_target(monkeypatch, config):
    cursor = Cursor(sources(), count=17)
    monkeypatch.setattr(v, 'gf_entries', lambda *_: iter([]))
    results = v.validate_online(SimpleNamespace(conn=SimpleNamespace(cursor=lambda: cursor)), config,
                                {'orga_table': 'ORG_TARGET'}, {'datasets': []})
    assert any('17 Datensätze' in line and 'gesperrt' in line for line in results)


def test_cli_validate_online_optional(monkeypatch, tmp_path):
    directory = tmp_path / 'config'
    init_config(directory, cli.DEFAULTS)
    monkeypatch.setenv('MAO_CONFIG', str(directory))
    assert cli.main(['validate']) == 0  # Darf nicht einmal die DB laden
    monkeypatch.setattr(cli, 'load_db_config', lambda *_: SimpleNamespace(host='localhost', port=3306))
    monkeypatch.setattr(cli, 'can_reach', lambda *_a, **_kw: True)
    class MockDB:
        def __init__(self, *_): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
    monkeypatch.setattr(cli, 'DB', MockDB)
    called = []
    monkeypatch.setattr(cli, 'validate_online', lambda *args: called.append(1) or ['OK'])
    assert cli.main(['validate', '--online']) == 0
    assert called == [1]
    assert cli.parse_args(['validate']).online is False
    assert cli.parse_args(['import-gf', '--dry-run', '--report', 'reports/check.json']).report == Path('reports/check.json')


def test_import_report_tracks_class_source_and_no_personal_details(tmp_path):
    mapped = [
        {'klasse': '5-1', 'stufe': 5, '_source': 'gf', 'lehrkraft_id': 7, 'kurs_id': None},
        {'klasse': '5-1', 'stufe': 5, '_source': 'mandatory', 'lehrkraft_id': 7, 'kurs_id': None},
        {'klasse': '11', 'stufe': 11, '_source': 'gf', 'lehrkraft_id': None, 'kurs_id': None},
    ]
    summary = gf._class_summary(mapped)
    assert summary[0]['klasse'] == '5-1' and summary[0]['mandatory'] == 1 and summary[0]['missing_course'] == 0
    assert summary[1]['klasse'] == '11' and summary[1]['missing_teacher'] == 1 and summary[1]['missing_course'] == 1
    report = {'schema_version': 1, 'status': 'ready', 'mode': 'dry-run', 'issues': [],
              'classes': summary, 'summary': {'participants': 3, 'gf_entries': 2,
                                              'mandatory_added': 1, 'missing_teacher': 1, 'missing_course_total': 1}}
    workspace = tmp_path / 'workspace'
    target = gf.write_import_report(workspace, tmp_path / 'private', Path('reports/check.json'), report)
    loaded = json.loads(target.read_text(encoding='utf-8'))
    assert loaded['classes'][0]['mandatory'] == 1
    assert 'vorname' not in target.read_text() and 'matrikelnummer' not in target.read_text()
    with pytest.raises(ConfigurationError, match='existiert bereits'):
        gf.write_import_report(workspace, tmp_path / 'private', Path('reports/check.json'), report)
    with pytest.raises(ConfigurationError, match='innerhalb des Workspaces'):
        gf.write_import_report(workspace, tmp_path / 'private', Path('../private/secret.json'), report)
    csv_target = gf.write_import_report(workspace, tmp_path / 'private', Path('check.csv'), report)
    with csv_target.open(encoding='utf-8') as handle:
        rows = list(csv.DictReader(handle))
    assert rows[-1]['klasse'] == 'GESAMT' and rows[-1]['participants'] == '3'


def test_blocked_report_has_issue_in_csv_and_json(tmp_path):
    report = {'schema_version': 1, 'mode': 'dry-run', 'status': 'blocked',
              'issues': ['Matrikelnummer 9 wurde mehrfach angemeldet'], 'classes': [], 'summary': {}}
    destination = gf.write_import_report(tmp_path / 'workspace', tmp_path / 'config', Path('blocked.csv'), report)
    assert 'mehrfach' in destination.read_text(encoding='utf-8')


def test_import_build_report_without_database_writes(monkeypatch, config):
    opts = gf.load_import_settings(config / 'gf-import.yaml')
    data = sources()
    def fake_rows(cursor, sql):
        return next((rows for key,rows in data.items() if key in sql), [])
    monkeypatch.setattr(gf, '_rows', fake_rows)
    monkeypatch.setattr(gf, 'gf_entries', lambda *args: iter([{'id': 1, 'status': 'active', '1': '5-1',
                                                              '3': '7', '5': '10', '7.1': '1'}]))
    monkeypatch.setattr(gf, 'persist_import', lambda *args, **kwargs: 0 if kwargs['dry_run'] else pytest.fail('write'))
    report = {'status': 'blocked', 'issues': [], 'classes': [], 'summary': {}}
    db = SimpleNamespace(conn=SimpleNamespace(cursor=lambda: SimpleNamespace(close=lambda: None)))
    result = gf.import_participants(db, config, 'ORG_TARGET', dry_run=True, report=report)
    assert result['participants'] == 2 and result['mandatory_added'] == 1
    assert report['status'] == 'ready'
    assert report['classes'][0]['gf'] == 1 and report['classes'][0]['mandatory'] == 1


def test_multiple_invalid_form_entries_are_collected():
    students = [{'matrikelnummer': 1}, {'matrikelnummer': 2}]
    entries = [{'id': 10, 'status': 'active', '7.1': '1', '7.2': '999'},
               {'id': 11, 'status': 'active', '7.1': '1'},
               {'id': 12, 'status': 'spam', '7.1': '999'},
               {'id': 13, 'status': 'active', '7.1': 'none'}]
    issues = gf._entry_id_issues(entries, students, '7')
    assert len(issues) == 3
    assert 'unbekannte Matrikelnummer 999' in issues[0]
    assert 'mehrfach angemeldet' in issues[1]
    assert 'ohne Matrikelnummer' in issues[2]


def test_report_restricts_apply_and_escapes_spreadsheet_formula(tmp_path):
    with pytest.raises(SystemExit):
        cli.parse_args(['import-gf', '--apply', '--report', 'result.csv'])
    report = {'status': 'blocked', 'issues': ['=HYPERLINK("https://unsafe.example")'],
              'classes': [], 'summary': {}}
    target = gf.write_import_report(tmp_path/'work', tmp_path/'cfg', Path('output.csv'), report)
    with target.open(encoding='utf-8') as handle:
        row = list(csv.DictReader(handle))[-1]
    assert row['issue'].startswith("'=")


def test_import_report_on_duplicate_matrikel_is_blocked_without_persist(monkeypatch, config):
    data = sources()
    monkeypatch.setattr(gf, '_rows', lambda cursor,sql: next((rows for key, rows in data.items() if key in sql), []))
    monkeypatch.setattr(gf, 'gf_entries', lambda *_: iter([
        {'id': 1, '7.1': '1', '3': '7', '5': '10', '1': '5-1'},
        {'id': 2, '7.1': '1', '3': '7', '5': '10', '1': '5-1'},
    ]))
    monkeypatch.setattr(gf, 'persist_import', lambda *args, **kw: pytest.fail('must not write'))
    report = {'status': 'blocked', 'issues': [], 'classes': [], 'summary': {}}
    db = SimpleNamespace(conn=SimpleNamespace(cursor=lambda: SimpleNamespace(close=lambda: None)))
    with pytest.raises(gf.ImportErrorGF, match='fehlerhafte Teilnehmerzuordnung'):
        gf.import_participants(db, config, 'ORG_TARGET', dry_run=True, report=report)
    assert len(report['issues']) == 1
    assert report['status'] == 'blocked'
