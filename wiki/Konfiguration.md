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

`gf-import.yaml` legt `form_id`, `fields` und die vier (bei deaktivierter
Pflichtteilnahme drei) SQL-Lookups fest:

- `queries.students`: `matrikelnummer`, `vorname`, `nachname`, `geschlecht`, `klasse`.
- `queries.mandatory`: dieselben Felder plus `lehrkraft_token`; erforderlich bei `mandatory_classes`.
- `queries.teachers`: `id`, `token` mit der zugehörigen Kennung aus dem GF-Feld `lehrkraft`.
- `queries.courses`: `id`, `token` mit dem passenden Wert aus dem GF-Feld `kurs`.

Die beiden ID-Abfragen müssen die tatsächlichen Fremdschlüsselziele der lokalen
Datenbank berücksichtigen; mehrdeutige Tokens werden abgewiesen. `course` wird
für die Jahrgangsstufen 11/12 verwendet. Die SQL-Definitionen werden **nicht**
mit dem Programm ausgeliefert; sie sind an das Schul-Datenbankschema anzupassen.

Die API wird paginiert per OAuth 1.0/HMAC-SHA1 über HTTPS abgefragt. Für den
Schreibmodus wird ausschließlich der aktuelle Workspace akzeptiert; auf
bestehende Ergebnisdaten wird nicht schreibend zugegriffen.
