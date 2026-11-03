# Changelog
- Konfigurationsordner neben Editable-Quelle automatisch erkennen; `students_table` statt `students_view`; Gravity-Forms-Leermeldung (Checkbox 10) in Statistik und Bericht berücksichtigen.
- Editable-Installationen mit `pip install -e .` werden direkt am Quellort aktualisiert (Prüfsumme, Sicherung, Selbsttest, Rollback); klassische Wheel-Installationen bleiben verwaltet.
- `mao config init` legt private `db.config`, `gf.config` und `gf-import.yaml` ohne `.example` an (ohne Überschreiben); GF-SQL nutzt dieselben geprüften `{{table.*}}`-Kennungen aus `tables.ini` wie die Pipeline.

Änderungen am Programm werden hier direkt im **Haupt-Git-Repository** dokumentiert. Die Seiten im Ordner `wiki/` bilden die ausführliche Bedienungsanleitung.

Das Projekt befindet sich vor Version 2.0 in einer **nicht kompatibilitätsgebundenen Entwicklungsphase**. Ab Version 2.0 sollen Breaking Changes explizit versioniert und für Updates entsprechend behandelt werden.

## [0.6.0.dev0] – in Entwicklung

### Raumzuteilung: Übersicht je Stufe und Zuteilung von Hand

- `mao raumzuteilung` zeigt am Ende zusätzlich „Räume je Stufe“ (Raum und Anzahl je Stufe); der Bericht enthält `raeume_je_stufe`. Bleiben Teilnehmende ohne Raum, nennt das Log den Befehl zum Nachtragen.
- Neuer Befehl `mao raum-setzen --raum CODE --matrikel NUMMER [...]`: weist einzelnen Teilnehmenden von Hand einen Raum zu. Dry-Run ist Standard, `--apply` schreibt (Transaktion, Sperre, Zählabgleich). Er prüft Raum (vorhanden, aktiv), Matrikelnummern und vorhandene Zuordnungen (`--ueberschreiben`); die Kapazität wird bei Handzuteilung nur gemeldet, nicht erzwungen (`--trotzdem` ohne Wirkung). Von Hand gesetzte Räume bleiben bei der nächsten Raumzuteilung erhalten.
- `mao raum-setzen --interaktiv`: Schritt für Schritt durch alle Teilnehmenden ohne Raum, mit Raumvorschlägen (Stufe schon im Raum, wenig Mischung, schon genutzte Räume zuerst), Überspringen (Enter), `u` = zurück, `r` = Raumübersicht, `q` = Ende. Ohne `--apply` nur Zusammenfassung; mit `--apply` nach Rückfrage `[j/N]` geprüftes Schreiben je Raum. Braucht ein Terminal.
  Alle aktiven Räume stehen zur Wahl; volle erscheinen am Ende der Vorschläge mit „VOLL“, Überbelegung ist ausdrücklich möglich.

### Raumzuteilung: Sammelräume

- Neu: `rooms.sammel_max_spanne` (mit `rein_zuerst`). Gemischte Sammelräume enthalten nur Stufen innerhalb dieser Spanne (1 = nur Nachbarstufen, 2 = z. B. 8 bis 10). Es bleiben so viele reine Räume wie möglich, die Raumzahl bleibt gleich; lässt sich die Spanne nicht einhalten, gibt es eine Warnung.

- Neuer Standard `sammel_stil: rein_zuerst`: Jeder Sammelraum, den eine Stufe allein füllen kann, wird mit genau dieser Stufe gefüllt; nur die Reste aller Stufen kommen gemeinsam in die übrigen Räume (wenn möglich in einen einzigen Sammelraum). `sammel_stil: nachbarn` behält das bisherige Verhalten (Stufenblöcke nebeneinander, gemischt nur mit Nachbarstufen).

- `grade_rooms` darf für einzelne Stufen leer sein (`"8": []`), wenn `rooms.sammel` gesetzt ist; die Kapazitätswarnung vergleicht dann den Gesamtrest mit den Sammelräumen statt je Stufe.

- Neu: `rooms.sammel` in `raumregeln.yaml`. Alle, die nicht in ihre festen Räume passen, kommen mit möglichst wenigen Räumen und möglichst wenig Stufenmischung in die Sammelräume (Stufenblöcke nebeneinander, volle Räume rein, Reihenfolge der Stufen optimiert). Ohne Eintrag bleibt das Verhalten unverändert.
- `mao aufsichten`: die Fachaufsicht ist die Wettkampfleitung und gehört keinem Raum an (Hinweis, wenn keine eingetragen ist).

### Aufsichtenprüfung (`mao aufsichten`)

- Neuer, nur lesender Befehl: prüft nach der Raumzuteilung, ob jeder belegte Raum (auch die der Externen) die vorgesehene Zahl Lehrkraft-Aufsichten hat, und meldet Plätze ohne Lehrkraft, Doppelbelegungen, nicht mehr aktive Lehrkräfte und Einträge in leeren Räumen. Regeln im Abschnitt `aufsichten` der `raumregeln.yaml`; neuer tables.ini-Schlüssel `aufsichten_table`.

### Raumregeln je Jahrgang

- `raumregeln.yaml` gilt jetzt je Jahrgang: Die Datei im Jahrgangsordner hat Vorrang vor der zentralen Vorlage im Konfigurationsordner (mit Warnung, wenn nur die Vorlage vorhanden ist). `mao raumzuteilung --regeln-anlegen` legt die Jahrgangsdatei aus der Vorlage an, `mao jahrgang new` übernimmt die Regeln des letzten Jahrgangs. Gilt auch für `--extern`; der Bericht nennt die verwendete Datei.

### Externe Teilnehmende (`mao import-extern`)

- `mao raumzuteilung --extern` teilt Externe den Räumen aus `raumregeln.yaml` (Abschnitt `extern`) zu: Raum für Raum nach Teilnehmernummer, Dry-Run/`--apply`/`--plan-id`/`--ueberschreiben` wie sonst. Externe-Räume dürfen nicht mit Räumen des Hauptwettbewerbs übereinstimmen.

- Neuer Befehl: übernimmt die Anmeldungen externer Grundschulkinder aus dem Gravity-Forms-Formular in die Tabellen `extern_personen_table` und `extern_teilnahmen_table` (tables.ini). Standard ist `--dry-run`; `--apply` schreibt in einer Transaktion mit Sperre und Zählabgleich. Wiederholbar über die GF-Eintrags-ID; Doppelanmeldungen werden übersprungen, mögliche wiederkehrende Kinder nur gemeldet, nie automatisch zusammengeführt.
- Neue Konfigurationsdatei `gf-extern.yaml` (Formular-ID und Feldnummern), von `mao config init` angelegt. Übernommen werden nur Name des Kindes, Klassenstufe, Schule und (optional, Feld `geschlecht`, Werte m/w/d) das Geschlecht.

### Update-Kanal develop

- `mao update --channel develop` vergleicht bei Installationen von `@develop` jetzt den installierten Commit mit dem aktuellen Stand des Branches: Bei gleichem Stand heißt es „Bereits aktuell“ und `--run` installiert nichts, bei neuem Stand wird er genannt. `--force` installiert trotzdem neu. Ist der Vergleich nicht möglich (offline), wird wie bisher neu installiert.

### Zertifikatsfehler bei Abrufen (Windows, Schulnetz)

- `schulen-sync` und `mao update` prüfen HTTPS-Zertifikate jetzt über das Betriebssystem (Paket `truststore`). Das behebt „unable to get local issuer certificate", wenn Server ihre Zwischenzertifikate nicht mitliefern oder das Netz Zertifikate austauscht. Die Prüfung bleibt immer aktiv.
- Neue Ausweichmöglichkeiten: Umgebungsvariable `MAO_CA_BUNDLE=<Datei.pem>` und `mao schulen-sync --file DATEI.json` (im Browser gespeichertes GeoJSON). Die Fehlermeldung nennt beides.

### Update: `mao update --run` und Versionshinweis

- `mao update --run` führt `uv tool install --force --reinstall …` selbst aus (nur bei `uv tool`-Installation, nicht bei Quellinstallationen). Unter Linux/macOS läuft uv direkt; unter Windows startet mao ein neues PowerShell-Fenster, wartet dort, bis sich `mao.exe` beendet hat, und installiert dann. Ohne `--run` nennt der Befehl wie bisher nur die Befehlszeile.
- Nach jedem Befehl (außer `update`) weist mao in einem Terminal in einer Zeile auf eine neuere Version hin: bei Tag-Installationen auf neuere Release-Versionen (Kanal je nach Installation stable/rc), bei Branch-Installationen (`@develop`) auf einen neueren Commit. Höchstens eine Abfrage je 24 Stunden, Ergebnis rechnerlokal gespeichert, Fehler und Offline-Betrieb bleiben still. Abschalten mit der Umgebungsvariable `MAO_NO_UPDATE_CHECK=1`.

### Schulabgleich (`mao schulen-sync`)

- Neuer Befehl: lädt das Schulverzeichnis Berlin (GeoJSON, öffentlich) und gleicht es mit der Tabelle aus `schools_table` (tables.ini) ab. Standard ist `--dry-run`; `--apply` legt neue Schulen an, aktualisiert geänderte und setzt weggefallene auf `aktiv = 0`. Es wird nie gelöscht, und manuell gepflegte Spalten bleiben unberührt.
- Schutz: nur https, Vollständigkeitsprüfung gegen die gemeldete Gesamtzahl, Abbruch bei weniger als 100 Schulen oder weniger als der Hälfte der bisher aktiven, Transaktion mit Sperre und Zählabgleich. `--report DATEI.json` speichert den Bericht.

### Raumzuteilung: Auffüllen, Top-15, Plan-ID

- `fill: full` (Standard) füllt die Zielräume Raum für Raum bis zur Kapazität auf, um Aufsichten zu sparen; `fill: even` verteilt gleichmäßig. Klassen werden dabei weiterhin gemischt.
- Top-15 sind die besten Punktzahlen unter den aktuell Teilnehmenden (nicht mehr: Beste des Vorjahres, soweit wieder dabei).
- Vorab-Kapazitätsübersicht je Stufe im Log und im Bericht, Warnung bei Engpass.
- Inaktive Räume (`aktiv = 0`) in `raumregeln.yaml` blockieren die Zuteilung.
- Der Dry-Run nennt eine Plan-ID; `--apply --plan-id ID` schreibt nur, wenn sich die Verteilung seitdem nicht geändert hat.

### Raumzuteilung (`mao raumzuteilung`)

- Neuer Befehl: teilt `raum_id` in der Organisationstabelle nach `raumregeln.yaml` zu (Top-15-Räume, Stufen 5/6 gemeinsam, klassenbalanciert, Reserveraum). Standard ist `--dry-run`; geschrieben wird nur mit `--apply` (Transaktion, Sperre, Zählabgleich).
- Vorhandene Zuordnungen bleiben erhalten und zählen zur Raumkapazität; `--ueberschreiben` verteilt alles neu. `--seed N` macht die Verteilung reproduzierbar (der Seed steht im Log), `--report` schreibt einen Bericht ohne Namen.
- Die Top-15-Punkte stammen aus der neuen tables.ini-Angabe `points_last_year_view` (Spalten Matrikelnummer, Klasse, Punkte); Stufe 11/12 zählen zusammen. Bei Punktgleichstand an der Grenze entscheidet die kleinere Matrikelnummer (mit Hinweis).
- `mao config init` legt `raumregeln.yaml` aus einer Vorlage an.

### Export-Option `fill_empty`

- Leere oder NULL-Werte einzelner Spalten werden durch einen Platzhalter ersetzt (Warnung im Log). Der Export `klassenleitungen` setzt `N.N.` für fehlende `kl`/`stellvkl`, damit die Klassenleitungslisten nicht mit einem LaTeX-Fehler abbrechen.

### GF-Import: schlanker Insert

- Der Import schreibt nur noch `tnr`, `matrikelnummer`, `lehrkraft_id` und `kurs_id`. Vorname, Nachname, Geschlecht, Klasse und Stufe setzt der Trigger auf MaO_Orga_2 aus den Stammdaten (Voraussetzung: Trigger setzt auch `stufe`).

### Weniger Datasets

- Die Datasets `orga_preis`, `orga_delegierte` und `klassenleitungslisten` entfallen. Die Preisgeld-Exporte filtern das Dataset `orga` (`preis` nicht leer); `preisgeld` wird dort als `Int64` ausgegeben.

### LaTeX: leere Dokumente

- Ein LaTeX-Job, der keine Seite erzeugt (z. B. weil alle Listen leer sind), wird mit Warnung übersprungen statt als Fehler abzubrechen.

### Ohne Ordner `Listen`

- Exporte liegen in `<Jahrgang>/db/`, PDFs in `<Jahrgang>/<Dokument>/` (Standardpfade der `pipeline.yaml`, `latex.db_dir` jetzt `db`). Eine bestehende `pipeline.yaml` mit `Listen/…`-Pfaden braucht entweder dieselbe Umstellung oder `latex: db_dir: "Listen/db"`.
- Erzeugte Makrodateien binden CSV-Dateien über `\MaOdb` ein statt über `../db/…`; sie hängen nicht mehr von der Ordnertiefe ab.

### LaTeX: \MaOdb relativ

- `\MaOdb` zeigt relativ zum Job-Ordner auf die Exporte (z. B. `../db/`). Absolute Pfade mit Leerzeichen (etwa im Nextcloud-Ordner) ließen `\DTLloaddb` im Vorspann scheitern.

### GF-Import: Reihenfolge

- `import-gf` sortiert Teilnehmende jetzt Sek I zuerst (5-1, 5-2, 6-1 … 10-x, danach Nachname/Vorname), die Oberstufe (11, 12) zuletzt in der Reihenfolge der Gravity-Forms-Einträge; `tnr` folgt dieser Reihenfolge.

### Cloud-Synchronisation (Schritt 6, Teil 5)
- MaO warnt bei jedem Aufruf vor Konfliktkopien (Nextcloud/Dropbox) in `config/` und `tex/`. Wiki: Nextcloud-Hinweise (Ausschlussliste für LaTeX-Hilfsdateien). Bewusst keine Sperrdatei.

### Installation mit uv (Schritt 6, Teil 4)
- MaO wird mit `uv tool install git+https://github.com/hhgym/MaO@<Ref>` installiert und aktualisiert. `mao update [--channel stable|rc|develop] [--version X]` erkennt die Installationsart (uv-Tool, Editable, pip), ermittelt bei Bedarf den neuesten Tag und nennt den passenden Befehl, installiert aber nichts selbst. Die alten Unterbefehle `check|download|install|status|rollback` werden mit Hinweis akzeptiert.
- **Entfernt:** verwaltete Umgebungen und Starter, Editable-Updater, ZIP-Abgleich (`update_install.py`, `editable_update.py`, alter `updater.py` samt Tests) sowie der CI-Job `develop-build` mit dem Release `develop-build`. Release-Wheels bleiben als Download am Release.
- `ruff` und `mypy` stehen jetzt in `[project.optional-dependencies].dev`, `tomli` nur noch dort; Laufzeitabhängigkeit `tomli` entfällt. Umstieg: Wiki „Updates“.

### LaTeX-Vorlagen im MaO-Ordner (Schritt 6, Teil 3)
- Job-Feld `tex: "@tex/<Ordner>/<Datei>.tex"` liest die Vorlage aus `<MaO>/tex`, während LaTeX im Job-Ordner läuft und dort das PDF erzeugt. `TEXINPUTS` enthält den Vorlagenordner, `tex/base` und `tex/` (Vorlage braucht nur `\input{template.tex}` und Dateinamen für Bilder). Neue Makros `\MaOdb`, `\MaOroot`, `\MaOtex`, `\MaOjahrgang` (mit `\detokenize` gesetzt, Leerzeichen im Pfad sind erlaubt). Neue Seite im Wiki „LaTeX-Vorlagen“; mitgelieferte `pipeline.yaml` nutzt `@tex/`. Ohne `@tex/` bleibt alles wie bisher.

### Zentraler MaO-Ordner (Schritt 6, Teil 1)
- `mao config init <MaO-Ordner>` legt `config/` und `tex/` an; `mao config use <MaO-Ordner>` merkt den Ordner pro Rechner. Der Konfigurationsordner wird außerdem vom aktuellen Ordner aufwärts gefunden; `--config-dir`/`MAO_CONFIG` dürfen den MaO-Ordner selbst nennen. `mao config show` nennt die Quelle.
- Jahrgangsordner innerhalb des MaO-Ordners stehen in `settings.yaml` relativ und gelten damit auf jedem Rechner; in einem Jahrgangsordner gilt dieser Jahrgang automatisch. `mao-hier.*`-Wrapper entstehen nur noch mit `workspace add --wrapper`.
- Jahrgangsordner `<MaO>/Jahrgang/<MaO-Nr>_<Schuljahr>` werden automatisch erkannt (keine Registrierung nötig, nicht in `settings.yaml` gespeichert); neuer Befehl `mao jahrgang list|new|use`. `mao jahrgang new` ohne Angaben liest das aktive Schuljahr und die MaO-Nummer lesend aus der Datenbank, legt den Ordner an und setzt ihn als aktuell; `workspace` bleibt für Sonderfälle außerhalb des MaO-Ordners.

### Korrekturen vor dem ersten Release
- LaTeX-Jobs: neues `pdf_name` lässt LaTeX das PDF direkt unter dem Zielnamen (z. B. `MaO_{mao}_Korrekturlisten`) im Ordner des Jobs erzeugen, statt es zu kopieren; die mitgelieferte `pipeline.yaml` nutzt es. `copy_pdf` bleibt unterstützt. Hilfsdateien (`.aux`, `.log` …) werden nur auf Wunsch gelöscht (`--cleanup-latex` bei `run`/`latex` oder `cleanup_aux: true`), nie nach einem Fehler; alte PDFs werden nie automatisch entfernt.
- LaTeX-Makrolisten: Werte, die in `entry_template`/`label_template` eingesetzt werden, werden für TeX maskiert (`% & # $ { } ^ ~ \`); Komma, Schrägstrich oder Zeilenumbruch in einem verwendeten Wert brechen mit klarer Meldung ab, statt die Liste still zu zerlegen. Fehlende Werte (NaN) erscheinen als leer statt als `nan`.
- Split-Exporte: Zeilen ohne Schlüssel (NULL oder leer) werden im Protokoll gezählt statt still ausgelassen; `on_missing_key: error` bricht stattdessen ab.
- `latex.jobs` darf in der Konfiguration fehlen (bisher `KeyError` in der Prüfung).
- Mitgelieferte `pipeline.yaml` an die ID-basierte Organisationstabelle angepasst: Die Datasets `orga`, `orga_preis` und `klassenleitungslisten` lesen aus einer View (`orga_view` in `tables.ini`), die `lehrkraft_id`, `kurs_id`, `raum_id` und `preis_id` zusätzlich als Klartext-Spalten `lehrkraft` (Kürzel), `kurs`, `raum` und `preis` bereitstellt. Filter, Splits und Makros bleiben unverändert; `orga_preis` filtert auf `preis_id`.
- `mao validate --online` und `import-gf --apply` brachen mit „Unread result found“ ab, weil einzeilige Abfrageergebnisse nicht vollständig gelesen wurden; die Testcursor verhalten sich jetzt wie der echte MySQL-Cursor.
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
