# MaO aktualisieren – GitHub Releases und Develop-Artefakte

MaO verwendet die GitHub-Releases von [`hhgym/MaO`](https://github.com/hhgym/MaO) und drei Kanäle:

| Kanal | Inhalt | Voraussetzung |
| --- | --- | --- |
| `stable` | Nur finale Releases | Verwaltete lokale Installation (wird beim ersten Installieren erstellt) |
| `rc` | Release Candidates und finale Releases | Wie Stable, ausdrücklich zum Erproben |
| `develop` | Letzter getesteter Commit aus GitHub Actions | GitHub-Zugriff mit `Contents: read` und `Actions: read`, **kein Git-Checkout** |

Das Repository ist **öffentlich**; für die Prüfung von Branch und Releases ist in der Regel kein GitHub-Token nötig. Der Abruf von Develop-Artefakten über die GitHub-Actions-API kann eine Authentifizierung erfordern. Dafür kannst du [GitHub CLI](https://cli.github.com/) installieren und `gh auth login` verwenden oder `MAO_GITHUB_TOKEN` (ersatzweise `GH_TOKEN` oder `GITHUB_TOKEN`) setzen. Die GitHub-Verbindung in ChatGPT ist keine Anmeldung der lokalen CLI. Tokens gehören nie in `db.config`, Git-Dateien oder Screenshots.

## Befehle

```bash
mao update status
mao update check --channel stable
mao update check --channel rc
mao update download --channel rc --output ./downloads
mao update install --channel rc --yes
mao update rollback --yes
mao update check --channel develop
mao update download --channel develop --output ./downloads
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

## Develop-Kanal ohne Git

Auch `develop` wird direkt über GitHub geprüft und installiert, genauso wie die Release-Kanäle. Ein lokales Git-Repository oder `pip install -e` wird **nicht** benötigt.

```powershell
mao update check --channel develop
mao update download --channel develop --output ./downloads
mao update install --channel develop --yes
mao update status
mao update rollback --yes
```

1. `check` liest den Branch-HEAD über die GitHub API und verlangt einen **erfolgreichen GitHub-CI-Lauf genau für diesen Commit** sowie ein nicht abgelaufenes Build-Artefakt.
2. `download` lädt das Actions-Artefakt (Wheel + `.sha256`) und kontrolliert ZIP-Struktur, Prüfsumme sowie die in der Wheel-Version enthaltene Commitkennung.
3. `install` legt eine separate virtuelle Umgebung an, prüft den Programmstart und aktiviert sie atomar. Ein Rollback auf die vorherige verwaltete Version bleibt möglich.

Jedes Develop-Wheel erhält während des CI-Builds eine einmalige Version wie `0.6.0.dev0+gabcdef123456`. Die Zeichen nach `+g` entsprechen den ersten zwölf Stellen der GitHub-Commit-ID. Die offizielle Releaseversion in den Git-Quellen bleibt unverändert.

**Wichtig:** GitHub-Actions-Artefakte sind aktuell **30 Tage** verfügbar. Ein noch nicht fertig gebauter Develop-Commit ist nicht installierbar. Für einen authentifizierten Abruf benötigt das lokale GitHub-Token `Actions: read`.

**Einmalige Umstellung älterer Installationen:** Versionen vor diesem Updater unterstützen nur Git-Checkouts im Kanal `develop`. Um den neuen Updater erstmalig zu installieren, kannst du das getestete Develop-Artefakt einmalig über `gh run download` auf der GitHub-CLI beziehen und das darin enthaltene Wheel mit `python -m pip install <wheel>` installieren. Danach übernimmt `mao update` den gesamten Ablauf.

## Für Maintainer

Der Workflow `.github/workflows/release.yml` baut nach einem Release-Tag auf `main` ein Wheel samt SHA-256-Datei. Ein Tag für Release Candidate 1 könnte `v0.5.0-rc.1` lauten; `pyproject.toml` und `mao_cli/__init__.py` müssen zu diesem Zeitpunkt die Version `0.5.0rc1` tragen. Solange die Entwicklungsversion `0.5.0.dev0` lautet, ist **noch kein** RC-Release erzeugt. Siehe [Git Flow](Git-Flow.md).

Bei `HTTP 404` für das private Repository zuerst `gh auth status` und die Tokenberechtigung prüfen. Ist die `.sha256`-Datei beschädigt, wird vor der Installation abgebrochen. Bei `--yes` fehlend findet keine Installation statt. Mit `mao update status` werden aktive und vorherige Version angezeigt. Fehlermeldungen des Updaters haben CLI-Exitcode `5`.
