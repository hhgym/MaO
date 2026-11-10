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

Führt nur lesende SQL-Abfragen aus und plant die gewählten Exportdateien (Auswahlmenü, `--job` oder mit `--pipeline`/`--profile` die in der Pipeline aktivierten). Eine bestehende Manifestdatei wird zur Unterscheidung von `NEU`, `AKTUALISIEREN`, `UNVERÄNDERT` und `ENTFERNEN` eingelesen. **Keine Datenexporte werden geschrieben oder gelöscht.**

```bash
mao plan --workspace "D:/MaO/2026-27" --profile vorbereitung
mao plan --workspace "D:/MaO/2026-27" --profile ergebnisse --no-latex
mao plan --workspace "D:/MaO/2026-27" --output export_plan.md
```

Optionen: `--job NAME`, `--pipeline`, `--alle-exporte`, `--output MARKDOWN` (legt die Plan-Datei im Arbeitsordner ab, wenn relativ angegeben), `--no-latex` (LaTeX-Jobs aus Liste entfernen) und `--mao NUMMER` (Dokumentnummer, **kein SQL-Wechsel**).

## `mao run`

Lädt die für das Profil erforderlichen SQL-Datasets, erzeugt CSV- und TeX-Dateien und kompiliert aktivierte LaTeX-Jobs. Vorhandene, unveränderte Dateien bleiben unverändert. Veraltete Dateien werden nur gelöscht, wenn sie im Manifest dem ausgewählten Export gehören **und** seit ihrer Erzeugung nicht manuell geändert wurden.

```bash
mao run --workspace "D:/MaO/2026-27" --profile vollpaket
mao run --workspace "D:/MaO/2026-27" --profile turniertag --no-latex
mao run --workspace "D:/MaO/2026-27" --profile ergebnisse --mao 66
```

Optionen: `--no-latex`, `--mao NUMMER` (nur Dokumentnummer), `--job NAME` (nur dieser LaTeX-Job, mehrfach angebbar), `--pipeline` und `--alle-exporte`.

**Auswahlmenü (Standard):** `mao run`, `mao latex` und `mao plan` ohne weitere Angaben öffnen ein Menü mit Pfeiltasten. **Nichts ist vorausgewählt.** `↑`/`↓` wählen, Leertaste setzt oder entfernt das Häkchen, `a` wählt alle Einträge der Gruppe, `n` keine, Enter führt aus, `q` oder Esc bricht ab. Das Menü hat zwei Gruppen: *LaTeX-Jobs* und *Exporte (nur Daten, ohne PDF)*; `mao latex` zeigt nur die Jobs. Exporte, die ein gewählter Job braucht, erscheinen automatisch als `[+]`. Unten steht, wie viele Jobs und Exporte gewählt sind.

Es werden **nur die CSV-/TeX-Dateien erzeugt (und nur die SQL-Abfragen ausgeführt), die zu den gewählten Jobs und Exporten gehören**. Welche Exporte ein Job braucht, wird aus seiner Vorlage erkannt (Pfade der CSV- und Makrodateien, auch in eingebundenen Dateien; TeX-Kommentare zählen nicht). Man kann es im Job mit `exports: [id, …]` festlegen. Lässt es sich nicht erkennen, kommen die in der Pipeline aktivierten Exporte dazu.

**Ohne Menü:** `--pipeline` führt Jobs und Exporte so aus, wie sie in `pipeline.yaml` (und im Profil) aktiviert sind; das ist die Form für Skripte und geplante Aufgaben. `--profile NAME` und `--no-latex` übernehmen ebenfalls die Pipeline-Auswahl. `--job NAME` wählt Jobs direkt und erzeugt nur die nötigen Exporte; `--alle-exporte` erzeugt dabei trotzdem alle aktivierten. Ohne Terminal und ohne eine dieser Angaben bricht der Befehl mit einem Hinweis ab.

## `mao latex`

Benötigt keine Datenbankverbindung und verändert keine CSV-Dateien. Kompiliert aktivierte LaTeX-Jobs aus bereits vorhandenen Daten im Arbeitsordner.

```bash
mao latex --workspace "D:/MaO/2026-27" --profile ergebnisse --mao 66
```

Wenn `--mao` fehlt, wird die Jahrgangsnummer rechnerisch anhand des Datums bestimmt. Für vergangene oder künftige Wettbewerbsjahrgänge deshalb `--mao` ausdrücklich setzen.

Einzelne Jobs: `mao latex --job aufsichten_lehrkraefte` (mehrfach angebbar) oder das Auswahlmenü (siehe `mao run`); fehlen Dateien der nötigen Exporte, erscheint eine Warnung, dass erst `mao run` laufen muss. Der Job läuft auch, wenn er in der Pipeline ausgeschaltet ist; alle anderen entfallen. Unbekannte Namen werden mit der Liste der vorhandenen Jobs abgelehnt. Die Namen stehen unter `latex.jobs` in der `pipeline.yaml`.

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
- `mao import-gf --abgleich` vergleicht die **bereits befüllte** Organisationstabelle
  mit dem Formular: Es zeigt Meldungen, die seit dem Import neu hinzugekommen sind,
  und Teilnehmende, die nicht mehr im Formular stehen. Im Dry-Run wird nur
  angezeigt. Mit `--apply --abgleich` fragt es nach (`j` = alle, `e` = einzeln,
  sonst keine), ob die neuen eingetragen und die fehlenden **gelöscht** werden
  (Tnr, Raum und Lehrkraft gehen dabei verloren). Neue erhalten die nächsten freien
  Tnr; ohne Eingabemöglichkeit (z. B. in einem Skript) wird nichts geändert.
  Die Raumzuteilung wird dabei nicht neu berechnet: Wer gelöscht wird, verliert seinen
  Raum mit der Zeile, alle anderen Zuteilungen bleiben unverändert. Nach dem Eintragen
  neuer Teilnehmender bietet der Befehl an, ihnen sofort von Hand Räume zuzuteilen
  (entspricht `mao raum-setzen --interaktiv --apply`, das nur Teilnehmende ohne Raum
  anbietet und dabei **alle** aktiven Räume zur Wahl stellt, auch volle, deren Grenze dann bewusst überschritten wird); das geht auch später jederzeit.
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
- `mao raumzuteilung --statistik` zeigt nur an, welche Stufen mit welcher Anzahl in welchem Raum sitzen (Hauptwettbewerb und Externe, dazu die Zahl ohne Raum). Top-15-Räume (`T/7` …) und Sammelräume (`S`) laut `raumregeln.yaml` sind gekennzeichnet. Es plant und schreibt nichts.
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
- Regeln in `raumregeln.yaml` (je Jahrgang): `aufsichten: {lehrkraefte_pro_raum: 1, zaehlende_rollen: ["Aufsicht"]}`.
  Die Rollen `Reserve` (Springer), `Fachaufsicht` (Wettkampfleitung: beantwortet Fragen zu den Aufgaben) und `Abiturerstellung` (Lehrkräfte, die statt der Aufsicht eine Abituraufgabe erstellen; muss in der Rollenspalte der Aufsichtentabelle vorhanden sein) gehören keinem Raum an und zählen standardmäßig nicht als Raumaufsicht. Fehlt eine Fachaufsicht, gibt es einen Hinweis; ist sie einem Raum zugeordnet, ebenfalls.
- **Aufsichten setzen** (Dry-Run ist Standard, `--apply` schreibt):
  - `mao aufsichten --raum 105 --lehrkraft GER NIX [--apply]` trägt Lehrkräfte per Kürzel als Raumaufsicht ein.
  - `mao aufsichten --rolle Fachaufsicht --lehrkraft MEI [--apply]` (ebenso `Reserve`, `Abiturerstellung`) trägt Lehrkräfte ohne Raum ein.
  - `--entfernen` löscht die genannten Einträge. Eingetragen werden können alle aktiven Lehrkräfte, nicht nur Mathematik.
    Ist jemand schon in einem anderen Raum eingeteilt, gibt es eine Warnung (Eintrag erfolgt trotzdem).
  - **Tabelle mit Cursor:** `mao aufsichten --interaktiv [--apply]` öffnet eine Tabelle aller belegten Räume (plus
    Fachaufsicht, Reserve und Abiturerstellung; `K` = Kurzraum, `T/7` = Top-15-Raum der Stufe 7, `S` = Sammelraum (laut `raumregeln.yaml`); Spalte **Bemerkung** zeigt die Bemerkungen der Lehrkräfte je Zeile). Mit den Pfeiltasten die Zeile wählen, `Enter` bearbeitet sie (Kürzel eintippen, `Tab`
    ergänzt, `+KÜ`/`-KÜ` ändert nur), `b` setzt eine Bemerkung zu einer Lehrkraft der Zeile (`KÜRZEL Text`, ohne Text löscht), `Leertaste` markiert eine Zeile und tauscht sie mit der nächsten markierten,
    `r` zeigt den Rotationsvorschlag für Kurzräume (`j` übernimmt), `u` nimmt zurück, `l`/`L` zeigen die Lehrkräfte
    (Mathematik/alle) getrennt nach „noch nicht verplant“ und „schon verplant“ (mit Raum bzw. Fach/Reserve) in der aktuellen Sitzung, `s` prüft (Diff `+`/`−`) und schreibt mit `--apply` nach `j` alles in einer Transaktion, `q` beendet.
    Ohne Zusatzpaket; braucht ein Terminal. Mit `--einfach` gibt es stattdessen die zeilenweise Eingabe:
    `mao aufsichten --interaktiv --einfach` zeigt eine Tabelle aller belegten Räume
    mit der Aufsicht und bearbeitet sie in einer Sitzung; abgeschickt wird erst am Ende:
    `105 GER NIX` besetzt einen Raum neu (`+KÜ`/`-KÜ` ändert nur, `-` leert), `fach MEI`, `reserve KRE` und `abitur GER` setzen
    Fachaufsicht, Reserve und Abiturerstellung, `bem 105 GER nur bis 10 Uhr` setzt eine Bemerkung (ohne Text: löschen), `tausch 105 204` tauscht zwei Raum-Teams, `tausch GER NIX` zwei Personen, `u` nimmt zurück,
    `z` zeigt die Tabelle, `l`/`la` listen die Lehrkräfte (Mathematik zuerst/alle). `fertig` zeigt die Änderungen (`+`/`−`)
    und schreibt mit `--apply` nach `j` alles in einer Transaktion; `q` bricht ab.
  - **Stufen und Entwurf:** Die Tabelle zeigt je Raum die schreibenden Stufen (`5, 6`; Externe als `E4`). Im Dry-Run
    (ohne `--apply`) speichern `s`/`fertig` und `w` den Stand als Entwurf `aufsichten-plan.yaml` im Jahrgangsordner
    (beim Beenden mit Änderungen wird gefragt). Jeder erneute Aufruf von `mao aufsichten --interaktiv` startet mit diesem
    Entwurf; so lässt sich ein Stand über mehrere Sitzungen weiterbearbeiten, bis er mit `--apply` bestätigt wird. Danach
    wird der Entwurf gelöscht. `--neu` ignoriert einen vorhandenen Entwurf, `--plan DATEI.yaml` nimmt eine andere Datei.
  - **Kurzräume rotieren:** Räume, in denen nur Stufe 5/6 schreibt (2 statt 4 Stunden, in der Tabelle mit `K`), besetzt
    `kurz` per Vorschlag: aus den eingeteilten Lehrkräften kommt zuerst, wer in früheren Jahrgängen am seltensten und
    am längsten nicht in einem Kurzraum war (Grundlage: `archive_table` und frühere Aufsichten). Wer in den Kurzraum
    rückt, tauscht mit jemandem aus dem Kurzraum. `kurz ok` übernimmt den Vorschlag (mit `u` rückgängig).
  - **Alles auf einmal (Plandatei):** `mao aufsichten --plan-exportieren` legt `aufsichten-plan.yaml` im Jahrgangsordner an
    (nie überschreibend): je Raum eine Kürzelliste, dazu `fachaufsicht` und `reserve`. Die Datei im Editor bearbeiten
    (Tausche, Lücken füllen), dann `mao aufsichten --plan aufsichten-plan.yaml` (Dry-Run: zeigt `+`/`−`/Warnungen) und
    `--apply` schreibt alle Änderungen in einer Transaktion. Die Datei ist der Zielzustand der darin genannten Abschnitte
    und Räume; fehlende Abschnitte/Räume bleiben unverändert. Eine Lehrkraft in mehreren Räumen ergibt eine Warnung.
  - Für die Mathematik-Auswahl `teacher_subjects_table` und `subjects_table` in tables.ini setzen (optional; sonst zeigt `l` alle).
- Die Oberstufenschüler als Aufsicht folgen später.
- **PDF des Aufsichtsplans:** `mao run` exportiert die Aufsichten nach `db/Aufsichten/` und `mao latex` erzeugt
  `MaO_<Nr>_Aufsichten-Lehrkraefte.pdf` (Seite 1 nach Lehrkraft, Seite 2 nach Raum; Reserve und Fachaufsicht mit Rolle in
  „Bemerkungen“). Vorlagen: `tex/Aufsichten-Lehrkraefte/` und (noch ausgeschaltet) `tex/Aufsichten-Schueler/` im MaO-Ordner.
  Das Hinweisblatt für Aufsichten (`mao latex --job hinweise_aufsichten`, Vorlage `tex/Hinweise-Aufsichten/`) braucht keine Exportdaten; sein Text steht in `tex/base/Hinweise-Aufsichten-Inhalt.tex` und ist auch auf der Rückseite der Raum-Anwesenheitslisten eingebunden.


## Einsatz der Oberstufe (`mao einsatz`)

Plant den Einsatz der Stufen 11 und 12 und schreibt ihn in die Einsatztabelle (`oberstufe_einsaetze_table`, Rollen aus `oberstufe_rollen_table`; beide in tables.ini setzen).

- **Regeln:** Teilnehmende (Tnr in der Organisationstabelle) der Stufe 11 und 12 → `Teilnahme`. Stufe 12 ohne Teilnahme → `5PK`. Stufe 11 ohne Teilnahme: Wunsch (`Aufsicht` oder `Hof`) aus dem Gravity-Forms-Formular (`--formular`, Standard 59; Zugang wie beim GF-Import in `gf.config`).
- **Verteilung:** Wünsche werden erfüllt; gibt es mehr Wünsche als Plätze, entscheidet das Los (`--seed` macht es wiederholbar). Wer nichts gewünscht hat oder nicht zum Zug kam, füllt freie Aufsichtsplätze in den Räumen (nicht die Reserve: deren Plätze füllen zuerst die Wünsche `Aufsicht`, noch vor den Räumen; wer keinen Wunsch abgab, kommt nie in die Reserve; bleibt sie unbesetzt, erscheint eine Warnung), dann die Hofgruppen; `--hof-pro-gruppe` ist eine harte Grenze: wer dann keinen Platz hat, bleibt „ohne Rolle“ (Warnung „Nicht verplant“, wird nicht in die Datenbank geschrieben) und wird in der Konsole von Hand zugeordnet (auch in eine volle Hofgruppe, dann mit Warnung) (`v NR ZIEL`, Ziel `ohne` setzt zurück). `5PK` gibt es nie für Stufe 11.
- **Einstellbar:** `--aufsichten-pro-raum N` (Standard 1, je Raum mit Teilnehmenden, ausgenommen `--ohne-raum CODE …`), `--reserve N` (Standard 3, Modus Reserve ohne Raum), `--hof-gruppen N` (Standard 2, Modus Gruppe mit `gruppe_nr`) und `--hof-pro-gruppe N` (Standard unbegrenzt).
- **Voreinstellungen in `raumregeln.yaml`:** Abschnitt `einsatz:` mit `aufsichten_pro_raum`, `reserve`, `hof_gruppen`, `hof_pro_gruppe`, `ohne_raum` und `formular` (alle optional, je Jahrgang). Schalter auf der Kommandozeile haben Vorrang.
- **Schutz:** Einträge mit `quelle = MANUAL` und fremde Rollen (z. B. Korrektur) bleiben unverändert und belegen ihre Plätze. Automatische Einträge werden aktualisiert, nicht gelöscht.
- **Dry-Run ist Standard.** Der Stand liegt als `einsatz-plan.yaml` im Jahrgangsordner (Matrikelnummer am Zeilenanfang jedes Eintrags; bearbeitbar). Ein erneuter Aufruf nutzt diese Datei (`--neu` ignoriert sie und startet wie `mao aufsichten` aus der Datenbank, `--plan DATEI` wählt eine andere). **Ausgangspunkt ist der gespeicherte Stand:** Zuordnungen der Stufe 11 aus der Datenbank (Aufsicht, Reserve, Hof) bleiben und belegen ihre Plätze; ausgelost werden nur Personen ohne Eintrag. Von Hand gesetzte Einträge (`MANUAL`) bleiben fest. `--auslosen` übernimmt den Datenbankstand nicht und lost alles neu aus (ebenso `n` in der Konsolensitzung). `--apply` schreibt in einer Transaktion mit Sperre und löscht den Entwurf.
- **`--interaktiv`:** Konsolensitzung mit `l` (Übersicht nur mit Anzahlen), `l ZIEL` (Matrikelnummern eines Ziels), `r` (alle Räume), `s TEXT` (Matrikelnummer oder Klasse suchen), `v NR ZIEL` (verschieben, z. B. `v 123456 hof2`), `t NR NR` (tauschen). Es werden nur Matrikelnummern angezeigt, mit Kennzeichen `A+`/`H+` (Wunsch Aufsicht/Hof erfüllt), `A!`/`H!` (nicht erfüllt) und `-` (ohne Wunsch), `n [SEED]` (neu auslosen), `w` (offene Wünsche), `u` (zurück), `d` (Entwurf speichern), `ok` (Änderungen zeigen, mit `--apply` nach Rückfrage schreiben) und `q`.
- **PDF:** Drei Exporte aus derselben Abfrage `einsatz_oberstufe` (Spalten `nachname, vorname, klasse, rolle, bemerkung, gruppe, entitaet`; `entitaet` = Bemerkung, bei Teilnahme der Raum der Teilnahme): `db/Einsatz-Oberstufe/Einsatz-Oberstufe.csv` (nach Name, dazu je Hofgruppe `…-Gruppe-N.csv`), `…-Klasse.csv` (nach Klasse) und `…-Raum.csv` (nach Raum). Vorlagen im MaO-Ordner, gemeinsame Bausteine in `tex/base/Oberstufe-Bausteine.tex`, Erklärung der Einsätze in `tex/base/Oberstufe-Legende.tex`:

| Job | Vorlage | Inhalt |
|---|---|---|
| `oberstufe_anwesenheitslisten` | `tex/Oberstufe-Anwesenheitslisten/` | je Einsatz (Aufsicht, Hof nach Gruppen, Teilnahme) eine Anwesenheitsliste |
| `oberstufe_einteilungslisten` | `tex/Oberstufe-Einteilungslisten/` | je Einsatz (Aufsicht, Teilnahme, Hof, 5PK) eine Einteilungsliste nach Klasse |
| `oberstufe_uebersicht` | `tex/Oberstufe-Uebersicht/` | Gesamtübersicht mit Legende; Entität = Raum, Gruppe oder Reserve |
| `aufsichten_schueler` | `tex/Aufsichten-Schueler/` | Schüler:innen in der Aufsicht nach Name, nach Raum und die Reserve |
| `aufsichten_raumkontrolle` | `tex/Aufsichten-Raumkontrolle/` | je Raum Lehrkräfte (Kürzel, Bemerkung) und Schüler:innen in der Aufsicht, mit Kästchen „ausgeteilt“ und „eingesammelt“ |

  Die PDF-Dateien liegen im Jahrgangsordner unter `Oberstufe/` (Aufsichten-Schueler unter `Aufsichten/`). Nur bestimmte Einsätze: im Job `extra_defs: ["\\def\\OberstufeEinsaetze{Aufsicht,Hof}"]`.

```bash
mao einsatz --aufsichten-pro-raum 1 --reserve 3 --hof-gruppen 2    # Plan + Entwurf
mao einsatz --interaktiv                                           # prüfen und ändern
mao einsatz --apply                                                # schreiben
```

## Raum-Anwesenheitslisten

Der Job `anwesenheitslisten` (`tex/Raum-Anwesenheitslisten/Raum-Anwesenheitslisten.tex`) druckt je Raum eine Anwesenheitsliste (Vorname, Nachname) und auf der Rückseite die Hinweise für die Aufsicht. Er braucht die Exporte `teilnehmende_raum` (Liste je Raum, einschließlich der externen Teilnehmenden: Nr. `E<Nr>`, Klasse `<Stufe> (ext.)`), `aufsichten_lehrkraefte_nach_raum` (Aufsicht der Lehrkräfte) und `einsatz_oberstufe_raum` (Aufsicht der Oberstufe). Die beiden Aufsichten sind optional; fehlt die Datei, bleibt das Feld leer.

## Kopierliste

Der Job `kopierliste` (`tex/Kopierliste/Kopierliste.tex`, standardmäßig deaktiviert) zeigt auf Seite 1 alle Räume mit der Zahl der Teilnehmenden je Stufe 5–12 samt Summenzeile, danach je Raum eine Seite zum Abhaken. Die Abfrage `kopierliste` liest die View `kopierliste_view`; deren Zeile GESAMT liefert die Summen und steht als letzte Zeile der Gesamttabelle. Der Export schreibt `db/Kopierliste/Kopierliste.csv`, je Raum `Kopierliste-Raum-<Raum>.csv` und die Makrodatei `db/Include/Tabellen-Kopierliste.tex` (`\Kopierraumliste`). Da die CSV-Dateien erst entstehen müssen: `mao run --job kopierliste` (nicht nur `mao latex`).

Die Externen (Klassenstufe 3 bis 6) stehen in einer zweiten Tabelle unter der ersten und bekommen je Raum eine eigene Seite mit „Raum: XXX (externe)“ in der Überschrift. Grundlage ist die Datenbank-View `MaO_Orga_2_view_Kopierliste_externe` (Kennung `kopierliste_externe_view` in `tables.ini`; Spalten `raum`, `stufe_3` … `stufe_6`, `gesamt`, zuletzt die Zeile `GESAMT`) und der Export `kopierliste_externe`. Gibt es keine Externen mit Raum, entfällt die zweite Tabelle.

## Raumübersicht und Korrekturlisten

Der Job `raumuebersicht` (`tex/Raumuebersicht/Raumuebersicht.tex`) listet alle Teilnehmenden mit ihrem Raum (sortiert nach Klasse, Vorname, Nachname; ohne Raum steht „--“). Er braucht den Export `teilnehmende_gesamt` (`db/Teilnehmende/Teilnehmendeliste.csv`).

Der Job `korrekturlisten` (`tex/Korrekturlisten/Korrekturlisten.tex`) druckt je Klassenstufe und Aufgabe 1 bis 4 eine Punkteliste (Vorname, Nachname) mit leerer Spalte „Punkte“. Die Klassenstufen 11 und 12 stehen gemeinsam auf einer Liste. Die Aufgabennummer setzt sich aus `\MaO`, dem zweistelligen Stufencode (12 für 11-12), `2` und der Aufgabe zusammen (z. B. 660521). Grundlage sind die Abfrage `korrekturliste` (ohne Abgemeldete, `anwesenheit = 0`) und der gleichnamige Export mit der Makrodatei `db/Include/Tabellen-Korrekturliste.tex` (`\KorrekturListe`). Beide Jobs sind standardmäßig deaktiviert; aufgerufen werden sie mit `mao run --job raumuebersicht` bzw. `mao run --job korrekturlisten` (die CSV-Dateien müssen erst entstehen, `mao latex` allein genügt nicht).

## Aushang

Der Job `aushang` (`tex/Aushang/Aushang.tex`) erzeugt den Aushang für den Wettbewerbstag. Die Vorlage ist eigenständig und braucht nur den kleinen Export `wettbewerbstag`. Der Wettbewerbstag kommt aus `MaO_Runden` (`runde_nr = 2` des aktuellen Jahrgangs, Spalte `datum`) über den Export `wettbewerbstag` (`db/Wettbewerb/Wettbewerbstag.csv`); fehlt die Datei oder das Datum, steht `TT.MM.JJJJ` im Aushang. Die Zeiten stellst du in der .tex ein; die Beispiel-Aufgabennummer folgt der Nummer des Jahrgangs. Der Job ist im Profil `vorbereitung` enthalten (standardmäßig deaktiviert): `mao run --job aushang` (holt das Datum und setzt das PDF).

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

## Korrekturangebote (Korrektor:innen)

Das Formular „Korrekturangebot 2. Stufe“ (Gravity Forms) wird per API gelesen und in die Tabellen
`korrektoren_table`, `korrektoren_prefs_table`, `korrektoren_angebote_table`, `korrektoren_adressen_table` und
`korrektoren_angehoerige_table` (tables.ini) übernommen. Die Feldnummern stehen in `gf-korrektoren.yaml`
(wird von `mao config init` angelegt), die Zugangsdaten in `gf.config`. Für die Zuordnung der Hertz-Kinder braucht
tables.ini zusätzlich `schueler_alle_table` (alle Schüler:innen, auch inaktive und ohne Klasse).

- `mao import-korrektoren --dry-run --report korrektoren.json` zeigt, was passieren würde (Standard, schreibt nichts).
- `mao import-korrektoren --interaktiv --apply` klärt unklare Fälle und die Hertz-Kinder in einer Konsolensitzung und
  schreibt danach. Die Entscheidungen stehen in `korrektoren-entscheidungen.yaml` im Workspace (nur IDs) und gelten bei
  jedem weiteren Lauf; `--interaktiv` ohne `--apply` sammelt sie nur.
- Nur Einträge des aktuellen Jahrgangs zählen (Jahrgangsnummer in Feld „Jahrgang MaO“ und/oder Schuljahr-ID); ist der Jahrgang
  nicht erkennbar, wird der Eintrag gemeldet und übersprungen. Mehrere Einträge derselben Person: der neueste gilt.
- Zuordnung zu bekannten Korrektor:innen: erst `uid` (= `gf_uid`), dann eindeutige E-Mail-Adresse; beides wird automatisch
  übernommen. Treffer nur über den Namen oder mehrdeutige E-Mails sind „unklar“ und brauchen eine Entscheidung
  (Nummer, `n` = neu anlegen, `s` = überspringen). Neue Korrektor:innen erhalten die vom Formular erzeugte ID als `gf_uid`.
- Präferenzen (`kl_5` … `kl_11_12`, Wünsche, Erfahrung, Übergabe, Anmerkungen) werden vollständig aus dem Eintrag
  übernommen, weil bekannte Korrektor:innen ihre Daten vorbefüllt bekommen. Name, Titel, Geschlecht und E-Mail werden bei
  Abweichung aktualisiert, ein deaktivierter Korrektor wird wieder aktiv. Die Anschrift wird nur bei Übergabe „Post“ gespeichert.
- Hertz-Kinder (Feld „Weitere aktive Hertz-Kinder“) werden nie automatisch geschrieben: Zu Name und Klasse (8_1, 8.1, 8a und 8-1
  gelten gleich) zeigt die Sitzung die wahrscheinlichsten Schüler:innen, auch inaktive und ohne Klasse. Mit Nummer oder
  Matrikelnummer wird `hertzkind_matrikelnummer` angelegt, `k` = kein Kind, Enter = offen lassen.
- Zu lange Werte (z. B. Name über der Spaltenlänge) werden nicht gekürzt: Der Eintrag wird übersprungen und die Meldung
  nennt die zu verlängernde Spalte.
- Bericht und Entscheidungsdatei enthalten nur IDs, keine Namen, E-Mail-Adressen oder Anschriften. Geschrieben wird in einer
  Transaktion mit Zählabgleich; die Aufgabenzuordnung (`MaO_Korrektoren_Aufgabe_Zuordnung`) folgt später.
