# MaO aktualisieren – v0.5

MaO verwendet die GitHub-Releases von [`hhgym/MaO`](https://github.com/hhgym/MaO) und drei Kanäle:

| Kanal | Inhalt | Voraussetzung |
| --- | --- | --- |
| `stable` | Nur finale Releases | Verwaltete lokale Installation (wird beim ersten Installieren erstellt) |
| `rc` | Release Candidates und finale Releases | Wie Stable, ausdrücklich zum Erproben |
| `develop` | Letzter erfolgreicher CI-Stand von `origin/develop` | Sauberer Git-Checkout auf `develop`, editable Installation |

Für das **private** Repository benötigt die *lokale* Anwendung ein GitHub-Token mit `Contents: read`. Die GitHub-Verbindung innerhalb von ChatGPT ist **nicht automatisch** eine Anmeldung der lokalen CLI. Bevorzugt kannst du [GitHub CLI](https://cli.github.com/) installieren und `gh auth login` ausführen. Alternativ wird `MAO_GITHUB_TOKEN` unterstützt (danach `GH_TOKEN` und `GITHUB_TOKEN`). Tokens niemals in `db.config`, Git-Dateien, Befehlsargumente oder Screenshots schreiben. Fehlermeldungen enthalten keine Tokenwerte.

## Befehle

```bash
mao update status
mao update check --channel stable
mao update check --channel rc
mao update download --channel rc --output ./downloads
mao update install --channel rc --yes
mao update rollback --yes
mao update check --channel develop
mao update install --channel develop --yes
```

Als Repository wird `hhgym/MaO` benutzt. `--repository OWNER/REPO` oder `MAO_GITHUB_REPO` sind nur für abweichende GitHub-Projekte erforderlich. `--home PFAD` beziehungsweise `MAO_UPDATE_HOME` bestimmen das Verzeichnis der verwalteten Versionen.

## Release-Installation und Starter

`mao update install --channel rc --yes` lädt ein Release-Wheel über die authentifizierte GitHub-Asset-API und überprüft die mit dem Release veröffentlichte `.sha256`-Datei. Die neue Version wird in einer **eigenen virtuellen Python-Umgebung** installiert und vor Aktivierung gestartet und geprüft. Erst bei Erfolg ersetzt das Programm die Datei `active.json` atomar. Die vorherige Umgebung bleibt erhalten und kann mit `mao update rollback --yes` reaktiviert werden.

Die erste Installation erzeugt einen **Starter**, den du künftig für den normalen Programmstart verwenden solltest:

- **Windows:** `%LOCALAPPDATA%\MaO\bin\mao.cmd`
- **Linux:** `~/.local/share/mao/bin/mao`

Der tatsächliche Pfad wird nach der Installation ausgegeben. Der Starter ist vom aktuellen Python-Installationsverzeichnis unabhängig und ruft immer die in `active.json` ausgewählte Version auf. Optional kannst du diesen `bin`-Ordner zur PATH-Umgebungsvariable hinzufügen. Ohne diesen Schritt gilt das Umschalten nur für den ausgegebenen Starter; eine bestehende `mao`-Datei aus `pip install -e .` wird **nicht** automatisch ersetzt.

**Rollback:** Wechsel zur bisherigen Version mit `mao update rollback --yes`. Der Befehl benötigt keine Netzwerkverbindung und verändert keine Arbeitsdaten. Bei Fehlstart eines neuen Wheels wird die Version nicht aktiviert. Bereits laufende Prozesse benutzen bis zum Neustart ihre ursprüngliche Version. Die beiden zuletzt verwendeten Umgebungen werden aufbewahrt; es findet kein automatisches Löschen älterer Installationen statt. Beim Ausfall des Starters kann der vorherige Interpreter auch direkt gestartet werden.

**Grenzen:** Eine selbst modifizierte Python-Installation, ein beschädigtes Dateisystem oder inkompatible Datenbankmigrationen sind nicht durch den Versionswechsel abgesichert. Bis Version 2.0 gibt es noch keine automatischen DB-Migrationen. SHA-256 sichert die Dateiintegrität relativ zur ebenfalls auf GitHub veröffentlichten Prüfsumme, ist **keine unabhängige kryptografische Signatur**. `pip` kann beim Einrichten einer neuen Umgebung Pakete von den konfigurierten Paketindizes beziehen.

## Develop-Kanal

```bash
git clone https://github.com/hhgym/MaO.git
cd MaO
git switch develop
python -m pip install -e '.[dev]'
mao update check --channel develop
mao update install --channel develop --yes
```

Develop benutzt `git fetch` und nur Fast-Forward, benötigt einen sauberen Checkout sowie erfolgreichen GitHub-CI-Lauf für das Ziel. Bei Fehlern beim aktualisierenden `pip install -e` versucht der Befehl, den vorherigen Commit und die editable Installation wiederherzustellen. Das ist ein *Best-Effort*-Rollback und **nicht** mit dem atomischen Release-Rollback vergleichbar.

## Für Maintainer

Der Workflow `.github/workflows/release.yml` baut nach einem Release-Tag auf `main` ein Wheel samt SHA-256-Datei. Ein Tag für Release Candidate 1 könnte `v0.5.0-rc.1` lauten; `pyproject.toml` und `mao_cli/__init__.py` müssen zu diesem Zeitpunkt die Version `0.5.0rc1` tragen. Solange die Entwicklungsversion `0.5.0.dev0` lautet, ist **noch kein** RC-Release erzeugt. Siehe [Git Flow](Git-Flow.md).

Bei `HTTP 404` für das private Repository zuerst `gh auth status` und die Tokenberechtigung prüfen. Ist die `.sha256`-Datei beschädigt, wird vor der Installation abgebrochen. Bei `--yes` fehlend findet keine Installation statt. Mit `mao update status` werden aktive und vorherige Version angezeigt. Fehlermeldungen des Updaters haben CLI-Exitcode `5`.
