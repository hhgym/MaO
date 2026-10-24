"""Zentrale Konfiguration, Jahrgangszuordnung und risikoarme Schema-Vorprüfung."""
from __future__ import annotations

from pathlib import Path
import pytest
import pandas as pd

from mao_cli import cli
from mao_cli.config import ConfigurationError
from mao_cli.workspaces import (config_dir_for, init_config, load_settings, save_settings,
                                resolve_workspace, write_wrapper, check_year)
from mao_cli.year_ops import preliminary_plan, database_preflight


def test_cli_and_env_config_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv('MAO_CONFIG', str(tmp_path/'from-env'))
    assert config_dir_for() == tmp_path/'from-env'
    assert config_dir_for(tmp_path/'explicit') == tmp_path/'explicit'
    monkeypatch.delenv('MAO_CONFIG')
    assert config_dir_for() != tmp_path/'from-env'


def test_init_is_idempotent_and_does_not_create_secrets(tmp_path):
    config = tmp_path/'central'
    created = init_config(config, cli.DEFAULTS)
    assert len(created) == 7
    assert init_config(config, cli.DEFAULTS) == []
    assert not (config/'db.config').exists()
    assert (config/'db.config.example').exists()
    assert load_settings(config)['workspaces'] == {}


def test_workspace_paths_explicit_not_computed(tmp_path):
    config = tmp_path/'central'
    init_config(config, cli.DEFAULTS)
    settings = load_settings(config)
    base = tmp_path/'school/2026-27/MaO'
    settings['workspaces']['2026-27'] = {'root':str(base),'status':'current','mao':66}
    settings['current_year']='2026-27'
    save_settings(config, settings)
    selected = resolve_workspace(load_settings(config))
    assert selected.directory == base
    assert selected.mao_number == 66
    assert resolve_workspace(load_settings(config), workspace=tmp_path/'test').directory == tmp_path/'test'
    with pytest.raises(ConfigurationError, match='verschiedene'):
        resolve_workspace(load_settings(config),year='2026-27',workspace=tmp_path/'elsewhere')


def test_invalid_years_duplicate_roots_and_relative_rejected(tmp_path):
    config=tmp_path/'central';init_config(config,cli.DEFAULTS)
    with pytest.raises(ConfigurationError): check_year('2026-25')
    settings=load_settings(config)
    settings['workspaces']['2026-27']={'root':'relative/folder'}
    with pytest.raises(ConfigurationError,match='absoluter'):
        save_settings(config,settings)
    settings['workspaces']['2026-27']={'root':str(tmp_path/'a')}
    settings['workspaces']['2027-28']={'root':str(tmp_path/'a')}
    with pytest.raises(ConfigurationError,match='dasselbe'):
        save_settings(config,settings)
    assert load_settings(config)['workspaces']=={}


def test_wrapper_generation_no_password_and_no_overwrite(tmp_path):
    root=tmp_path/'work'
    files=write_wrapper(tmp_path/'central','2026-27',root)
    bat, shell=files
    assert bat.exists() and shell.exists()
    assert '--config-dir' in bat.read_text(encoding='utf-8')
    assert '--year "2026-27" %*' in bat.read_text(encoding='utf-8')
    assert ' "$@"' in shell.read_text(encoding='utf-8')
    assert 'password' not in bat.read_text(encoding='utf-8').lower()
    with pytest.raises(ConfigurationError,match='existiert'):
        write_wrapper(tmp_path/'central','2026-27',root)


def test_cli_management_and_year_resolution(tmp_path,monkeypatch,capsys):
    config=tmp_path/'private-config';workspace=tmp_path/'school/2026-27/MaO'
    monkeypatch.setenv('MAO_CONFIG',str(config))
    assert cli.main(['config','init'])==0
    assert cli.main(['workspace','add','2026-27','--root',str(workspace)])==0
    assert cli.main(['workspace','list'])==0
    assert '2026-27' in capsys.readouterr().out
    assert cli.main(['validate','--year','2026-27'])==0
    assert cli.main(['--config-dir',str(config),'validate'])==0
    assert cli.main(['--year','2026-27','--config-dir',str(config),'validate'])==0
    assert cli.main(['validate','--workspace',str(tmp_path/'other'),'--year','2026-27'])==2
    assert cli.main(['workspace','select','2026-27'])==0
    assert cli.main(['config','show'])==0
    assert cli.main(['workspace','add','2026-27','--root',str(tmp_path/'test')])==2


def test_archived_workspace_blocks_database_export(tmp_path,monkeypatch):
    config=tmp_path/'central';init_config(config,cli.DEFAULTS)
    settings=load_settings(config)
    settings['current_year']='2026-27'
    settings['workspaces']={
        '2026-27':{'root':str(tmp_path/'current'),'status':'current'},
        '2025-26':{'root':str(tmp_path/'past'),'status':'archived'},
    }
    save_settings(config,settings)
    monkeypatch.setenv('MAO_CONFIG',str(config))
    assert cli.main(['run','--year','2025-26'])==2
    assert cli.main(['plan','--year','2025-26'])==2
    assert cli.main(['validate','--year','2025-26'])==0


def test_preflight_requires_distinct_real_tables():
    generic={k:'TEST_'+v for k,v in {
        'orga_table':'CURRENT','jahrgaenge_table':'YEAR','archive_table':'ARCHIVE',
        'runden_table':'ROUNDS','teilnahmen_table':'PARTICIPATIONS'}.items()}
    assert 'KEINE Datenänderungen' in preliminary_plan(generic,'2026-27')[-1]
    with pytest.raises(ConfigurationError): preliminary_plan({'orga_table':'TEST_X'},'2026-27')
    with pytest.raises(ConfigurationError): preliminary_plan({**generic,'archive_table':'DEMO_ARCHIVE'},'2026-27')


def test_database_preflight_only_queries_reads():
    tab={k:v for k,v in {
        'orga_table':'TEST_ORGA','jahrgaenge_table':'TEST_YEAR','archive_table':'TEST_ARCHIVE',
        'runden_table':'TEST_ROUNDS','teilnahmen_table':'TEST_PARTICIPANTS'}.items()}
    class FakeDB:
        def __init__(self): self.queries=[]
        def query_df(self,sql):
            self.queries.append(sql)
            assert sql.upper().lstrip().startswith('SELECT ')
            if 'information_schema.COLUMNS' in sql:
                return pd.DataFrame([{'TABLE_NAME':v,'COLUMN_NAME':'id','EXTRA':''} for v in tab.values()] + [
                    {'TABLE_NAME':'TEST_ORGA','COLUMN_NAME':'test_col','EXTRA':''}])
            if 'information_schema.KEY_COLUMN_USAGE' in sql:
                return pd.DataFrame(columns=['TABLE_NAME','CONSTRAINT_NAME'])
            return pd.DataFrame([{'anzahl':42}])
    fake=FakeDB()
    result=database_preflight(fake,tab)
    assert '42' in result[0]
    assert len(fake.queries)==3


def test_init_jahrgang_offline_is_read_only(tmp_path, monkeypatch, capsys):
    config=tmp_path/'central';cli.init_config(config,cli.DEFAULTS)
    settings=load_settings(config)
    settings['current_year']='2026-27'
    settings['workspaces']={'2026-27':{'root':str(tmp_path/'MaO'),'status':'current'}}
    save_settings(config,settings)
    (config/'tables.ini').write_text((cli.DEFAULTS/'tables.ini').read_text(encoding='utf8').replace('DEMO_','TEST_'),encoding='utf8')
    monkeypatch.setenv('MAO_CONFIG',str(config))
    assert cli.main(['init-jahrgang','--offline','--year','2026-27'])==0
    output=capsys.readouterr().out
    assert 'KEINE Datenänderungen' in output
    assert not (tmp_path/'MaO').exists()
    with pytest.raises(SystemExit) as ex:
        cli.parse_args(['init-jahrgang','--apply'])
    assert ex.value.code==2
