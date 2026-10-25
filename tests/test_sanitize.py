"""Schutz gegen unbeabsichtigte Veröffentlichung von realen Tabellenbezeichnungen."""
from pathlib import Path
import pytest
from mao_cli import cli
from mao_cli.config import load_tables
from mao_cli.db import current_MaO_number_db

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = ROOT / "mao_cli" / "defaults"


def test_default_table_names_are_only_demo():
    values = load_tables(DEFAULTS / "tables.ini")
    assert len(values) == 20
    assert all(v.startswith("DEMO_") for v in values.values())
    assert values["jahrgaenge_table"] == "DEMO_JAHRGAENGE"


def test_sql_current_year_uses_only_configured_identifiers():
    class StubDB:
        def query_df(self, sql):
            assert "`TEST_JAHRGAENGE`" in sql
            assert "`TEST_SCHULJAHRE`" in sql
            import pandas as pd
            return pd.DataFrame([{"jahrgang_nr": 66}])
    assert current_MaO_number_db(StubDB(), {"jahrgaenge_table":"TEST_JAHRGAENGE", "schuljahre_table":"TEST_SCHULJAHRE"}) == 66
    with pytest.raises(ValueError):
        current_MaO_number_db(StubDB(), {"jahrgaenge_table":"oops;DROP", "schuljahre_table":"x"})


def test_cli_refuses_database_run_with_demo_tables(tmp_path, monkeypatch):
    central = tmp_path / "central"
    cli.init_config(central, cli.DEFAULTS)
    monkeypatch.setenv("MAO_CONFIG", str(central))
    assert cli.main(["validate", "--workspace", str(tmp_path)]) == 0
    assert cli.main(["plan", "--workspace", str(tmp_path)]) == 2
    assert cli.main(["run", "--workspace", str(tmp_path)]) == 2


def test_no_real_table_identifiers_in_versioned_current_files():
    needles = ("LIVE_DB_TABLE", "PRODUCTION_DB_VIEW")
    checked = [ROOT / "README.md", ROOT / "wiki/Konfiguration.md", ROOT / "mao_cli/db.py", DEFAULTS / "pipeline.yaml", DEFAULTS / "tables.ini", ROOT / "tests/fixtures/step1_reference.json"]
    for path in checked:
        content = path.read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in content, (path, needle)


def test_packaged_sql_references_tables_only_via_tokens():
    """Mitgelieferte SQL nennt Tabellen nur als {{table.*}}; reale Namen stehen in der privaten tables.ini."""
    import re
    import yaml
    target = re.compile(r"\b(?:FROM|JOIN)\s+(?!\()(\S+)", re.IGNORECASE)
    pipeline = yaml.safe_load((DEFAULTS / "pipeline.yaml").read_text(encoding="utf-8"))
    gf = yaml.safe_load((DEFAULTS / "gf-import.yaml.example").read_text(encoding="utf-8"))
    sources = {f"pipeline/{item['name']}": item["sql"] for item in pipeline["datasets"]}
    sources.update({f"gf-import/{key}": sql for key, sql in gf["queries"].items()})
    assert len(sources) > 10
    for label, sql in sources.items():
        names = target.findall(sql)
        assert names, label
        assert all(name.startswith("{{table.") for name in names), (label, names)
