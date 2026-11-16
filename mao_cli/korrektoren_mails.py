"""Mailtexte an Korrektor:innen (Zuteilung, Absage) für Thunderbird Mail Merge im Jahrgangsordner.

Die Vorlagen liegen im Vorlagenordner `<MaO>/tex/Korrektoren-Mails/` (`Zuteilung.txt`, `Absage.txt`) und lassen sich dort anpassen; fehlen
sie, legt MaO-CLI neutrale Standardfassungen an. Mail-Merge-Platzhalter stehen in doppelten geschweiften Klammern ({{anrede}}, {{aufgabe}},
{{email}}, {{datei}}, {{uebergabe|Wert|Text}}, {{uebergabe_sonst}}) und bleiben unverändert. Die jahresabhängigen Angaben ersetzt MaO-CLI
beim Erzeugen: [[Angebote]], [[Aufgaben]], [[Rückgabe]] (in doppelten eckigen Klammern).
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Callable

TEMPLATE_DIR = "Korrektoren-Mails"
TEMPLATE_ZUTEILUNG = "Zuteilung.txt"
TEMPLATE_ABSAGE = "Absage.txt"
MAIL_ZUTEILUNG = "Korrektoren-Mail-Zuteilung.txt"
MAIL_ABSAGE = "Korrektoren-Mail-Absage.txt"
_TOKEN_RE = re.compile(r"\[\[([^\[\]]+)\]\]")

DEFAULT_TEMPLATES = {
    TEMPLATE_ZUTEILUNG: """An: {{email}}
Betreff: Ihre Aufgabe zur Korrektur der Mathematik-Olympiade

{{anrede}},

wir möchten uns nochmals herzlich für Ihr Angebot zur Korrektur bedanken. In diesem Jahr hatten wir [[Angebote]] Korrekturangebote für [[Aufgaben]] Aufgaben.

Wir haben die Aufgaben inzwischen zugeteilt und dabei Ihre Wünsche, soweit möglich, berücksichtigt. Ihnen haben wir die Aufgabe {{aufgabe}} anvertraut. Anbei finden Sie die Aufgabe zusammen mit der zugehörigen Lösung.

{{uebergabe|Hertzkind|Wie gewünscht, übergeben wir den Hefter mit den Lösungen Ihrem Hertz-Kind.}}{{uebergabe|Sekretariat|Wie gewünscht, können Sie den Hefter mit den Lösungen im Sekretariat abholen.}}{{uebergabe|Sonstiges|Wie vereinbart, organisieren wir die Übergabe des Hefters wie folgt: {{uebergabe_sonst}}.}} Im Hefter finden Sie neben Aufgabe und Lösung auch einige Hinweise zur Korrektur.

Wir würden uns freuen, wenn wir die korrigierten Lösungen bis spätestens [[Rückgabe]] zurückerhalten. Bitte melden Sie sich, falls dies für Sie nicht zu schaffen ist.

Mit freundlichen Grüßen
[Name]
""",
    TEMPLATE_ABSAGE: """An: {{email}}
Betreff: Rückmeldung zu Ihrem Korrekturangebot

{{anrede}},

nochmals vielen Dank für Ihr Angebot, uns bei der Korrektur zu unterstützen. In diesem Jahr hatten wir [[Angebote]] Angebote für [[Aufgaben]] Aufgaben.

Wir bitten um Verständnis, dass wir Ihnen in diesem Jahr keine Aufgabe zuteilen konnten. Wir würden uns freuen, wenn Sie auch im nächsten Jahr wieder bereit wären, eine Aufgabe zu korrigieren.

Mit freundlichen Grüßen
[Name]
""",
}


def template_dir(tex_dir: Path) -> Path:
    return tex_dir / TEMPLATE_DIR


def ensure_templates(tex_dir: Path | None) -> list[str]:
    """Fehlende Vorlagen mit den Standardtexten anlegen; Rückgabe: neu angelegte Dateinamen. Ohne Vorlagenordner nichts."""
    created: list[str] = []
    if tex_dir is None:
        return created
    folder = template_dir(tex_dir)
    for name, text in DEFAULT_TEMPLATES.items():
        target = folder / name
        if not target.exists():
            folder.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="")
            created.append(name)
    return created


def load_templates(tex_dir: Path | None) -> dict[str, str]:
    """Vorlagen aus dem Vorlagenordner; fehlende Dateien durch die Standardtexte ersetzt."""
    result = dict(DEFAULT_TEMPLATES)
    if tex_dir is not None:
        for name in result:
            path = template_dir(tex_dir) / name
            if path.is_file():
                result[name] = path.read_text(encoding="utf-8")
    return result


RUECKGABE_FEHLT = "[Rückgabetermin fehlt: mao korrektoren, Menü 7]"


def fill_tokens(text: str, values: dict[str, str]) -> tuple[str, list[str]]:
    """[[Name]] durch Werte ersetzen (Groß-/Kleinschreibung egal); zweite Rückgabe: unbekannte Platzhalter."""
    lookup = {key.casefold(): value for key, value in values.items()}
    unknown: list[str] = []

    def fill(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key.casefold() in lookup:
            return lookup[key.casefold()]
        if key not in unknown:
            unknown.append(key)
        return match.group(0)

    return _TOKEN_RE.sub(fill, text), unknown


def default_template(name: str) -> str | None:
    """Standardtext zu einem Vorlagen-Dateinamen (Zuteilung.txt/Absage.txt) oder None."""
    return DEFAULT_TEMPLATES.get(Path(name).name)


def render_mails(angebote: int, aufgaben: int, termin: date | None,
                 templates: dict[str, str] | None = None) -> tuple[dict[str, str], list[str]]:
    """Dateiname -> Text mit eingesetzten Zahlen und Rückgabetermin; zweite Rückgabe: unbekannte [[Platzhalter]]."""
    source = templates or DEFAULT_TEMPLATES
    values = {"Angebote": str(angebote), "Aufgaben": str(aufgaben),
              "Rückgabe": termin.strftime("%d.%m.%Y") if termin else RUECKGABE_FEHLT}
    unknown: list[str] = []
    texts: dict[str, str] = {}
    for out_name, tmpl_name in ((MAIL_ZUTEILUNG, TEMPLATE_ZUTEILUNG), (MAIL_ABSAGE, TEMPLATE_ABSAGE)):
        texts[out_name], missing = fill_tokens(source.get(tmpl_name, DEFAULT_TEMPLATES[tmpl_name]), values)
        unknown.extend(x for x in missing if x not in unknown)
    return texts, unknown


def write_mails(workspace: Path, angebote: int, aufgaben: int, termin: date | None,
                confirm_overwrite: Callable[[str], bool] = lambda _name: False,
                templates: dict[str, str] | None = None) -> tuple[list[str], list[str]]:
    """Texte im Jahrgangsordner anlegen; vorhandene nur nach Bestätigung überschreiben. Rückgabe: (geschriebene Dateien, unbekannte Platzhalter)."""
    written: list[str] = []
    texts, unknown = render_mails(angebote, aufgaben, termin, templates)
    for name, text in texts.items():
        target = workspace / name
        if target.exists() and not confirm_overwrite(name):
            continue
        workspace.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="")
        tmp.replace(target)
        written.append(name)
    return written, unknown
