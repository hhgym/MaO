# MaO-CLI

Python-Anwendung für die Organisation der Mathematik-Olympiade: MySQL-Abfragen, CSV-/LaTeX-Datenexporte und PDF-Erstellung.

**Entwicklungsstand `0.6.0.dev0`.** Die Befehle und Konfigurationsorte sind vor Version 2.0 noch nicht kompatibilitätsstabil. Die frühere Aufteilung von `db.config` und `config/` auf einzelne Arbeitsverzeichnisse wurde durch eine zentrale Konfiguration ersetzt. Vorhandene Daten und Einstellungen vor der Umstellung sichern.

## Installation

Python 3.10 oder neuer, für PDF-Jobs zusätzlich eine LaTeX-Distribution:

```bash
python -m pip install -e .
python -m pip install -e '.[dev]'  # nur für die Entwicklung
```

Die CLI heißt `mao`; alternativ `python -m mao_cli`.

## Zentrale Konfiguration und Workspaces

Für mehrere Rechner siehe Wiki „Konfiguration“, Abschnitt *Zentraler MaO-Ordner*: `mao config init <MaO-Ordner>` und pro Rechner einmal `mao config use <MaO-Ordner>`; danach entfällt `--config-dir`.

`--config-dir` bestimmt das Konfigurationsverzeichnis. Fehlt der Schalter, wird `MAO_CONFIG` verwendet; andernfalls das benutzerspezifische Standardverzeichnis (Windows: `%APPDATA%/MaO`, Linux: `${XDG_CONFIG_HOME:-~/.config}/mao`).

```powershell
mao --config-dir "D:/MaO/Config" config init
mao --config-dir "D:/MaO/Config" workspace add 2026-27 --root "D:/Schuljahre/2026-27/MaO"
mao --config-dir "D:/MaO/Config" config show
mao --config-dir "D:/MaO/Config" workspace list
mao --config-dir "D:/MaO/Config" validate --year 2026-27
```

Zentrale Dateien:

```text
D:/MaO/Config/
  settings.yaml        # zugeordnete Schuljahre, absolute Arbeitsverzeichnisse, Standardprofil
  pipeline.yaml        # SQL-Datasets, Exporte und LaTeX-Jobs
  profiles.ini         # Ausführungsprofile
  tables.ini           # private, tatsächliche SQL-Tabellen und Views
  db.config            # private Zugangsdaten, selbst aus db.config.example erstellen
```

Im jeweiligen **Workspace** werden ausschließlich erzeugte Daten und Dokumente abgelegt:

```text
D:/Schuljahre/2026-27/MaO/
  mao-hier.cmd        # Windows-Wrapper
  mao-hier.sh         # Linux/macOS-Wrapper
  Listen/
  .mao-cli/exports-manifest.json
  ...                # LaTeX- und PDF-Ausgaben gemäß pipeline.yaml
```

Die `settings.yaml` enthält **für jedes Schuljahr einen expliziten absoluten Pfad**. Es gibt keine automatische Berechnung aus einem Pfadmuster. Ein eigener Pfad je Jahrgang darf auf einem anderen Laufwerk liegen.

```yaml
current_year: "2026-27"
workspaces:
  "2026-27":
    root: "D:/Schuljahre/2026-27/MaO"
    status: current
  "2025-26":
    root: "D:/Schuljahre/2025-26/MaO"
    status: archived
defaults:
  profile: vollpaket
```

`workspace add` registriert den Ordner und erstellt die Wrapper. Ein Aufruf aus dem Workspace erfolgt z. B. mit `mao-hier.cmd run --profile turniertag`. Die Wrapper enthalten keine Zugangsdaten, sondern nur `--config-dir`, `--year` und die weitergereichten Argumente.

Optionen `--config-dir`, `--year` und `--workspace` können **vor oder nach dem Unterbefehl** stehen. Ein explizites `--workspace` hat Vorrang; zusammen mit einem ausdrücklich angegebenen `--year` muss der Ordner jedoch mit dem registrierten Pfad übereinstimmen. Liegt ein per `--workspace` angegebener Ordner in einem registrierten Workspace, gilt dessen Jahrgang, einschließlich der Sperre für archivierte Jahrgänge. Ohne explizite Auswahl gilt `current_year`.

## Private SQL-Konfiguration

Die mitgelieferte `mao_cli/defaults/tables.ini` enthält ausschließlich `DEMO_`-Namen. Nach `mao config init` muss die **zentrale** `tables.ini` lokal bearbeitet werden; außerdem `db.config.example` nach `db.config` kopieren und mit den eigenen Zugangsdaten ausfüllen.

**Weder echte Tabellennamen noch SQL-Strukturdumps, Passwörter oder Schülerdaten gehören in Git.** Das Repository enthält nur neutrale Standardkonfigurationen. Fehlende echte Namen blockieren Datenbankbefehle, während `mao validate` offline funktioniert.

Der Jahrgang bestimmt das Arbeitsverzeichnis, **nicht** automatisch den Datenbankstand. `run` und `plan` sperren gezielt registrierte Nicht-Standardjahrgänge gegen aktuelle SQL-Exporte. Die optionale `mao`-Nummer in einem registrierten Jahrgang wird bei Datenbankläufen mit dem aus der Datenbank ermittelten Stand verglichen.

## Wichtige Befehle

```powershell
mao --config-dir "D:/MaO/Config" workspace select 2026-27
mao validate                 # bisherige Offline-Prüfung
mao validate --online        # zusätzlich MySQL, SQL-Quellen, GF-API
mao profiles
mao run --profile vorbereitung --no-latex
mao plan --profile ergebnisse
mao init-jahrgang --year 2026-27 --offline
mao update check --channel rc
mao update status
mao update rollback --yes
```

**`mao init-jahrgang` ist in v0.6 nur eine schreibgeschützte Vorprüfung.** Ohne `--offline` verbindet sich der Befehl zur Datenbank und prüft Tabellen/Spalten/Fremdschlüssel sowie die aktuelle Datensatzanzahl, führt aber **keine** Archivierung, Migration, Löschung oder Neuanlage durch. Die SQL-Datei enthielt keine Datensätze und nicht alle referenzierten Tabellen, deshalb wäre ein Reset zum jetzigen Zeitpunkt unsicher.

Weitere Informationen im [öffentlichen GitHub-Wiki](https://github.com/hhgym/MaO/wiki): [Befehlsreferenz](wiki/Befehle.md), [Konfiguration](wiki/Konfiguration.md), [Arbeitsverzeichnisse](wiki/Arbeitsverzeichnisse.md), [Jahrgangswechsel](wiki/Jahrgangswechsel.md) und [Updateverfahren](wiki/Updates.md).

## Git Flow und Tests

`main` enthält den bislang veröffentlichten Entwicklungsstand, `develop` den Integrationsstand. Funktionen werden auf `feature/*` entwickelt. CI prüft Python 3.10 und 3.13 unter Windows und Linux. `CHANGELOG.md` verbleibt im Hauptrepository; `wiki/` enthält versionierte Dokumentation.

```bash
python -m pytest -q
python -m mao_cli --version
```

Produktive Datenbank- und LaTeX-Läufe müssen zusätzlich auf dem Zielsystem getestet werden. In der zentralen Konfiguration sollten Zugangsdaten restriktiv geschützt werden.

## Gravity Forms: Teilnahme-Import (sichere Erstbefüllung)

Das frühere Skript `mao_archive_and_import.py` fragte das Meldeformular via OAuth 1.0
ab. Diese Funktion ist jetzt als `mao import-gf` integriert. Der Befehl ist vom
späteren Archivierungs-/Jahrgangswechsel **getrennt**; er archiviert oder löscht
keine Tabellen und übernimmt noch keine Raumzuteilung.

`mao config init` erzeugt zwei **leere bzw. neutrale Vorlagen** im zentralen
Konfigurationsverzeichnis. Diese privat kopieren und vervollständigen:

- `gf.config` (durch `mao config init` direkt angelegt): `[oauth1]` mit `api_base`, `consumer_key`, `consumer_secret`.
- `gf-import.yaml` (durch `mao config init` direkt angelegt): Formular-ID (ursprünglich 16),
  Gravity-Forms-Feldnummern, aktuelle Pflichtklassen und **private SQL-SELECT-Abfragen**.
  Die SELECT-Abfragen müssen die festgelegten Alias-Spalten zurückgeben.

Beispiel:

```powershell
mao --config-dir "D:\MaO\Config" import-gf --dry-run --report reports/mao-pruefung.json
mao --config-dir "D:\MaO\Config" import-gf --apply
```

Das **Online-Validieren** mit `mao validate --online` prüft die konfigurierte
MySQL-Verbindung, Spaltenstruktur der Organisationstabelle, Pipeline- und GF-SQL-
Abfragen, Stammdaten-IDs, eindeutige Lerngruppeneinsätze, Pflichtklassen sowie
die Erreichbarkeit der GF-API. Nur `--online` verbindet sich extern; die normale
`validate`-Variante bleibt offline und schnell. Eine nicht leere Zieltabellen-
struktur wird als nicht importbereit gemeldet, ohne die allgemeine Validierung
zu blockieren.

Mit `--report PFAD.json` oder `--report PFAD.csv` schreibt der Import-Dry-Run
im registrierten Workspace einen neuen Prüfbericht. Darin stehen Gesamtzahlen,
Klassenübersichten, direkt angemeldete und automatisch ergänzte Teilnehmende,
fehlende Lehrer-/Kurszuordnungen und bei Abbruch Diagnosen wie doppelte oder
unbekannte Matrikelnummern. Der Bericht überschreibt keine vorhandene Datei.
Keine persönlichen Namen werden in den regulären Klassensummen veröffentlicht;
bei Fehlern können Matrikelnummern in der privaten Berichtdatei stehen.
`--report` ist mit `--apply` bewusst nicht kombinierbar.

Standard ist **Dry-Run**. Schreibmodus erfordert `--apply` und eine **vollständig
leere** Organisationstabelle. Es gibt kein TRUNCATE, kein blindes UPSERT und
keinen schreibenden Archivierungsaufruf. Das Programm prüft unbekannte und
doppelte Matrikelnummern, nicht auflösbare Lehrer-/Kurskennungen und ein
passendes Zielschema mit den Spalten `lehrkraft_id`, `kurs_id`. Erst nach
vollständigem Download und Prüfung werden Teilnehmernummern vergeben und die
Zeilen transaktional eingefügt. Fehler führen zum Rollback; ein zweiter
Import in eine bereits gefüllte Tabelle wird abgewiesen.

**Feste ID-Zuordnung:** GF-Feld `3` enthält bereits `lehrkraft_id` und wird
nur anhand der aktuellen Lehrkräfte überprüft. GF-Feld `5` enthält
`lerngruppe_schuljahr_id`. Für die Stufen 11 und 12 übernimmt der Import diesen
Wert unverändert in `kurs_id` der Organisationstabelle; in den Stufen 5–10 bleibt
`kurs_id` `NULL`, auch bei Pflichtteilnahmen. Eine Einsatz-ID wird nicht ermittelt.
Die private `queries.courses` liefert die Spalten `lerngruppe_schuljahr_id` und
`lehrkraft_id`; die Kombination der beiden GF-IDs muss darin vorkommen, sonst wird
der Import blockiert. Diese Prüfung gilt für Oberstufen-Meldungen und für die
Lerngruppen der Pflichtklassen. Worauf die Fremdschlüssel von `kurs_id` und
`lehrkraft_id` tatsächlich verweisen, zeigen `mao validate --online` und jeder
Import an; mit `foreign_keys` in `gf-import.yaml` wird das erwartete Ziel
verbindlich geprüft (siehe [Konfiguration](wiki/Konfiguration.md)).

Die Pflichtklassen `5-1`, `5-2`, `6-1`, `6-2` und `7-3` stehen in
`mandatory_classes`; `7-4` ist optional. Die Abfrage `queries.mandatory`
ermittelt **nur Mathematik-Lerngruppen des aktiven Schuljahres** und liefert
`klasse` (= `klassenname`), `lehrkraft_id` und `lerngruppe_schuljahr_id`. Die eigentlichen Schülerinnen
und Schüler stammen aus `queries.students`. Vorhandene GF-Meldungen werden
nicht doppelt angelegt. Fehlt eine feste Pflichtklasse oder ist die Mathe-
Lehrkraft nicht eindeutig, wird der Import abgebrochen. 7-4 wird nur
berücksichtigt, wenn sie im aktiven Schuljahr existiert.

Produktive SQL-Objektnamen und Zugangsdaten bleiben in `MAO_CONFIG` außerhalb
von GitHub. Die tatsächliche Auswahl der Mathematik-Lerngruppen hängt von
der lokalen View-Definition ab und ist vor dem ersten Datenbanklauf zu prüfen.
Die erste produktive Ausführung sollte zunächst als Dry-Run und mit
vorhandener Datenbanksicherung stattfinden.


### Öffentliche Develop-Updates

`mao update check --channel develop` und `mao update install --channel develop --yes` verwenden öffentliche GitHub-Release-Dateien und benötigen weder Token noch Git-Checkout. Nach erfolgreichen Develop-CI-Builds veröffentlicht der Workflow commitgebundene Wheels samt SHA-256 im [Develop-Release](https://github.com/hhgym/MaO/releases/tag/develop-build). Für ältere CLI-Installationen ist einmalig ein neues Wheel erforderlich.

Die SQL-Abfragen in `gf-import.yaml` verwenden `{{table.teachers_table}}`, `{{table.students_table}}` und `{{table.current_groups_view}}` aus der zentralen `tables.ini`. Eine separate Mathematikfilterung ist derzeit nicht erforderlich, da nur Mathematik-Lerngruppen verwaltet werden.

### Aktualisieren der Editable-Installation

Wurde MaO mit `python -m pip install -e .` installiert, schreibt
`mao update install --yes --channel develop` die geprüften Programmdateien
in den tatsächlichen Quellordner zurück (beispielsweise `D:/MaO/mao_cli`
und `D:/MaO/pyproject.toml`). `mao.exe` aus dem Python-Scripts-Verzeichnis
bleibt der Einstiegspunkt, eine Änderung des PATH ist nicht erforderlich.
Die vorherigen Dateien werden unter `D:/MaO/.mao-updates/` gesichert und
können durch `mao update rollback --yes` wiederhergestellt werden.
Der neue Code wird vor und nach dem Austausch geprüft; bei Fehlern erfolgt
die Wiederherstellung automatisch. Werden Abhängigkeiten oder Entry-Points
geändert, stoppt der Updater und fordert eine kontrollierte Neuinstallation.

**Einmaliger Übergang:** Eine bereits installierte ältere Editable-Version
enthält diese Funktion noch nicht. Zunächst einmalig die aktuelle
`develop`-Quell-ZIP nach `D:/MaO` übernehmen; anschließend können
weitere Updates direkt über `mao update install` erfolgen.
Weitere Hinweise: [Updateverfahren](wiki/Updates.md).


### Lokale Konfiguration, Schülerstammdaten und Leermeldung

Bei Editable-Installationen (`D:/MaO/mao_cli`, `D:/MaO/pyproject.toml`, `D:/MaO/config`) wird der private Ordner automatisch verwendet; `--config-dir` und `MAO_CONFIG` haben Vorrang. Die Kennung für die Schülerstammdatentabelle lautet `students_table`. `mao config init` migriert vorhandene `students_view`-Einträge und erhält den Tabellennamen.

Gravity-Forms-Checkbox 10 (`Leermeldung`) bedeutet keine ausgewählten Schülerinnen und Schüler. Solche Einträge werden im Dry-Run pro Klasse gezählt, nicht als Teilnahme gespeichert. Eine gleichzeitige Teilnehmerauswahl blockiert den Import; Pflichtteilnahmen werden unabhängig davon ergänzt.
