# Zentrale Konfiguration

MaO verwaltet **alle** Einstellungen in genau einem privaten Konfigurationsordner. Er wird über den globalen Schalter `--config-dir`, danach über `MAO_CONFIG` und schließlich über das Betriebssystem-Standardverzeichnis gefunden.

```powershell
mao --config-dir "D:/MaO/Config" config init
$env:MAO_CONFIG = "D:/MaO/Config"
mao config show
```

`config init` erzeugt direkt `settings.yaml`, `pipeline.yaml`, `profiles.ini`, `tables.ini`, `db.config`, `gf.config` und `gf-import.yaml` (ohne `.example`). Zugangsdaten bleiben zunächst leer und müssen privat ergänzt werden. Bestehende Dateien werden nicht überschrieben; frühere `.example`-Dateien bleiben erhalten. Die Inhalte individuell angepasster Vorlagen werden in fehlende Zieldateien übernommen; veraltete GF-Demo-Queries werden durch die neue Standardvorlage ersetzt. Fehlende Schlüssel `students_table` und `current_groups_view` werden in einer bestehenden `tables.ini` ergänzt; ein bisheriger `students_view`-Eintrag wird unter Erhalt des Tabellenwerts in `students_table` umbenannt.

| Datei | Aufgabe |
|---|---|
| `settings.yaml` | Exakter Workspace-Pfad je Schuljahr, aktueller Jahrgang, Standardprofil |
| `pipeline.yaml` | SQL-Datasets, Daten-Exporte, LaTeX-Jobs |
| `profiles.ini` | Profile aus eindeutigen Export-/Job-IDs |
| `tables.ini` | Zuordnung privater Tabellen- und Viewnamen |
| `db.config` | MySQL-Verbindung, nicht im Repository |

Beispiel `settings.yaml`:

```yaml
current_year: "2026-27"
workspaces:
  "2025-26":
    root: "D:/Schuljahre/2025-26/MaO"
    status: archived
  "2026-27":
    root: "D:/Schuljahre/2026-27/MaO"
    status: current
    # mao: 66  # optional: zusätzlich mit DB-Nummer abgleichen
defaults:
  profile: vollpaket
```

Jede `root`-Angabe muss absolut und eindeutig sein. Es gibt **kein** berechnetes `workspace.root`/`pattern`. Der Schalter `--workspace` überschreibt die Auswahl; mit einem expliziten `--year` wird dabei Pfadgleichheit erzwungen.

## Datenbankzugang

`db.config` ist eine einfache lokale `key=value`-Datei:

```ini
host=localhost
port=3306
user=ANWENDER
password=KEIN_ECHTES_PASSWORT
database=DATENBANK
```

`password` und tatsächliche SQL-Namen weder in Wrapper noch im Git-Repository speichern. In der zentralen `tables.ini` stehen unter `[table]` die echten MySQL-Identifier. Die öffentlichen `DEMO_`-Werte sind für SQL-Läufe unzulässig. Die `pipeline.yaml` benutzt `{{table.<kennung>}}` nur für SQL-Objektnamen, nicht für Datenwerte.

## Konfiguration bei Updates

Programmdateien und ausgelieferte `DEMO_`-Defaults werden mit MaO aktualisiert; zentrale Einstellungen und Arbeitsdaten bleiben unabhängig. Der Konfigurationsassistent überschreibt keine existierenden lokalen Dateien. Schema-Migrationen für neue Pflichtfelder erfolgen nicht automatisch; fehlende Werte werden gemeldet.

## Gravity Forms (private zentrale Konfiguration)

Mit `mao config init` werden `gf.config` und `gf-import.yaml` direkt angelegt. Es ist kein Umbenennen erforderlich. Beide Dateien werden nur im privaten Konfigurationsordner gepflegt.
Im Repository existieren weder Zugangsdaten noch produktive GF-Importabfragen.

`gf.config` hat das bestehende Format:

```ini
[oauth1]
api_base=https://example.org/wp-json/gf/v2
consumer_key=HIER_EINTRAGEN
consumer_secret=HIER_EINTRAGEN
```

`gf-import.yaml` legt `form_id`, `fields`, `mandatory_classes`,
`optional_mandatory_classes` und vier lokale SQL-SELECT-Abfragen fest:

- `queries.students`: `{{table.students_table}}` aus `tables.ini`, Schülerstammdaten mit `matrikelnummer`, `vorname`, `nachname`, `geschlecht`, `klasse` des aktuellen Schuljahres.
- `queries.mandatory`: `{{table.current_groups_view}}` aus `tables.ini`, aktuelle Lerngruppen (derzeit ausschließlich Mathematik) mit `klasse` (Alias für `klassenname`), `lehrkraft_id` und `lerngruppe_schuljahr_id`. Ein Fachfilter ist gegenwärtig nicht notwendig. Bei Einführung weiterer Fächer muss die private Abfrage zusätzlich auf Mathematik filtern. Pro Pflichtklasse genau eine eindeutige Lehrkraft.
- `queries.teachers`: `{{table.teachers_table}}` aus `tables.ini`, gültige `id`-Werte der Lehrkräfte; Formularfeld `3` enthält bereits diese ID und wird nur geprüft.
- `queries.courses`: `{{table.current_groups_view}}` aus `tables.ini`, die Spalten `lerngruppe_schuljahr_id` und `lehrkraft_id`. Das Paar aus den GF-Feldern `5` und `3` muss darin vorkommen.

**Feste ID-Zuordnung.** Gravity Forms liefert in Feld `5` die
`lerngruppe_schuljahr_id` und in Feld `3` die `lehrkraft_id`. Für Teilnehmende
der Stufen 11 und 12 schreibt der Import die `lerngruppe_schuljahr_id`
unverändert in `kurs_id` der Organisationstabelle; in den Stufen 5–10 bleibt
`kurs_id` leer (`NULL`), auch bei Pflichtteilnahmen. Vorher wird geprüft, dass
die Kombination aus Lerngruppe und Lehrkraft in `queries.courses` vorkommt;
sonst bricht der Import ab. Eine Einsatz-ID wird nicht ermittelt. Der
Spaltenname `kurs_id` bleibt vorerst unverändert.

Standard-Pflichtklassen: `5-1`, `5-2`, `6-1`, `6-2`, `7-3`. Die `7-4`
steht unter `optional_mandatory_classes`: Sie wird nur übernommen, wenn sie
in den aktuellen Mathematik-Lerngruppen vorkommt. Alle Schülerinnen und
Schüler der jeweiligen Klasse kommen aus der Schüler-Stammdatenabfrage.
Eine bereits aus GF angemeldete Matrikelnummer wird nicht erneut hinzugefügt.
Fehlt eine der fünf festen Klassen, ist eine Mathe-Lehrkraft mehrdeutig oder
fehlt die Schülerliste, wird abgebrochen. Die erforderliche Mathematikfilterung
muss in der **privaten** SQL-Abfrage umgesetzt werden; der View-Quelltext
enthält kein eindeutiges `fach`-Feld.

Die API wird paginiert per OAuth 1.0/HMAC-SHA1 über HTTPS abgefragt. Für den
Schreibmodus wird ausschließlich der aktuelle Workspace akzeptiert; auf
bestehende Ergebnisdaten wird nicht schreibend zugegriffen.

### Fremdschlüssel der Organisationstabelle prüfen

`mao validate --online` und jeder Lauf von `mao import-gf` lesen aus
`information_schema`, worauf die Fremdschlüssel von `kurs_id` und
`lehrkraft_id` verweisen, und zeigen das Ergebnis an. Optional legt
`foreign_keys` in `gf-import.yaml` fest, welche Tabellen erwartet werden:

```yaml
foreign_keys:
  kurs_id: "{{table.lerngruppe_schuljahr_table}}"
  lehrkraft_id: "{{table.teachers_table}}"
```

Den Schlüssel `lerngruppe_schuljahr_table` dazu in der privaten `tables.ini`
ergänzen (die Tabelle, deren `id` in GF-Feld 5 steht). Verweist ein
Fremdschlüssel auf eine andere Tabelle, brechen Online-Prüfung und Import ab,
bevor etwas geschrieben wird. Fehlt der Fremdschlüssel ganz, gibt es nur eine
Warnung. Groß- und Kleinschreibung der Tabellennamen spielt keine Rolle; noch
auf `DEMO_` stehende Namen gelten als nicht eingerichtet.


## Online-Validierung und GF-Prüfbericht

Der bestehende Befehl `mao validate` bleibt offline. Der zusätzliche Schalter
`--online` aktiviert **nur lesende** Zugriffe auf MySQL und die
Gravity-Forms-REST-API:

```powershell
mao validate --online
mao import-gf --dry-run --report reports/gf-pruefung.json
# Alternativ tabellarisch: --report reports/gf-pruefung.csv
```

Online validiert MaO die Organisationstabelle (`tnr`, `matrikelnummer`,
`lehrkraft_id`, `kurs_id` usw.), alle im Profil aktiven SQL-Datasets,
die SELECT-Abfragen des GF-Imports mit ihren erforderlichen Spalten,
Schüler-/Lehrkraft-IDs, die Kombinationen aus Lerngruppe und Lehrkraft,
die Ziele der Fremdschlüssel, die Pflichtklassen
und die erste Antwort der GF-API. Eine befüllte Organisationstabelle wird
als nicht erstimportbereit ausgewiesen; die Validierung selbst bleibt lesend.

Der Dry-Run lädt sämtliche Meldungen, erkennt unter anderem doppelte oder
unbekannte Matrikelnummern und erstellt die Klassenzahlen inklusive
GF-/Pflicht-Anteilen. JSON enthält strukturierte Statistiken und Diagnosen,
CSV die Klassenstatistik sowie eine Abschlusszeile mit Status/Fehlern.
Abweichungen zwischen GF-Klasse und Stammdaten-Klasse sowie nicht zuordenbare
Geschlechtsangaben blockieren nicht, stehen aber als `warnings` im Bericht und
im Log; übernommen wird weiterhin die GF-Klasse. Der Abruf bricht ab, wenn die
API weniger oder mehr Einträge liefert als angekündigt oder einen Eintrag
doppelt liefert. Dann haben sich Meldungen während des Abrufs geändert, und der
Lauf wird einfach wiederholt.
Berichte werden nur **neu** im registrierten Workspace angelegt, nicht im
privaten Konfigurationsordner. Bei einem Fehler bleibt der Bericht als
`blocked` gekennzeichnet; bei erfolgreicher Planung als `ready`.
Für den echten Erstimport `mao import-gf --apply` ohne `--report` ausführen.
Beide Befehle archivieren oder löschen keinerlei bestehende Datensätze.


## Zentraler MaO-Ordner (mehrere Rechner)

Empfohlener Aufbau, z. B. in der Nextcloud:

```
MaO/
  config/          settings.yaml, pipeline.yaml, profiles.ini, tables.ini, db.config, gf.config, gf-import.yaml
  tex/             LaTeX-Vorlagen
  65_2025-26/      Jahrgangsordner (Exporte, PDFs)
  66_2026-27/
```

Einmalig anlegen und auf jedem Rechner bekannt machen:

```powershell
mao config init "D:/Nextcloud/MaO"      # legt config/ und tex/ an
mao config use  "D:/Nextcloud/MaO"      # pro Rechner einmal; merkt sich den Ordner lokal
mao workspace add 2026-27 --root "D:/Nextcloud/MaO/66_2026-27"
mao config show                         # zeigt auch, woher der Ordner stammt
```

Reihenfolge, in der MaO den Konfigurationsordner sucht: `--config-dir` → `MAO_CONFIG` → vom aktuellen Ordner aufwärts nach `config/settings.yaml` → Verweis aus `mao config use` → Quellordner (Editable) → Benutzerordner. `--config-dir` und `MAO_CONFIG` dürfen auch den MaO-Ordner selbst nennen.

Jahrgangsordner innerhalb des MaO-Ordners werden in `settings.yaml` **relativ** gespeichert (`root: 66_2026-27`), so gilt dieselbe Datei auf jedem Rechner, egal wo die Nextcloud liegt. Wer in einem Jahrgangsordner (oder darunter) arbeitet, meint automatisch diesen Jahrgang; `--year` und `--workspace` haben Vorrang. Wrapper `mao-hier.*` sind im zentralen Ordner nicht mehr nötig und werden nur mit `workspace add --wrapper` angelegt. Der Verweis liegt rechnerlokal (Windows `%APPDATA%/MaO/mao-ordner.txt`, Linux `~/.config/mao/mao-ordner.txt`) und gehört nicht in die Nextcloud.

## Editable-Installation: lokaler Konfigurationsordner

Bei `D:/MaO/mao_cli`, `D:/MaO/pyproject.toml` und `D:/MaO/config` erkennt MaO den vorhandenen Ordner `config` automatisch. `--config-dir` und `MAO_CONFIG` behalten Vorrang, für andere Installationstypen bleibt der Benutzer-Konfigurationsordner maßgeblich.

## Leermeldungen im Meldeformular

`fields.leermeldung: "10"` bezeichnet die Checkbox mit dem Wert `Leermeldung`. GF kann sie als `10` oder `10.1` übertragen. Aktive Leermeldungen ohne Teilnehmerauswahl werden als gültig gezählt, erzeugen keine Teilnehmerzeile und erscheinen im Prüfbericht unter `empty_reports` (insgesamt und je Klasse). Gleichzeitig ausgewählte Teilnehmende und Leermeldung gelten als Konflikt und blockieren den Import. Ohne Leermeldung bleibt eine fehlende Auswahl fehlerhaft. Für ältere lokale `gf-import.yaml` gilt Feld 10 auch ohne zusätzliche Konfiguration. Die automatisch ergänzten Pflichtteilnahmen bleiben bestehen. Bestehende `{{table.students_view}}`-Platzhalter werden beim GF-Import als `students_table` interpretiert.
