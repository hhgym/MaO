# Befehlsreferenz

Alle Befehle setzen eine installierte Entwicklungsfassung von MaO-CLI voraus (`python -m pip install -e .`).

Die Aufrufe sind nach dem neuen Befehlsschema aufgebaut:

```text
mao <befehl> [optionen]
```

Alternative ohne installierten Konsolenbefehl aus dem Projektordner: `python -m mao_cli <befehl> [optionen]`.

## Neue Jahrgangs- und Konfigurationsbefehle (v0.6)

```powershell
mao --config-dir "D:/MaO/Config" config init
mao --config-dir "D:/MaO/Config" config show
mao --config-dir "D:/MaO/Config" workspace add 2026-27 --root "D:/Schuljahre/2026-27/MaO"   # Sonderfall
mao jahrgang new / list / use 66        # Jahrgänge unter MaO/Jahrgang/
mao workspace list
mao workspace select 2026-27
mao workspace wrapper 2026-27
mao validate --year 2026-27
mao init-jahrgang --year 2026-27 --offline
```

Die zentralen Schalter `--config-dir`, `--year`, `--workspace` können vor oder nach dem Unterbefehl stehen. Konfigurationsdateien liegen im Ordner `MAO_CONFIG`, Workspaces enthalten ausschließlich Exporte und Dokumente. **`init-jahrgang` ist read-only; keine Daten werden verändert.**

## Übersicht

| Befehl | Zweck | Datenbank nötig? | Schreibzugriffe? |
|---|---|---|---|
| `mao profiles` | Profile anzeigen | Nein | Nein (außer optionales Logfile) |
| `mao validate` | Offline YAML/INI/Profiles prüfen; mit `--online` zusätzlich Datenbank und GF-API | Nur mit `--online` | Nein (außer optionales Logfile) |
| `mao plan` | Exportdateien und Aktionen voraussehen | Ja | Nur optional die angegebene Markdown-Plan-Datei und ein Logfile |
| `mao run` | Exporte erzeugen, optional PDFs kompilieren | Ja | Ja |
| `mao latex` | Nur LaTeX mit vorhandenen Dateien | Nein | Ja |
| `mao update` | Aktualisierungsbefehl für uv nennen (stable, rc, develop) | Nein | Nein |
| `mao --version` | Programmversion anzeigen | Nein | Nein |
| `mao --help` | Hilfe anzeigen | Nein | Nein |

### Gemeinsame Optionen

| Option | Bedeutung |
|---|---|
| `--config-dir ORDNER` | Zentrales Konfigurationsverzeichnis (alternativ `MAO_CONFIG`) |
| `--year YYYY-YY` | Registrierter Jahrgang, standardmäßig `current_year` |
| `--workspace ORDNER` | Explizites Arbeitsverzeichnis; Standard: registrierter aktueller Jahrgang in `settings.yaml` |
| `--profile NAME` | Ausführungsprofil; Standard aus `settings.yaml` (initial `vollpaket`). Unterstützt bei `run`, `plan`, `latex`, `validate` |
| `--verbose` | Detailliertes Logging |
| `--log-file DATEI` | Zusätzliche Logdatei; relative Namen beziehen sich auf den Arbeitsordner |

**Wichtig:** Globale Optionen (`--config-dir`, `--year`, `--workspace`) dürfen auch vor dem Befehl stehen. Befehlsbezogene Optionen wie `--profile` gehören hinter den Befehl.

## `mao profiles`

Listet die Namen und Beschreibungen aller Profile aus `profiles.ini`.

```bash
mao profiles
mao profiles --workspace "D:/MaO/2026-27"
```

## `mao validate`

Prüft die YAML-Struktur, Tabellen-Identifier, Profilreferenzen, Export-IDs, LaTeX-Job-IDs, Dateikollisionen und Ausgabepfade. Es werden **keine DB-Abfragen** ausgeführt; die Existenz tatsächlicher SQL-Spalten kann daher nicht vollständig überprüft werden.

```bash
mao validate
mao validate --workspace "D:/MaO/2026-27" --profile ergebnisse
```

Ein fehlender SQL-View wird beim Offline-Validieren nicht erkannt.

```powershell
mao validate                 # offline, Standard
mao validate --online        # DB-Struktur, Pipeline-/GF-SELECTs, IDs, GF-API
```

`--online` prüft nur lesend: Zielspalten und Tabellenbelegung, SQL-Spalten,
doppelte Matrikelnummern, referenzierte Lehrkräfte und eindeutige
Lerngruppeneinsätze sowie die erste GF-API-Seite. Die Zieltabelle darf hierbei
bereits Daten enthalten (Hinweis: erneuter Erstimport gesperrt). Eine Prüfung
sämtlicher konkreter GF-Meldungen mit Klassenauswertung erfolgt anschließend
mittels `mao import-gf --dry-run`. Die normale `validate`-Variante benötigt
weiterhin weder Datenbank noch Internetzugang.

## `mao plan`

Führt nur lesende SQL-Abfragen aus und plant alle im Profil aktivierten Exportdateien. Eine bestehende Manifestdatei wird zur Unterscheidung von `NEU`, `AKTUALISIEREN`, `UNVERÄNDERT` und `ENTFERNEN` eingelesen. **Keine Datenexporte werden geschrieben oder gelöscht.**

```bash
mao plan --workspace "D:/MaO/2026-27" --profile vorbereitung
mao plan --workspace "D:/MaO/2026-27" --profile ergebnisse --no-latex
mao plan --workspace "D:/MaO/2026-27" --output export_plan.md
```

Optionen: `--output MARKDOWN` (legt die Plan-Datei im Arbeitsordner ab, wenn relativ angegeben), `--no-latex` (LaTeX-Jobs aus Liste entfernen) und `--mao NUMMER` (Dokumentnummer, **kein SQL-Wechsel**).

## `mao run`

Lädt die für das Profil erforderlichen SQL-Datasets, erzeugt CSV- und TeX-Dateien und kompiliert aktivierte LaTeX-Jobs. Vorhandene, unveränderte Dateien bleiben unverändert. Veraltete Dateien werden nur gelöscht, wenn sie im Manifest dem ausgewählten Export gehören **und** seit ihrer Erzeugung nicht manuell geändert wurden.

```bash
mao run --workspace "D:/MaO/2026-27" --profile vollpaket
mao run --workspace "D:/MaO/2026-27" --profile turniertag --no-latex
mao run --workspace "D:/MaO/2026-27" --profile ergebnisse --mao 66
```

Optionen: `--no-latex` und `--mao NUMMER` (nur Dokumentnummer).

## `mao latex`

Benötigt keine Datenbankverbindung und verändert keine CSV-Dateien. Kompiliert aktivierte LaTeX-Jobs aus bereits vorhandenen Daten im Arbeitsordner.

```bash
mao latex --workspace "D:/MaO/2026-27" --profile ergebnisse --mao 66
```

Wenn `--mao` fehlt, wird die Jahrgangsnummer rechnerisch anhand des Datums bestimmt. Für vergangene oder künftige Wettbewerbsjahrgänge deshalb `--mao` ausdrücklich setzen.

## `mao update`

```bash
mao update                       # neuester stabiler Release
mao update --channel rc          # inklusive Release Candidates
mao update --channel develop
mao update --version 0.6.0 [--repository OWNER/REPO]
```

Der Befehl erkennt die Installationsart, fragt bei stable/rc anonym die Tags auf GitHub ab und nennt den passenden `uv tool install --force --reinstall git+https://github.com/...@<Ref>`-Befehl. Er installiert nichts selbst. Details: [Updates](Updates).

## Beenden und Fehlercodes

| Code | Bedeutung |
|---:|---|
| `0` | Befehl erfolgreich beendet |
| `2` | Konfigurations-, Dateipfad- oder Validierungsfehler |
| `3` | Datenbankzugang oder Netzwerkverbindung fehlgeschlagen |
| `4` | Unerwarteter Verarbeitungsfehler (z. B. SQL-/LaTeX-Prozess) |
| `5` | `mao update`: Netzwerk/Version nicht ermittelbar |

**Sicherheit:** `db.config` nie in Git einchecken. Die Befehle `run` und `plan` lesen echte Daten aus der konfigurierten Datenbank. Bei Fehlern niemals vorhandene Listen ohne Prüfung als aktuell ansehen.

## Noch nicht verfügbare Befehle

`gui` ist nicht implementiert. `init-jahrgang` existiert bislang ausschließlich als schreibgeschützte Vorprüfung.

## Updateverwaltung

Installation und Updates laufen über uv: [Updates](Updates.md).

### Gravity-Forms-Import

- `mao import-gf` oder `mao import-gf --dry-run` prüft das Meldeformular und
  alle Zuordnungen **ohne INSERT**. Mit `--report reports/pruefung.json` bzw.
  `.csv` wird im Workspace ein neuer Prüfbericht mit Klassensummen,
  Pflichtteilnahmen, fehlenden Zuordnungen und gegebenenfalls Gründen für
  einen Abbruch abgelegt. Bestehende Berichte werden nicht überschrieben.
- `mao import-gf --apply` fügt die Teilnehmenden ausschließlich in eine
  nachweislich leere Organisationstabelle ein.
- `mao import-gf --no-mandatory` unterdrückt ausnahmsweise Pflichtteilnahmen.
- `--report` ist ausschließlich für den Dry-Run vorgesehen; mit `--apply` unzulässig.
- `mao --config-dir "D:/MaO/Config" --year 2026-27 import-gf` verwendet die
  zentral hinterlegten Einstellungen des registrierten Schuljahres.

Erforderlich sind `gf.config`, `gf-import.yaml`, `db.config` und die echte
`orga_table` in der zentralen `tables.ini`. Archivieren, Leeren und die
Raumverteilung des historischen Skripts gehören **nicht** zu `import-gf`.


## Raumzuteilung

- `mao raumzuteilung --dry-run --report raum.json` plant die Verteilung nach
  `raumregeln.yaml` und zeigt sie im Log (Raum, Belegung, Stufen, offene
  Teilnehmende). Der Bericht enthält keine Namen. Es wird nichts geschrieben.
- `mao raumzuteilung --seed 123 --apply` schreibt `raum_id` in die
  Organisationstabelle (Transaktion, Sperre, Zählabgleich). Mit demselben
  `--seed` entsteht dieselbe Verteilung wie im Dry-Run (der Seed steht im Log).
  Der Dry-Run nennt außerdem eine Plan-ID; `--apply --plan-id ID` schreibt nur,
  wenn sich Daten oder Regeln seitdem nicht geändert haben.
- Vorhandene Zuordnungen bleiben erhalten und belegen Plätze; so lassen sich
  Lücken nach einer Korrektur nachtragen. `--ueberschreiben` verwirft alles und
  verteilt neu.
- Die Top-15 sind die Besten unter den aktuell Teilnehmenden; ihre Vorjahrespunkte werden über die View aus `points_last_year_view`
  (tables.ini) bestimmt: Spalten `Matrikelnummer`, `Klasse` (aktuell), `Punkte`.
  Nur Zahlen zählen; Stufe 11 und 12 werden gemeinsam gewertet.
- Regeln (Zielräume je Stufe, Reserve, Top-15-Räume, Kapazitäten) stehen in
  `raumregeln.yaml` und gelten **je Jahrgang**: maßgeblich ist die Datei im Jahrgangsordner
  (`Jahrgang/<Nr>_<Schuljahr>/raumregeln.yaml`), weil sich die Teilnehmerzahlen je Stufe jährlich ändern.
  Fehlt sie dort, nimmt mao die zentrale Vorlage im Konfigurationsordner (`mao config init` legt sie an)
  und warnt. `mao raumzuteilung --regeln-anlegen` kopiert die Vorlage in den Jahrgangsordner (nie
  überschreibend); `mao jahrgang new` übernimmt die Regeln des letzten Jahrgangs als Ausgangspunkt.
  Der Bericht nennt die verwendete Datei (`rules_file`).


- **Sammelräume (optional):** `rooms.sammel: [...]` in `raumregeln.yaml` nimmt alle auf, die nicht in ihre festen
  Räume (`grade_rooms`, Top-15-Räume) passen oder dafür keinen Raum haben. Es werden so wenige Räume wie möglich
  genutzt (größte freie Kapazität zuerst). `rooms.sammel_stil` bestimmt die Aufteilung:
  - `rein_zuerst` (Standard): Jeder Raum, den eine Stufe allein füllen kann, bekommt genau diese Stufe. Nur die
    Reste aller Stufen kommen gemeinsam in die übrigen Räume; passen sie in einen Raum, gibt es genau einen
    gemischten Sammelraum.
  - `nachbarn`: Die Stufen liegen als Blöcke hintereinander; gemischt wird nur mit benachbarten Stufen, dafür
    können mehrere Räume gemischt sein.
  - `sammel_max_spanne: N` (zusätzlich zu `rein_zuerst`): gemischte Räume enthalten nur Stufen innerhalb von N
    Stufen Abstand (1 = nur Nachbarstufen). Dann bleiben so viele reine Räume wie möglich, ohne die Raumzahl zu erhöhen.
    Beispiel (Rest 7: 32, 8: 32, 9: 23, 10: 3 in sechs Räumen): ohne Begrenzung 5 reine Räume und 1 Sammelraum mit
    vier Stufen; Spanne 2 gibt 4 reine und 2 gemischte Räume; Spanne 1 gibt 3 reine und 3 Räume mit je zwei Nachbarstufen.
  Klassen werden innerhalb einer Stufe weiter gemischt. Die Verteilung steht im Log und im Bericht (`sammelraeume`).
  Ohne `sammel` bleibt das bisherige Verhalten.
- Am Ende des Laufs steht „Räume je Stufe“: für jede Stufe die Räume mit der Anzahl (im Bericht `raeume_je_stufe`).
- **Von Hand nachtragen:** `mao raum-setzen --raum 105 --matrikel 12345 12346` prüft zuerst (Dry-Run) und
  schreibt erst mit `--apply`. Geprüft werden Raum (vorhanden, aktiv), Matrikelnummern, vorhandener Raum
  (nur mit `--ueberschreiben` ersetzt) ; die Kapazität wird nur gemeldet, nicht erzwungen. Von Hand gesetzte Räume
  bleiben bei der nächsten `mao raumzuteilung` erhalten (Ausnahme: `--ueberschreiben` verteilt alles neu).
- **Schritt für Schritt:** `mao raum-setzen --interaktiv [--apply]` zeigt jede Person ohne Raum mit bis zu fünf Raumvorschlägen. Eingabe: Nummer oder Raumcode, Enter überspringt, `u` nimmt zurück, `r` zeigt die Räume, `q` beendet. Geschrieben wird erst nach der Zusammenfassung und `j` (nur mit `--apply`).
- `mao raumzuteilung --extern` teilt stattdessen die externen Teilnehmenden zu. Die Räume stehen in
  `raumregeln.yaml` unter `extern: {rooms: [...]}` und dürfen nicht zugleich im Hauptwettbewerb vorkommen.
  Gefüllt wird nach Teilnehmernummer Raum für Raum bis `max_per_room` (bzw. Datenbankkapazität). Wer
  keinen Platz findet, wird gemeldet; dann einen weiteren Raum eintragen. Dry-Run, `--apply`,
  `--plan-id` und `--ueberschreiben` verhalten sich wie bei der normalen Zuteilung.


## Aufsichten prüfen

- `mao aufsichten --report aufsichten.json` vergleicht nach der Raumzuteilung die belegten Räume
  (Hauptwettbewerb und Externe) mit der Aufsichtentabelle (`aufsichten_table` in tables.ini:
  Jahrgang, Raum, Lehrkraft, Rolle). Der Befehl liest nur und schreibt nichts.
- Gemeldet werden: Räume mit zu wenigen Aufsichten (`fehlt`), Plätze ohne Lehrkraft, eine Lehrkraft
  in mehreren Räumen, nicht mehr aktive oder unbekannte Lehrkräfte (`fehler`) sowie Hinweise
  (überzählige Aufsichten, Aufsicht in einem leeren Raum, Eintrag ohne Raum).
- Regeln in `raumregeln.yaml` (je Jahrgang): `aufsichten: {lehrkraefte_pro_raum: 1, zaehlende_rollen: ["Standard"]}`.
  Die Rollen `Reserve` (Springer) und `Fachaufsicht` (Wettkampfleitung: beantwortet Fragen zu den Aufgaben) gehören keinem Raum an und zählen standardmäßig nicht als Raumaufsicht. Fehlt eine Fachaufsicht, gibt es einen Hinweis; ist sie einem Raum zugeordnet, ebenfalls.
- **Aufsichten setzen** (Dry-Run ist Standard, `--apply` schreibt):
  - `mao aufsichten --raum 105 --lehrkraft GER NIX [--apply]` trägt Lehrkräfte per Kürzel als Raumaufsicht ein.
  - `mao aufsichten --rolle Fachaufsicht --lehrkraft MEI [--apply]` (ebenso `Reserve`) trägt Lehrkräfte ohne Raum ein.
  - `--entfernen` löscht die genannten Einträge. Eingetragen werden können alle aktiven Lehrkräfte, nicht nur Mathematik.
    Ist jemand schon in einem anderen Raum eingeteilt, gibt es eine Warnung (Eintrag erfolgt trotzdem).
  - `mao aufsichten --interaktiv [--apply]` geht alle Räume ohne volle Aufsicht der Reihe nach durch
    (danach Fachaufsicht und Reserve, falls noch keine eingetragen ist). Eingabe: Kürzel (mehrere mit Leerzeichen oder Komma),
    Enter überspringt, `l` zeigt die Mathematik-Lehrkräfte, `la` alle, `q` beendet. Geschrieben wird erst nach der Zusammenfassung und `j`.
  - **Alles auf einmal (Plandatei):** `mao aufsichten --plan-exportieren` legt `aufsichten-plan.yaml` im Jahrgangsordner an
    (nie überschreibend): je Raum eine Kürzelliste, dazu `fachaufsicht` und `reserve`. Die Datei im Editor bearbeiten
    (Tausche, Lücken füllen), dann `mao aufsichten --plan aufsichten-plan.yaml` (Dry-Run: zeigt `+`/`−`/Warnungen) und
    `--apply` schreibt alle Änderungen in einer Transaktion. Die Datei ist der Zielzustand der darin genannten Abschnitte
    und Räume; fehlende Abschnitte/Räume bleiben unverändert. Eine Lehrkraft in mehreren Räumen ergibt eine Warnung.
  - Für die Mathematik-Auswahl `teacher_subjects_table` und `subjects_table` in tables.ini setzen (optional; sonst zeigt `l` alle).
- Die Oberstufenschüler als Aufsicht folgen später.


## Schulabgleich

- `mao schulen-sync --dry-run --report schulen.json` lädt das Schulverzeichnis Berlin und
  zeigt, welche Schulen neu, geändert oder weggefallen sind. Es wird nichts geschrieben.
- `mao schulen-sync --apply` schreibt die Änderungen in die Tabelle aus `schools_table`
  (tables.ini). Weggefallene Schulen werden auf `aktiv = 0` gesetzt, nie gelöscht.
- Bei unvollständiger Antwort der Quelle (weniger als 100 Schulen, weniger als die Hälfte der
  bisher aktiven, abweichende Gesamtzahl) bricht der Befehl ab, ohne etwas zu schreiben.
- `--url` ersetzt die Standardadresse (Schulverzeichnis Berlin, GeoJSON).


## Externe Teilnehmende

Anmeldungen von Kindern aus Grundschulen laufen über ein eigenes Gravity-Forms-Formular und landen
in eigenen Tabellen, getrennt von Stammdaten und Organisationstabelle (`extern_personen_table`,
`extern_teilnahmen_table` in tables.ini). Personen gelten jahrgangsübergreifend, Teilnahmen je Jahrgang;
dadurch ist das Archiv bereits enthalten.

- `mao import-extern --dry-run --report extern.json` liest das Formular (Nummern in `gf-extern.yaml`,
  Zugangsdaten in `gf.config`) und zeigt, wer neu übernommen würde. Es wird nichts geschrieben.
- `mao import-extern --apply` legt Person und Teilnahme im aktuellen Jahrgang an. Die Teilnehmernummer
  (E01, E02, …) vergibt die Datenbank in der Reihenfolge der Anmeldung.
- Der Befehl ist wiederholbar: Anmeldungen, deren GF-Eintrag schon importiert ist, werden übersprungen.
- Gleicher Name und gleiche Schule im selben Jahrgang gilt als Doppelanmeldung und wird übersprungen
  (steht im Bericht). Gleicher Name an anderer Schule wird übernommen, mit Hinweis.
- Ein Kind, dessen Name schon aus einem früheren Jahrgang bekannt ist, wird nicht automatisch
  zusammengeführt; der Bericht nennt unter `wiederkehrend` die möglichen `person_id`s.
- Einträge mit fehlendem Namen, unzulässiger Klassenstufe (nur 3 bis 6) oder unbekannter Schulnummer
  werden übersprungen und im Bericht aufgeführt; nach der Korrektur (z. B. `mao schulen-sync --apply`)
  genügt ein erneuter Lauf.
- Aus dem Formular werden nur Name des Kindes, Klassenstufe, Schule und optional das Geschlecht (`geschlecht` in `gf-extern.yaml`, Werte m, w, d) gespeichert, nicht die Daten der Eltern.
