# Entwicklung und Veröffentlichung

## Versionierung

Bis Version 2.0 werden bewusst noch inkompatible Veränderungen vorgenommen. Ab Version 2.0 sollen stabile Aufrufe und Datenformate dokumentiert und bei Updates berücksichtigt werden.

Das Changelog wird als `CHANGELOG.md` **im Hauptrepository** geführt; dort gehört es in jeden Commit, der neue öffentliche Funktionen oder Korrekturen bringt.

## Installation im Entwicklungsmodus

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
mao --version
```

## Wiki veröffentlichen

Die Quelldateien liegen unter `wiki/*.md` auf `develop`. GitHub verwendet für das eigentliche Wiki ein eigenes Repository (`MaO.wiki.git`), daher erscheinen Änderungen im Ordner nicht automatisch auf der Wiki-Website.

Der GitHub-Actions-Workflow `.github/workflows/publish-wiki.yml` veröffentlicht Änderungen im Ordner `wiki/` automatisch nach jedem entsprechenden Push auf `develop`. Er verwendet das eingebaute `GITHUB_TOKEN` mit `contents: write` und das Skript `scripts/publish_wiki.py`. **Zusätzliche, ausschließlich im Wiki angelegte Seiten werden nicht gelöscht.**

**Ersteinrichtung:** In den Repository-Einstellungen muss „Wikis“ aktiviert sein. Beim ersten Mal muss in der GitHub-Wiki-Oberfläche unter <https://github.com/hhgym/MaO/wiki> eine Startseite angelegt werden, damit GitHub das separate Wiki-Repository initialisiert. Danach kann ein fehlgeschlagener Veröffentlichungs-Workflow in GitHub Actions erneut ausgeführt werden.

Alternativ kann die Veröffentlichung lokal mit Git-Schreibrechten erfolgen:

```bash
python scripts/publish_wiki.py --remote https://github.com/hhgym/MaO.wiki.git --dry-run
python scripts/publish_wiki.py --remote https://github.com/hhgym/MaO.wiki.git
```

Die Quelldateien im Hauptrepository bleiben die maßgebliche Dokumentation. Änderungen sollten deshalb dort erfolgen.

## Git-Flow-Repository

Das Hauptrepository enthält CI- und Release-Workflows, `CHANGELOG.md` und die versionierten Wiki-Quelldateien. Detaillierte Anleitung: [Git Flow](Git-Flow). Aktualisierungsbefehl und Kanäle: [Updates](Updates).

## Geplante Erweiterungen

Jahrgangsinitialisierung mit Datenbankarchivierung, Schema-Migrationen, automatisches Rollback und GUI sind **nicht** Teil dieser Vorabversion.
