# MaO-CLI

Python-Anwendung für die Organisation der Mathematik-Olympiade: MySQL-Abfragen, CSV-/LaTeX-Datenexporte und PDF-Erstellung.

> **Entwicklungsstand `0.5.0.dev0` – bewusster Schnitt vor Version 2.0.**  
> Die bisherige Datei `mao-cli.py` und die früheren Parameter werden **nicht** unterstützt. Ein bestehendes Projekt muss ausdrücklich auf die neue Struktur umgestellt werden. Bestehende Ausgabedateien sollten vorher gesichert werden.

## Installation

Python 3.10 oder neuer und eine LaTeX-Distribution für PDF-Jobs:

```bash
python -m pip install -e .
# Für die Tests:
python -m pip install -e '.[dev]'
```

Danach funktionieren `mao` sowie `python -m mao_cli` (bei Aufruf im Projektverzeichnis).

## Erste Verwendung

1. Lege einen Arbeitsordner an, z. B. `D:/MaO/2026-27`.
2. Lege deine vorhandene **lokale** `db.config` in diesen Ordner. Zugangsdaten gehören nicht in Git.
3. **Erforderlich für Datenbankläufe:** Kopiere `mao_cli/defaults/tables.ini` nach `ARBEITSORDNER/config/tables.ini` und ersetze dort alle `DEMO_`-Namen durch die tatsächlichen lokalen Tabellennamen. Diese Datei darf nicht in Git gelangen. Weitere Dateien aus `mao_cli/defaults/` kannst du optional lokal überschreiben.
4. Prüfe Konfiguration und geplante Exporte, bevor du echte Dateien schreibst:

```bash
mao profiles
mao validate --workspace "D:/MaO/2026-27"
mao plan --workspace "D:/MaO/2026-27" --profile vorbereitung --no-latex
mao run --workspace "D:/MaO/2026-27" --profile vorbereitung --no-latex
```

Ohne `--profile` wird das Profil `vollpaket` verwendet. Die mitgelieferten Profile sind **Beispielauswahlen**, die vor dem Produktionslauf geprüft werden sollten.

## Struktur des Repositories

```text
pyproject.toml              # Python-Paket / CLI-Entrypoint
CHANGELOG.md                # Änderungen, wird im Haupt-Git-Repository versioniert
mao_cli/                    # Programmcode
  cli.py                    # Befehle / Steuerung
  config.py                 # Validierung, Profile, SQL-Identifier
  db.py                     # MySQL-Verbindung
  transforms.py             # Filter, Sortierung, Datentypen
  exports.py                # Exportplan, Manifest, CSV und TeX-Makros
  latex.py                  # PDF-Erstellung
  defaults/                 # Ausgelieferte Standardkonfiguration
    pipeline.yaml
    profiles.ini
    tables.ini
wiki/                       # Wiki-Quellseiten im Hauptrepository
scripts/publish_wiki.py     # Veröffentlichung ins separate Git-Wiki
scripts/check_release.py    # Versions-/Tagprüfung
.github/workflows/          # CI und Release-Automatisierung
tests/                      # Offline-Regressionstests
```

## Konfiguration und Daten

- `mao_cli/defaults/pipeline.yaml`: SQL-Datasets, Exporte, LaTeX-Jobs.
- `mao_cli/defaults/profiles.ini`: Profile anhand eindeutiger Export- und Job-IDs.
- `mao_cli/defaults/tables.ini`: Tabellen und Views als geprüfte SQL-Identifier.
- `ARBEITSORDNER/config/`: optionaler Override einzelner Standardkonfigurationen.
- `ARBEITSORDNER/db.config`: lokale Datenbank-Zugangsdaten im bisherigen `key=value`-Format.
- `ARBEITSORDNER/.mao-cli/exports-manifest.json`: generierter, neuer Manifeststandard **Schema 2**.
- `ARBEITSORDNER/Listen/`: Datenexporte und LaTeX-Arbeitsdateien.

Die MaO-Nummer über `--mao` steuert nur die Dokumentnummer und **nicht** die SQL-Datenquelle. die in der lokalen `tables.ini` hinterlegte Organisationstabelle bleibt die aktuelle Datenquelle; `init-jahrgang` wird erst in Schritt 3 eingeführt.

**Kein automatisches Upgrade:** Manifestdateien aus vorherigen Entwicklungsständen werden nicht migriert. Verwende für diese Version einen neuen Arbeitsordner oder sichere und entferne dort vor dem ersten Lauf ausschließlich das alte `.mao-cli/exports-manifest.json`. Automatisches Löschen alter, nicht manifestierter Exportdateien erfolgt nicht.

## Befehle / Wiki

Die vollständige CLI-Referenz liegt unter [`wiki/Befehle.md`](wiki/Befehle.md). Alle Quellseiten werden **mit dem Hauptrepository** in Git versioniert. Hosting-Plattformen speichern den sichtbaren Git-Wiki-Inhalt gewöhnlich in einem **separaten Wiki-Repository**; der Ordner `wiki/` allein veröffentlicht noch kein Wiki.

Zum Veröffentlichen (nachdem das Wiki auf der Git-Plattform aktiviert/initialisiert wurde):

```bash
python scripts/publish_wiki.py --remote https://HOST/OWNER/REPO.wiki.git --dry-run
python scripts/publish_wiki.py --remote https://HOST/OWNER/REPO.wiki.git
```

Ein Wiki-Push erfordert Git, passende Zugriffsrechte und bei Änderungen eine konfigurierte Git-Identität. Das Skript löscht keine fremden Wiki-Seiten. In GitHub/GitLab kann die konkrete Wiki-URL abweichen; benutze die Git-Clone-URL, die dein Wiki anzeigt. Die Zugangsdaten niemals in den Quellcode eintragen.

## Git Flow und Updatekanäle

Die Repositoryvorlage liegt unter `.github/workflows/`; Branchmodell, Tags und Branchschutz sind in [`wiki/Git-Flow.md`](wiki/Git-Flow.md) dokumentiert. `main` steht für Releases, `develop` für laufende Entwicklung; neue Features werden auf `feature/*` entwickelt. **Es wurde noch kein externes GitHub-Repository angelegt.** Für den Erstimport liegt eine Git-Bundle-Historie mit `main` und `develop` bei (siehe `SETUP-GIT.md` im Downloadpaket).

```bash
# Ohne Datenbankkonfiguration nutzbar:
mao update check --channel stable --repository OWNER/REPO
mao update check --channel rc --repository OWNER/REPO
mao update check --channel develop   # nur im sauberen develop-Git-Checkout

# Release-Wheel mit SHA-256-Prüfung herunterladen:
mao update download --channel stable --repository OWNER/REPO --output ./downloads

# Installation ausdrücklich bestätigen:
mao update install --channel stable --repository OWNER/REPO --yes
```

**Wichtig:** Stable-/RC-Installationen gehören in eine eigene Python-Umgebung; Develop-Installationen sind für `pip install -e .`-Checkouts vorgesehen. Ein Update verändert keine SQL-Tabellen. **Stable-/RC-Updates werden in getrennten virtuellen Umgebungen installiert**, und die vorherige Version bleibt für `mao update rollback --yes` erhalten. Dazu ist der beim ersten Update angelegte Starter zu verwenden. Ein fehlgeschlagener Develop-Pip-Lauf wird bestmöglich zurückgesetzt. Details stehen im Wiki. Die ausführliche Beschreibung steht in [`wiki/Updates.md`](wiki/Updates.md).

## Tests

```bash
python -m pytest -q
mao --version
mao --help
```

Die Tests benötigen weder eine Verbindung zur produktiven Datenbank noch eine LaTeX-Installation. Produktive SQL- und LaTeX-Läufe müssen auf dem Zielsystem gesondert geprüft werden.

## Geplante Schritte

`init-jahrgang` (Archivierung der konfigurierten Organisationstabelle und Datenübernahmen), Datenbankschema-Migrationen, GUI und Datenbankmigrationen sind **noch nicht implementiert**. Das Rollback der verwalteten Release-Installationen ist umgesetzt. Erst ab stabiler Version 2.0 ist Abwärtskompatibilität als feste Anforderung vorgesehen.

## Veröffentlichungsprüfung

Die versionierten Tabellennamen in `mao_cli/defaults/tables.ini` sind reine `DEMO_`-Werte. Eine **lokale** `config/tables.ini` ist für SQL-Läufe zwingend nötig; sie wird weder von Git noch vom Updateprozess veröffentlicht. VORSICHT: Ältere Commits im bisherigen privaten Repository enthielten reale Objektnamen. Das Ändern der aktuellen Dateien bereinigt die Git-Historie nicht. Das Repository darf erst nach Historien-Neustart und Prüfung aller alten Referenzen/Forks/PRs veröffentlicht werden.
