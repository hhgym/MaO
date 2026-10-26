# Jahrgangsordner

## Im zentralen MaO-Ordner (Normalfall)

```text
MaO/
  config/                 Einstellungen und Zugangsdaten
  tex/                    LaTeX-Vorlagen
  Jahrgang/
    65_2025-26/           <MaO-Nr>_<Schuljahr>
    66_2026-27/
      Listen/ ...         Exporte und PDFs
```

Jeder Ordner `Jahrgang/<Nr>_<Schuljahr>` wird **automatisch erkannt**; eine Registrierung ist nicht nötig. Der aktuelle Jahrgang steht als `current_year` in `settings.yaml`, alle anderen gelten als archiviert (SQL-Exporte gesperrt). Wer in einem Jahrgangsordner arbeitet, meint diesen Jahrgang.

```powershell
mao jahrgang list                     # erkannte Jahrgänge
mao jahrgang new                      # aktiver Jahrgang aus der Datenbank: Ordner anlegen und als aktuell setzen
mao jahrgang new --nr 67 --year 2027-28 [--current]   # ohne Datenbank
mao jahrgang use 66                   # aktuellen Jahrgang wählen (MaO-Nr oder Schuljahr)
```

`mao jahrgang new` ohne Angaben liest nur: das als aktiv markierte Schuljahr und dessen MaO-Nummer (`jahrgaenge_table`, `schuljahre_table` in `tables.ini`). Fehlt dort der Jahrgang, nennt der Befehl einen Vorschlag für `--nr`/`--year`; das Anlegen in der Datenbank gehört zum Jahrgangswechsel und geschieht nicht hier.

## Sonderfall: Ordner außerhalb von MaO/Jahrgang/

Das Programm wird einmal installiert; Konfiguration und Dateien eines Schuljahres liegen davon getrennt. Ordner an beliebiger Stelle (auch auf anderen Laufwerken) werden mit `mao workspace add` registriert und absolut gespeichert; ein ausdrücklich registrierter Ordner hat Vorrang vor der Erkennung. Wrapper `mao-hier.cmd/.sh` mit festem Konfigurationspfad entstehen nur mit `--wrapper`.

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

Das Hinzufügen erzeugt nur mit `--wrapper` einen CMD- und einen Shell-Wrapper. Bereits existierende Wrapper werden **nicht** überschrieben; `mao workspace wrapper 2026-27` erzeugt sie später.

Im Jahrgangsordner können die Befehle mit `mao-hier.cmd validate`, `mao-hier.cmd plan`, `mao-hier.cmd run --profile turniertag` aufgerufen werden. Die CLI liest die zentralen Einstellungen und verwendet den registrierten Pfad.

**Wichtig:** Ein älterer Workspace bedeutet nicht automatisch historische SQL-Daten. Die SQL-Exportbefehle verweigern deshalb den Zugriff auf archivierte registrierte Jahre, solange keine sichere jahrgangsabhängige Datenquelle eingerichtet ist. `validate` und `latex` sind davon nicht betroffen. Die Sperre gilt auch, wenn der Ordner eines archivierten Jahrgangs (oder ein Unterordner davon) per `--workspace` angegeben wird: Ein registrierter Ordner behält immer seinen Jahrgang.
