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
