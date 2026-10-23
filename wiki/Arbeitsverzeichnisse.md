# Arbeitsverzeichnisse

Programm und Arbeitsdaten werden getrennt gehalten. Das Programm liegt in einem Git-Repository, aber Schülerdaten, Zugangsdaten, Exportdateien und PDFs gehören nicht in dieses Repository.

Beispiel:

```text
MaO-CLI/                         # Git-Repository (Programm)
  mao_cli/
  pyproject.toml
  CHANGELOG.md
  wiki/

D:/MaO/2026-27/                  # Arbeitsordner (nicht in Git)
  db.config
  config/                        # optional: eigene Anpassungen
    profiles.ini
  Listen/
  .mao-cli/
    exports-manifest.json
```

Verwendung:

```bash
mao validate --workspace "D:/MaO/2026-27"
mao plan --workspace "D:/MaO/2026-27" --profile vollpaket
```

Ohne `--workspace` gilt das aktuelle Arbeitsverzeichnis, von dem der Befehl gestartet wird. Relative Pfade bei `--output` und `--log-file` werden auf den gewählten Arbeitsordner bezogen.

**Wichtig:** Andere Arbeitsverzeichnisse ändern nicht automatisch die SQL-Datenbank oder den MaO-Jahrgang. Die `db.config` entscheidet, welche Datenbank abgefragt wird. Die Konfiguration `tables.ini` definiert die verwendeten Tabellen und Views. `--mao` ändert nur die Dokumentnummer.
