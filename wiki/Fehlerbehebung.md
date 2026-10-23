# Fehlerbehebung

## `mao: command not found` / Befehl nicht gefunden

Installiere das Paket im aktiven Python-Environment:

```bash
python -m pip install -e .
```

Als Alternative funktioniert aus dem Projektordner `python -m mao_cli --help`.

## `db.config nicht gefunden`

Die Datei muss im mit `--workspace` angegebenen Ordner liegen. Die Datei gehört nicht ins Git-Repository. `mao validate` und `mao profiles` funktionieren ohne `db.config`.

## Ungültiges Exportmanifest

Dieses Entwicklungspaket verwendet ausschließlich **Schema 2**. Altes Manifest aus früheren Entwicklungsversionen wird nicht migriert. Vorhandene `Listen/` sichern und mit einem separaten neuen Arbeitsordner beginnen oder das alte Manifest bewusst entfernen. Manuell vorhandene Dateien werden dann nicht als vom Programm verwaltet erkannt und nicht automatisch bereinigt.

## SQL-Spalte fehlt / View unbekannt

`mao validate` kontrolliert nur die Konfiguration. `mao plan` führt die benötigten SQL-Abfragen aus und zeigt daher Fehler in echten Views oder Spalten. Tabellen- und Viewnamen in `config/tables.ini` prüfen.

## LaTeX-Job übersprungen

Die angegebene `.tex`-Datei muss bereits im korrekten Unterordner `Listen/` existieren. Die Anwendung liefert nur das Python-Programm und die Pipeline-Konfiguration, nicht sämtliche schulischen TeX-Vorlagen.

## Veraltete Ausgabedateien

Der Export bereinigt nur manifestgeführte Dateien, deren Erzeuger zum aktuell gewählten Profil gehört. Manuell geänderte Dateien werden aus Sicherheitsgründen nicht automatisch gelöscht. `mao plan` zeigt die geplanten Aktionen vorher an.

## Wiki wird nach Commit nicht aktualisiert

Die Markdown-Seiten im Hauptrepository sind **nicht** automatisch identisch mit dem Git-Wiki. Führe die Veröffentlichung über `scripts/publish_wiki.py` aus; dazu muss das separate Wiki-Repository existieren und beschreibbar sein.
