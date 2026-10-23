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

## Wiki-Quellen und Wiki-Repository

`wiki/*.md` ist der versionierte, maßgebliche Dokumentationsstand im Hauptrepository. Ein GitHub- oder GitLab-Wiki hat normalerweise ein **eigenes Git-Repository**; deshalb erscheinen die Seiten nicht allein dadurch im Wiki, dass der Ordner `wiki/` im Hauptrepo liegt.

Das Projekt enthält ein Veröffentlichungsskript:

```bash
python scripts/publish_wiki.py --remote https://HOST/OWNER/REPO.wiki.git --dry-run
python scripts/publish_wiki.py --remote https://HOST/OWNER/REPO.wiki.git
```

Ersetze die Beispiel-URL durch die tatsächlich angezeigte Git-Clone-URL des Wikis. Das Skript klont das externe Wiki in einen temporären Ordner, aktualisiert nur gleichnamige Seiten, führt `git add`, `git commit` und `git push` aus und verändert das Hauptrepository nicht. Es löscht keine zusätzlichen Wiki-Seiten.

Vorher das Wiki auf der jeweiligen Plattform aktivieren und gegebenenfalls die Startseite einmal initialisieren. Git-Schreibrechte und Benutzeridentität (`git config user.name` und `git config user.email`) müssen vorhanden sein.

## Git-Flow-Repository

Das Hauptrepository enthält CI- und Release-Workflows, `CHANGELOG.md` und die versionierten Wiki-Quelldateien. Detaillierte Anleitung: [Git Flow](Git-Flow). Aktualisierungsbefehl und Kanäle: [Updates](Updates). Ein externes Repository muss noch eingerichtet werden.

## Geplante Erweiterungen

Jahrgangsinitialisierung mit Datenbankarchivierung, Schema-Migrationen, automatisches Rollback und GUI sind **nicht** Teil dieser Vorabversion.
