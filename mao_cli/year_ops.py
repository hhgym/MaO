"""Read-only Vorprüfung der Jahrgangsinitialisierung.

DDL/DML ist absichtlich nicht verfügbar: SQL-Exports enthalten keine Daten und
keine Definitionen aller referenzierten Fremdtabellen. Ein automatischer Reset
wäre ohne geprüfte Archivstrategie und Restore-Probe nicht vertretbar.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .config import ConfigurationError

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ROLE_LABELS = {
    "orga_table": "Aktuelle Organisation",
    "jahrgaenge_table": "Jahrgangsregister",
    "archive_table": "Historisches Teilnehmerarchiv",
    "runden_table": "Rundenregister",
    "teilnahmen_table": "Rundenbezogene Teilnahmen",
}


def _required(tables: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for role in ROLE_LABELS:
        value = tables.get(role)
        if not value or not IDENTIFIER.fullmatch(value) or value.startswith("DEMO_"):
            raise ConfigurationError(f"Jahrgangsprüfung benötigt echte Kennung '{role}' in tables.ini")
        result[role] = value
    return result


def preliminary_plan(tables: Mapping[str, str], year: str) -> list[str]:
    _required(tables)
    return [
        f"Jahrgang: {year}",
        "1. Vollständige Datenbanksicherung einschließlich referenzierter Tabellen nachweisen.",
        "2. Existierende Jahrgangs- und Rundenzuordnungen sowie aktive Daten zählen.",
        "3. Struktur der aktuellen Organisationstabelle und des historischen Archivs vergleichen.",
        "4. Eingehende Fremdschlüssel und abhängige Views überprüfen.",
        "5. Archivierung der aktuellen Teilnehmerdaten mit Zuordnung zu Jahrgang/Runde planen.",
        "6. Anzahl und Integrität aller archivierten Datensätze überprüfen.",
        "7. Erst danach neuen Jahrgang vorbereiten; die Organisationstabelle und Views behalten ihre Namen.",
        "HINWEIS: Dieser Befehl führt KEINE Datenänderungen aus. --apply ist nicht implementiert.",
    ]


def database_preflight(db: Any, tables: Mapping[str, str]) -> list[str]:
    """Unveränderliche Metadatenabfragen, keinerlei DDL oder DML."""
    names = _required(tables)
    quoted = ", ".join("'" + x + "'" for x in names.values())
    df = db.query_df(
        "SELECT TABLE_NAME, COLUMN_NAME, EXTRA FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({quoted}) ORDER BY TABLE_NAME, ORDINAL_POSITION"
    )
    columns: dict[str, set[str]] = {name: set() for name in names.values()}
    generated: dict[str, set[str]] = {name: set() for name in names.values()}
    for _, row in df.iterrows():
        table = str(row["TABLE_NAME"])
        if table in columns:
            col = str(row["COLUMN_NAME"])
            columns[table].add(col)
            if "GENERATED" in str(row.get("EXTRA", "")).upper():
                generated[table].add(col)
    missing = [role for role, name in names.items() if not columns[name]]
    if missing:
        raise ConfigurationError("Fehlende SQL-Tabellen oder Views: " + ", ".join(missing))
    src = names["orga_table"]
    archive = names["archive_table"]
    only_source = sorted(columns[src] - columns[archive])
    only_archive = sorted(columns[archive] - columns[src])
    fk = db.query_df(
        "SELECT TABLE_NAME, CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
        f"WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_SCHEMA = DATABASE() "
        f"AND REFERENCED_TABLE_NAME = '{src}'"
    )
    count = db.query_df(f"SELECT COUNT(*) AS anzahl FROM `{src}`")
    n = int(count.iloc[0]["anzahl"])
    return [
        f"Organisation: {n} Datensätze (nur gezählt; nicht verändert)",
        f"Spalten nur in Organisation: {', '.join(only_source) or 'keine'}",
        f"Spalten nur in Archiv: {', '.join(only_archive) or 'keine'}",
        f"Berechnete Spalten in Organisation: {', '.join(sorted(generated[src])) or 'keine'}",
        f"Eingehende Fremdschlüssel auf Organisation: {len(fk)}",
        "Archiv und aktuelle Tabelle werden NICHT automatisch als schemaidentisch betrachtet.",
    ]
