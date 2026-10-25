# Changelog
- Konfigurationsordner neben Editable-Quelle automatisch erkennen; `students_table` statt `students_view`; Gravity-Forms-Leermeldung (Checkbox 10) in Statistik und Bericht berücksichtigen.
- Editable-Installationen mit `pip install -e .` werden direkt am Quellort aktualisiert (Prüfsumme, Sicherung, Selbsttest, Rollback); klassische Wheel-Installationen bleiben verwaltet.
- `mao config init` legt private `db.config`, `gf.config` und `gf-import.yaml` ohne `.example` an (ohne Überschreiben); GF-SQL nutzt dieselben geprüften `{{table.*}}`-Kennungen aus `tables.ini` wie die Pipeline.

Änderungen am Programm werden hier direkt im **Haupt-Git-Repository** dokumentiert. Die Seiten im Ordner `wiki/` bilden die ausführliche Bedienungsanleitung.

Das Projekt befindet sich vor Version 2.0 in einer **nicht kompatibilitätsgebundenen Entwicklungsphase**. Ab Version 2.0 sollen Breaking Changes explizit versioniert und für Updates entsprechend behandelt werden.

## [0.6.0.dev0] – in Entwicklung

### Korrekturen vor dem ersten Release
- Archivsperre gilt auch bei `--workspace`: Ein Ordner innerhalb eines registrierten Workspaces übernimmt dessen Jahrgang, sodass `run` und `plan` archivierte Jahrgänge nicht mehr mit aktuellen SQL-Daten überschreiben.
- `mao validate --online` und `mao import-gf` zeigen, worauf die Fremdschlüssel von `kurs_id` und `lehrkraft_id` verweisen; das optionale `foreign_keys` in `gf-import.yaml` erzwingt das erwartete Ziel vor jedem Schreibzugriff.
- GF-Import meldet Abweichungen zwischen GF-Klasse und Stammdaten sowie nicht zuordenbare Geschlechtsangaben als Warnung (Log und `warnings` im Prüfbericht), ohne das bisherige Ergebnis zu ändern.
- GF-Abruf bricht ab, wenn die Zahl der Einträge nicht zu `total_count` passt oder ein Eintrag doppelt geliefert wird.
- Dokumentation zur `kurs_id`-Zuordnung an den tatsächlichen Stand angeglichen; reale Tabellennamen aus Vorlagen, Kommentaren und Wiki entfernt.
- CI prüft zusätzlich mit ruff und mypy.

### Validierung und Importberichte
- `mao validate --online` ergänzt die bestehende Offline-Prüfung um lesende MySQL-Schema-, SQL-Quellen-, ID- und Gravity-Forms-API-Checks; kein weiterer Hauptbefehl.
- `mao import-gf --dry-run --report <pfad>.json|.csv` erzeugt private Prüfberichte mit Klassen-, Pflichtteilnahme- und Zuordnungsstatistik sowie Abbruchdiagnosen. Ohne `--report` weiterhin keine Berichtsdateien; `--apply` unverändert transaktional und ohne Bericht.
- Doppelte/unbekannte Matrikelnummern aus GF werden vorab gemeinsam als Fehler ausgewiesen; keine INSERTs bei Konflikten.


### Updateverwaltung
- ZIP-/Quellinstallationen werden gegen das SHA-256-geprüfte Develop-Wheel inhaltlich abgeglichen; identische Paketdateien werden nicht fälschlich als Update gemeldet.
- Isolierte Wheel-Selbstprüfung mit `python -I` verhindert über `PYTHONPATH` importierte Quell-ZIPs; verständlichere Diagnose bei fehlgeschlagener Prüfung und eigener Smoke-Test des CI-Wheels.
- Develop-Updates für öffentliches Repository: anonyme GitHub-Abfragen, öffentliches rollierendes Develop-Release, commitgebundene Wheel-/SHA-256-Prüfung (keine lokale GitHub-Anmeldung).
- Develop-Kanal nutzt direkt den GitHub-Branch-HEAD und ein erfolgreich getestetes Actions-Artefakt statt `git rev-parse` und Git-Checkout.
- `mao update check|download|install --channel develop` mit Commit-Versionierung der Wheels, SHA-256-Prüfung und verwalteter Installation samt Rollback.
- Der öffentliche Download erfordert keine GitHub-Anmeldung; CI-Artefakte bleiben intern für die Workflow-Auswertung erhalten.


### Gravity Forms (Erstbefüllung)
- `kurs_id` erhält in Stufe 11/12 die unveränderte `lerngruppe_schuljahr_id` aus GF-Feld 5, nachdem die Kombination mit der Lehrkraft geprüft wurde; in Stufe 5–10 bleibt `kurs_id` `NULL`.
- Separater `mao import-gf` mit zentraler privater `gf.config`/`gf-import.yaml` und explizitem `--apply`.
- OAuth1-API, Checkboxmeldungen, Pflichtteilnahmen, Teilnehmernummern und FK-ID-Auflösung.
- GF-Feld 3 direkt als Lehrkraft-ID; Feld 5 als Lerngruppe-Schuljahr-ID mit geprüfter Beziehung zur Kurs-FK-ID.
- Feste Pflichtklassen 5-1, 5-2, 6-1, 6-2, 7-3; 7-4 nur falls im aktuellen Schuljahr vorhanden; vollständiger Stammdatenabgleich.
- Nur Import in leere Organisationstabelle; niemals Archivierung oder TRUNCATE.

### Zentrale Konfigurationsverwaltung und Jahrgänge
- `MAO_CONFIG` und `--config-dir` statt Konfiguration pro Workspace.
- `settings.yaml` mit explizitem, eindeutigem Arbeitsverzeichnis je Schuljahr und Standardprofil.
- Neue Befehle `mao config init|show`, `mao workspace add|list|select|wrapper`.
- Windows- und Shell-Wrapper im registrierten Workspace, ohne Zugangsdaten.
- Sicherheitssperre: aktuelle SQL-Exporte werden nicht in archivierte registrierte Workspaces geschrieben.
- Neue read-only Vorprüfung `mao init-jahrgang` auf Grundlage der vorhandenen Jahrgangs-/Archivstruktur.
- Öffentliche `tables.ini` weiterhin ausschließlich mit `DEMO_`-Bezeichnungen; private SQL-Strukturdateien werden nicht versioniert.

## [Unreleased]

### Vorbereitung auf die öffentliche Veröffentlichung
- Ausgelieferte `tables.ini` enthält nur noch neutrale `DEMO_`-Tabellen und Views.
- Die Jahrgangsermittlung verwendet die lokalen Bezeichnungen `jahrgaenge_table` und `schuljahre_table` statt fest eingetragener Tabellennamen.
- `mao plan` und `mao run` verweigern einen Datenbanklauf mit Demo-Namen; `mao validate` weist darauf hin.
- Namen aus YAML-Beispielen, Ausgabepfaden, Dokumentation und Test-Referenzen entfernt.
- Die `.gitignore` schützt lokale Tabellenzuordnungen; vor einer Veröffentlichung muss auch die bestehende Git-Historie bereinigt werden.

## [0.5.0.dev0] – 2026-10-23

### Updateverwaltung
- GitHub-Updatekanäle für Stable, RC und Develop; private Assets können authentifiziert abgerufen werden.
- Getrennte virtuelle Python-Umgebungen pro Release; kontrollierter Wechsel und Offline-Rollback.
- Tests unter Linux und Windows für Python 3.10 und 3.13.

## [0.4.0.dev0] – 2026-10-23

### Hinzugefügt
- Git-Flow-Repositoryvorlage mit CI auf `main`, `develop` und Feature-/Release-/Hotfix-Zweigen.
- GitHub-Release-Workflow mit versioniertem Python-Wheel und SHA-256-Prüfdatei.
- Manueller Updatebefehl `mao update check|download|install` mit Kanälen `stable`, `rc` und `develop`.
- Absicherung von Develop-Updates durch sauberen Git-Checkout, Fast-Forward und erfolgreichen GitHub-CI-Lauf.
- Zusätzliche Wiki-Dokumentation zu Updateprozess, Git Flow, Branchschutz und Releases.
- Offline-Tests für Updatekanäle, Prüfsummen, Bestätigung und Versionsverwaltung.

### Hinweise
- Keine externe GitHub-Repositoryanlage oder Veröffentlichung; dafür sind Repository und Zugriffsrechte notwendig.
- Noch kein automatisches Rollback, keine Datenbankmigration, keine Jahrgangsinitialisierung.
- Bis Version 2.0 bleiben inkompatible Entwicklungsänderungen ausdrücklich möglich.

## [0.3.0.dev0] – 2026-10-23

### Geändert
- Sauberer Schnitt: `mao-cli.py` und der bisherige flache Optionssatz entfallen.
- Einheitliche Kommandozeile: `mao run`, `mao plan`, `mao latex`, `mao validate`, `mao profiles`.
- Installation als Python-Paket mit `pyproject.toml` und Konsolenbefehl `mao`.
- Standardkonfigurationen liegen im Paket unter `mao_cli/defaults/`; lokale Overrides unter `<workspace>/config/`.
- Eindeutige IDs für LaTeX-Jobs und Exporte; alte Namens-Fallbacks entfallen.
- Alle von der Pipeline verwendeten Datenbank-Views sind in `tables.ini` konfigurierbar.
- Exportmanifest: nur noch Schema 2 mit Besitzerkennungen; keine automatische Manifestmigration.
- `CHANGELOG.md` verbleibt im Hauptrepository, Wiki-Quellseiten in `wiki/`, Veröffentlichung über `scripts/publish_wiki.py`.

### Beibehalten
- Bestehende fachliche SQL-Abfragen (nach Auflösung der Tabellenplatzhalter), CSV-/TeX-Inhalte und LaTeX-Jobkonfigurationen.
- Manifestgesteuerte Bereinigung, sichere Exportveröffentlichung, Offline-LaTeX und bedarfsgesteuerte Datenbankabfragen.

### Nicht enthalten
- Automatische Einrichtung eines Jahrgangs, Datenbankmigrationen, Git-Selbstupdates, GUI.

## Vorherige Entwicklungsstände

Schritt 1 und der frühere Schritt 2 waren interne Arbeitsstände ohne zugesicherte CLI- oder Manifestkompatibilität. Es gibt dafür **keine** automatischen Upgrades auf `0.3.0.dev0`.
