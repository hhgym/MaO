"""Lesende Online-Prüfung unter dem bestehenden Befehl ``mao validate``.

Ohne ``--online`` werden weder Datenbank noch Gravity Forms kontaktiert.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .gf_import import (
    ImportErrorGF, WRITE_COLUMNS, _cache, _course_links, _rows, _teacher_ids,
    _valid_id, gf_entries, load_gf_credentials, load_import_settings,
)


def _query_columns(cursor: Any, sql: str, required: set[str], label: str) -> None:
    """SQL und Spaltenstruktur prüfen, ohne Ergebnismengen zu laden."""
    cursor.execute(f"SELECT * FROM ({sql.strip()}) AS _mao_validate LIMIT 0")
    columns = set(cursor.column_names or ())
    missing = required - columns
    if missing:
        raise ImportErrorGF(f"SQL-Quelle '{label}': fehlende Spalten: {', '.join(sorted(missing))}")


def validate_online(db: Any, config_dir: Path, tables: Mapping[str, str],
                    cfg: Mapping[str, Any]) -> list[str]:
    """Datenbank, GF-SELECTs, ID-Beziehungen und GF-API lesen, nie schreiben.

    Liefert Diagnosezeilen; bei Fehlern wird die Validierung beendet. Die
    eigentliche Teilnehmerprüfung bleibt Aufgabe von ``import-gf --dry-run``.
    """
    if db.conn is None:
        raise ImportErrorGF("Keine Datenbankverbindung für Online-Validierung")
    result: list[str] = ["Datenbankverbindung: OK"]
    options = load_import_settings(config_dir / "gf-import.yaml")
    credentials = load_gf_credentials(config_dir / "gf.config")
    target = tables.get("orga_table", "")
    if not target or target.startswith("DEMO_"):
        raise ImportErrorGF("tables.ini: orga_table muss einen echten Tabellennamen enthalten")
    from .gf_import import IDENT
    if not IDENT.fullmatch(target):
        raise ImportErrorGF("Ungültiger Zieltabellenname in tables.ini")
    cur = db.conn.cursor()
    try:
        cur.execute(f"SHOW COLUMNS FROM `{target}`")
        columns = {r[0] for r in cur.fetchall()}
        missing = set(WRITE_COLUMNS) - columns
        if missing:
            raise ImportErrorGF(f"Zieltabelle {target}: fehlende Spalten: {', '.join(sorted(missing))}")
        cur.execute(f"SELECT COUNT(*) FROM `{target}`")
        count = int(cur.fetchone()[0])
        result.append(f"Zieltabelle {target}: {count} Datensätze"
                      + (" (nicht leer – Erstimport gesperrt)" if count else " (bereit für Erstimport)"))

        for ds in cfg.get("datasets", []):
            if ds.get("enabled", True):
                _query_columns(cur, ds["sql"], set(), f"Pipeline/{ds['name']}")
        result.append("Pipeline-SQL: gültige SELECT-Abfragen")
        expected = {
            "students": {"matrikelnummer", "vorname", "nachname", "geschlecht", "klasse"},
            "teachers": {"id"},
            "courses": {"lerngruppe_schuljahr_id", "lehrkraft_id"},
            "mandatory": {"klasse", "lehrkraft_id", "lerngruppe_schuljahr_id"},
        }
        for key, query in options.queries.items():
            if query:
                _query_columns(cur, query, expected[key], f"GF/{key}")
        result.append("GF-SQL: alle erforderlichen Spalten vorhanden")

        # Konsistenzchecks auf aktuellen Stammdaten, ohne GF-Meldungen zu
        # importieren. Der spätere Dry-Run prüft jede einzelne Anmeldung.
        students = _rows(cur, options.queries["students"])
        students_by_id: dict[int, dict] = {}
        for row in _cache(students, expected["students"], "students"):
            if row.get("matrikelnummer") is None:
                continue
            matrikel = _valid_id(row["matrikelnummer"], "Matrikelnummer")
            if matrikel in students_by_id:
                raise ImportErrorGF(f"Doppelte Matrikelnummer {matrikel} in Schülerstammdaten")
            students_by_id[matrikel] = row
        teachers = _teacher_ids(_rows(cur, options.queries["teachers"]))
        courses = _course_links(_rows(cur, options.queries["courses"]))
        if not teachers or not courses or not students_by_id:
            raise ImportErrorGF("Lehrkräfte, Lerngruppen oder Schülerstammdaten sind leer")
        result.append(f"Stammdaten: {len(students_by_id)} Schüler:innen, "
                      f"{len(teachers)} Lehrkräfte, {len(courses)} Lerngruppen-/Lehrkraftzuordnungen")
        for (_, teacher_id), _ in courses.items():
            if teacher_id not in teachers:
                raise ImportErrorGF(f"Lerngruppe referenziert unbekannte Lehrkraft-ID {teacher_id}")

        if options.mandatory_classes or options.optional_mandatory_classes:
            groups = _rows(cur, options.queries["mandatory"])
            chosen: dict[str, tuple[int, int]] = {}
            relevant = set(options.mandatory_classes) | set(options.optional_mandatory_classes)
            for item in _cache(groups, expected["mandatory"], "mandatory"):
                klass = str(item.get("klasse") or "").strip()
                if klass not in relevant:
                    continue
                teacher = _valid_id(item["lehrkraft_id"], f"Pflichtklasse {klass}: Lehrkraft")
                group = _valid_id(item["lerngruppe_schuljahr_id"], f"Pflichtklasse {klass}: Lerngruppe")
                if teacher not in teachers or (group, teacher) not in courses:
                    raise ImportErrorGF(f"Pflichtklasse {klass}: ungültige Mathematik-Lerngruppe/Lehrkraft")
                pair = (teacher, group)
                if klass in chosen and chosen[klass] != pair:
                    raise ImportErrorGF(f"Pflichtklasse {klass}: mehrdeutige Lerngruppe/Lehrkraft")
                chosen[klass] = pair
            missing = set(options.mandatory_classes) - set(chosen)
            optional_in_students = {str(s.get("klasse") or "").strip() for s in students_by_id.values()} & set(options.optional_mandatory_classes)
            missing |= optional_in_students - set(chosen)
            if missing:
                raise ImportErrorGF("Keine eindeutige Lerngruppe für Pflichtklasse(n): " + ", ".join(sorted(missing)))
            for klass in chosen:
                if not any(str(s.get("klasse") or "").strip() == klass for s in students_by_id.values()):
                    raise ImportErrorGF(f"Keine Schülerstammdaten für Pflichtklasse {klass}")
            result.append("Pflichtklassen: " + ", ".join(sorted(chosen)))
    finally:
        cur.close()

    # Verbindung nur lesen: Bereits eine erfolgreiche erste API-Seite reicht;
    # ein Komplettdownload gehört zum Import-Dry-Run.
    entries = gf_entries(credentials, options.form_id, options.page_size, options.timeout)
    try:
        first = next(iter(entries), None)
    finally:
        close = getattr(entries, "close", None)
        if close is not None:
            close()
    result.append(f"Gravity Forms: Formular {options.form_id} erreichbar"
                  + (" (keine Meldungen auf erster Seite)" if first is None else ", Antwort lesbar"))
    return result
