"""Regression tests without credentials, MySQL server or LaTeX installation."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import pandas as pd
import pytest

from mao_cli.config import ConfigurationError, load_config, validate_config, resolve_table_placeholders, load_tables
from mao_cli.transforms import _apply_cast, _apply_sort
from mao_cli.exports import ExportPlan, run_exports, load_datasets, render_plan
from mao_cli.latex import run_latex_jobs
from mao_cli.common import atomic_write
import mao_cli.latex as latex_module
import mao_cli.exports as exports_mod

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = ROOT / 'mao_cli/defaults'


@pytest.fixture
def sample_config():
    return resolve_table_placeholders(load_config(DEFAULTS / "pipeline.yaml"), load_tables(DEFAULTS / "tables.ini"))


def sample_dataset():
    return pd.DataFrame([{
        "tnr": 1, "nachname": "Muster", "vorname": "Max", "geschlecht": "m",
        "klasse": "7-1", "stufe": 7, "anwesenheit": 1, "bemerkung": "",
        "aufgabe_1": 3, "aufgabe_2": 3, "aufgabe_3": 4, "aufgabe_4": 2,
        "punkte": 12, "preis": "1. Preis", "preisgeld": 15, "delegierung": 1,
        "lehrkraft": "AB", "kurs": "Ma7", "raum": "R001", "lerngruppe": "7-1",
        "kl": "AB", "stellvkl": "CD"
    }])


def make_datasets(config, df=None):
    df = sample_dataset() if df is None else df
    return {d["name"]: df.copy() for d in config["datasets"] if d.get("enabled", True)}


def test_original_pipeline_validates(sample_config, tmp_path):
    assert validate_config(sample_config, tmp_path) == []
    assert len(sample_config["exports"]) == 25


def test_invalid_sort_is_not_silent():
    with pytest.raises(KeyError, match="Sortierspalte"):
        _apply_sort(sample_dataset(), [{"column": "Preis", "order": "asc"}])
    with pytest.raises(ValueError, match="Sortierrichtung"):
        _apply_sort(sample_dataset(), [{"column": "preis", "order": "up"}])


def test_cast_invalids_are_visible(caplog):
    df = pd.DataFrame({"punkte": ["2", "abc", None]})
    cast = _apply_cast(df, {"punkte": "Int64"})
    assert cast["punkte"].tolist()[0] == 2
    assert pd.isna(cast["punkte"].iloc[1])
    assert "1 nichtleere Werte" in caplog.text
    with pytest.raises(KeyError):
        _apply_cast(df, {"aufgabe_99": "Int64"})


def test_export_actual_config_and_manifest_cleanup(sample_config, tmp_path):
    data = make_datasets(sample_config)
    plan = ExportPlan(tmp_path)
    run_exports(data, sample_config, tmp_path, plan)
    assert len(plan.outputs) > 30
    assert any(x.endswith("Tabellen-Rangliste-Stufe.tex") for x in plan.outputs)
    counts = plan.commit()
    assert counts["NEU"] == len(plan.outputs)
    old_split = tmp_path / "db/Teilnehmende/Klasse/Teilnehmende-Klasse-7-1.csv"
    assert old_split.exists()
    # Regression: a formerly used split is stale if its group disappears.
    data["orga"]["klasse"] = "7-2"
    second = ExportPlan(tmp_path)
    run_exports(data, sample_config, tmp_path, second)
    actions = second.actions()
    assert ("ENTFERNEN", old_split.relative_to(tmp_path).as_posix(), "alt") in actions
    second.commit()
    assert not old_split.exists()
    assert (tmp_path / ".mao-cli/exports-manifest.json").is_file()


def test_stale_modified_file_is_preserved(tmp_path):
    plan = ExportPlan(tmp_path)
    target = tmp_path / "out.csv"
    plan.add(target, b"generated", "csv", "x")
    plan.commit()
    target.write_bytes(b"edited-by-user")
    subsequent = ExportPlan(tmp_path)
    assert ("SCHÜTZEN (geändert)", "out.csv", "alt") in subsequent.actions()
    subsequent.commit()
    assert target.read_bytes() == b"edited-by-user"


def test_empty_dataset_replaces_previously_generated_csv(sample_config, tmp_path):
    data = make_datasets(sample_config)
    first = ExportPlan(tmp_path)
    run_exports(data, sample_config, tmp_path, first)
    first.commit()
    overall = tmp_path / "db/Rangliste/Rangliste-Gesamt.csv"
    assert "Muster" in overall.read_text(encoding="utf-8")
    data["orga"] = data["orga"].iloc[:0].copy()
    second = ExportPlan(tmp_path)
    run_exports(data, sample_config, tmp_path, second)
    second.commit()
    assert overall.exists()
    assert "Muster" not in overall.read_text(encoding="utf-8")
    assert "nachname" in overall.read_text(encoding="utf-8")
    tex = tmp_path / "db/Include/Tabellen-Rangliste-Stufe.tex"
    assert "\\newcommand{\\RanglisteStufenListe}{}" in tex.read_text(encoding="utf-8")


def test_dry_run_does_not_write_export_files(sample_config, tmp_path):
    datasets = make_datasets(sample_config)
    plan = ExportPlan(tmp_path)
    run_exports(datasets, sample_config, tmp_path, plan)
    assert not (tmp_path / "db").exists()
    assert not plan.manifest.exists()
    assert "NEU" in render_plan(plan.actions(), ["urkunden"], 66, source="test")


def test_dataset_loading_only_used_by_enabled_exports(sample_config):
    needed = {e['from_dataset'] for e in sample_config['exports'] if e.get('enabled', True)}
    class FakeDB:
        def __init__(self): self.queries = []
        def query_df(self, sql):
            self.queries.append(sql)
            return sample_dataset()
    db = FakeDB()
    datasets = load_datasets(db, sample_config)
    assert set(datasets) == needed
    assert len(db.queries) == len(needed)


def test_collision_detection_and_outside_path(tmp_path):
    plan = ExportPlan(tmp_path)
    plan.add(tmp_path / "test.csv", b"hi", "csv", "one")
    with pytest.raises(ConfigurationError, match="doppelt"):
        plan.add(tmp_path / "test.csv", b"bye", "csv", "two")
    with pytest.raises(ConfigurationError, match="außerhalb"):
        plan.add(tmp_path.parent / "external.csv", b"no", "csv", "two")


def test_failed_macro_does_not_publish_anything(sample_config, tmp_path):
    bad = make_datasets(sample_config)
    bad["orga"].drop(columns=["stufe"], inplace=True)
    plan = ExportPlan(tmp_path)
    with pytest.raises(KeyError):
        run_exports(bad, sample_config, tmp_path, plan)
    assert not list(tmp_path.iterdir())


def test_invalid_configuration_is_error(sample_config, tmp_path):
    sample_config['exports'][6]['from_dataset'] = 'DOES_NOT_EXIST'
    with pytest.raises(ConfigurationError, match='DOES_NOT_EXIST'):
        validate_config(sample_config, tmp_path)


def test_latex_missing_tex_warning(tmp_path, caplog):
    config = {"latex": {"runs": 1, "engine": "lualatex", "jobs": [{"name": "missing", "tex": "missing.tex"}]}}
    stats = run_latex_jobs(config, tmp_path, 66)
    assert stats['skipped'] == 1
    assert 'Quelldatei fehlt' in caplog.text


def test_latex_stale_pdf_is_rejected_and_wrapper_removed(tmp_path, monkeypatch):
    directory = tmp_path / 'tex'
    directory.mkdir()
    (directory / 'document.tex').write_text('Test', encoding='utf-8')
    (directory / 'document.pdf').write_bytes(b'OLD')
    config = {"latex": {"runs": 1, "engine": "lualatex", "jobs": [{"name": "test", "tex": "document.tex", "workdir": "tex"}]}}
    monkeypatch.setattr(latex_module.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='', stderr=''))
    with pytest.raises(RuntimeError, match='nicht neu erzeugt'):
        run_latex_jobs(config, tmp_path, 66)
    assert not (directory / '.__mao_cli_wrap__document.tex').exists()


def test_commit_rollback_on_publication_failure(tmp_path, monkeypatch):
    initial = ExportPlan(tmp_path)
    initial.add(tmp_path / 'first.csv', b'old first', 'csv', 'x')
    initial.add(tmp_path / 'second.csv', b'old second', 'csv', 'x')
    initial.commit()
    old_manifest = initial.manifest.read_bytes()
    next_plan = ExportPlan(tmp_path)
    next_plan.add(tmp_path / 'first.csv', b'new first', 'csv', 'x')
    next_plan.add(tmp_path / 'second.csv', b'new second', 'csv', 'x')
    original = atomic_write
    already_failed = False
    def fail_once(path, payload):
        nonlocal already_failed
        if path.name == 'second.csv' and not already_failed:
            already_failed = True
            raise OSError('disk full')
        return original(path, payload)
    monkeypatch.setattr(exports_mod, 'atomic_write', fail_once)
    with pytest.raises(OSError, match='disk full'):
        next_plan.commit()
    assert (tmp_path / 'first.csv').read_bytes() == b'old first'
    assert (tmp_path / 'second.csv').read_bytes() == b'old second'
    assert initial.manifest.read_bytes() == old_manifest


def test_latex_valid_output_copied_and_wrapper_removed(tmp_path, monkeypatch):
    directory = tmp_path / 'tex'
    directory.mkdir()
    (directory / 'document.tex').write_text('Test', encoding='utf-8')
    config = {"latex": {"runs": 1, "engine": "lualatex", "jobs": [
        {"name": "test", "tex": "document.tex", "workdir": "tex",
         "copy_pdf": {"from": "document.pdf", "to": "MaO_{mao}_document.pdf"}}]}}
    def simulate_engine(args, cwd, **kw):
        (Path(cwd) / 'document.pdf').write_bytes(b'PDF')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(latex_module.subprocess, 'run', simulate_engine)
    stats = run_latex_jobs(config, tmp_path, 66)
    assert stats['compiled'] == 1
    assert (directory / 'MaO_66_document.pdf').read_bytes() == b'PDF'
    assert not (directory / '.__mao_cli_wrap__document.tex').exists()


def test_latex_engine_failure_does_not_copy_old_pdf(tmp_path, monkeypatch):
    directory = tmp_path / 'tex'
    directory.mkdir()
    (directory / 'document.tex').write_text('Test', encoding='utf-8')
    (directory / 'document.pdf').write_bytes(b'old')
    config = {"latex": {"runs": 1, "jobs": [
        {"name": "bad", "tex": "document.tex", "workdir": "tex",
         "copy_pdf": {"to": "export.pdf"}}]}}
    monkeypatch.setattr(latex_module.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=1, stdout='error', stderr='bad'))
    with pytest.raises(RuntimeError, match='LaTeX fehlgeschlagen'):
        run_latex_jobs(config, tmp_path, 66)
    assert not (directory / 'export.pdf').exists()


def _engine_writing(names, calls=None):
    def simulate(args, cwd, **kw):
        if calls is not None:
            calls.append(args)
        job = [a for a in args if a.startswith("-jobname=")][0].split("=", 1)[1]
        (Path(cwd) / f"{job}.pdf").write_bytes(b"PDF")
        for suffix in names:
            (Path(cwd) / f"{job}{suffix}").write_text("x")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return simulate


def test_latex_pdf_name_creates_final_pdf_directly(tmp_path, monkeypatch):
    d = tmp_path / "Doc"
    d.mkdir(parents=True)
    (d / "document.tex").write_text("T", encoding="utf-8")
    config = {"latex": {"runs": 1, "jobs": [
        {"name": "t", "tex": "document.tex", "workdir": "Doc", "pdf_name": "MaO_{mao}_Liste"}]}}
    calls = []
    monkeypatch.setattr(latex_module.subprocess, "run", _engine_writing([".aux", ".log"], calls))
    run_latex_jobs(config, tmp_path, 66)
    assert "-jobname=MaO_66_Liste" in calls[0]
    assert (d / "MaO_66_Liste.pdf").read_bytes() == b"PDF"
    assert not (d / "document.pdf").exists()
    assert (d / "MaO_66_Liste.aux").exists()  # ohne Wunsch kein Aufräumen
    assert not (d / ".__mao_cli_wrap__MaO_66_Liste.tex").exists()


def test_latex_cleanup_only_on_request_and_keeps_pdf(tmp_path, monkeypatch):
    d = tmp_path / "t"
    d.mkdir()
    (d / "document.tex").write_text("T", encoding="utf-8")
    (d / "other.aux").write_text("keep")
    config = {"latex": {"runs": 1, "jobs": [
        {"name": "t", "tex": "document.tex", "workdir": "t", "pdf_name": "Out"}]}}
    monkeypatch.setattr(latex_module.subprocess, "run", _engine_writing([".aux", ".log", ".out"]))
    run_latex_jobs(config, tmp_path, 1, cleanup=True)
    assert (d / "Out.pdf").exists()
    assert not (d / "Out.aux").exists() and not (d / "Out.log").exists() and not (d / "Out.out").exists()
    assert (d / "other.aux").exists()


def test_latex_cleanup_not_after_failure(tmp_path, monkeypatch):
    d = tmp_path / "t"
    d.mkdir()
    (d / "document.tex").write_text("T", encoding="utf-8")
    (d / "Out.log").write_text("log")
    config = {"latex": {"runs": 1, "jobs": [
        {"name": "t", "tex": "document.tex", "workdir": "t", "pdf_name": "Out"}]}}
    monkeypatch.setattr(latex_module.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=""))
    with pytest.raises(RuntimeError):
        run_latex_jobs(config, tmp_path, 1, cleanup=True)
    assert (d / "Out.log").exists()


def test_latex_pdf_name_umlaut_uses_ascii_jobname(tmp_path, monkeypatch):
    d = tmp_path / "t"
    d.mkdir()
    (d / "document.tex").write_text("T", encoding="utf-8")
    config = {"latex": {"runs": 1, "jobs": [
        {"name": "t", "tex": "document.tex", "workdir": "t", "pdf_name": "Übersicht Gäste"}]}}
    monkeypatch.setattr(latex_module.subprocess, "run", _engine_writing([]))
    run_latex_jobs(config, tmp_path, 1)
    assert (d / "Übersicht Gäste.pdf").read_bytes() == b"PDF"


def test_pdf_name_validation(tmp_path):
    job = {"id": "a", "name": "a", "tex": "a.tex"}
    for bad in ({"pdf_name": "a/b"}, {"pdf_name": ".."}, {"pdf_name": "x", "copy_pdf": {"to": "y.pdf"}}):
        cfg = {"datasets": [], "exports": [], "latex": {"jobs": [{**job, **bad}]}}
        with pytest.raises(ConfigurationError, match="pdf_name"):
            validate_config(cfg, tmp_path)


def test_fill_empty_replaces_missing_values_with_placeholder(tmp_path):
    import pandas as pd
    from mao_cli.exports import ExportPlan, run_exports
    df = pd.DataFrame({"lerngruppe": ["7-1", "7-2"], "kl": [None, "AB"], "stellvkl": ["", None]})
    cfg = {"exports": [{"name": "kl", "from_dataset": "d", "fill_empty": {"kl": "N.N.", "stellvkl": "N.N."},
                        "output": {"path": "db/kl.csv", "sep": ";", "columns": "*"}}]}
    plan = ExportPlan(tmp_path)
    run_exports({"d": df}, cfg, tmp_path, plan)
    content = next(iter(plan.outputs.values()))[0].decode("utf-8")
    assert "7-1;N.N.;N.N." in content and "7-2;AB;N.N." in content
