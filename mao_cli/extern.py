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
from .rooms import RoomError, RoomPlan, RoomRules, load_room_rules, load_rooms, log_rules_source, resolve_rules_file

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


def load_state_year(cur: Any, tables: Mapping[str, str]) -> int:
    years_t, sy_t = _ident(tables, "jahrgaenge_table"), _ident(tables, "schuljahre_table")
    cur.execute(f"SELECT j.id FROM `{years_t}` j JOIN `{sy_t}` s ON s.id = j.schuljahr_id "
                "WHERE s.aktiv = 1 ORDER BY j.id DESC LIMIT 1")
    found = cur.fetchall()
    if not found:
        raise ExternError("Kein aktiver Jahrgang in der Datenbank gefunden")
    return int(found[0][0])


def load_state(cur: Any, tables: Mapping[str, str]) -> tuple[int, dict[str, tuple[str, bool]],
                                                            dict[int, tuple[str, str]], list[dict[str, Any]]]:
    schools_t, persons_t = _ident(tables, "schools_table"), _ident(tables, "extern_personen_table")
    part_t = _ident(tables, "extern_teilnahmen_table")
    jahrgang_id = load_state_year(cur, tables)
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


# ------------------------------------------------------------------ Raumzuteilung

def plan_extern_rooms(participants: list[dict[str, Any]], rooms: Mapping[str, Mapping[str, Any]],
                      rules: RoomRules, *, overwrite: bool = False) -> RoomPlan:
    """Externe nach Teilnehmernummer in die Räume aus raumregeln.yaml/extern füllen (Raum für Raum).

    participants: id, nr, raum (Code oder None). Vorhandene Zuordnungen bleiben erhalten und zählen zur Kapazität.
    """
    plan = RoomPlan()
    capacity: dict[str, int] = {}
    for code in rules.extern_rooms:
        db_cap = rooms[code]["capacity"]
        capacity[code] = rules.max_per_room if db_cap is None else min(rules.max_per_room, db_cap)
        if db_cap is not None and db_cap < rules.max_per_room:
            plan.warnings.append(f"Raum {code}: Datenbankkapazität {db_cap} unter max_per_room {rules.max_per_room}")
    plan.capacities = dict(capacity)
    usage = {code: 0 for code in rules.extern_rooms}
    pending: list[dict[str, Any]] = []
    for person in sorted(participants, key=lambda p: (p["nr"] is None, p["nr"], p["id"])):
        room = None if overwrite else person["raum"]
        if room is None:
            pending.append(person)
            continue
        plan.kept += 1
        if room in usage:
            usage[room] += 1
        else:
            plan.warnings.append(f"Teilnahme {person['id']} sitzt in Raum {room}, der nicht zu den Räumen der Externen gehört; bleibt unverändert")
    for person in pending:
        target = next((code for code in rules.extern_rooms if usage[code] < capacity[code]), None)
        if target is None:
            plan.unassigned.append(person)
            continue
        usage[target] += 1
        plan.assignments[person["id"]] = target
    plan.per_room = {code: count for code, count in usage.items() if count}
    for code in plan.per_room:
        plan.per_room_grade[code] = {}
    for person in participants:
        room = plan.assignments.get(person["id"]) or (None if overwrite else person["raum"])
        if room in plan.per_room_grade:
            stufe = str(person["klassenstufe"])
            plan.per_room_grade[room][stufe] = plan.per_room_grade[room].get(stufe, 0) + 1
    return plan


def assign_extern_rooms(db: Any, config_dir: Path, tables: Mapping[str, str], *, dry_run: bool, overwrite: bool = False,
                        plan_id: str | None = None, report: dict[str, Any] | None = None,
                        workspace: Path | None = None) -> dict[str, Any]:
    rules_path = resolve_rules_file(workspace, config_dir)
    log_rules_source(rules_path, config_dir)
    if report is not None:
        report["rules_file"] = str(rules_path)
    rules = load_room_rules(rules_path)
    if not rules.extern_rooms:
        raise RoomError("In raumregeln.yaml fehlt der Abschnitt extern: {rooms: [...]}")
    cnx = db.conn
    if cnx is None:
        raise RoomError("Keine Datenbankverbindung")
    cur = cnx.cursor()
    lock_name = ""
    try:
        part_t = _ident(tables, "extern_teilnahmen_table")
        jahrgang_id = load_state_year(cur, tables)
        rooms = load_rooms(cur, tables)
        issues = [f"Raum {c} fehlt in der Raumtabelle" for c in rules.extern_rooms if c not in rooms]
        issues += [f"Raum {c} ist inaktiv (aktiv = 0)" for c in rules.extern_rooms if c in rooms and not rooms[c]["active"]]
        if report is not None:
            report["issues"] = issues
        if issues:
            raise RoomError("; ".join(issues))
        by_id = {info["id"]: code for code, info in rooms.items()}
        cur.execute(f"SELECT id, nr, klassenstufe, raum_id FROM `{part_t}` WHERE mao_jahrgang_id = %s", (jahrgang_id,))
        participants = [{"id": int(i), "nr": None if n is None else int(n), "klassenstufe": int(k),
                         "raum": by_id.get(int(r)) if r is not None else None} for i, n, k, r in cur.fetchall()]
        if not participants:
            raise RoomError("Keine externen Teilnehmenden im aktuellen Jahrgang (erst import-extern ausführen)")
        plan = plan_extern_rooms(participants, rooms, rules, overwrite=overwrite)
        stats = {"participants": len(participants), "kept": plan.kept, "assigned": len(plan.assignments),
                 "unassigned": len(plan.unassigned), "plan_id": plan.plan_id}
        LOGGER.info("Raumzuteilung Externe | Teilnehmende: %d | bereits mit Raum: %d | neu zugeteilt: %d | offen: %d | Plan-ID: %s%s",
                    stats["participants"], plan.kept, stats["assigned"], stats["unassigned"], plan.plan_id,
                    " | alle Zuordnungen werden ersetzt" if overwrite else "")
        for room, count in plan.per_room.items():
            LOGGER.info("  Raum %-8s %3d / %d", room, count, plan.capacities[room])
        for note in plan.warnings:
            LOGGER.warning("Raumzuteilung Externe | %s", note)
        for person in plan.unassigned:
            LOGGER.warning("  Ohne Raum: E%02d (Stufe %s); weiteren Raum unter extern.rooms in raumregeln.yaml eintragen",
                           person["nr"] or 0, person["klassenstufe"])
        if report is not None:
            report["summary"] = stats
            report["rooms"] = [{"raum": c, "teilnehmende": n, "kapazitaet": plan.capacities[c],
                                "stufen": plan.per_room_grade.get(c, {})} for c, n in sorted(plan.per_room.items())]
            report["unassigned"] = [{"nr": p["nr"], "klassenstufe": p["klassenstufe"]} for p in plan.unassigned]
            report["warnings"] = plan.warnings
        if not dry_run and plan_id and plan_id != plan.plan_id:
            raise RoomError(f"Plan-ID {plan.plan_id} weicht von der geprüften {plan_id} ab: Daten oder Regeln haben sich "
                            "seit dem Dry-Run geändert. Dry-Run wiederholen und neu prüfen.")
        stats["written"] = 0
        if not dry_run and (plan.assignments or overwrite):
            lock_name = f"mao-extern-rooms-{part_t}"
            cur.execute("SELECT GET_LOCK(%s, 10)", (lock_name,))
            if cur.fetchall()[0][0] != 1:
                raise RoomError("Konkurrierende Raumzuteilung oder Sperr-Timeout")
            if cnx.in_transaction:
                cnx.rollback()
            cnx.start_transaction()
            try:
                if overwrite:
                    cur.execute(f"UPDATE `{part_t}` SET raum_id = NULL WHERE mao_jahrgang_id = %s", (jahrgang_id,))
                guard = "" if overwrite else " AND raum_id IS NULL"
                if plan.assignments:
                    cur.executemany(f"UPDATE `{part_t}` SET raum_id = %s WHERE id = %s AND mao_jahrgang_id = %s{guard}",
                                    [(rooms[room]["id"], pid, jahrgang_id) for pid, room in sorted(plan.assignments.items())])
                cur.execute(f"SELECT COUNT(*) FROM `{part_t}` WHERE mao_jahrgang_id = %s AND raum_id IS NOT NULL", (jahrgang_id,))
                total = int(cur.fetchall()[0][0])
                if total != plan.kept + len(plan.assignments):
                    raise RoomError(f"Zählabgleich fehlgeschlagen: {total} mit Raum, erwartet {plan.kept + len(plan.assignments)}")
                cnx.commit()
            except Exception:
                cnx.rollback()
                raise
            stats["written"] = len(plan.assignments)
        if report is not None:
            report["status"] = "ready" if dry_run else "applied"
        return stats
    finally:
        if lock_name:
            try:
                cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                cur.fetchall()
            except Exception:
                LOGGER.warning("Konnte Raumsperre nicht regulär freigeben (DB-Disconnect gibt sie frei)")
        cur.close()
