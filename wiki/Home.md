# MaO-CLI – Dokumentation

**MaO-CLI** erzeugt aus Datenbankdaten zur Mathematik-Olympiade CSV-Listen, LaTeX-Makros und PDF-Dokumente. Zusätzlich unterstützt das Programm den sicheren Import von Gravity-Forms-Teilnehmermeldungen und die Vorbereitung des Jahrgangswechsels.

**Stand:** Entwicklungszweig `develop`, Quellversion `0.6.0.dev0`. Die Funktionen können sich vor Version 2.0 noch ändern.

## Schnellstart

MaO wird als Python-Paket installiert. Die persönliche Konfiguration und die Arbeitsverzeichnisse liegen getrennt vom Programm:

```powershell
python -m pip install .
mao config init
mao config show
mao workspace list
mao validate
```

Die Konfiguration muss anschließend um die privaten, tatsächlichen Datenbank- und Tabelleneinstellungen ergänzt werden. Produktive Zugangsdaten und Schülerinnen-/Schülerdaten gehören nicht ins Git-Repository.

## Daten exportieren

```powershell
mao plan --profile vollpaket --no-latex
mao run --profile vollpaket --no-latex
```

## Teilnehmermeldungen importieren

```powershell
mao import-gf --dry-run
# Erst nach erfolgreicher Prüfung und nur bei leerer Organisationstabelle:
mao import-gf --apply
```

Die Zugangsdaten (`gf.config`) und die SQL-Zuordnungen (`gf-import.yaml`) bleiben lokal und werden nicht veröffentlicht.

## Aktualisieren

```powershell
mao update check --channel develop
mao update download --channel develop --output ./downloads
mao update install --channel develop --yes
```

## Dokumentationsseiten

- [Befehle](Befehle) – CLI-Befehle und Parameter
- [Konfiguration](Konfiguration) – SQL, Profile, Gravity Forms und private Einstellungen
- [Profile](Profile) – Profile und Exportsteuerung
- [Arbeitsverzeichnisse](Arbeitsverzeichnisse) – schuljahresbezogene Arbeitsordner
- [Jahrgangswechsel](Jahrgangswechsel) – schreibgeschützte Vorprüfung
- [Updates](Updates) – Stable-, RC- und Develop-Kanal
- [Fehlerbehebung](Fehlerbehebung) – Fehlermeldungen und Lösungen
- [Entwicklung](Entwicklung) – Tests, Wiki-Veröffentlichung und Entwicklung
- [Git-Flow](Git-Flow) – Branches, CI und Releases

Die Versionsänderungen stehen im [CHANGELOG.md](https://github.com/hhgym/MaO/blob/develop/CHANGELOG.md) des Hauptrepositories.
