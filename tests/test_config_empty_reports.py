"""Regressionen für student_table, lokale Config-Auflösung und GF-Leermeldungen."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from mao_cli import cli, gf_import as g
from mao_cli import workspaces as w


def test_local_config_sibling_precedence(tmp_path, monkeypatch):
    root = tmp_path / 'MaO'
    package = root / 'mao_cli'
    package.mkdir(parents=True)
    (root / 'pyproject.toml').write_text('[project]\nname="mao-cli"\n', encoding='utf8')
    local = root / 'config'
    local.mkdir()
    monkeypatch.setattr(w, '__file__', str(package / 'workspaces.py'))
    monkeypatch.delenv('MAO_CONFIG', raising=False)
    assert w.config_dir_for() == local
    monkeypatch.setenv('MAO_CONFIG', str(tmp_path / 'override'))
    assert w.config_dir_for() == tmp_path / 'override'
    assert w.config_dir_for(tmp_path / 'explicit') == tmp_path / 'explicit'
    monkeypatch.delenv('MAO_CONFIG')
    local.rmdir()
    assert w.config_dir_for() != local


def test_init_uses_students_table_and_migrates_legacy_key(tmp_path):
    c = tmp_path / 'config'
    w.init_config(c, cli.DEFAULTS)
    content = (c / 'tables.ini').read_text(encoding='utf8')
    assert 'students_table = DEMO_CURRENT_STUDENTS' in content
    assert 'students_view =' not in content
    assert '{{table.students_table}}' in (c / 'gf-import.yaml').read_text(encoding='utf8')
    old = content.replace('students_table = DEMO_CURRENT_STUDENTS',
                          '# legacy source\nstudents_view = ORGA_SCHUELER')
    (c / 'tables.ini').write_text(old, encoding='utf8')
    # Realistische bestehende gf-import.yaml mit altem Tabellen-Token.
    gf_file = c / 'gf-import.yaml'
    gf_file.write_text(gf_file.read_text(encoding='utf8').replace('{{table.students_table}}',
                      '{{table.students_view}}'), encoding='utf8')
    created = w.init_config(c, cli.DEFAULTS)
    assert created == []
    new = (c / 'tables.ini').read_text(encoding='utf8')
    assert 'students_table = ORGA_SCHUELER' in new
    assert 'students_view =' not in new
    assert w.init_config(c, cli.DEFAULTS) == []
    # Der alte GF-Token wird zur Laufzeit in den neuen Namen überführt.
    # Tabellenname + andere SQL-Datenquellen werden für diesen Test angepasst.
    new = new.replace('teachers_table = DEMO_TEACHERS', 'teachers_table = LEHRKRAEFTE')
    new = new.replace('current_groups_view = DEMO_CURRENT_GROUP_VIEW',
                      'current_groups_view = LERNGRUPPEN')
    (c / 'tables.ini').write_text(new, encoding='utf8')
    loaded = g.load_import_settings(gf_file)
    assert 'FROM ORGA_SCHUELER' in loaded.queries['students']


def test_empty_checkbox_direct_and_subfield():
    assert g.is_empty_report({'10': 'Leermeldung'})
    assert g.is_empty_report({'10.1': 'Leermeldung'})
    assert g.is_empty_report({'10.2': ' leermeldung '})
    assert not g.is_empty_report({'10.1': '', '10': None})
    assert not g.is_empty_report({'10.1': 'Andere Auswahl'})
    assert not g.is_empty_report({'100.1': 'Leermeldung'})


def test_empty_report_import_and_mandatory_participation():
    opts = g.ImportSettings(16, 200, 60, ('5-1',), (),
        {'klasse':'1','kurs':'5','lehrkraft':'3','teilnehmende':'7','leermeldung':'10'}, {})
    students = [{'matrikelnummer': 4, 'vorname': 'Ada', 'nachname':'X', 'geschlecht':'w', 'klasse':'5-1'},
                {'matrikelnummer': 5, 'vorname': 'Ben', 'nachname':'Y', 'geschlecht':'m', 'klasse':'8-2'}]
    entries = [{'id': 17,'status':'active', '1':'5-1', '10.1':'Leermeldung'},
               {'id': 18,'status':'active','1':'8-2','3':'7','5':'11','7.1':'5'},
               {'id': 19,'status':'trash','1':'8-2','10.1':'Leermeldung'}]
    rows, stats = g.build_participants(entries, students,
        [{'klasse':'5-1','lehrkraft_id':7,'lerngruppe_schuljahr_id':10}],
        {7}, {(10,7): 100, (11,7): 101}, opts)
    assert [r['matrikelnummer'] for r in rows] == [4,5]
    assert stats['gf_entries'] == 2
    assert stats['empty_reports'] == 1
    assert stats['mandatory_added'] == 1
    assert stats['inactive_entries'] == 1
    assert rows[0]['kurs_id'] is None
    assert rows[1]['kurs_id'] is None  # auch der gemeldete Jahrgang 8


def test_empty_report_conflict_is_blocked():
    opts = g.ImportSettings(16,200,60,(),(),
        {'klasse':'1','kurs':'5','lehrkraft':'3','teilnehmende':'7'}, {})
    students = [{'matrikelnummer': 5,'vorname':'Ben','nachname':'X','geschlecht':'m','klasse':'8-2'}]
    with pytest.raises(g.ImportErrorGF, match='Leermeldung und Teilnehmende'):
        g.build_participants([{'id': 3,'1':'8-2','10.1':'Leermeldung','7.1':'5'}],students,[],set(),{},opts)
    with pytest.raises(g.ImportErrorGF, match='keine auslesbare Teilnehmerauswahl'):
        g.build_participants([{'id': 3,'1':'8-2'}],students,[],set(),{},opts)


def test_class_report_contains_empty_group_if_supported():
    if not hasattr(g, '_class_summary'):
        pytest.skip('Klassenbericht ist erst im aktuellen develop vorhanden')
    data = g._class_summary([], [{'id':1,'1':'9-1','10.1':'Leermeldung'}],
                            {'klasse':'1','leermeldung':'10'})
    assert data == [{'klasse':'9-1','gf':0,'mandatory':0,'participants':0,
                     'missing_teacher':0,'missing_course':0,'empty_reports':1}]
