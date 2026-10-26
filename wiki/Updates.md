# MaO installieren und aktualisieren (uv)

MaO wird mit [uv](https://docs.astral.sh/uv/) installiert. uv legt für MaO eine eigene Python-Umgebung an, stellt `mao` im PATH bereit und braucht weder einen Git-Klon noch pip. Das Repository [`hhgym/MaO`](https://github.com/hhgym/MaO) ist öffentlich; es wird kein Token benötigt. Für die Installation aus einem Git-Ref muss `git` installiert sein.

## Einmalig: uv installieren

```powershell
winget install astral-sh.uv                                  # Windows
curl -LsSf https://astral.sh/uv/install.sh | sh              # Linux/macOS
```

## Installieren

```powershell
uv tool install --python 3.13 git+https://github.com/hhgym/MaO@develop    # Entwicklungsstand
uv tool install --python 3.13 git+https://github.com/hhgym/MaO@v0.6.0     # festes Release
```

## Aktualisieren und zurückgehen

```powershell
uv tool install --force --reinstall git+https://github.com/hhgym/MaO@develop   # neuester Stand
uv tool install --force --reinstall git+https://github.com/hhgym/MaO@v0.6.0    # bestimmte Version (auch zum Zurückgehen)
uv tool uninstall mao-cli                                                      # entfernen
```

`mao update` ermittelt den passenden Befehl, **führt ihn aber nicht aus**, weil sich eine laufende `mao.exe` unter Windows nicht überschreiben lässt:

```powershell
mao update                       # neuester stabiler Release
mao update --channel rc          # inklusive Release Candidates
mao update --channel develop     # Branch develop
mao update --version 0.6.0       # bestimmte Version
```

Konfiguration (`MaO/config`), Vorlagen und Jahrgangsordner liegen außerhalb der Programmumgebung und bleiben bei jedem Update unberührt. `--repository OWNER/REPO` oder `MAO_GITHUB_REPO` sind nur für ein abweichendes GitHub-Projekt nötig.

## Umstieg von `pip install -e .` (Quellordner)

1. `python -m pip uninstall mao-cli`
2. `uv tool install --python 3.13 git+https://github.com/hhgym/MaO@develop`
3. Den bisherigen privaten Ordner `D:\MaO\config` in den zentralen MaO-Ordner übernehmen (siehe [Konfiguration](Konfiguration)): nach `<Nextcloud>/MaO/config` kopieren, dann `mao config use "<Nextcloud>/MaO"`. Bisherige Jahrgangsordner nach `MaO/Jahrgang/<MaO-Nr>_<Schuljahr>` verschieben.
4. Den alten Quellordner (außer `config`) kann man danach löschen; `.mao-updates/` ebenso.

Die Regel „Ordner `config` neben dem Quellordner“ gilt nur noch für Entwicklerinnen und Entwickler mit `pip install -e .`.

## Für Maintainer

Der Workflow `.github/workflows/release.yml` baut nach einem Release-Tag auf `main` ein Wheel samt SHA-256-Datei. Ein Tag für Release Candidate 1 könnte `v0.5.0-rc.1` lauten; `pyproject.toml` und `mao_cli/__init__.py` müssen zu diesem Zeitpunkt die Version `0.5.0rc1` tragen. Solange die Entwicklungsversion `0.5.0.dev0` lautet, ist **noch kein** RC-Release erzeugt. Siehe [Git Flow](Git-Flow.md).

Release-Wheels samt `.sha256` bleiben als Download am Release erhalten; installiert wird aber direkt aus dem Git-Tag. Fehlermeldungen von `mao update` haben den Exitcode `5`.
