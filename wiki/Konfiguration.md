# Zentrale Konfiguration

MaO verwaltet **alle** Einstellungen in genau einem privaten Konfigurationsordner. Er wird über den globalen Schalter `--config-dir`, danach über `MAO_CONFIG` und schließlich über das Betriebssystem-Standardverzeichnis gefunden.

```powershell
mao --config-dir "D:/MaO/Config" config init
$env:MAO_CONFIG = "D:/MaO/Config"
mao config show
```

`config init` erzeugt `settings.yaml`, `pipeline.yaml`, `profiles.ini`, `tables.ini` und `db.config.example`, sofern sie noch nicht vorhanden sind. Bestehende Dateien werden nicht überschrieben. `db.config` mit echten Zugangsdaten muss man selbst anlegen.

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

Mit `mao config init` werden zusätzlich `gf.config.example` und
`gf-import.yaml.example` angelegt. Beide Vorlagen in derselben zentralen
Konfiguration als `gf.config` und `gf-import.yaml` kopieren und privat anpassen.
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

- `queries.students`: Schülerstammdaten mit `matrikelnummer`, `vorname`, `nachname`, `geschlecht`, `klasse` des aktuellen Schuljahres.
- `queries.mandatory`: aktuelle **Mathematik-Lerngruppen** mit `klasse` (Alias für `klassenname`), `lehrkraft_id` und `lerngruppe_schuljahr_id`. Die View enthält auch andere Lerngruppen; die SELECT-Abfrage muss auf Mathematik eingeschränkt werden. Pro Pflichtklasse genau eine eindeutige Lehrkraft.
- `queries.teachers`: gültige `id`-Werte der Lehrkräfte; Formularfeld `3` enthält bereits diese ID und wird nur geprüft.
- `queries.courses`: die Spalten `lerngruppe_schuljahr_id`, `lehrkraft_id`, `lerngruppen_einsatz_id` aus der erweiterten Lerngruppen-View. Das Paar aus den GF-Feldern `5` und `3` muss genau einen Einsatz ergeben.

**Achtung: unterschiedliche IDs.** Gravity Forms liefert in Feld `5` die
`lerngruppe_schuljahr_id` und in Feld `3` die `lehrkraft_id`. Die bereits
vorhandene Spalte `MaO_Orga_2.kurs_id` verweist jedoch weiterhin auf
`orga_lerngruppen_einsatz.id`. Die erweiterte aktuelle Lerngruppen-View
liefert dafür `lerngruppen_einsatz_id`; diese wird aus **beiden GF-IDs**
eindeutig ermittelt. Stimmen die Zuordnungen nicht oder gibt es mehrere
Einsätze, wird der Import abgebrochen. Datenbankschema und bisherige Views
bleiben unverändert.

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

### Lerngruppen-/Einsatzkennungen beim GF-Import

`kurs_id` in der Organisationstabelle bleibt aus Kompatibilitätsgründen mit bestehenden
Views und Fremdschlüsseln erhalten und referenziert die ID aus
der Einsatz-Tabelle. Das GF-Feld 5 liefert dagegen
`lerngruppe_schuljahr_id`, Feld 3 die `lehrkraft_id`. Die private SQL-Quelle
`queries.courses` muss daher **drei Spalten** liefern:
`lerngruppe_schuljahr_id`, `lehrkraft_id` und `lerngruppen_einsatz_id`.
Die erweiterte View des aktuellen Schuljahres stellt diese bereit.
Das Skript ordnet anhand **beider** GF-IDs genau eine Einsatz-ID zu.
Gleiche Zahlenwerte aus unterschiedlichen Tabellen dürfen nicht als
identische Primärschlüssel interpretiert werden.
