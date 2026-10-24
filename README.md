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

Optionen `--config-dir`, `--year` und `--workspace` können **vor oder nach dem Unterbefehl** stehen. Ein explizites `--workspace` hat Vorrang; zusammen mit einem ausdrücklich angegebenen `--year` muss der Ordner jedoch mit dem registrierten Pfad übereinstimmen. Ohne explizite Auswahl gilt `current_year`.

## Private SQL-Konfiguration

Die mitgelieferte `mao_cli/defaults/tables.ini` enthält ausschließlich `DEMO_`-Namen. Nach `mao config init` muss die **zentrale** `tables.ini` lokal bearbeitet werden; außerdem `db.config.example` nach `db.config` kopieren und mit den eigenen Zugangsdaten ausfüllen.

**Weder echte Tabellennamen noch SQL-Strukturdumps, Passwörter oder Schülerdaten gehören in Git.** Das Repository enthält nur neutrale Standardkonfigurationen. Fehlende echte Namen blockieren Datenbankbefehle, während `mao validate` offline funktioniert.

Der Jahrgang bestimmt das Arbeitsverzeichnis, **nicht** automatisch den Datenbankstand. `run` und `plan` sperren gezielt registrierte Nicht-Standardjahrgänge gegen aktuelle SQL-Exporte. Die optionale `mao`-Nummer in einem registrierten Jahrgang wird bei Datenbankläufen mit dem aus der Datenbank ermittelten Stand verglichen.

## Wichtige Befehle

```powershell
mao --config-dir "D:/MaO/Config" workspace select 2026-27
mao validate
mao profiles
mao run --profile vorbereitung --no-latex
mao plan --profile ergebnisse
mao init-jahrgang --year 2026-27 --offline
mao update check --channel rc
mao update status
mao update rollback --yes
```

**`mao init-jahrgang` ist in v0.6 nur eine schreibgeschützte Vorprüfung.** Ohne `--offline` verbindet sich der Befehl zur Datenbank und prüft Tabellen/Spalten/Fremdschlüssel sowie die aktuelle Datensatzanzahl, führt aber **keine** Archivierung, Migration, Löschung oder Neuanlage durch. Die SQL-Datei enthielt keine Datensätze und nicht alle referenzierten Tabellen, deshalb wäre ein Reset zum jetzigen Zeitpunkt unsicher.

Weitere Informationen: [Befehlsreferenz](wiki/Befehle.md), [Konfiguration](wiki/Konfiguration.md), [Arbeitsverzeichnisse](wiki/Arbeitsverzeichnisse.md), [Jahrgangswechsel](wiki/Jahrgangswechsel.md) und [Updateverfahren](wiki/Updates.md).

## Git Flow und Tests

`main` enthält den bislang veröffentlichten Entwicklungsstand, `develop` den Integrationsstand. Funktionen werden auf `feature/*` entwickelt. CI prüft Python 3.10 und 3.13 unter Windows und Linux. `CHANGELOG.md` verbleibt im Hauptrepository; `wiki/` enthält versionierte Dokumentation.

```bash
python -m pytest -q
python -m mao_cli --version
```

Produktive Datenbank- und LaTeX-Läufe müssen zusätzlich auf dem Zielsystem getestet werden. In der zentralen Konfiguration sollten Zugangsdaten restriktiv geschützt werden.
