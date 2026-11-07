import pytest

from mao_cli import jobs as j
from mao_cli.config import ConfigurationError, load_config


def make_cfg():
    return {
        "datasets": [{"name": "orga", "sql": "x", "enabled": False}, {"name": "rooms", "sql": "y"}],
        "exports": [
            {"id": "teilnehmende", "from_dataset": "orga", "enabled": True,
             "output": {"path": "db/Teilnehmende/Teilnehmende.csv"},
             "splits": [{"name": "klasse", "filename_template": "db/Teilnehmende/Klasse/T-{key}.csv",
                         "macro": {"tex_path": "db/Include/Tabellen-Teilnehmende-Klasse.tex"}}]},
            {"id": "raeume", "from_dataset": "rooms", "enabled": True,
             "output": {"path": "db/Basis/Raeume.csv"}, "macro": {"tex_path": "db/Include/Tabellen-Raeume.tex"}},
            {"id": "rangliste", "from_dataset": "orga", "enabled": True, "output": {"path": "db/Rangliste/R.csv"}},
        ],
        "latex": {"db_dir": "db", "jobs": [
            {"name": "teil", "workdir": "T", "tex": "@tex/Teil/Teil.tex", "enabled": False},
            {"name": "raum", "workdir": "R", "tex": "@tex/Raum/Raum.tex", "enabled": True},
            {"name": "fest", "workdir": "F", "tex": "@tex/Fest/Fest.tex", "exports": ["rangliste"]},
        ]},
    }


def test_detects_exports_from_template_and_includes(tmp_path, tex):
    cfg = make_cfg()
    jobs = {x["name"]: x for x in cfg["latex"]["jobs"]}
    assert j.job_exports(cfg, jobs["teil"], tmp_path, tex) == {"teilnehmende", "raeume"}  # template.tex lädt Raeume.csv
    assert j.job_exports(cfg, jobs["raum"], tmp_path, tex) == {"raeume"}
    assert j.job_exports(cfg, jobs["fest"], tmp_path, tex) == {"rangliste"}  # explizit


def test_explicit_unknown_export_is_an_error(tmp_path, tex):
    cfg = make_cfg()
    cfg["latex"]["jobs"][2]["exports"] = ["gibt-es-nicht"]
    with pytest.raises(ConfigurationError):
        j.job_exports(cfg, cfg["latex"]["jobs"][2], tmp_path, tex)


def test_restrict_exports_enables_only_needed_and_their_datasets(tmp_path, tex):
    cfg = make_cfg()
    cfg["latex"]["jobs"][0]["enabled"] = False
    needed = j.restrict_exports(cfg, tmp_path, tex)  # aktiv: raum, fest
    assert needed == {"raeume", "rangliste"}
    assert [e["enabled"] for e in cfg["exports"]] == [False, True, True]
    assert {d["name"]: d["enabled"] for d in cfg["datasets"]} == {"orga": True, "rooms": True}  # Dataset für 'rangliste' aktiviert


def test_restrict_exports_keeps_enabled_exports_when_unknown(tmp_path, tex):
    cfg = make_cfg()
    cfg["latex"]["jobs"][1]["tex"] = "@tex/Gibts/Nicht.tex"
    assert j.restrict_exports(cfg, tmp_path, tex) is None
    assert all(e["enabled"] for e in cfg["exports"])


def test_missing_outputs_lists_absent_files(tmp_path):
    cfg = make_cfg()
    (tmp_path / "db/Basis").mkdir(parents=True)
    (tmp_path / "db/Basis/Raeume.csv").write_text("a", encoding="utf-8")
    assert j.missing_outputs(cfg, tmp_path, {"raeume"}) == ["raeume: db/Include/Tabellen-Raeume.tex"]


def test_comments_in_templates_are_ignored(tmp_path, tex):
    (tex / "base" / "template.tex").write_text("% \\MaOdb Basis/Raeume.csv\n", encoding="utf-8")
    cfg = make_cfg()
    assert j.job_exports(cfg, cfg["latex"]["jobs"][0], tmp_path, tex) == {"teilnehmende"}


def test_restrict_exports_adds_extra_and_falls_back_to_enabled_when_unknown(tmp_path, tex):
    cfg = make_cfg()
    cfg["exports"][0]["enabled"] = False
    cfg["latex"]["jobs"][1]["enabled"] = True  # raum → raeume
    cfg["latex"]["jobs"][0]["enabled"] = False
    cfg["latex"]["jobs"][2]["enabled"] = False
    assert j.restrict_exports(cfg, tmp_path, tex, extra=["teilnehmende"]) == {"raeume", "teilnehmende"}
    assert [e["enabled"] for e in cfg["exports"]] == [True, True, False]
    cfg = make_cfg()
    cfg["latex"]["jobs"][1]["tex"] = "@tex/Gibts/Nicht.tex"
    cfg["exports"][2]["enabled"] = False
    assert j.restrict_exports(cfg, tmp_path, tex, extra=["rangliste"]) is None
    assert [e["enabled"] for e in cfg["exports"]] == [True, True, True]  # aktivierte + extra


def test_template_without_data_reference_needs_no_export(tmp_path):
    tex = tmp_path / "tex"
    (tex / "Hinweise").mkdir(parents=True)
    (tex / "base").mkdir()
    (tex / "Hinweise" / "Hinweise.tex").write_text("\\input{template.tex}\n\\input{Inhalt.tex}\n", encoding="utf-8")
    (tex / "base" / "template.tex").write_text("\\usepackage{tikz}\n", encoding="utf-8")
    (tex / "base" / "Inhalt.tex").write_text("Hinweise für \\MaO\n", encoding="utf-8")
    cfg = make_cfg()
    job = {"name": "hinweise", "workdir": "H", "tex": "@tex/Hinweise/Hinweise.tex"}
    cfg["latex"]["jobs"] = [job]
    assert j.job_exports(cfg, job, tmp_path, tex) == set()
    assert j.restrict_exports(cfg, tmp_path, tex) == set()
    assert not any(e["enabled"] for e in cfg["exports"])
    (tex / "base" / "Inhalt.tex").write_text("\\DTLloaddb{x}{\\MaOdb Anderes/Datei.csv}\n", encoding="utf-8")
    assert j.job_exports(cfg, job, tmp_path, tex) is None  # Datenbezug, aber unbekannter Export → wie bisher


def test_explicit_empty_exports_means_none(tmp_path, tex):
    cfg = make_cfg()
    job = {"name": "leer", "workdir": "F", "tex": "@tex/Fest/Fest.tex", "exports": []}
    assert j.job_exports(cfg, job, tmp_path, tex) == set()


OBERSTUFE_DOKUMENTE = {
    "Einsatz-Oberstufe/Einsatz-Oberstufe.csv": {"einsatz_oberstufe"},
    "Einsatz-Oberstufe/Einsatz-Oberstufe-Gruppe-1.csv": {"einsatz_oberstufe"},
    "Einsatz-Oberstufe/Einsatz-Oberstufe-Klasse.csv": {"einsatz_oberstufe_klasse"},
    "Einsatz-Oberstufe/Einsatz-Oberstufe-Raum.csv": {"einsatz_oberstufe_raum"},
}


@pytest.mark.parametrize("csv", sorted(OBERSTUFE_DOKUMENTE))
def test_default_pipeline_oberstufe_exports_are_told_apart(tmp_path, csv):
    from pathlib import Path
    cfg = load_config(Path(j.__file__).parent / "defaults" / "pipeline.yaml")
    tex = tmp_path / "tex"
    (tex / "Doc").mkdir(parents=True)
    (tex / "Doc" / "Doc.tex").write_text(f"\\DTLloaddb[]{{x}}{{\\MaOdb {csv}}}\n", encoding="utf-8")
    job = {"name": "doc", "workdir": "D", "tex": "@tex/Doc/Doc.tex"}
    assert j.job_exports(cfg, job, tmp_path, tex) == OBERSTUFE_DOKUMENTE[csv]


def test_default_pipeline_oberstufe_jobs_and_exports_exist():
    from pathlib import Path
    cfg = load_config(Path(j.__file__).parent / "defaults" / "pipeline.yaml")
    jobs = {x["name"]: x for x in cfg["latex"]["jobs"]}
    for name in ("oberstufe_anwesenheitslisten", "oberstufe_einteilungslisten", "oberstufe_uebersicht", "aufsichten_schueler"):
        assert jobs[name]["tex"].startswith("@tex/")
    ids = {j.export_id(e) for e in cfg["exports"]}
    assert {"einsatz_oberstufe", "einsatz_oberstufe_klasse", "einsatz_oberstufe_raum"} <= ids
    sql = next(d["sql"] for d in cfg["datasets"] if d["name"] == "einsatz_oberstufe")
    assert "AS entitaet" in sql


def test_default_pipeline_kopierliste_dataset_uses_view_without_gesamt_and_int_cast():
    from pathlib import Path
    cfg = load_config(Path(j.__file__).parent / "defaults" / "pipeline.yaml")
    ds = next(d for d in cfg["datasets"] if d["name"] == "kopierliste")
    assert "{{table.kopierliste_view}}" in ds["sql"] and "<> 'GESAMT'" in ds["sql"]
    assert {f"stufe_{n}" for n in range(5, 13)} | {"gesamt"} <= set(ds["cast"])
    assert all(v == "Int64" for v in ds["cast"].values())
    exp = next(e for e in cfg["exports"] if e["id"] == "kopierliste")
    assert exp["from_dataset"] == "kopierliste"


def test_default_pipeline_raum_anwesenheitslisten_exports(tmp_path):
    from pathlib import Path
    cfg = load_config(Path(j.__file__).parent / "defaults" / "pipeline.yaml")
    tex = tmp_path / "tex"
    (tex / "Raum-Anwesenheitslisten").mkdir(parents=True)
    (tex / "Raum-Anwesenheitslisten" / "Raum-Anwesenheitslisten.tex").write_text(
        "\\input{\\MaOdb Include/Tabellen-Teilnehmende-Raum.tex}\n"
        "\\DTLloaddb{Aufsichten}{\\MaOdb Aufsichten/Aufsichten-Raeume.csv}\n"
        "\\DTLloaddb{OberstufeRaum}{\\MaOdb Einsatz-Oberstufe/Einsatz-Oberstufe-Raum.csv}\n", encoding="utf-8")
    job = {"name": "anwesenheitslisten", "workdir": "D", "tex": "@tex/Raum-Anwesenheitslisten/Raum-Anwesenheitslisten.tex"}
    assert j.job_exports(cfg, job, tmp_path, tex) == {
        "teilnehmende_raum", "aufsichten_lehrkraefte_nach_raum", "einsatz_oberstufe_raum"}
    ex = next(e for e in cfg["exports"] if j.export_id(e) == "teilnehmende_raum")
    assert ex["sort_by"] == ["raum", "vorname", "nachname"]
    assert ex["splits"][0]["macro"]["list_macro"] == "TeilnehmendeRaumListe"
