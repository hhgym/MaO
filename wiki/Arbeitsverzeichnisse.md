# Arbeitsverzeichnisse und Jahrgangs-Wrapper

Das Programm wird einmal installiert; Konfiguration und Dateien eines Schuljahres liegen davon getrennt.

```text
D:/MaO/Config/
  settings.yaml
  pipeline.yaml
  profiles.ini
  tables.ini          # echte SQL-Namen, privat
  db.config           # Zugangsdaten, privat

D:/Schuljahre/2026-27/MaO/
  mao-hier.cmd
  mao-hier.sh
  Listen/
  .mao-cli/exports-manifest.json
  ...                # PDF-/TeX-Ausgaben

E:/Archiv/2025-26/MaO/
  mao-hier.cmd
  Listen/
```

Die `root`-Pfade werden einzeln in `settings.yaml` erfasst. Unterschiedliche Laufwerke sind ausdrücklich erlaubt.

```powershell
mao --config-dir "D:/MaO/Config" workspace add 2026-27 --root "D:/Schuljahre/2026-27/MaO"
mao --config-dir "D:/MaO/Config" workspace add 2025-26 --root "E:/Archiv/2025-26/MaO"
mao --config-dir "D:/MaO/Config" workspace select 2026-27
```

Das Hinzufügen erzeugt standardmäßig einen CMD- und einen Shell-Wrapper. Bereits existierende Wrapper werden **nicht** überschrieben. Mit `--no-wrapper` wird nur registriert; `mao workspace wrapper 2026-27` erzeugt später fehlende Wrapper.

Im Jahrgangsordner können die Befehle mit `mao-hier.cmd validate`, `mao-hier.cmd plan`, `mao-hier.cmd run --profile turniertag` aufgerufen werden. Die CLI liest die zentralen Einstellungen und verwendet den registrierten Pfad.

**Wichtig:** Ein älterer Workspace bedeutet nicht automatisch historische SQL-Daten. Die SQL-Exportbefehle verweigern deshalb den Zugriff auf archivierte registrierte Jahre, solange keine sichere jahrgangsabhängige Datenquelle eingerichtet ist. `validate` und `latex` sind davon nicht betroffen. Die Sperre gilt auch, wenn der Ordner eines archivierten Jahrgangs (oder ein Unterordner davon) per `--workspace` angegeben wird: Ein registrierter Ordner behält immer seinen Jahrgang.
