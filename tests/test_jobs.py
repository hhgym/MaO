import pytest

from mao_cli import jobs as j
from mao_cli.config import ConfigurationError


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
