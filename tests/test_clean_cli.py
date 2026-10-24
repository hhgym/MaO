"""Verifiziert den neuen Schnitt und die Wiki-Veröffentlichung ohne externe Dienste."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from mao_cli import __version__
from mao_cli import cli
from mao_cli.config import ConfigurationError, load_config, load_profiles
from mao_cli.exports import ExportPlan
from scripts.publish_wiki import publish




@pytest.fixture(autouse=True)
def central_config(monkeypatch, tmp_path):
    directory = tmp_path / 'central'
    cli.init_config(directory, cli.DEFAULTS)
    monkeypatch.setenv('MAO_CONFIG', str(directory))
    return directory


class FakeDB:
    def __init__(self, conf):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def query_df(self, sql):
        from test_profiles import original_data
        return original_data()


def stub_database(monkeypatch):
    from mao_cli.db import DBConfig
    monkeypatch.setattr(cli, 'load_db_config', lambda _: DBConfig('example.test', 3306, 'u', '', 'mock'))
    monkeypatch.setattr(cli, 'can_reach', lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, 'current_MaO_number_db', lambda db, tables: 66)
    monkeypatch.setattr(cli, 'DB', FakeDB)


def test_parser_has_commands_not_legacy_flags():
    assert set(cli.parse_args(['profiles']).__dict__) >= {'command', 'workspace'}
    assert cli.parse_args(['run']).profile is None
    assert cli.parse_args(['plan']).command == 'plan'
    assert '2.0' not in __version__
    with pytest.raises(SystemExit) as ex:
        cli.parse_args(['--dry-run'])
    assert ex.value.code == 2


def test_local_configs_have_explicit_precedence(tmp_path):
    (tmp_path / 'config').mkdir()
    local = tmp_path / 'central/profiles.ini'
    local.write_text('[custom]\nexports=none\nlatex_jobs=none\n', encoding='utf8')
    assert cli.resolve_config_file(tmp_path / 'central', 'profiles.ini') == local
    assert cli.resolve_config_file(tmp_path / 'central', 'tables.ini') == tmp_path / 'central/tables.ini'
    assert cli.main(['profiles', '--workspace', str(tmp_path)]) == 0
    assert cli.main(['validate', '--workspace', str(tmp_path), '--profile', 'custom']) == 0
    assert cli.main(['validate', '--workspace', str(tmp_path), '--profile', 'vollpaket']) == 2


def test_config_requires_ids_in_all_jobs(tmp_path):
    cfg = load_config(cli.DEFAULTS / 'pipeline.yaml')
    cfg['latex']['jobs'][0].pop('id')
    local_cfg = tmp_path / 'central'
    local_cfg.mkdir(exist_ok=True)
    (local_cfg / 'pipeline.yaml').write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding='utf8')
    assert cli.main(['validate', '--workspace', str(tmp_path)]) == 2


def test_manifest_old_format_rejected(tmp_path):
    manifest = tmp_path / '.mao-cli/exports-manifest.json'
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({'schema':1, 'files': {}}), encoding='utf8')
    with pytest.raises(ConfigurationError, match='Schema 2'):
        ExportPlan(tmp_path, active_owners={'abc'})


def test_manifest_version2_tracks_owners(tmp_path):
    p = ExportPlan(tmp_path, active_owners={'a'})
    p.add(tmp_path / 'new.csv', b'123', 'csv', 'a')
    p.commit()
    doc = json.loads(p.manifest.read_text())
    assert doc == {'schema':2, 'files': {'new.csv': {'sha256': hashlib.sha256(b'123').hexdigest(), 'owner': 'a'}}}


def test_plan_writes_only_explicit_markdown(monkeypatch, tmp_path):
    stub_database(monkeypatch)
    (tmp_path / 'config').mkdir(exist_ok=True)
    (tmp_path / 'central/tables.ini').write_text((cli.DEFAULTS / 'tables.ini').read_text(encoding='utf8').replace('DEMO_', 'TEST_'), encoding='utf8')
    assert cli.main(['plan', '--workspace', str(tmp_path), '--profile', 'vorbereitung', '--no-latex', '--output', 'plan.md']) == 0
    assert (tmp_path / 'plan.md').exists()
    assert not (tmp_path / 'Listen').exists()
    assert not (tmp_path / '.mao-cli/exports-manifest.json').exists()


def test_plan_cannot_overwrite_output_or_config(monkeypatch, tmp_path):
    stub_database(monkeypatch)
    cfgdir = tmp_path / 'central'
    cfgdir.mkdir(exist_ok=True)
    (cfgdir / 'tables.ini').write_text((cli.DEFAULTS / 'tables.ini').read_text(encoding='utf8').replace('DEMO_', 'TEST_'), encoding='utf8')
    (cfgdir / 'pipeline.yaml').write_bytes((cli.DEFAULTS / 'pipeline.yaml').read_bytes())
    assert cli.main(['plan', '--workspace', str(tmp_path), '--profile', 'vorbereitung', '--output', str(cfgdir / 'pipeline.yaml')]) == 2


def test_missing_db_stops_run_but_not_validation(tmp_path):
    assert cli.main(['validate', '--workspace', str(tmp_path)]) == 0
    assert cli.main(['run', '--workspace', str(tmp_path), '--no-latex']) == 2


def test_wiki_publisher_dry_run_no_network():
    pages = publish('https://example.invalid/repo.wiki.git', dry_run=True)
    assert {'Home.md','Befehle.md','_Sidebar.md'} <= set(pages)
    with pytest.raises(RuntimeError, match='Wiki-Remote-URL'):
        publish('https://example.invalid/repo.git', dry_run=True)


def test_wiki_publisher_local_bare_repo(monkeypatch, tmp_path):
    from shutil import which
    if which('git') is None:
        pytest.skip('Git nicht installiert')
    bare = tmp_path / 'test.wiki.git'
    subprocess.run(['git','init','--bare',str(bare)],check=True,capture_output=True)
    monkeypatch.setenv('GIT_AUTHOR_NAME', 'MaO Tests')
    monkeypatch.setenv('GIT_AUTHOR_EMAIL', 'mao-tests@example.invalid')
    monkeypatch.setenv('GIT_COMMITTER_NAME', 'MaO Tests')
    monkeypatch.setenv('GIT_COMMITTER_EMAIL', 'mao-tests@example.invalid')
    pages = publish(str(bare))
    assert 'Home.md' in pages
    # Wiederholte Veröffentlichung ohne Änderungen darf ebenfalls erfolgreich sein.
    assert publish(str(bare)) == pages
    clone = tmp_path / 'wiki-clone'
    subprocess.run(['git', 'clone', str(bare), str(clone)], check=True, capture_output=True)
    assert (clone / 'Befehle.md').exists()
    assert (clone / 'Home.md').read_text(encoding='utf8').startswith('# MaO')
