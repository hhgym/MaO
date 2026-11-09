# Changelog
- `mao einsatz`: Schüler der Stufe 11/12 mit `aktiv = 0` (z. B. beurlaubt) werden nicht mehr verplant; die Zahl steht im Protokoll („mit aktiv = 0 … nicht verplant“). Bereits gespeicherte Einsätze solcher Personen erscheinen unter „nicht im Plan“.
- Raumübersicht und Korrekturlisten: Die Vorlagen `Raumuebersicht/Raumuebersicht.tex` und `Korrekturlisten/Korrekturlisten.tex` sind neu geschrieben (vorher brachen sie mit „export ?“ ab, weil die alten Dateien `ForeachListen/Tabellen-Raeume.tex` und `ForeachListen/Tabellen-Korrekturliste.tex` nicht mehr entstehen). Neuer Export `teilnehmende_gesamt` (`db/Teilnehmende/Teilnehmendeliste.csv`: tnr, nachname, vorname, geschlecht, klasse, stufe, raum; nach Stufe, Klasse, Vorname, Nachname) für die Raumübersicht; Teilnehmende ohne Raum erscheinen mit „--“. Neue Abfrage und neuer Export `korrekturliste` (je Klassenstufe `db/Korrekturliste/Korrekturliste-Stufe-<Stufe>.csv`, 11 und 12 gemeinsam als `11-12`, nach Vorname, Nachname; Abgemeldete mit `anwesenheit = 0` fehlen; Makrodatei `db/Include/Tabellen-Korrekturliste.tex` mit `\KorrekturListe` als `Stufengruppe/Stufencode/Datenbank`); die Aufgabennummer besteht aus `\MaO`, dem zweistelligen Stufencode (12 für 11-12), `2` und der Aufgabe (z. B. 660521). Das Profil `vorbereitung` enthält die neuen Exporte (zusätzlich `teilnehmende_raum`) und den Job `korrekturlisten`. In der eigenen `pipeline.yaml` Abfrage `korrekturliste` und die Exporte `teilnehmende_gesamt` und `korrekturliste` ergänzen.
- Kopierliste mit Externen: Neue Abfrage und neuer Export `kopierliste_externe` (Klassenstufe 3 bis 6, `db/Kopierliste/Kopierliste-Externe.csv`, je Raum `Kopierliste-Externe-Raum-<Raum>.csv`, Makrodatei `db/Include/Tabellen-Kopierliste-Externe.tex` mit `\Kopierexterneliste`) aus der neuen Datenbank-View `MaO_Orga_2_view_Kopierliste_externe` (neue Kennung `kopierliste_externe_view` in `tables.ini`; ältere `tables.ini` bekommen den Platzhalter automatisch, den echten Namen selbst eintragen). Die Vorlage `Kopierliste/Kopierliste.tex` setzt unter die Tabelle der Haupträume eine zweite Tabelle der Externen (nur wenn es welche gibt) und je externem Raum eine Seite mit „Raum: XXX (externe)“ in der Überschrift. Die Profile `vorbereitung` und `turniertag` enthalten den neuen Export. In der eigenen `pipeline.yaml` Abfrage und Export ergänzen.
- Raum-Anwesenheitslisten: Neuer Export `teilnehmende_raum` (je Raum eine Liste nach Vorname, Nachname, `db/Teilnehmende/Raum/Teilnehmende-Raum-<Raum>.csv`, Makrodatei `db/Include/Tabellen-Teilnehmende-Raum.tex` mit `\TeilnehmendeRaumListe`). Die Vorlage `Raum-Anwesenheitslisten.tex` nutzt diesen Export statt der alten Dateien `ForeachListen/Tabellen-Raeume.tex` und `Aufsichten/Aufsichten.csv`, zeigt die Aufsicht aus `Aufsichten-Raeume.csv` (Rolle Aufsicht) und die Oberstufen-Aufsicht aus `Einsatz-Oberstufe-Raum.csv` und erkennt gemischte Stufen aus den Teilnehmenden. In der eigenen `pipeline.yaml` den Export ergänzen.
- Kopierliste: Die Abfrage `kopierliste` liest die Datenbank-View `kopierliste_view` (Zahlen als Int64) einschließlich der Zeile GESAMT, die zuletzt steht. Die Vorlage `Kopierliste/Kopierliste.tex` übernimmt die Summen aus dieser Zeile (kein Aufsummieren in TeX), setzt sie als letzte Zeile der Gesamttabelle und überspringt sie bei den Raumseiten; sie nutzt `\Kopierraumliste` aus `Include/Tabellen-Kopierliste.tex`. In der eigenen `pipeline.yaml` die Abfrage ersetzen.
- Oberstufe: `Einsatz-Oberstufe.tex` ist in vier Dokumente aufgeteilt, die gemeinsame Bausteine (`tex/base/Oberstufe-Bausteine.tex`, `Oberstufe-Legende.tex`) nutzen: `Oberstufe-Anwesenheitslisten`, `Oberstufe-Einteilungslisten`, `Oberstufe-Uebersicht` und `Aufsichten-Schueler`. Neue Jobs `oberstufe_anwesenheitslisten`, `oberstufe_einteilungslisten`, `oberstufe_uebersicht` (der alte Job `einsatz_oberstufe` entfällt), neuer Export `einsatz_oberstufe_klasse` (nach Klasse sortiert). Die Abfrage `einsatz_oberstufe` liefert zusätzlich die Spalte `entitaet`: Bemerkung bzw. bei Teilnahme der Raum der Teilnahme. In der eigenen `pipeline.yaml` Abfrage, Exporte und Jobs übernehmen.
- Pipeline: Neuer Export `einsatz_oberstufe_raum` (`db/Einsatz-Oberstufe/Einsatz-Oberstufe-Raum.csv`, nach Raum, Nachname, Vorname sortiert). Die Vorlage `Aufsichten-Schueler` nutzt ihn statt `\dtlsort`, das mit datatool 3 (TeX Live 2026) abbricht. In der eigenen `pipeline.yaml` den Export ergänzen.
- Neuer LaTeX-Job `hinweise_aufsichten` (Hinweise für aufsichtsführende Lehrer:innen/Schüler:innen als eigenes Blatt, `tex/Hinweise-Aufsichten/`). Der Text liegt in `tex/base/Hinweise-Aufsichten-Inhalt.tex` und wird von diesem Job und von `Raum-Anwesenheitslisten.tex` (Rückseite) per `\input` eingebunden. Vorlagen ohne Bezug zu Exportdaten brauchen keine Exporte mehr (kein Hinweis „nicht erkennbar“, es entstehen keine CSV-Dateien); `exports: []` im Job legt das ausdrücklich fest. In der eigenen `pipeline.yaml` den Job `hinweise_aufsichten` ergänzen.
- Konfigurationsordner neben Editable-Quelle automatisch erkennen; `students_table` statt `students_view`; Gravity-Forms-Leermeldung (Checkbox 10) in Statistik und Bericht berücksichtigen.
- Pipeline: Der Export `teilnehmende` (Listen nach Stufe, Klasse, Kurs, Lehrkraft) filtert nicht mehr auf `anwesenheit = 1`. Vor dem Wettbewerb ist noch niemand anwesend, die Makrodateien (`Tabellen-Teilnehmende-*.tex`) blieben leer und die Teilnehmendenlisten ergaben kein PDF. In der eigenen `pipeline.yaml` den `filter:`-Block dieses Exports entfernen.
- `mao einsatz`: Wünsche `Aufsicht` füllen zuerst die Reserveplätze, dann die Raumaufsichten; so wird die Reserve besetzt, auch wenn es weniger Wünsche als Aufsichtsplätze gibt. Wer keinen Wunsch abgab, kommt nie in die Reserve.
- **Änderung:** `mao run`, `mao latex` und `mao plan` ohne Angaben starten ein Pfeiltasten-Menü, in dem nichts vorausgewählt ist (LaTeX-Jobs und Exporte). Es werden nur die Exporte und SQL-Abfragen ausgeführt, die zur Auswahl gehören (Zuordnung aus der Vorlage erkannt oder per `exports: [id, …]` im Job). `--pipeline` führt wie bisher die in `pipeline.yaml` aktivierten Jobs und Exporte aus (für Skripte/geplante Aufgaben), `--profile` und `--no-latex` ebenso. `mao run --job NAME` und `mao plan --job NAME` erzeugen nur noch die nötigen Exporte (`--alle-exporte` stellt das bisherige Verhalten wieder her).
- Pipeline: In `Aufsichten-Lehrkraefte.csv` steht bei der Rolle `Reserve` wie bei Fachaufsicht und Abiturerstellung die Rolle vor der Bemerkung (Leerzeichen, kein Komma).
- Sammelräume (`rooms.sammel`) werden wie die Top-15-Räume gekennzeichnet (`S`), in `mao aufsichten --interaktiv` und in `mao raumzuteilung --statistik`; die Statistik zeigt jetzt auch `T/<Stufe>` hinter dem Raum (Regeln aus dem Jahrgangsordner, sonst Vorlage).
- `mao aufsichten --interaktiv`: Die Tabellenansicht nutzt die Fensterhöhe besser (Kopf- und Fußzeilen kompakter, so passt die Zeile Abiturerstellung ohne Scrollen) und fordert bei zu kleinem Fenster per Terminalsequenz mehr Zeilen an (Terminals, die das ignorieren, bleiben unverändert). Die Top-15-Räume aus `raumregeln.yaml` (Jahrgangsordner, sonst Vorlage) sind in Tabelle und Konsolenansicht mit `T/<Stufe>` gekennzeichnet (Oberstufe: `T/11+12`).
- Aufsichten: Die Rolle der Regelaufsicht heißt jetzt durchgehend `Aufsicht` (wie im ENUM der Datenbank, vorher `Standard`): `--rolle`, Plandatei, Prüfungen, Pipeline (`Aufsichten-Lehrkraefte.csv` unverändert). Ältere `raumregeln.yaml` mit `zaehlende_rollen: ["Standard"]` werden weiter gelesen.
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
- `mao aufsichten --interaktiv`: Die Tabelle zeigt je Raum die schreibenden Stufen (Externe als `E3`…`E6`). Im Dry-Run wird der Stand als Entwurf `aufsichten-plan.yaml` gespeichert (`s`, `w`, beim Beenden); ein erneuter Aufruf startet damit, bis `--apply` bestätigt (dann wird der Entwurf gelöscht). `--neu` ignoriert den Entwurf, `--plan DATEI.yaml` wählt eine andere Datei.
- `mao aufsichten --interaktiv`: `l`/`L` (bzw. `l`/`la` mit `--einfach`) zeigen die Mathematik-Lehrkräfte (alle) getrennt in „noch nicht verplant“ und „schon verplant“ (mit Raum/Fach/Reserve) nach dem aktuellen Stand der Sitzung.
- `mao aufsichten --interaktiv` öffnet jetzt eine Tabelle mit Cursor (Pfeiltasten, Enter bearbeitet, Tab ergänzt Kürzel, Leertaste tauscht Zeilen, `r` Rotation, `u` zurück, `s` Diff und Abschicken); ohne Zusatzpaket (ANSI, msvcrt/termios). Die bisherige zeilenweise Sitzung gibt es mit `--einfach`.
- `mao aufsichten --interaktiv` (zeilenweise, `--einfach`) ist eine Konsolensitzung für alle Aufsichten auf einmal (Tabelle, Raum neu besetzen, `fach`/`reserve`, `tausch`, `u`, `fertig` mit Diff und gesammeltem Schreiben) und macht mit `kurz` Rotationsvorschläge für Kurzräume (Stufe 5/6, 2 Stunden): wer am seltensten/längsten nicht dran war, kommt zuerst.
- `mao aufsichten --plan-exportieren` / `--plan DATEI.yaml`: alle Aufsichten (Räume, Fachaufsicht, Reserve) in einer Plandatei bearbeiten, mit Dry-Run-Diff prüfen und mit `--apply` in einer Transaktion übernehmen (Tausche einfach möglich).
- `mao aufsichten` kann jetzt auch setzen: `--raum CODE --lehrkraft KÜRZEL…` (`--rolle Reserve|Fachaufsicht` ohne Raum, `--entfernen`), Dry-Run/`--apply`; `--interaktiv` besetzt Räume ohne Aufsicht Schritt für Schritt (Mathematik-Lehrkräfte zuerst gelistet, wenn `teacher_subjects_table`/`subjects_table` gesetzt sind). Neue optionale tables.ini-Schlüssel: `teacher_subjects_table`, `subjects_table`.
- `mao aufsichten`: die Fachaufsicht ist die Wettkampfleitung und gehört keinem Raum an (Hinweis, wenn keine eingetragen ist).

### GF-Import: Abgleich

- `mao raum-setzen --interaktiv` bietet alle aktiven Räume zur Wahl an (nicht nur fünf), volle zuletzt; die Kapazität darf bewusst überschritten werden.
- `mao import-gf --abgleich --apply` bietet nach dem Nachtragen neuer Teilnehmender an, ihnen sofort von Hand Räume zuzuteilen (`raum-setzen --interaktiv`); vorhandene Raumzuteilungen bleiben unberührt.
- `mao import-gf --abgleich`: Abgleich der befüllten Organisationstabelle mit dem Formular – neue Meldungen und nicht mehr vorhandene werden angezeigt und (mit `--apply`) nur nach Rückfrage eingetragen bzw. gelöscht.

### Einsatz der Oberstufe (`mao einsatz`)

- Neuer Befehl `mao einsatz`: verteilt Stufe 11/12 auf Teilnahme, Aufsicht (je Raum, einstellbar), Reserve, Hofgruppen und 5. PK; Wünsche aus Gravity Forms (Formular 59) werden erfüllt, bei Überhang entscheidet das Los. Dry-Run ist Standard, Zwischenstand in `einsatz-plan.yaml`, Konsolensitzung mit `--interaktiv`, Schreiben nur mit `--apply` (manuelle Einträge bleiben).
- `mao einsatz` (interaktiv): Bei Schülern der Stufe 11 steht der Wunschstatus dabei – „(Wunsch Hof, erfüllt)“, „(Wunsch Aufsicht, nicht erfüllt)“ oder „(ohne Wunsch)“ – in der Übersicht, bei `l ZIEL` und nach `v`.
- `mao einsatz`: `hof_pro_gruppe` ist jetzt eine harte Grenze. Überhang bleibt ohne Rolle (Ziel `ohne`, Abschnitt `ohne_rolle` im Entwurf) und wird von Hand zugeordnet; von Hand darf man auch in eine volle Hofgruppe verschieben (mit Warnung).
- `mao einsatz`: Die Reserve wird nur noch mit Schülern besetzt, die `Aufsicht` gewünscht haben; wer keinen Wunsch abgab, füllt nur Raumaufsichten oder Hof.
- `mao einsatz`: Warnung "Nicht verplant", falls jemand der Stufe 11/12 keinen Einsatz hat, und Hinweis auf Schüler ohne Matrikelnummer.
- `mao einsatz`: Die Rolle 5PK gibt es nur für Stufe 12 ohne Teilnahme. Schüler der Stufe 11 ohne Platz kommen in die kleinste Hofgruppe (auch über `hof_pro_gruppe` hinaus, mit Warnung); verschieben nach 5PK ist für Stufe 11 gesperrt.
- Voreinstellungen für `mao einsatz` stehen im Abschnitt `einsatz:` der `raumregeln.yaml` (aufsichten_pro_raum, reserve, hof_gruppen, hof_pro_gruppe, ohne_raum, formular); die Kommandozeile hat Vorrang.
- tables.ini: neue Schlüssel `oberstufe_einsaetze_table` und `oberstufe_rollen_table` (30 Einträge).
- Pipeline: Dataset und Export `einsatz_oberstufe` (`db/Einsatz-Oberstufe/…`, je Hofgruppe eine Datei); der Job `aufsichten_schueler` ist eingeschaltet.

### Aufsichtspläne als PDF (Pipeline)

- Aufsichten-Oberfläche: Terminalgröße war vertauscht (Zeilen/Spalten), dadurch wurde die Spalte „Aufsicht“ abgeschnitten; die Stufen-Spalte passt sich jetzt dem Inhalt an (Externe: E3, E4 …).
- `mao latex --job NAME` und `mao run --job NAME` führen nur die genannten LaTeX-Jobs aus (mehrfach angebbar, auch wenn ausgeschaltet).

- Neues Dataset `aufsichten_lehrkraefte` (Aufsichten des aktuellen Jahrgangs mit Kürzel, Name inkl. Titel, Raum, Stufen im Raum, Rolle, Bemerkung), zwei Exporte (`db/Aufsichten/Aufsichten-Lehrkraefte.csv` nach Kürzel, `Aufsichten-Raeume.csv` nach Raum) und der LaTeX-Job `aufsichten_lehrkraefte` (Vorlage `tex/Aufsichten-Lehrkraefte/Aufsichten-Lehrkraefte.tex`).
- Job `aufsichten_schueler` (Vorlage `tex/Aufsichten-Schueler/Aufsichten-Schueler.tex`, liest `Einsatz-Oberstufe.csv`) ist vorbereitet, aber ausgeschaltet, bis der Export der Oberstufen-Einsätze in der Pipeline existiert.

### Aufsichtenprüfung (`mao aufsichten`)

- `mao einsatz --interaktiv` kompakter: Die Übersicht zeigt nur Anzahlen und Warnungen, Personen erscheinen nur noch als Matrikelnummer (Kennzeichen A+/H+ Wunsch erfüllt, A!/H! nicht erfüllt, - ohne Wunsch); `l ZIEL` und `r` (alle Räume) listen die Matrikelnummern. Auch `einsatz-plan.yaml` enthält keine Namen mehr.
- `mao raumzuteilung --statistik`: zeigt nur die aktuelle Verteilung (Stufen mit Anzahl je Raum, Externe, ohne Raum); keine Planung, kein Schreiben.
- Pipeline `aufsichten_lehrkraefte`: Die Spalte `bemerkungen` enthält die Datenbankspalte `bemerkung`; nur bei `Fachaufsicht` und `Abiturerstellung` steht davor die Rolle, durch Leerzeichen getrennt (ohne Komma).
- Aufsichten: Fehlt ein Rollenwert (z. B. `Abiturerstellung`) im ENUM der Spalte `rolle`, bricht das Schreiben mit der nötigen `ALTER TABLE`-Anweisung ab (MySQL würde sonst still einen leeren Wert speichern); vorhandene Einträge mit leerer Rolle werden gemeldet. Pipeline: In `Aufsichten-Lehrkraefte.csv` heißt `Standard` jetzt `Aufsicht`.
- `mao aufsichten --interaktiv`: Beim Laden eines Entwurfs wird gemeldet, wo er von der Datenbank abweicht (ein alter Entwurf überschreibt sonst unbemerkt neuere Datenbankeinträge; ignorieren mit `--neu`). Pipeline: Bei `Abiturerstellung` steht die Bemerkung ohne Komma hinter der Rolle.
- Neue Rolle `Abiturerstellung` (ohne Raum, wie Reserve) in `--rolle`, Plandatei (`abitur:`), Konsole und Tabellenansicht. Die interaktiven Ansichten haben eine Spalte **Bemerkung** (Spalte `bemerkung` der Aufsichtentabelle); setzen mit `b` bzw. `bem ZEILE KÜRZEL Text`, in der Plandatei im Abschnitt `bemerkungen:` (`"105|GER": Text`).

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
