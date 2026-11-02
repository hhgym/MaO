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

Die angegebene `.tex`-Datei muss bereits im Vorlagenordner `MaO/tex/<Dokument>/` existieren. Die Anwendung liefert nur das Python-Programm und die Pipeline-Konfiguration, nicht sämtliche schulischen TeX-Vorlagen.

## Veraltete Ausgabedateien

Der Export bereinigt nur manifestgeführte Dateien, deren Erzeuger zum aktuell gewählten Profil gehört. Manuell geänderte Dateien werden aus Sicherheitsgründen nicht automatisch gelöscht. `mao plan` zeigt die geplanten Aktionen vorher an.

## Wiki wird nach Commit nicht aktualisiert

Der Workflow `.github/workflows/publish-wiki.yml` synchronisiert Änderungen unter `wiki/` nach entsprechenden Pushes auf `develop`. Prüfe den Status in **Actions → Wiki veröffentlichen**. Bei der erstmaligen Einrichtung muss im GitHub-Wiki eine Seite (z. B. `Home`) angelegt sein, damit das separate `.wiki.git`-Repository existiert. Eine lokale Veröffentlichung über `scripts/publish_wiki.py` bleibt möglich.


## „CERTIFICATE_VERIFY_FAILED" beim Schulabgleich oder bei `mao update`

Ursache ist meist ein Server, der Zwischenzertifikate nicht mitliefert, oder ein Netzfilter (Schulnetz, Virenscanner), der Zertifikate austauscht. Browser lösen das über Windows, Python bisher nicht.

1. MaO aktualisieren (`uv tool install --force --reinstall git+https://github.com/hhgym/MaO@develop`): neuere Versionen nutzen den Zertifikatspeicher von Windows.
2. Hilft das nicht: ein Zertifikatsbündel (`.pem`, z. B. von der IT) angeben: `$env:MAO_CA_BUNDLE = "C:\\Pfad\\bundle.pem"`.
3. Für den Schulabgleich zusätzlich möglich: die Adresse aus `mao schulen-sync --help` im Browser öffnen, die Seite als Datei speichern und mit `mao schulen-sync --file schulen.json` einlesen.

Die Zertifikatsprüfung wird nie abgeschaltet.
