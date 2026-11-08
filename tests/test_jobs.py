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


def test_default_pipeline_kopierliste_dataset_uses_view_with_gesamt_last_and_int_cast():
    from pathlib import Path
    cfg = load_config(Path(j.__file__).parent / "defaults" / "pipeline.yaml")
    ds = next(d for d in cfg["datasets"] if d["name"] == "kopierliste")
    assert "{{table.kopierliste_view}}" in ds["sql"] and "ORDER BY (raum = 'GESAMT')" in ds["sql"]
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


def test_default_pipeline_kopierliste_externe(tmp_path):
    from pathlib import Path
    cfg = load_config(Path(j.__file__).parent / "defaults" / "pipeline.yaml")
    ds = next(d for d in cfg["datasets"] if d["name"] == "kopierliste_externe")
    assert "{{table.kopierliste_externe_view}}" in ds["sql"] and "ORDER BY (raum = 'GESAMT')" in ds["sql"]
    assert set(ds["cast"]) == {"stufe_3", "stufe_4", "stufe_5", "stufe_6", "gesamt"}
    ex = next(e for e in cfg["exports"] if j.export_id(e) == "kopierliste_externe")
    assert ex["macro"]["list_macro"] == "Kopierexterneliste" and ex["macro"]["tex_path"].endswith("Tabellen-Kopierliste-Externe.tex")
    # Die Vorlage braucht beide Exporte; die Externen sind von den Hauptraeumen unterscheidbar
    tex = tmp_path / "tex"
    (tex / "Kopierliste").mkdir(parents=True)
    (tex / "Kopierliste" / "Kopierliste.tex").write_text(
        "\\input{\\MaOdb Include/Tabellen-Kopierliste.tex}\n"
        "\\input{\\MaOdb Include/Tabellen-Kopierliste-Externe.tex}\n", encoding="utf-8")
    job = {"name": "kopierliste", "workdir": "K", "tex": "@tex/Kopierliste/Kopierliste.tex"}
    assert j.job_exports(cfg, job, tmp_path, tex) == {"kopierliste", "kopierliste_externe"}
    (tex / "Kopierliste" / "Kopierliste.tex").write_text("\\input{\\MaOdb Include/Tabellen-Kopierliste.tex}\n", encoding="utf-8")
    assert j.job_exports(cfg, job, tmp_path, tex) == {"kopierliste"}


def test_default_pipeline_raumuebersicht_and_korrekturlisten(tmp_path):
    from pathlib import Path
    cfg = load_config(Path(j.__file__).parent / "defaults" / "pipeline.yaml")
    gesamt = next(e for e in cfg["exports"] if j.export_id(e) == "teilnehmende_gesamt")
    assert gesamt["output"]["path"] == "db/Teilnehmende/Teilnehmendeliste.csv"
    assert gesamt["sort_by"] == ["stufe", "klasse", "vorname", "nachname"]
    assert "matrikelnummer" not in gesamt["output"]["columns"] and "raum" in gesamt["output"]["columns"]
    ds = next(d for d in cfg["datasets"] if d["name"] == "korrekturliste")
    assert "{{table.orga_view}}" in ds["sql"] and "stufengruppe" in ds["sql"] and "stufencode" in ds["sql"]
    kor = next(e for e in cfg["exports"] if j.export_id(e) == "korrekturliste")
    assert kor["from_dataset"] == "korrekturliste"
    assert kor["sort_by"] == ["stufencode", "vorname", "nachname"]
    sp = kor["splits"][0]
    assert sp["by"] == "stufengruppe" and sp["macro"]["list_macro"] == "KorrekturListe"
    assert sp["macro"]["entry_template"] == "{stufengruppe}/{stufencode}/{db}"
    tex = tmp_path / "tex"
    for name, body in {
        "Raumuebersicht": "\\DTLloaddb{Teilnehmendeliste}{\\MaOdb Teilnehmende/Teilnehmendeliste.csv}\n",
        "Korrekturlisten": "\\input{\\MaOdb Include/Tabellen-Korrekturliste.tex}\n",
    }.items():
        (tex / name).mkdir(parents=True)
        (tex / name / f"{name}.tex").write_text(body, encoding="utf-8")
    ru = {"name": "raumuebersicht", "workdir": "R", "tex": "@tex/Raumuebersicht/Raumuebersicht.tex"}
    ko = {"name": "korrekturlisten", "workdir": "K", "tex": "@tex/Korrekturlisten/Korrekturlisten.tex"}
    assert j.job_exports(cfg, ru, tmp_path, tex) == {"teilnehmende_gesamt"}
    assert j.job_exports(cfg, ko, tmp_path, tex) == {"korrekturliste"}


def test_korrekturliste_groups_oberstufe_and_sorts_by_name(tmp_path):
    import pandas as pd
    from pathlib import Path
    from mao_cli.exports import ExportPlan, run_exports
    cfg = load_config(Path(j.__file__).parent / "defaults" / "pipeline.yaml")
    cfg["exports"] = [e for e in cfg["exports"] if j.export_id(e) == "korrekturliste"]
    rows = [(1, "Zorn", "Zoe", "11a", 11), (2, "Alt", "Max", "12b", 12), (3, "Bauer", "Ben", "5-1", 5), (4, "Alt", "Anna", "11b", 11)]
    df = pd.DataFrame(rows, columns=["tnr", "nachname", "vorname", "klasse", "stufe"])
    df["stufengruppe"] = df["stufe"].map(lambda s: "11-12" if s >= 11 else str(s))
    df["stufencode"] = df["stufe"].map(lambda s: f"{12 if s >= 11 else s:02d}")
    plan = ExportPlan(tmp_path)
    run_exports({"korrekturliste": df}, cfg, tmp_path, plan)
    outputs = {str(k).replace("\\", "/").split("db/", 1)[-1]: v[0].decode("utf-8") for k, v in plan.outputs.items()}
    ober = outputs["Korrekturliste/Korrekturliste-Stufe-11-12.csv"].splitlines()
    assert [line.split(";")[2] for line in ober[1:]] == ["Anna", "Max", "Zoe"]
    assert "Korrekturliste/Korrekturliste-Stufe-5.csv" in outputs
    macro = outputs["Include/Tabellen-Korrekturliste.tex"]
    assert "5/05/Korrekturliste-Stufe-Fuenf" in macro and "11-12/12/Korrekturliste-Stufe-EinsEins-EinsZwei" in macro
