"""Schritt 2: Profile, SQL-Identifier und Besitzer-isolierte Export-Manifeste."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from mao_cli.config import (ConfigurationError, apply_profile, load_config,
                            load_profiles, load_tables, resolve_table_placeholders,
                            validate_profiles)
from mao_cli.exports import ExportPlan, run_exports
from mao_cli import cli

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = json.loads((ROOT / 'tests/fixtures/step1_reference.json').read_text(encoding='utf-8'))


def original_data():
    return pd.DataFrame([{
        'tnr': 1, 'nachname': 'Muster', 'vorname': 'Max', 'geschlecht': 'm',
        'klasse': '7-1', 'stufe': 7, 'anwesenheit': 1, 'bemerkung': '',
        'aufgabe_1': 3, 'aufgabe_2': 3, 'aufgabe_3': 4, 'aufgabe_4': 2,
        'punkte': 12, 'preis': '1. Preis', 'preisgeld': 15, 'delegierung': 1,
        'lehrkraft': 'AB', 'kurs': 'Ma7', 'raum': 'R001', 'lerngruppe': '7-1',
        'kl': 'AB', 'stellvkl': 'CD',
        'stufe_3': 0, 'stufe_4': 0, 'stufe_5': 0, 'stufe_6': 0, 'stufe_7': 7, 'stufe_8': 0, 'stufe_9': 0, 'stufe_10': 0, 'stufe_11': 0, 'stufe_12': 0, 'gesamt': 1,
        'stufengruppe': '7', 'stufencode': '07'
    }])


def base_config():
    return resolve_table_placeholders(load_config(ROOT / 'mao_cli/defaults/pipeline.yaml'), load_tables(ROOT / 'mao_cli/defaults/tables.ini'))


def test_all_sql_datasets_reference_only_pseudonymous_tables():
    cfg = base_config()
    assert len(cfg['datasets']) == 17
    assert all('{{table.' not in d['sql'] for d in cfg['datasets'])
    assert any('DEMO_ORGA' in d['sql'] for d in cfg['datasets'])


def test_profile_selection_and_datasets():
    cfg = base_config()
    profiles = load_profiles(ROOT / 'mao_cli/defaults/profiles.ini')
    validate_profiles(cfg, profiles)
    earlier = [(e['id'], e.get('enabled', True)) for e in cfg['exports']]
    prepare = apply_profile(cfg, profiles['vorbereitung'])
    assert [(e['id'], e.get('enabled', True)) for e in cfg['exports']] == earlier
    assert {e['id'] for e in prepare['exports'] if e['enabled']} == {
        'klassenleitungen', 'klassen_sek_i', 'teilnehmende', 'teilnehmende_gesamt', 'teilnehmende_raum',
        'korrekturliste', 'kopierliste', 'kopierliste_externe'}
    assert {j['name'] for j in prepare['latex']['jobs'] if j['enabled']} == {
        'raumuebersicht', 'korrekturlisten', 'aushang', 'anwesenheitslisten', 'klassenleitungslisten'}
    assert prepare['datasets'][0]['enabled'] is True
    assert [e['enabled'] for e in apply_profile(cfg, profiles['vollpaket'])['exports']] == [
        e.get('enabled', True) for e in cfg['exports']]


def test_table_config_blocks_invalid_sql_identifier(tmp_path):
    path = tmp_path / 'table.config'
    path.write_text('[table]\norga_table = foo; DROP TABLE bar', encoding='utf8')
    with pytest.raises(ConfigurationError, match='Ungültiger'):
        load_tables(path)
    cfg = {'datasets': [{'name':'x', 'sql': 'SELECT * FROM {{table.unknown}}'}]}
    with pytest.raises(ConfigurationError, match='Unbekannte Tabellenkennung'):
        resolve_table_placeholders(cfg, {'orga_table': 'x'})


def test_profile_manifests_keep_owners_separate(tmp_path):
    first = ExportPlan(tmp_path)
    first.add(tmp_path / 'one.csv', b'one', 'csv', 'owner-a')
    first.add(tmp_path / 'two.csv', b'two', 'csv', 'owner-b')
    first.commit()
    assert json.loads(first.manifest.read_text())['schema'] == 2
    partial = ExportPlan(tmp_path, active_owners={'owner-a'})
    # The first exporter no longer produces any files, the second one is not selected.
    assert ('ENTFERNEN', 'one.csv', 'alt') in partial.actions()
    assert not any(item[1] == 'two.csv' for item in partial.actions())
    partial.commit()
    assert not (tmp_path / 'one.csv').exists()
    assert (tmp_path / 'two.csv').read_bytes() == b'two'
    again = ExportPlan(tmp_path, active_owners={'owner-a'})
    again.add(tmp_path / 'one.csv', b'new', 'csv', 'owner-a')
    again.commit()
    assert (tmp_path / 'two.csv').read_bytes() == b'two'
    assert 'two.csv' in json.loads(again.manifest.read_text())['files']


def test_output_parity_same_synthetic_input(tmp_path):
    """Bytegenaue SHA256-Referenzen aus dem Step-1-Skript, fest in Tests gebündelt."""
    cfg = base_config()
    df = original_data()
    # Die Spalten der Kopierliste gehören nur zu deren Abfrage, nicht zur Step-1-Referenz
    kopier_spalten = ['gesamt', 'stufengruppe', 'stufencode'] + [f'stufe_{n}' for n in range(3, 13)]
    datasets = {d['name']: df.drop(columns=kopier_spalten) for d in cfg['datasets'] if d.get('enabled', True)}
    datasets['kopierliste'] = df.drop(columns=['stufe_3', 'stufe_4', 'stufengruppe', 'stufencode'])
    datasets['korrekturliste'] = df[['tnr', 'nachname', 'vorname', 'klasse', 'stufe', 'stufengruppe', 'stufencode']].copy()
    datasets['kopierliste_externe'] = df[['raum', 'stufe_3', 'stufe_4', 'stufe_5', 'stufe_6', 'gesamt']].copy()
    # Aufsichten (nach Step 1 ergänzt): eigener Datensatz, nicht Teil der Step-1-Referenz
    datasets['aufsichten_lehrkraefte'] = pd.DataFrame([{
        'kuerzel': 'AB', 'name': 'A B', 'raum': 'R001', 'klassenstufen': '7', 'rolle': 'Standard', 'bemerkung': ''}])
    datasets['einsatz_oberstufe'] = pd.DataFrame([{
        'nachname': 'M', 'vorname': 'E', 'klasse': '11', 'rolle': 'Hof', 'bemerkung': 'Gruppe 1', 'gruppe': 1}])
    plan = ExportPlan(tmp_path)
    run_exports(datasets, cfg, tmp_path, plan)
    actual = {name: hashlib.sha256(content).hexdigest()
              for name, (content, _, _) in plan.outputs.items() if not any(x in str(name).replace('\\', '/') for x in ('Aufsichten/', 'Einsatz-Oberstufe/'))}
    assert actual == REFERENCE['exports_sha256']


def test_cli_workspace_profile_smoke(monkeypatch, tmp_path):
    class FakeDB:
        def __init__(self, conf): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def query_df(self, sql): return original_data()
    monkeypatch.setattr(cli, 'load_db_config', lambda _: __import__('mao_cli.db', fromlist=['DBConfig']).DBConfig('test',3306,'u','','db'))
    monkeypatch.setattr(cli, 'can_reach', lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, 'DB', FakeDB)
    monkeypatch.setattr(cli, 'current_MaO_number_db', lambda db, tables: 66)
    directory = tmp_path / 'central'
    cli.init_config(directory, cli.DEFAULTS)
    monkeypatch.setenv('MAO_CONFIG', str(directory))
    workspace = tmp_path / 'year66'
    workspace.mkdir()
    (workspace / 'config').mkdir()
    (tmp_path / 'central/tables.ini').write_text((cli.DEFAULTS / 'tables.ini').read_text(encoding='utf8').replace('DEMO_', 'TEST_'), encoding='utf8')
    assert cli.main(['plan','--workspace',str(workspace),'--profile','vorbereitung','--no-latex']) == 0
    assert not (workspace / 'db').exists()
    assert cli.main(['run','--workspace',str(workspace),'--profile','vorbereitung','--no-latex']) == 0
    assert (workspace / 'db').exists()
    assert (workspace / '.mao-cli/exports-manifest.json').exists()
    assert cli.main(['validate','--workspace',str(workspace),'--profile','unknown']) == 2


def test_select_latex_jobs_runs_only_named_jobs_even_if_disabled():
    from mao_cli.config import select_latex_jobs
    cfg = {'latex': {'jobs': [{'name': 'a', 'enabled': False}, {'name': 'b', 'enabled': True}, {'name': 'c'}]}}
    select_latex_jobs(cfg, ['a'])
    assert [j['enabled'] for j in cfg['latex']['jobs']] == [True, False, False]
    with pytest.raises(ConfigurationError, match='Unbekannter LaTeX-Job: x'):
        select_latex_jobs(cfg, ['x'])


def test_cli_latex_job_option_is_repeatable():
    args = cli.parse_args(['latex', '--job', 'a', '--job', 'b'])
    assert args.job == ['a', 'b']
