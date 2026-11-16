"""Mailtexte für Thunderbird Mail Merge (Vorlagen im Vorlagenordner)."""
from __future__ import annotations

from datetime import date

from mao_cli import korrektoren_mails as m


def test_render_fills_numbers_and_date_and_keeps_mail_merge_placeholders():
    texts, unknown = m.render_mails(65, 28, date(2026, 12, 14))
    zu, ab = texts[m.MAIL_ZUTEILUNG], texts[m.MAIL_ABSAGE]
    assert unknown == [] and "65 Korrekturangebote für 28 Aufgaben" in zu and "bis spätestens 14.12.2026" in zu
    assert "{{anrede}}," in zu and "{{aufgabe}}" in zu and "{{uebergabe|Hertzkind|" in zu
    assert "{{uebergabe|Sonstiges|Wie vereinbart, organisieren wir die Übergabe des Hefters wie folgt: {{uebergabe_sonst}}.}}" in zu
    assert zu.startswith("An: {{email}}\n") and "[[" not in zu and "65 Angebote für 28 Aufgaben" in ab


def test_missing_date_is_marked_and_unknown_tokens_reported():
    texts, unknown = m.render_mails(1, 2, None, {m.TEMPLATE_ZUTEILUNG: "Bis [[Rückgabe]] [[Quatsch]] [[ Angebote ]]", m.TEMPLATE_ABSAGE: "x"})
    assert "Rückgabetermin fehlt" in texts[m.MAIL_ZUTEILUNG] and "[[Quatsch]]" in texts[m.MAIL_ZUTEILUNG] and unknown == ["Quatsch"]
    assert texts[m.MAIL_ZUTEILUNG].endswith(" 1") and texts[m.MAIL_ABSAGE] == "x"


def test_templates_are_created_once_and_user_edits_are_used(tmp_path):
    tex = tmp_path / "tex"
    assert sorted(m.ensure_templates(tex)) == sorted([m.TEMPLATE_ZUTEILUNG, m.TEMPLATE_ABSAGE]) and m.ensure_templates(tex) == []
    assert m.ensure_templates(None) == []
    folder = m.template_dir(tex)
    (folder / m.TEMPLATE_ABSAGE).write_text("Meine Absage: [[Angebote]] von [[Aufgaben]]", encoding="utf-8")
    templates = m.load_templates(tex)
    texts, _ = m.render_mails(46, 28, date(2026, 12, 14), templates)
    assert texts[m.MAIL_ABSAGE] == "Meine Absage: 46 von 28" and "{{anrede}}" in texts[m.MAIL_ZUTEILUNG]
    assert m.load_templates(None) == m.DEFAULT_TEMPLATES


def test_write_mails_asks_before_overwriting(tmp_path):
    written, _ = m.write_mails(tmp_path, 46, 28, date(2026, 12, 14))
    assert sorted(written) == sorted([m.MAIL_ZUTEILUNG, m.MAIL_ABSAGE])
    (tmp_path / m.MAIL_ABSAGE).write_text("meine Fassung", encoding="utf-8")
    assert m.write_mails(tmp_path, 46, 28, date(2026, 12, 14))[0] == []
    assert (tmp_path / m.MAIL_ABSAGE).read_text(encoding="utf-8") == "meine Fassung"
    assert m.write_mails(tmp_path, 46, 28, date(2026, 12, 14), lambda name: name == m.MAIL_ABSAGE)[0] == [m.MAIL_ABSAGE]
    assert "46 Angebote" in (tmp_path / m.MAIL_ABSAGE).read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))


def test_text_export_renders_template_with_values(tmp_path):
    import pandas as pd
    from mao_cli.exports import ExportPlan, run_exports
    tex = tmp_path / "tex"
    (tex / "Korrektoren-Mails").mkdir(parents=True)
    (tex / "Korrektoren-Mails" / "Zuteilung.txt").write_text("{{anrede}}: [[Angebote]]/[[aufgaben]] bis [[Rückgabe]] [[X]]", encoding="utf-8")
    ws = tmp_path / "ws"
    (ws / "db" / "Korrektoren").mkdir(parents=True)
    (ws / "db" / "Korrektoren" / "Rueckgabetermin.tex").write_text("\\DTMdisplaydate{2026}{12}{1}{-1}\n", encoding="utf-8")
    cfg = {"exports": [
        {"id": "z", "name": "z", "from_dataset": "w", "template": "@tex/Korrektoren-Mails/Zuteilung.txt", "output": {"path": "Z.txt"}},
        {"id": "a", "name": "a", "from_dataset": "w", "template": "@tex/Korrektoren-Mails/Absage.txt", "output": {"path": "A.txt"}}]}
    plan = ExportPlan(ws)
    run_exports({"w": pd.DataFrame([{"angebote": 30, "aufgaben": 28}])}, cfg, ws, plan, tex)
    text = plan.outputs["Z.txt"][0].decode()
    assert text.startswith("{{anrede}}: 30/28 bis 01.12.2026 ") and "[[X]]" in text
    assert b"[[Angebote]]" not in plan.outputs["A.txt"][0]  # Standardtext als Rückfall


def test_text_export_validation():
    from mao_cli.config import validate_config
    cfg = {"latex": {"jobs": []}, "datasets": [{"name": "w", "id": "w", "sql": "SELECT 1"}],
           "exports": [{"id": "z", "name": "z", "from_dataset": "w", "template": "", "output": {}}]}
    try:
        validate_config(cfg)
    except Exception as exc:  # ConfigurationError mit Fehlerliste
        assert "template" in str(exc) or "Textvorlage" in str(exc)
    else:
        raise AssertionError("Fehler erwartet")
