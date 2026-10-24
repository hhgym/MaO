# MaO aktualisieren – öffentliche GitHub Releases und Develop-Builds

MaO verwendet die GitHub-Releases von [`hhgym/MaO`](https://github.com/hhgym/MaO) und drei Kanäle:

| Kanal | Inhalt | Voraussetzung |
| --- | --- | --- |
| `stable` | Nur finale Releases | Editable-Quellinstallation oder verwaltete Installation |
| `rc` | Release Candidates und finale Releases | Wie Stable, ausdrücklich zum Erproben |
| `develop` | Letzter getesteter Commit aus GitHub Actions | Öffentliche GitHub-Release-Dateien, **kein Token und kein Git-Checkout** |

Das Repository ist **öffentlich**. Die CLI fragt GitHub grundsätzlich **ohne Anmeldung** ab. Auch vorhandene, abgelaufene `MAO_GITHUB_TOKEN`-, `GH_TOKEN`- oder `GITHUB_TOKEN`-Werte werden dabei ignoriert. Develop-Wheels werden nach erfolgreichem Testlauf unter dem öffentlichen [Develop-Release](https://github.com/hhgym/MaO/releases/tag/develop-build) bereitgestellt. Die GitHub-CLI ist nicht erforderlich. Der im CI-Workflow benutzte `GITHUB_TOKEN` wird ausschließlich von GitHub Actions für die Veröffentlichung verwendet, niemals vom lokal installierten MaO.


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

## Editable-Installation (`pip install -e .`): Updates im Quellordner

Wenn `mao` durch `python -m pip install -e .` installiert wurde, erkennt die CLI
an den **PEP-660-Installationsmetadaten**, woher ihr tatsächlich geladener Code
stammt. In diesem Fall aktualisiert `mao update install --yes --channel develop`
**direkt den bisherigen Projektordner**, z. B. `D:\MaO\mao_cli` und
`D:\MaO\pyproject.toml`. Der bereits bestehende `mao.exe` im Python-Scripts-
Verzeichnis bleibt im PATH und lädt beim nächsten Start den neuen Quellcode.
`D:\MaO` selbst muss **nicht** zum PATH hinzugefügt werden.

Die Updates stammen weiterhin aus dem öffentlich heruntergeladenen und mit
SHA-256 geprüften Wheel. Die korrespondierende `pyproject.toml` wird aus dem
**exakt gleichen GitHub-Commit** gelesen und mit dem Wheel-Versionsstempel
versehen. Vor dem Austausch werden die neuen Module per isoliertem Python-Start
geprüft. Die vorherige Version wird samt Projektdatei unter
`D:\MaO\.mao-updates\<Sicherungs-ID>\` gesichert. Nach dem Austausch erfolgt
eine erneute Selbstprüfung; bei Fehlern wird unmittelbar zurückgestellt.
`mao update rollback --yes` tauscht die letzte Sicherung wieder ein.

`mao update status` zeigt in dieser Installationsart den tatsächlichen
Projektpfad und den Editable-Rollback an – nicht den Status einer daneben
existierenden verwalteten Umgebung. Bereits vorhandene `.mao-updates`
Sicherungen werden beim nächsten Update durch Dateihashes gegen nachträgliche
Änderungen geprüft; in solchen Fällen wird aus Sicherheitsgründen abgebrochen.
Beim **ersten** Update eines älteren, nicht versionierten ZIP-Quellordners sind
individuelle Änderungen nicht sicher von früheren Programmständen zu
unterscheiden. Deshalb empfiehlt sich vorher eine separate Sicherung;
`--yes` autorisiert den Austausch, und MaO erstellt automatisch die
Rollback-Kopie.

Ändern sich gegenüber der aktuellen Installation Abhängigkeiten, Entry-Points
oder das Build-System, wird das automatische Editable-Update gestoppt. In
diesem Fall muss einmal kontrolliert `python -m pip install -e .` im
Projektordner ausgeführt werden. Auf Windows wäre die automatische
Deinstallation/Aktualisierung der **gerade laufenden** `mao.exe` per pip nicht
zuverlässig. Da beim regulären In-place-Update pip nicht ausgeführt wird,
kann `pip show mao-cli` bis zur nächsten erneuten pip-Installation noch die
vorherige Paketmetadaten-Version nennen. `mao --version` und `mao update status`
zeigen den tatsächlich gestarteten Code.

**Einmalige Aktivierung bei vorhandener älterer Editable-Installation:** Die
bereits installierte alte CLI kennt diesen Updateweg noch nicht. Um ihn zu
aktivieren, die Dateien aus dem neuen `develop`-Quell-ZIP **einmalig** in den
bestehenden MaO-Projektordner übernehmen und gegebenenfalls dort
`python -m pip install -e .` erneut ausführen. Erst danach verwaltet
`mao update install` künftige Updates unmittelbar am Quellort. Die zentrale
Konfiguration unter `MAO_CONFIG` bleibt unberührt. Alternativ bleibt die
separate verwaltete Installation nutzbar, wenn MaO nicht editable installiert
wurde.

## Release-Installation und Starter

`mao update install --channel rc --yes` lädt ein Release-Wheel über den öffentlichen GitHub-Release-Downloadlink und überprüft die mit dem Release veröffentlichte `.sha256`-Datei. Die neue Version wird in einer **eigenen virtuellen Python-Umgebung** installiert und vor Aktivierung gestartet und geprüft. Erst bei Erfolg ersetzt das Programm die Datei `active.json` atomar. Die vorherige Umgebung bleibt erhalten und kann mit `mao update rollback --yes` reaktiviert werden.

Die erste Installation erzeugt einen **Starter**, den du künftig für den normalen Programmstart verwenden solltest:

- **Windows:** `%LOCALAPPDATA%\MaO\bin\mao.cmd`
- **Linux:** `~/.local/share/mao/bin/mao`

Der tatsächliche Pfad wird nach der Installation ausgegeben. Der Starter ist vom aktuellen Python-Installationsverzeichnis unabhängig und ruft immer die in `active.json` ausgewählte Version auf. Optional kannst du diesen `bin`-Ordner zur PATH-Umgebungsvariable hinzufügen. Ohne diesen Schritt gilt das Umschalten nur für den ausgegebenen Starter; eine bestehende `mao`-Datei aus `pip install -e .` wird **nicht** automatisch ersetzt.

**Rollback:** Wechsel zur bisherigen Version mit `mao update rollback --yes`. Der Befehl benötigt keine Netzwerkverbindung und verändert keine Arbeitsdaten. Bei Fehlstart eines neuen Wheels wird die Version nicht aktiviert. Bereits laufende Prozesse benutzen bis zum Neustart ihre ursprüngliche Version. Die beiden zuletzt verwendeten Umgebungen werden aufbewahrt; es findet kein automatisches Löschen älterer Installationen statt. Beim Ausfall des Starters kann der vorherige Interpreter auch direkt gestartet werden.

**Grenzen:** Eine selbst modifizierte Python-Installation, ein beschädigtes Dateisystem oder inkompatible Datenbankmigrationen sind nicht durch den Versionswechsel abgesichert. Bis Version 2.0 gibt es noch keine automatischen DB-Migrationen. SHA-256 sichert die Dateiintegrität relativ zur ebenfalls auf GitHub veröffentlichten Prüfsumme, ist **keine unabhängige kryptografische Signatur**. `pip` kann beim Einrichten einer neuen Umgebung Pakete von den konfigurierten Paketindizes beziehen.

## Develop-Kanal ohne Git

Auch `develop` wird direkt über GitHub geprüft. Ein lokales Git-Repository wird nicht benötigt. Ist das gestartete Paket eine Editable-Installation, erfolgt der Austausch direkt im Quellordner; ansonsten gilt der verwaltete Starter.

```powershell
mao update check --channel develop
mao update download --channel develop --output ./downloads
mao update install --channel develop --yes
mao update status
mao update rollback --yes
```

1. `check` liest den Branch-HEAD über die GitHub API und verlangt einen **erfolgreichen GitHub-CI-Lauf genau für diesen Commit** sowie ein öffentlich veröffentlichtes, zu diesem Commit gehörendes Wheel und dessen `.sha256`-Datei.
2. `download` lädt das öffentliche Release-Wheel und seine `.sha256`-Datei und kontrolliert Prüfsumme sowie die in der Wheel-Version enthaltene Commitkennung.
3. `install` tauscht bei `pip install -e .` den gesicherten Quellordner aus; bei normalen Wheel-Installationen legt es weiterhin eine separate virtuelle Umgebung an. Beide Varianten besitzen ihren jeweiligen Rollback.

Jedes Develop-Wheel erhält während des CI-Builds eine einmalige Version wie `0.6.0.dev0+gabcdef123456`. Die Zeichen nach `+g` entsprechen den ersten zwölf Stellen der GitHub-Commit-ID. Die offizielle Releaseversion in den Git-Quellen bleibt unverändert.

**Direkt aus einem GitHub-ZIP installiert?** Ein heruntergeladenes Branch-ZIP enthält keine verlässliche Commit-Kennung. Daher vergleicht `mao update check --channel develop` bei gleicher Basisversion die tatsächlichen Programmdateien der aktuellen Installation mit dem SHA-256-geprüften öffentlichen Develop-Wheel. Die Version wird dabei für den Vergleich normalisiert. Bei identischem Quellcode meldet `check` **aktuell**, selbst wenn die lokale `--version` noch `0.6.0.dev0` lautet. Das erfordert einen kleinen Download während der Prüfung, verändert aber keine Arbeitsdaten. Bei unterschiedlichen Dateien ist ein Update verfügbar. Für eine aktivierte verwaltete Version mit Commitkennung genügt weiterhin der SHA-Vergleich.

Die Selbstprüfung bei `install` verwendet `python -I`, damit ein `PYTHONPATH` aus einem entpackten GitHub-ZIP nicht versehentlich dessen veralteten Quellcode statt der neu installierten Version importiert. Bei Fehlern zeigt die CLI jetzt den Exitcode und einen gekürzten diagnostischen Auszug; der bisherige aktive Zustand bleibt unverändert.

**Wichtig:** Develop-Downloads werden im rollierenden GitHub-Release `develop-build` als commitbezogene Assets veröffentlicht. Ein noch nicht fertig getesteter oder nicht erfolgreich veröffentlichter Develop-Commit ist nicht installierbar. Für einen öffentlichen Download ist keine GitHub-Authentifizierung nötig. Die separaten GitHub-Actions-Artefakte bleiben für CI-Zwecke bestehen.

**Einmalige Umstellung älterer Installationen:** Versionen vor diesem Updater unterstützen nur Git-Checkouts im Kanal `develop`. Um den neuen Updater erstmalig zu installieren, kannst du das öffentliche Develop-Wheel aus dem [GitHub-Release `develop-build`](https://github.com/hhgym/MaO/releases/tag/develop-build) einmalig mit `python -m pip install --force-reinstall --no-deps <wheel>` installieren. Danach übernimmt `mao update` den gesamten Ablauf.

## Für Maintainer

Der Workflow `.github/workflows/release.yml` baut nach einem Release-Tag auf `main` ein Wheel samt SHA-256-Datei. Ein Tag für Release Candidate 1 könnte `v0.5.0-rc.1` lauten; `pyproject.toml` und `mao_cli/__init__.py` müssen zu diesem Zeitpunkt die Version `0.5.0rc1` tragen. Solange die Entwicklungsversion `0.5.0.dev0` lautet, ist **noch kein** RC-Release erzeugt. Siehe [Git Flow](Git-Flow.md).

Bei `HTTP 404` bitte öffentliche GitHub-Release-Dateien und die CI-Veröffentlichung überprüfen. Ist die `.sha256`-Datei beschädigt, wird vor der Installation abgebrochen. Bei `--yes` fehlend findet keine Installation statt. Mit `mao update status` werden aktive und vorherige Version angezeigt. Fehlermeldungen des Updaters haben CLI-Exitcode `5`.
