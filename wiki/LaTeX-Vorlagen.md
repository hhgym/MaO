# LaTeX-Vorlagen

Vorlagen (`.tex`, Logos, Präambel) liegen **getrennt** von den erzeugten Daten und PDFs im zentralen MaO-Ordner:

```text
MaO/
  tex/
    base/                      gemeinsame Präambel und Bilder (template.tex, Siegel-Logo)
    Teilnehmendenlisten/       ein Ordner je Dokument
      Teilnehmendenlisten.tex
      MO-Logo_200.jpg
  Jahrgang/66_2026-27/
    Listen/
      db/                      CSV- und Makrodateien (Export)
      Teilnehmendenlisten/     hier entsteht das PDF, z. B. MaO_66_Teilnehmendenlisten.pdf
```

## Job in der `pipeline.yaml`

```yaml
- name: teilnehmendenlisten
  id: teilnehmendenlisten
  workdir: "Listen/Teilnehmendenlisten"             # hier läuft LaTeX, hier landet das PDF
  tex: "@tex/Teilnehmendenlisten/Teilnehmendenlisten.tex"
  pdf_name: "MaO_{mao}_Teilnehmendenlisten"
```

`@tex/` verweist auf `MaO/tex`. Ohne diesen Präfix wird die Vorlage wie bisher relativ zum `workdir` gesucht.

## Was die Vorlage nutzen kann

MaO setzt beim Aufruf den TeX-Suchpfad (`TEXINPUTS`) auf den Ordner der Vorlage, auf `tex/base` und auf `tex/`; danach gelten die Standardpfade von TeX Live. Dadurch genügt in der Vorlage:

```latex
\input{template.tex}                          % aus tex/base
\includegraphics{MO-Logo_200.jpg}             % aus dem Ordner der Vorlage
\includegraphics{siegel-transparent-black_entwurf.png}   % aus tex/base
```

Statt `\input{../tex/template.tex}` und `../tex/siegel….png` also nur den Dateinamen angeben. Die Exportdaten werden weiterhin relativ zum Job-Ordner eingebunden (`../db/...`); die von MaO erzeugten `Include`-Dateien enthalten diese Pfade bereits.

Zusätzlich definiert MaO diese Makros (Pfade mit `/`, mit abschließendem `/`, Leerzeichen im Pfad sind erlaubt):

| Makro | Inhalt |
|---|---|
| `\MaO` | MaO-Nummer |
| `\MaOjahrgang` | Schuljahr, z. B. `2026-27` |
| `\MaOroot` | Jahrgangsordner |
| `\MaOdb` | Exportordner (`Listen/db/`, änderbar mit `latex.db_dir`) |
| `\MaOtex` | Vorlagenordner `MaO/tex/` |

Beispiel: `\DTLloaddb{t}{\MaOdb Teilnehmende/Teilnehmende-Stufe-5.csv}`.

Pfade mit `%`, `#`, `{`, `}` oder `\` werden mit einer Fehlermeldung abgelehnt.

## Aufräumen

Hilfsdateien (`.aux`, `.log`, …) bleiben liegen. Nur auf Wunsch: `mao run --cleanup-latex` bzw. `mao latex --cleanup-latex` oder `cleanup_aux: true`.
