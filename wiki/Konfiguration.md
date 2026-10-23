# Konfiguration

MaO-CLI hat drei versionierte Standarddateien unter `mao_cli/defaults/`:

| Datei | Format | Aufgabe |
|---|---|---|
| `pipeline.yaml` | YAML | SQL-Abfragen, Datensätze, Transformationen, Ausgabedateien, LaTeX-Jobs |
| `tables.ini` | INI | Zuordnung zentraler SQL-Tabellen und Views |
| `profiles.ini` | INI | Auswahl von Export- und LaTeX-IDs für Aufgabenprofile |

## Lokale Konfiguration

Wird im Arbeitsordner `config/pipeline.yaml`, `config/tables.ini` oder `config/profiles.ini` gefunden, überschreibt diese Datei **vollständig** die entsprechende mitgelieferte Standarddatei. Es gibt kein automatisches Zusammenführen einzelner Abschnitte. Fehlt eine lokale Datei, wird die mitgelieferte verwendet.

```text
Arbeitsordner/
  db.config                 # nur lokal, niemals in Git!
  config/
    profiles.ini            # optional
    tables.ini              # erforderlich für Datenbankläufe, nur lokal
    pipeline.yaml           # optional
  Listen/
  .mao-cli/
```

## Datenbank-Zugangsdaten

`db.config` verwendet das vorhandene `key=value`-Format:

```ini
host=localhost
port=3306
user=ANWENDER
password=HIER_NICHT_EIN_ECHTES_PASSWORT_EINTRAGEN
database=DATENBANK
```

Diese Darstellung ist nur ein **Muster ohne funktionierende Zugangsdaten**. Produktive Zugangsdaten nie veröffentlichen.

## Tabellenplatzhalter

`tables.ini` enthält eine Sektion `[table]`. Namen sind auf MySQL-Identifier mit Buchstaben, Ziffern und Unterstrich beschränkt, z. B.:

```ini
[table]
orga_table = DEMO_ORGA
```

SQL in `pipeline.yaml` referenziert diese Einträge als `{{table.orga_table}}`:

```yaml
- name: orga
  enabled: true
  sql: "SELECT * FROM {{table.orga_table}}"
```

Die Ersetzung ist nur für Tabellennamen und Views gedacht. **Datenwerte gehören nicht in solche Platzhalter.** In `pipeline.yaml` sind SQL-Abfragen weiterhin reine Leseabfragen.

## Eindeutige Kennungen

Jeder Export benötigt eine eindeutige `id` und jeder LaTeX-Job ebenfalls. Die `name`-Felder sind zur Anzeige vorgesehen und dürfen von der ID abweichen. Profile referenzieren **IDs**, nicht Anzeigenamen. Datasets werden wie bisher über eindeutige `name`-Felder referenziert.

## Validierung

```bash
mao validate --workspace "D:/MaO/2026-27"
```

Die Validierung ist offline. Ob Views erreichbar sind und SQL-Ergebnisse die erwarteten Spalten enthalten, wird erst bei `mao plan` oder `mao run` geprüft.


## Schutz der tatsächlichen SQL-Objektnamen

Die versionierte `mao_cli/defaults/tables.ini` enthält ausschließlich künstliche `DEMO_`-Namen. Erstelle im Arbeitsordner `config/tables.ini` eine Kopie und ersetze dort alle Werte durch die echten Tabellen- und Viewnamen. Auch die beiden für die MaO-Jahrgangsermittlung benötigten Tabellen `jahrgaenge_table` und `schuljahre_table` werden ausschließlich hier konfiguriert.

Die lokale Datei ist in `.gitignore` ausdrücklich ausgenommen und darf **nicht** in das Repository gelangen. `mao validate` überprüft die öffentlichen Musterwerte; `mao plan` und `mao run` verweigern Datenbankläufe, solange diese Musterwerte verwendet werden. `mao latex` ist davon nicht betroffen. Eine `.gitignore` beseitigt **keine** historischen Commits. Vor einer Veröffentlichung muss die gesamte Historie geprüft und bereinigt bzw. das Repository mit einer frischen Historie neu erstellt werden.
