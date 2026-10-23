# MaO-CLI – Dokumentation

MaO-CLI automatisiert die Erstellung von CSV-Listen, LaTeX-Makros und PDF-Dokumenten aus der MySQL-Datenbank zur Mathematik-Olympiade.

**Stand:** `0.4.0.dev0` (Entwicklung vor Version 2, keine Kompatibilitätszusage)

Der Einstieg erfolgt nach der Installation mit `mao`. Alternativ kann aus dem Projektordner `python -m mao_cli` verwendet werden.

## Schnellstart

```bash
python -m pip install -e .
mao profiles
mao validate --workspace "D:/MaO/2026-27"
mao plan --workspace "D:/MaO/2026-27" --profile vollpaket --no-latex
mao run --workspace "D:/MaO/2026-27" --profile vollpaket --no-latex
```

Für den normalen Lauf muss eine gültige `db.config` im Arbeitsordner liegen. Die gedruckten Pläne basieren auf echten Leseabfragen, ändern aber keine CSV- oder PDF-Dateien.

## Seiten

- [Befehle](Befehle) – vollständige Befehle, Parameter, Beispiele, Exit-Codes
- [Konfiguration](Konfiguration) – SQL, YAML, INI, lokale Overrides, Sicherheit
- [Profile](Profile) – Beispielprofile und Ausführungslogik
- [Arbeitsverzeichnisse](Arbeitsverzeichnisse) – Trennung von Anwendung und Daten
- [Fehlerbehebung](Fehlerbehebung) – typische Fehlermeldungen und Lösungen
- [Entwicklung](Entwicklung) – Installation, Tests und Wiki-Veröffentlichung
- [Git-Flow](Git-Flow) – Branches, CI, Releases und Versionspflege
- [Updates](Updates) – stabile Updates, RCs und Develop-Kanal

Änderungen pro Version werden in **`CHANGELOG.md` im Stammverzeichnis des Hauptrepositories** geführt.
