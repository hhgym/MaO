# Changelog

Änderungen am Programm werden hier direkt im **Haupt-Git-Repository** dokumentiert. Die Seiten im Ordner `wiki/` bilden die ausführliche Bedienungsanleitung.

Das Projekt befindet sich vor Version 2.0 in einer **nicht kompatibilitätsgebundenen Entwicklungsphase**. Ab Version 2.0 sollen Breaking Changes explizit versioniert und für Updates entsprechend behandelt werden.

## [0.6.0.dev0] – in Entwicklung

### Updateverwaltung
- Develop-Updates für öffentliches Repository: anonyme GitHub-Abfragen, öffentliches rollierendes Develop-Release, commitgebundene Wheel-/SHA-256-Prüfung (keine lokale GitHub-Anmeldung).
- Develop-Kanal nutzt direkt den GitHub-Branch-HEAD und ein erfolgreich getestetes Actions-Artefakt statt `git rev-parse` und Git-Checkout.
- `mao update check|download|install --channel develop` mit Commit-Versionierung der Wheels, SHA-256-Prüfung und verwalteter Installation samt Rollback.
- Der öffentliche Download erfordert keine GitHub-Anmeldung; CI-Artefakte bleiben intern für die Workflow-Auswertung erhalten.


### Gravity Forms (Erstbefüllung)
- Einsatz-ID per `(lerngruppe_schuljahr_id, lehrkraft_id)` aus der erweiterten Lerngruppen-View auflösen; `kurs_id` und FK unverändert.
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
