"""R3/R4: LaTeX-Makrolisten absichern und Zeilen ohne Split-Schlüssel nicht still verlieren."""
import pandas as pd
import pytest

from mao_cli.config import ConfigurationError, validate_config
from mao_cli.exports import ExportPlan, latex_list_value, run_exports


def df():
    return pd.DataFrame([
        {"nachname": "A", "kurs": "LK_11_A", "lehrkraft": "AB", "stufe": 11, "bemerkung": "a, b"},
        {"nachname": "B", "kurs": "", "lehrkraft": "CD", "stufe": 7, "bemerkung": ""},
        {"nachname": "C", "kurs": None, "lehrkraft": "CD", "stufe": 7, "bemerkung": ""},
    ])


def cfg(split_extra=None, template="{kurs}/{lehrkraft}/{db}"):
    split = {"name": "kurs", "by": "kurs", "filename_template": "out/{key}.csv",
             "macro": {"tex_path": "out/liste.tex", "list_macro": "KursListe", "entry_template": template}}
    split.update(split_extra or {})
    return {"datasets": [{"name": "d"}],
            "exports": [{"name": "e", "from_dataset": "d", "output": {"columns": "*"}, "splits": [split]}]}


def run(config, data, tmp_path):
    plan = ExportPlan(tmp_path)
    run_exports({"d": data}, config, tmp_path, plan)
    return plan


def tex_text(plan):
    return next(payload for payload, kind, _ in plan.outputs.values() if kind == "tex").decode("utf-8")


@pytest.mark.parametrize("raw, expected", [
    ("LK_11_A", "LK_11_A"),
    ("50% & 5$", r"50\% \& 5\$"),
    ("a#b", r"a\#b"),
    ("x{y}", r"x\{y\}"),
    ("a^b~c", r"a\^{}b\~{}c"),
    ("a\\b", r"a\textbackslash{}b"),
    (None, ""), (float("nan"), ""), (7, "7"),
])
def test_latex_list_value_escapes_tex_specials(raw, expected):
    assert latex_list_value(raw) == expected


@pytest.mark.parametrize("raw", ["a,b", "a/b", "a\nb"])
def test_latex_list_value_rejects_list_breaking_characters(raw):
    with pytest.raises(ValueError, match="LaTeX-Makroliste"):
        latex_list_value(raw, column="kurs")


def test_macro_list_escapes_values(tmp_path):
    data = pd.DataFrame([{"nachname": "A", "kurs": "Info 50%", "lehrkraft": "A&B", "stufe": 11}])
    text = tex_text(run(cfg(), data, tmp_path))
    assert r"Info 50\%/A\&B/" in text


def test_only_template_columns_are_checked(tmp_path):
    # bemerkung enthält ein Komma, wird vom Template aber nicht verwendet
    plan = run(cfg(), df().iloc[:1], tmp_path)
    assert "LK_11_A/AB/" in tex_text(plan)


def test_separator_in_used_column_is_an_error(tmp_path):
    data = pd.DataFrame([{"nachname": "A", "kurs": "x,y", "lehrkraft": "AB", "stufe": 11}])
    with pytest.raises(ValueError, match="Komma"):
        run(cfg(), data, tmp_path)


def test_rows_without_key_are_reported(tmp_path, caplog):
    caplog.set_level("INFO")
    plan = run(cfg(), df(), tmp_path)
    assert "2 Zeile(n) ohne Wert in 'kurs'" in caplog.text
    names = [p for p in plan.outputs if p.endswith(".csv")]
    assert len(names) == 1 and names[0].endswith("LK_11_A.csv")


def test_on_missing_key_error_aborts(tmp_path):
    with pytest.raises(ValueError, match="2 Zeile"):
        run(cfg({"on_missing_key": "error"}), df(), tmp_path)
    assert not list(tmp_path.iterdir())


def test_no_message_when_all_rows_have_keys(tmp_path, caplog):
    caplog.set_level("INFO")
    run(cfg(), df().iloc[:1], tmp_path)
    assert "ohne Wert" not in caplog.text


def test_invalid_on_missing_key_value_is_a_config_error(tmp_path):
    config = cfg({"on_missing_key": "ignore"})
    config["datasets"] = [{"name": "d", "sql": "SELECT 1"}]
    config["exports"][0]["id"] = "e"
    config["latex"] = {}
    with pytest.raises(ConfigurationError, match="on_missing_key"):
        validate_config(config, tmp_path)
