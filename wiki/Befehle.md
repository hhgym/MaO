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
mao --config-dir "D:/MaO/Config" workspace add 2026-27 --root "D:/Schuljahre/2026-27/MaO"
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
| `mao validate` | YAML und INI prüfen | Nein | Nein (außer optionales Logfile) |
| `mao plan` | Exportdateien und Aktionen voraussehen | Ja | Nur optional die angegebene Markdown-Plan-Datei und ein Logfile |
| `mao run` | Exporte erzeugen, optional PDFs kompilieren | Ja | Ja |
| `mao latex` | Nur LaTeX mit vorhandenen Dateien | Nein | Ja |
| `mao update check` | Aktualisierung prüfen (stable, RC, develop) | Nein | Develop: Git-Fetch |
| `mao update download` | Releasewheel geprüft herunterladen | Nein | Downloadordner |
| `mao update install` | Manuelles, bestätigtes Update | Nein | Programm-Pythonumgebung oder Git-Checkout |
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

Ein fehlender SQL-View wird erst bei `plan` bzw. `run` sichtbar.

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

Prüfen ohne Installation:

```bash
mao update check --channel stable --repository OWNER/REPO
mao update check --channel rc --repository OWNER/REPO
mao update check --channel develop
```

Prüfsummen-gesicherten Release-Download speichern bzw. installieren:

```bash
mao update download --channel stable --repository OWNER/REPO --output ./downloads
mao update install --channel stable --repository OWNER/REPO --yes
mao update install --channel develop --yes
```

Stable und RC benötigen veröffentlichte GitHub-Releases mit Python-Wheel und SHA-256-Prüfsumme. Develop erfordert einen sauberen Git-Checkout auf `develop`, Fast-Forward und erfolgreichen GitHub-CI-Lauf. Es gibt kein automatisches Update und noch kein automatisches Rollback. Details: [Updates](Updates).

## Beenden und Fehlercodes

| Code | Bedeutung |
|---:|---|
| `0` | Befehl erfolgreich beendet |
| `2` | Konfigurations-, Dateipfad- oder Validierungsfehler |
| `3` | Datenbankzugang oder Netzwerkverbindung fehlgeschlagen |
| `4` | Unerwarteter Verarbeitungsfehler (z. B. SQL-/LaTeX-Prozess) |
| `5` | Updatefehler (Git/Netzwerk/Prüfsumme/Installation) |

**Sicherheit:** `db.config` nie in Git einchecken. Die Befehle `run` und `plan` lesen echte Daten aus der konfigurierten Datenbank. Bei Fehlern niemals vorhandene Listen ohne Prüfung als aktuell ansehen.

## Noch nicht verfügbare Befehle

`init-jahrgang` und `gui` gehören zu geplanten späteren Schritten. Sie sind in dieser Version noch nicht implementiert.

## Updateverwaltung (ab v0.5)

`mao update check|download|install|status|rollback` – Beispiele und Erklärung des versionierten Starters: [Updates](Updates.md).
