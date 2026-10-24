# Git Flow, Releases und CI

## Branches

- `main`: veröffentlichte und getaggte Versionen.
- `develop`: Entwicklung mit grünen CI-Tests; kein produktiver Releasekanal.
- `feature/*`: Funktionsentwicklung → Pull Request nach `develop`.
- `release/*`: Version einfrieren, Dokumentation/Version/Changelog aktualisieren → PR nach `main` → Release-Tag auf Main-Commit → `main` wieder nach `develop` synchronisieren.
- `hotfix/*`: vom aktuellen `main`; Fix nach `main` und `develop` übernehmen.

## Repository erstmalig einrichten

```bash
git init -b main
git add .
git commit -m "Initialer Git-Flow-Stand"
git branch develop
git remote add origin <GITHUB-CLONE-URL>
git push -u origin main
git push -u origin develop
git switch develop
```

Nur das neu erstellte Remote des Projekts verwenden. `db.config`, Schülerdaten, Exporte und `.mao-cli` gehören **niemals** ins Git-Repository. Empfohlen: Branchschutz für `main` und `develop`, verpflichtende PRs und bestandene Checks; Schreibrechte auf Release-Tags einschränken. Der Code in diesem Paket erstellt/öffnet kein externes GitHub-Repository.

## Feature und Release

```bash
git switch develop
git switch -c feature/jahrgang-init
# entwickeln, testen und committen; PR gegen develop erstellen

git switch develop
git switch -c release/0.4.0
# Versionsnummer, README, Wiki und CHANGELOG.md aktualisieren
python scripts/check_release.py v0.4.0
python -m pytest -q
# Release-PR nach main erstellen, mergen; dann main auschecken:
git switch main
git pull --ff-only
git tag -a v0.4.0 -m "MaO-CLI 0.4.0"
git push origin v0.4.0
# main anschließend per PR nach develop synchronisieren
```

**Achtung:** Das Beispieltag `v0.4.0` funktioniert erst, nachdem die Versionsnummer entsprechend angepasst ist. Der aktuelle Entwicklungsstand ist `0.6.0.dev0`. Vor Version 2.0 werden Veränderungen gegebenenfalls ohne Migration und Kompatibilitätszusage vorgenommen.

## GitHub Actions

- `.github/workflows/publish-wiki.yml`: Synchronisiert `wiki/*.md` bei Änderungen auf `develop` ins GitHub-Wiki (separates Wiki-Repository).
- `.github/workflows/ci.yml`: Tests mit Python 3.10 und 3.13, Konfigurationsvalidierung, bei erfolgreichen Pushes nach `develop` zusätzlich Wheel und Prüfsumme als CI-Artefakt.
- `.github/workflows/release.yml`: Beim Push eines `v*`-Tags prüft der Workflow zunächst, ob der Commit auf `main` enthalten ist und ob die Version exakt passt; danach Tests, Build, `.sha256` und GitHub-Release. RC-/Development-Tags erscheinen als Prerelease.

Das `develop`-Update im CLI verwendet **nur einen getesteten Git-Commit**. CI-Artefakte stehen zusätzlich für Testzwecke bereit, werden aber nicht automatisch als Release installiert.

## Versionspflege

`pyproject.toml` enthält die Paketversion und `mao_cli/__init__.py` die Laufzeitversion. Beide müssen übereinstimmen; der Release-Workflow prüft die Paketversion und den Git-Tag, die Regressionstests prüfen die beiden Python-Versionswerte. Das Changelog liegt im **Hauptrepo** unter `CHANGELOG.md`.

## Wiki

Die Markdown-Quellen im Ordner `wiki/` werden mitentwickelt; Veröffentlichung im separaten GitHub-Wiki mit:

```bash
python scripts/publish_wiki.py --remote https://github.com/OWNER/REPO.wiki.git --dry-run
python scripts/publish_wiki.py --remote https://github.com/OWNER/REPO.wiki.git
```

Die Quelle im Hauptrepo bleibt maßgeblich. Das Wiki sollte nach erfolgreichem Merge/Release veröffentlicht werden.
