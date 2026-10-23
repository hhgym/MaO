# Profile

Ausführungsprofile bestimmen, **welche** fachlichen Exporte und LaTeX-Jobs ausgeführt werden. Sie wechseln nicht automatisch das Datenbankschema oder den Jahrgang.

Die mitgelieferte `profiles.ini` enthält:

| Profil | Zweck |
|---|---|
| `vorbereitung` | Klassen- und Raumlisten sowie Anwesenheitslisten |
| `turniertag` | Teilnehmer-, Abwesenheits- und Rückgabelisten |
| `ergebnisse` | Ranglisten, Urkunden, Statistik |
| `vollpaket` | Alle in der YAML standardmäßig aktivierten Einträge |

Die Zusammenstellungen sind Beispiele, vor der Produktion fachlich prüfen.

## Eigene Profile

Lege im Arbeitsordner `config/profiles.ini` an. Wenn dort eine Datei liegt, ist sie **vollständig** maßgeblich.

```ini
[vorbereitung]
description = Vorbereitung vor dem Wettbewerb
exports = klassenleitungen, klassen_sek_i, teilnehmende, kopierliste
latex_jobs = raumuebersicht, anwesenheitslisten, klassenleitungslisten

[vollpaket]
description = Alle regulär aktivierten Produkte
exports = enabled
latex_jobs = enabled
```

`exports` verweist auf `id` der YAML-Exports, `latex_jobs` auf `id` der LaTeX-Jobs.

## Selektoren

- `enabled` – nur in `pipeline.yaml` bereits aktivierte Einträge.
- `all` – alle Einträge, auch ursprünglich deaktivierte.
- `none` – keine Einträge.
- `id1, id2, id3` – explizite Auswahl.

`enabled`, `all` und `none` dürfen nicht mit weiteren IDs kombiniert werden.

## Verhalten beim Export

Ein Manifest führt die erzeugten Dateien jeweils unter der Export-ID. Dadurch kann ein Aufruf für `ergebnisse` veraltete Dateien aus genau diesen Exporten entfernen, ohne unveränderte Dateien eines anderen Profils anzutasten. Das Manifest wird unter `.mao-cli/exports-manifest.json` gespeichert und verwendet das **Schema 2**.
