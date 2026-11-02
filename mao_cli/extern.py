"""Externe Teilnehmende (Grundschulen): Gravity-Forms-Anmeldungen in eigene Tabellen übernehmen.

Getrennt von den Stammdaten und von der Organisationstabelle. Pro Anmeldung (ein Kind) entsteht eine
Person (jahrgangsunabhängig) und eine Teilnahme im aktuellen Jahrgang. Der Import ist wiederholbar:
Anmeldungen, deren GF-Eintrags-ID schon vorhanden ist, werden übersprungen. Personen werden nie
automatisch zusammengeführt; mögliche Wiederkehrende erscheinen als Hinweis im Bericht.
Aus dem Formular werden nur Name des Kindes, Klassenstufe und Schule übernommen (keine Daten der Eltern).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .config import ConfigurationError
from .gf_import import IDENT, gf_entries, load_gf_credentials

LOGGER = logging.getLogger(__name__)
FIELD_ID = re.compile(r"^\d+(\.\d+)?$")
FIELD_KEYS = ("vorname", "nachname", "klassenstufe", "schule")
OPTIONAL_KEYS = ("geschlecht",)
GENDERS = ("m", "w", "d")
STUFEN = range(3, 7)
NAME_MAX = 255
WRITE_PERSON = ("vorname", "nachname", "geschlecht")
WRITE_PARTICIPATION = ("mao_jahrgang_id", "person_id", "schulnummer", "klassenstufe", "gf_eintrag_id")


class ExternError(ValueError):
    """Unbrauchbare Daten oder Tabellen. Es wird nichts geschrieben."""


@dataclass(frozen=True)
class ExternSettings:
    form_id: int
    page_size: int
    timeout: int
    fields: Mapping[str, str]


def load_extern_settings(path: Path) -> ExternSettings:
    if not path.is_file():
        raise ConfigurationError(f"Konfiguration für Externe fehlt: {path}; bitte mao config init ausführen")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"gf-extern.yaml unlesbar: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) - {"form_id", "page_size", "timeout", "fields"}:
        raise ConfigurationError("gf-extern.yaml: ungültige oder unbekannte Schlüssel")
    fields = raw.get("fields")
    if (not isinstance(fields, dict) or not set(FIELD_KEYS) <= set(fields) or set(fields) - set(FIELD_KEYS + OPTIONAL_KEYS)
            or not all(isinstance(v, str) and FIELD_ID.fullmatch(v) for v in fields.values())):
        raise ConfigurationError("gf-extern.yaml: fields benötigt " + ", ".join(FIELD_KEYS) + " (optional: geschlecht)"
                                 + " (Feldnummern als Zeichenketten, z. B. \"3.3\")")

    def number(key: str, default: int, maximum: int) -> int:
        value = raw.get(key, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ConfigurationError(f"gf-extern.yaml: {key} außerhalb zulässiger Grenzen")
        return value

    return ExternSettings(number("form_id", 1, 100000), number("page_size", 100, 500),
                          number("timeout", 60, 300), dict(fields))


def clean_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def name_key(first: str, last: str) -> tuple[str, str]:
    return clean_name(first).casefold(), clean_name(last).casefold()


def parse_entries(entries: Iterable[Mapping[str, Any]], fields: Mapping[str, str]
                  ) -> tuple[list[dict[str, Any]], list[str], int]:
    """GF-Einträge in Zeilen umwandeln. Rückgabe: Zeilen, Probleme (Einträge werden übersprungen), inaktive Einträge."""
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    inactive = 0
    for entry in entries:
        entry_id = str(entry.get("id") or "").strip()
        if entry.get("status") not in (None, "active"):
            inactive += 1
            continue
        if not entry_id.isdigit():
            issues.append("Eintrag ohne gültige ID übersprungen")
            continue
        first = clean_name(entry.get(fields["vorname"]))
        last = clean_name(entry.get(fields["nachname"]))
        school = str(entry.get(fields["schule"]) or "").strip().upper()
        try:
            level = int(str(entry.get(fields["klassenstufe"]) or "").strip())
        except ValueError:
            level = 0
        gender_raw = str(entry.get(fields["geschlecht"]) or "").strip().lower() if "geschlecht" in fields else ""
        gender = gender_raw if gender_raw in GENDERS else None
        if gender_raw and gender is None:
            issues.append(f"GF-Eintrag {entry_id}: Geschlecht '{gender_raw[:10]}' unbekannt (ohne Angabe übernommen)")
        problems = []
        if not first or not last:
            problems.append("Name des Kindes unvollständig")
        if len(first) > NAME_MAX or len(last) > NAME_MAX:
            problems.append("Name zu lang")
        if level not in STUFEN:
            problems.append("Klassenstufe nicht 3 bis 6")
        if not school:
            problems.append("keine Schule gewählt")
        if problems:
            issues.append(f"GF-Eintrag {entry_id}: " + ", ".join(problems) + " (übersprungen)")
            continue
        rows.append({"gf_eintrag_id": int(entry_id), "vorname": first, "nachname": last,
                     "klassenstufe": level, "schulnummer": school, "geschlecht": gender})
    return rows, issues, inactive


@dataclass
class ExternPlan:
    new: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    hints: list[dict[str, Any]] = field(default_factory=list)
    already: int = 0


def plan_import(rows: list[dict[str, Any]], schools: Mapping[str, tuple[str, bool]],
                persons: Mapping[int, tuple[str, str]], participations: list[dict[str, Any]],
                jahrgang_id: int) -> ExternPlan:
    """Reine Planung ohne Datenbankzugriff.

    schools: Schulnummer -> (Name, aktiv); persons: id -> (Vorname, Nachname);
    participations: vorhandene Teilnahmen aller Jahrgänge.
    """
    plan = ExternPlan()
    imported = {p["gf_eintrag_id"] for p in participations if p.get("gf_eintrag_id") is not None}
    this_year = [p for p in participations if p["jahrgang_id"] == jahrgang_id]
    in_year_ids = {p["person_id"] for p in this_year}
    by_key: dict[tuple[str, str], list[int]] = {}
    for person_id, (first, last) in persons.items():
        by_key.setdefault(name_key(first, last), []).append(person_id)
    seen: dict[tuple[str, str], list[tuple[str, Any]]] = {}
    for p in this_year:
        if p["person_id"] in persons:
            seen.setdefault(name_key(*persons[p["person_id"]]), []).append((p["schulnummer"], p["gf_eintrag_id"]))
    for row in sorted(rows, key=lambda r: r["gf_eintrag_id"]):
        label = f"GF-Eintrag {row['gf_eintrag_id']} ({row['vorname']} {row['nachname']})"
        if row["gf_eintrag_id"] in imported:
            plan.already += 1
            continue
        school = schools.get(row["schulnummer"])
        if school is None:
            plan.skipped.append(f"{label}: Schulnummer {row['schulnummer']} nicht in der Schulentabelle "
                                "(erst mao schulen-sync --apply?)")
            continue
        key = name_key(row["vorname"], row["nachname"])
        same = seen.get(key, [])
        twin = [entry for _school, entry in same if _school == row["schulnummer"]]
        if twin:
            plan.skipped.append(f"{label}: Doppelanmeldung (gleicher Name und gleiche Schule wie GF-Eintrag {twin[0]})")
            continue
        if same:
            plan.warnings.append(f"{label}: gleicher Name wie ein anderes Kind in diesem Jahrgang, aber andere Schule; übernommen")
        if not school[1]:
            plan.warnings.append(f"{label}: Schule {row['schulnummer']} steht nicht mehr im Schulverzeichnis")
        earlier = [pid for pid in by_key.get(key, []) if pid not in in_year_ids]
        if earlier:
            plan.hints.append({"gf_eintrag_id": row["gf_eintrag_id"], "kind": f"{row['vorname']} {row['nachname']}",
                               "moegliche_person_ids": earlier,
                               "fruehere_teilnahmen": [{"person_id": p["person_id"], "jahrgang_id": p["jahrgang_id"],
                                                        "schulnummer": p["schulnummer"], "klassenstufe": p["klassenstufe"]}
                                                       for p in participations if p["person_id"] in earlier]})
        seen.setdefault(key, []).append((row["schulnummer"], row["gf_eintrag_id"]))
        plan.new.append({**row, "schule": school[0]})
    return plan


def _ident(tables: Mapping[str, str], key: str) -> str:
    name = tables.get(key, "")
    if not IDENT.fullmatch(name or "") or name.startswith("DEMO_"):
        raise ExternError(f"{key} fehlt oder ist ein DEMO-Platzhalter: tables.ini prüfen")
    return name


def load_state(cur: Any, tables: Mapping[str, str]) -> tuple[int, dict[str, tuple[str, bool]],
                                                            dict[int, tuple[str, str]], list[dict[str, Any]]]:
    schools_t, persons_t = _ident(tables, "schools_table"), _ident(tables, "extern_personen_table")
    part_t, years_t = _ident(tables, "extern_teilnahmen_table"), _ident(tables, "jahrgaenge_table")
    sy_t = _ident(tables, "schuljahre_table")
    cur.execute(f"SELECT j.id FROM `{years_t}` j JOIN `{sy_t}` s ON s.id = j.schuljahr_id "
                "WHERE s.aktiv = 1 ORDER BY j.id DESC LIMIT 1")
    found = cur.fetchall()
    if not found:
        raise ExternError("Kein aktiver Jahrgang in der Datenbank gefunden")
    jahrgang_id = int(found[0][0])
    cur.execute(f"SELECT schulnummer, name, aktiv FROM `{schools_t}`")
    schools = {str(n).upper(): (str(name), bool(active)) for n, name, active in cur.fetchall()}
    cur.execute(f"SELECT id, vorname, nachname FROM `{persons_t}`")
    persons = {int(i): (str(v), str(n)) for i, v, n in cur.fetchall()}
    cur.execute(f"SELECT mao_jahrgang_id, person_id, schulnummer, klassenstufe, gf_eintrag_id FROM `{part_t}`")
    participations = [{"jahrgang_id": int(j), "person_id": int(p), "schulnummer": str(s), "klassenstufe": int(k),
                       "gf_eintrag_id": None if g is None else int(g)} for j, p, s, k, g in cur.fetchall()]
    return jahrgang_id, schools, persons, participations


def import_extern(db: Any, config_dir: Path, tables: Mapping[str, str], *, dry_run: bool,
                  report: dict[str, Any] | None = None, entries: Iterable[Mapping[str, Any]] | None = None
                  ) -> dict[str, int]:
    settings = load_extern_settings(config_dir / "gf-extern.yaml")
    cnx = db.conn
    if cnx is None:
        raise ExternError("Keine Datenbankverbindung")
    if entries is None:
        entries = gf_entries(load_gf_credentials(config_dir / "gf.config"), settings.form_id,
                             settings.page_size, settings.timeout)
    entries = list(entries)
    rows, issues, inactive = parse_entries(entries, settings.fields)
    cur = cnx.cursor()
    lock_name = ""
    try:
        jahrgang_id, schools, persons, participations = load_state(cur, tables)
        plan = plan_import(rows, schools, persons, participations, jahrgang_id)
        problems = issues + plan.skipped
        stats = {"gf_eintraege": len(entries), "inaktive": inactive, "bereits_importiert": plan.already,
                 "neu": len(plan.new), "uebersprungen": len(problems), "hinweise": len(plan.hints)}
        LOGGER.info("Externe | GF-Einträge: %d | inaktiv: %d | schon importiert: %d | neu: %d | übersprungen: %d "
                    "| mögliche Wiederkehrende: %d | Modus: %s", stats["gf_eintraege"], inactive, plan.already,
                    len(plan.new), len(problems), len(plan.hints), "DRY-RUN" if dry_run else "APPLY")
        for row in plan.new[:20]:
            LOGGER.info("  neu: GF %s | %s %s | Stufe %s | %s", row["gf_eintrag_id"], row["vorname"], row["nachname"],
                        row["klassenstufe"], row["schule"])
        for text in [*problems, *plan.warnings][:20]:
            LOGGER.warning("  %s", text)
        for hint in plan.hints:
            LOGGER.warning("  Wiederkehrend? %s (GF %s): vorhandene Person(en) %s", hint["kind"], hint["gf_eintrag_id"],
                           ", ".join(map(str, hint["moegliche_person_ids"])))
        if report is not None:
            report["summary"] = stats
            report["neu"] = [{k: r[k] for k in ("gf_eintrag_id", "vorname", "nachname", "geschlecht", "klassenstufe", "schulnummer", "schule")}
                             for r in plan.new]
            report["uebersprungen"] = problems
            report["warnings"] = plan.warnings
            report["wiederkehrend"] = plan.hints
        if dry_run or not plan.new:
            if report is not None:
                report["status"] = "ready" if dry_run else "applied"
            return stats
        persons_t, part_t = _ident(tables, "extern_personen_table"), _ident(tables, "extern_teilnahmen_table")
        lock_name = f"mao-extern-{part_t}"
        cur.execute("SELECT GET_LOCK(%s, 10)", (lock_name,))
        if cur.fetchall()[0][0] != 1:
            raise ExternError("Konkurrierender Import oder Sperr-Timeout")
        for table, wanted in ((persons_t, WRITE_PERSON), (part_t, WRITE_PARTICIPATION)):
            cur.execute(f"SHOW COLUMNS FROM `{table}`")
            missing = set(wanted) - {row[0] for row in cur.fetchall()}
            if missing:
                raise ExternError(f"{table} benötigt Spalten: {sorted(missing)}")
        if cnx.in_transaction:
            cnx.rollback()
        cnx.start_transaction()
        try:
            cur.execute(f"SELECT COUNT(*) FROM `{persons_t}`")
            persons_before = int(cur.fetchall()[0][0])
            cur.execute(f"SELECT COUNT(*) FROM `{part_t}`")
            part_before = int(cur.fetchall()[0][0])
            if persons_before != len(persons) or part_before != len(participations):
                raise ExternError("Die Tabellen haben sich während der Prüfung geändert; bitte erneut ausführen")
            for row in plan.new:
                cur.execute(f"INSERT INTO `{persons_t}` (`vorname`, `nachname`, `geschlecht`) VALUES (%s, %s, %s)",
                            (row["vorname"], row["nachname"], row.get("geschlecht")))
                person_id = cur.lastrowid
                if not person_id:
                    raise ExternError("Keine Personen-ID erhalten")
                cur.execute(f"INSERT INTO `{part_t}` ({', '.join('`' + c + '`' for c in WRITE_PARTICIPATION)}) "
                            f"VALUES ({', '.join(['%s'] * len(WRITE_PARTICIPATION))})",
                            (jahrgang_id, person_id, row["schulnummer"], row["klassenstufe"], row["gf_eintrag_id"]))
            cur.execute(f"SELECT COUNT(*) FROM `{persons_t}`")
            persons_after = int(cur.fetchall()[0][0])
            cur.execute(f"SELECT COUNT(*) FROM `{part_t}`")
            part_after = int(cur.fetchall()[0][0])
            if persons_after != persons_before + len(plan.new) or part_after != part_before + len(plan.new):
                raise ExternError("Zählabgleich fehlgeschlagen; nichts geschrieben")
            cnx.commit()
        except Exception:
            cnx.rollback()
            raise
        if report is not None:
            report["status"] = "applied"
        return stats
    finally:
        if lock_name:
            try:
                cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                cur.fetchall()
            except Exception:
                LOGGER.warning("Konnte Sperre nicht regulär freigeben (DB-Disconnect gibt sie frei)")
        cur.close()
