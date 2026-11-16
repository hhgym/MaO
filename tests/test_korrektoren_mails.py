"""Mailtexte für Thunderbird Mail Merge."""
from __future__ import annotations

from datetime import date

from mao_cli import korrektoren_mails as m


def test_render_fills_numbers_and_date_and_keeps_mail_merge_placeholders():
    texts = m.render_mails(65, 28, date(2026, 12, 14))
    zu, ab = texts[m.MAIL_ZUTEILUNG], texts[m.MAIL_ABSAGE]
    assert "sogar 65 Korrekturangebote" in zu and "bis spätestens 14.12.2026" in zu
    assert "{{anrede}}," in zu and "{{aufgabe}}" in zu and "{{uebergabe|Hertzkind|" in zu
    assert "{{uebergabe|Sonstiges|Wie vereinbart, organisieren wir die Übergabe des Hefters wie folgt: {{uebergabe_sonst}}.}}" in zu
    assert zu.startswith("An: {{email}}\n") and "[[" not in zu and "{rueckgabe}" not in zu
    assert "insgesamt 65 Angebote für 28 Aufgaben" in ab and "?email={{email}}" in ab and ab.startswith("An: {{email}}\n")
    assert "Anbei finden Sie die Aufgabe zusammen mit der zugehörigen Lösung." in zu and zu.count("Im Anhang") == 0


def test_fewer_offers_than_tasks_uses_neutral_sentence_and_missing_date_is_marked():
    zu = m.render_mails(20, 28, None)[m.MAIL_ZUTEILUNG]
    assert "deutlich mehr" not in zu and "20 Korrekturangebote für 28 Aufgaben" in zu and "Rückgabetermin fehlt" in zu


def test_write_mails_asks_before_overwriting(tmp_path):
    assert sorted(m.write_mails(tmp_path, 46, 28, date(2026, 12, 14))) == sorted([m.MAIL_ZUTEILUNG, m.MAIL_ABSAGE])
    (tmp_path / m.MAIL_ABSAGE).write_text("meine Fassung", encoding="utf-8")
    assert m.write_mails(tmp_path, 46, 28, date(2026, 12, 14)) == []
    assert (tmp_path / m.MAIL_ABSAGE).read_text(encoding="utf-8") == "meine Fassung"
    assert m.write_mails(tmp_path, 46, 28, date(2026, 12, 14), lambda name: name == m.MAIL_ABSAGE) == [m.MAIL_ABSAGE]
    assert "insgesamt 46 Angebote" in (tmp_path / m.MAIL_ABSAGE).read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))
