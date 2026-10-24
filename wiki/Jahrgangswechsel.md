# Jahrgangswechsel – Datenbank-Vorprüfung

Die Tabellenstruktur der vorhandenen Datenbank zeigt drei unterschiedliche fachliche Rollen: eine **aktuelle Organisationstabelle**, jahrgangsbezogene Runden-/Teilnahmetabellen und ein **historisches Teilnehmerarchiv**. Diese Strukturen sind nicht identisch und dürfen nicht durch eine einfache `INSERT ... SELECT *`-Operation gleichgesetzt werden.

Alle realen SQL-Objektnamen bleiben in der **privaten** zentralen `tables.ini`; das Repository enthält nur die neutralen Kennungen `orga_table`, `jahrgaenge_table`, `archive_table`, `runden_table`, `teilnahmen_table`.

## Vorprüfung

```powershell
mao --config-dir "D:/MaO/Config" init-jahrgang --year 2026-27 --offline
mao --config-dir "D:/MaO/Config" init-jahrgang --year 2026-27
```

Die Offline-Variante zeigt einen Plan. Die verbundene Vorprüfung verwendet ausschließlich `SELECT`-Abfragen auf `information_schema` und eine `COUNT(*)`-Abfrage. Sie prüft die Existenz relevanter Tabellen, unterscheidet aktuelle und Archiv-Spalten, erkennt berechnete Spalten und zeigt die Zahl eingehender Fremdschlüssel an. **Abhängige Views werden noch nicht automatisch validiert** und gehören zur manuellen Freigabe vor späteren Schreiboperationen.

**Es existiert absichtlich kein `--apply`-Schalter**: Dieser Entwicklungsstand archiviert, löscht oder migriert keinerlei Daten. Die vorhandene SQL-Struktursicherung enthält keine Datensätze und nicht alle referenzierten Stammdatentabellen. Ohne Nachweis der tatsächlichen Archivzuordnung, Wiederherstellungsprobe und fachlich geprüften Übernahmeregeln wäre ein destruktiver Vorgang unsicher.

## Vor einem ausführbaren Jahrgangswechsel zu klären

1. Welche Runde und welcher MaO-Jahrgang den aktuellen Datensätzen zugeordnet wird, insbesondere wenn sie selbst keine Jahrgangs-ID besitzen.
2. Welche Informationen bereits in historischen Teilnahmen bzw. im Archiv vorhanden sind, um Doppelübernahmen zu verhindern.
3. Wie fremdschlüsselgebundene Personen, Räume, Preise und Einsätze sicher erhalten bleiben.
4. Ob zusätzlich zur fachlichen Archivierung ein unveränderlicher, vollständiger Snapshot der aktuellen Organisationstabelle nötig ist.
5. Wie Sicherung, Prüfsummen/Zeilenzahlen und Wiederherstellung getestet werden.
6. Welche weiteren jahresübergreifenden Tabellen beim Umstellen anzulegen oder zu aktualisieren sind.

Die aktuelle Organisationstabelle soll auch nach der Umstellung **unter demselben Namen** bestehen bleiben, sodass Views nicht durch eine Umbenennung zerstört werden. Die Datenbank-Migration wird als separater, geprüfter Schritt entwickelt.
