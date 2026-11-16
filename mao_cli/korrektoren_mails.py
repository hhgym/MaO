"""Mailtexte an Korrektor:innen (Zuteilung, Absage) für Thunderbird Mail Merge im Jahrgangsordner.

Die Texte nutzen Platzhalter aus `Korrektoren-Mailmerge.csv` / `Korrektoren-Ohne-Aufgabe.csv` in doppelten geschweiften Klammern
({{anrede}}, {{aufgabe}}, {{email}}, {{uebergabe|Wert|Text}}, {{uebergabe_sonst}}). Die jahresabhängigen Angaben (Zahl der Angebote und
Aufgaben, Rückgabetermin) setzt MaO-CLI beim Erzeugen ein.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

MAIL_ZUTEILUNG = "Korrektoren-Mail-Zuteilung.txt"
MAIL_ABSAGE = "Korrektoren-Mail-Absage.txt"

_ZUTEILUNG = """An: {{{{email}}}}
Betreff: Ihre Aufgabe zur Korrektur der Mathematik-Olympiade

{{{{anrede}}}},

wir möchten uns nochmals her(t)zlich für Ihr Angebot zur Korrektur bedanken. {angebote_satz}

Herr Ohnesorge und ich haben die Aufgaben inzwischen zugeteilt und dabei Ihre Wünsche, soweit möglich, berücksichtigt. Ihnen haben wir die Aufgabe {{{{aufgabe}}}} anvertraut. Anbei finden Sie die Aufgabe zusammen mit der zugehörigen Lösung.

Die Verteilung der Aufgaben erfolgt ab Montag. {{{{uebergabe|Hertzkind|Wie gewünscht, übergeben wir den Hefter mit den Lösungen Ihrem Hertz-Kind.}}}}{{{{uebergabe|Sekretariat|Wie gewünscht, können Sie den Hefter mit den Lösungen im Sekretariat abholen.}}}}{{{{uebergabe|Sonstiges|Wie vereinbart, organisieren wir die Übergabe des Hefters wie folgt: {{{{uebergabe_sonst}}}}.}}}} Im Hefter finden Sie neben Aufgabe und Lösung auch einige Hinweise zur Korrektur.

Sollten bei der Korrektur Fragen oder Probleme auftreten, können Sie sich gerne an mich oder Herrn Ohnesorge (robert.ohnesorge@hhgym.de) wenden. Unser Ziel ist es, die Ergebnisse in der Woche vor den Weihnachtsferien bekannt zu geben, um genügend Zeit für eventuelle Einsprüche zu haben, bevor die Delegierungen festgelegt werden.

Daher würden wir uns freuen, wenn wir die korrigierten Lösungen bis spätestens {rueckgabe} zurückerhalten. Bitte melden Sie sich, falls dies für Sie nicht zu schaffen ist.

Mit freundlichen Grüßen
Marcel Pietschmann und Robert Ohnesorge
"""

_ABSAGE = """An: {{{{email}}}}
Betreff: Rückmeldung zu Ihrem Korrekturangebot

{{{{anrede}}}},

nochmals vielen Dank für Ihr Angebot, uns bei der Korrektur zu unterstützen. Wir waren sehr erfreut darüber, dass wir dieses Jahr mehr Korrekturangebote als zu korrigierende Aufgaben erhalten haben – insgesamt {angebote} Angebote für {aufgaben} Aufgaben.

Ich hoffe, Sie können es nachvollziehen, dass wir Ihnen in diesem Jahr keine Aufgabe zugeteilt haben. Die Verteilung der Aufgaben ist eine anspruchsvolle, multidimensionale Optimierungsaufgabe, bei der wir zahlreiche Faktoren berücksichtigen.

Es würde mich sehr freuen, wenn Sie auch im nächsten Jahr wieder bereit wären, eine Aufgabe zu korrigieren. Diesbezüglich werde ich mich rechtzeitig bei Ihnen melden. Sollten Sie in Zukunft keine Korrekturaufrufe mehr erhalten wollen, können Sie sich über den folgenden Link austragen: https://hnrhtz.de/austragen-korrekturaufruf/?email={{{{email}}}}

Vielen Dank für Ihr Verständnis und Ihre Unterstützung – ohne Ihre Hilfe würde unsere Korrekturoption gegen Null streben.

Mit freundlichen Grüßen
Marcel Pietschmann
"""


def render_mails(angebote: int, aufgaben: int, termin: date | None) -> dict[str, str]:
    """Dateiname -> Text, mit eingesetzten Zahlen und Rückgabetermin."""
    if angebote > aufgaben:
        satz = (f"In diesem Jahr hatten wir sogar {angebote} Korrekturangebote und damit deutlich mehr Angebote als Aufgaben. "
                "Das freut uns wirklich sehr.")
    else:
        satz = f"In diesem Jahr hatten wir {angebote} Korrekturangebote für {aufgaben} Aufgaben. Das freut uns wirklich sehr."
    rueckgabe = termin.strftime("%d.%m.%Y") if termin else "[Rückgabetermin fehlt: mao korrektoren, Menü 7]"
    return {MAIL_ZUTEILUNG: _ZUTEILUNG.format(angebote_satz=satz, rueckgabe=rueckgabe),
            MAIL_ABSAGE: _ABSAGE.format(angebote=angebote, aufgaben=aufgaben)}


def write_mails(workspace: Path, angebote: int, aufgaben: int, termin: date | None,
                confirm_overwrite: Callable[[str], bool] = lambda _name: False) -> list[str]:
    """Texte im Jahrgangsordner anlegen; vorhandene nur nach Bestätigung überschreiben. Rückgabe: geschriebene Dateinamen."""
    written = []
    for name, text in render_mails(angebote, aufgaben, termin).items():
        target = workspace / name
        if target.exists() and not confirm_overwrite(name):
            continue
        workspace.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="")
        tmp.replace(target)
        written.append(name)
    return written
