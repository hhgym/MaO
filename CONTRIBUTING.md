# Entwicklung und Mitarbeit

Die Weiterentwicklung erfolgt nach Git Flow: `main` für Release-Stände, `develop` für laufende Entwicklung sowie `feature/*`, `release/*` und `hotfix/*` für Änderungen.

Vor einem Pull Request:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m mao_cli validate
```

**Keine lokalen Daten veröffentlichen:** `db.config`, `config/tables.ini`, personenbezogene Daten, exportierte CSV/PDF und Produktions-Logdateien niemals committen. Die versionierte `tables.ini` enthält ausschließlich neutrale `DEMO_`-Namen. Vor der ersten öffentlichen Freigabe auch Repositoryhistorie, Pull-Requests und CI-Logs prüfen.

Bedienungsanleitungen stehen unter `wiki/`, Versionsänderungen in `CHANGELOG.md`.
