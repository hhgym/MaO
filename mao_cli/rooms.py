"""Raumzuteilung für MaO_Orga_2.raum_id: Plan berechnen, berichten, erst zuletzt schreiben.

Port der Logik des früheren Monolithen (Top-15-Räume, Stufen 5/6 gemeinsam,
klassenbalancierte Verteilung, Reserveraum). Neu: Vorhandene Zuordnungen
bleiben geschützt, die Top-15 kommen aus einer View mit den Vorjahrespunkten,
und geschrieben wird nur mit --apply.
"""
from __future__ import annotations

import csv
import json
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from .config import ConfigurationError
from .gf_import import IDENT, grade, report_target

LOGGER = logging.getLogger(__name__)
OBER = "11/12"
GRADES = tuple(str(n) for n in range(5, 13))


class RoomError(ValueError):
    """Ungültige Regeln oder Daten. Es wird nichts geschrieben."""


@dataclass(frozen=True)
class RoomRules:
    grade_rooms: dict[str, list[str]]
    reserve: str | None = None
    top15_by_grade: dict[str, str] = field(default_factory=dict)
    top15_oberstufe: str | None = None
    top_count: int = 15
    max_per_room: int = 15
    paired_capacity: int = 30
    db_capacity_rooms: tuple[str, ...] = ("Aula",)

    def all_rooms(self) -> set[str]:
        rooms = {r for values in self.grade_rooms.values() for r in values}
        rooms |= set(self.top15_by_grade.values())
        for extra in (self.reserve, self.top15_oberstufe):
            if extra:
                rooms.add(extra)
        return rooms


def load_room_rules(path: Path) -> RoomRules:
    if not path.is_file():
        raise ConfigurationError(f"Raumregeln fehlen: {path} (mao config init legt eine Vorlage an)")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"{path.name}: ungültiges YAML ({exc})") from exc
    rooms = data.get("rooms")
    if not isinstance(rooms, dict):
        raise ConfigurationError(f"{path.name}: Abschnitt 'rooms' fehlt")
    raw_grades = rooms.get("grade_rooms")
    if not isinstance(raw_grades, dict) or not raw_grades:
        raise ConfigurationError(f"{path.name}: rooms.grade_rooms fehlt")
    grade_rooms: dict[str, list[str]] = {}
    for key, value in raw_grades.items():
        if str(key) not in GRADES:
            raise ConfigurationError(f"{path.name}: Klassenstufe '{key}' unbekannt (erlaubt 5–12)")
        if not isinstance(value, list) or not value or not all(isinstance(x, (str, int)) for x in value):
            raise ConfigurationError(f"{path.name}: grade_rooms.{key} muss eine nicht leere Liste von Raumcodes sein")
        grade_rooms[str(key)] = [str(x) for x in value]
    top15 = rooms.get("top15") or {}
    if not isinstance(top15, dict):
        raise ConfigurationError(f"{path.name}: rooms.top15 muss ein Mapping sein")
    by_grade = top15.get("by_grade") or {}
    if not isinstance(by_grade, dict) or any(str(k) not in GRADES[:6] for k in by_grade):
        raise ConfigurationError(f"{path.name}: top15.by_grade erwartet Stufen 5–10 als Schlüssel")

    def number(name: str, default: int) -> int:
        value = data.get(name, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ConfigurationError(f"{path.name}: {name} muss eine positive ganze Zahl sein")
        return value

    db_rooms = data.get("db_capacity_rooms", ["Aula"])
    if not isinstance(db_rooms, list):
        raise ConfigurationError(f"{path.name}: db_capacity_rooms muss eine Liste sein")
    reserve = rooms.get("reserve")
    ober = top15.get("oberstufe")
    return RoomRules(
        grade_rooms=grade_rooms,
        reserve=str(reserve) if reserve not in (None, "") else None,
        top15_by_grade={str(k): str(v) for k, v in by_grade.items()},
        top15_oberstufe=str(ober) if ober not in (None, "") else None,
        top_count=number("top_count", 15),
        max_per_room=number("max_per_room", 15),
        paired_capacity=number("paired_capacity", 30),
        db_capacity_rooms=tuple(str(x) for x in db_rooms),
    )


@dataclass
class RoomPlan:
    assignments: dict[int, str] = field(default_factory=dict)
    unassigned: list[dict[str, Any]] = field(default_factory=list)
    per_room: dict[str, int] = field(default_factory=dict)
    per_room_grade: dict[str, dict[str, int]] = field(default_factory=dict)
    top15: dict[str, list[int]] = field(default_factory=dict)
    paired_rooms: list[str] = field(default_factory=list)
    capacities: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    kept: int = 0


def parse_points(value: Any) -> int | None:
    """Nur echte Punktzahlen zählen; Texte wie 'keine Teilnahme' nicht."""
    text = str(value).strip() if value is not None else ""
    return int(text) if re.fullmatch(r"\d+", text) else None


def group_key(stufe: int | None) -> str | None:
    if stufe is None:
        return None
    return OBER if stufe in (11, 12) else str(stufe)


def select_top(points: list[tuple[int, int, int | None]], count: int) -> tuple[dict[str, list[int]], list[str]]:
    """Beste `count` je Gruppe (7–10 einzeln, 11/12 zusammen). Gleichstand: kleinere Matrikelnummer zuerst."""
    groups: dict[str, list[tuple[int, int]]] = {}
    for matrikel, punkte, stufe in points:
        key = group_key(stufe)
        if key is not None:
            groups.setdefault(key, []).append((punkte, matrikel))
    result: dict[str, list[int]] = {}
    notes: list[str] = []
    for key, rows in groups.items():
        rows.sort(key=lambda row: (-row[0], row[1]))
        result[key] = [m for _, m in rows[:count]]
        if len(rows) > count and rows[count - 1][0] == rows[count][0]:
            notes.append(f"Top-{count} Stufe {key}: Punktgleichstand an der Grenze ({rows[count - 1][0]} Punkte); "
                         "es entscheidet die kleinere Matrikelnummer")
    return result, notes


def plan_rooms(participants: list[dict[str, Any]], capacities: Mapping[str, int | None], rules: RoomRules,
               points: list[tuple[int, int, int | None]], *, seed: int) -> RoomPlan:
    """Reiner Planer ohne Datenbankzugriff.

    participants: matrikelnummer, stufe, klasse, raum (vorhandener Raumcode oder None).
    capacities: Raumcode -> Wettbewerbskapazität (None = unbegrenzt).
    points: (matrikelnummer, punkte, aktuelle Stufe) aller aktiven Schüler:innen mit Vorjahrespunkten.
    """
    rng = random.Random(seed)
    plan = RoomPlan()
    rooms_5 = set(rules.grade_rooms.get("5", []))
    rooms_6 = set(rules.grade_rooms.get("6", []))
    plan.paired_rooms = sorted(rooms_5 & rooms_6)
    paired = set(plan.paired_rooms)
    all_rooms = rules.all_rooms()
    usage: dict[str, int] = {room: 0 for room in all_rooms}

    def cap(room: str, stufe: str) -> int:
        db_cap = capacities.get(room)
        limit = db_cap if db_cap is not None else 10 ** 9
        if room in rules.db_capacity_rooms:
            return limit
        if room in paired:
            return min(limit, rules.paired_capacity) if stufe in ("5", "6") else 0
        return min(limit, rules.max_per_room)

    for room in all_rooms | set(rules.db_capacity_rooms):
        plan.capacities[room] = min(cap(room, "5" if room in paired else "7"), 10 ** 9)

    pool: list[dict[str, Any]] = []
    for person in participants:
        if person.get("stufe") is None:
            plan.warnings.append(f"Matrikelnummer {person['matrikelnummer']}: keine Klassenstufe, nicht zugeteilt")
            continue
        if person.get("raum"):
            usage[person["raum"]] = usage.get(person["raum"], 0) + 1
            plan.per_room_grade.setdefault(person["raum"], {}).setdefault(str(person["stufe"]), 0)
            plan.per_room_grade[person["raum"]][str(person["stufe"])] += 1
            plan.kept += 1
        else:
            pool.append(person)
    by_stufe: dict[str, list[dict[str, Any]]] = {}
    for person in pool:
        by_stufe.setdefault(str(person["stufe"]), []).append(person)

    def place(person: dict[str, Any], room: str, stufe: str) -> bool:
        limit = cap(room, stufe)
        if limit <= 0 or usage.get(room, 0) >= limit:
            return False
        usage[room] = usage.get(room, 0) + 1
        plan.assignments[int(person["matrikelnummer"])] = room
        plan.per_room_grade.setdefault(room, {}).setdefault(stufe, 0)
        plan.per_room_grade[room][stufe] += 1
        return True

    def assigned(person: dict[str, Any]) -> bool:
        return int(person["matrikelnummer"]) in plan.assignments

    # 1) Top-15 in ihre Räume
    plan.top15, notes = select_top(points, rules.top_count)
    plan.warnings.extend(notes)
    if rules.top15_oberstufe:
        wanted = set(plan.top15.get(OBER, []))
        for person in by_stufe.get("11", []) + by_stufe.get("12", []):
            if int(person["matrikelnummer"]) in wanted:
                place(person, rules.top15_oberstufe, str(person["stufe"]))
    for stufe, room in rules.top15_by_grade.items():
        wanted = set(plan.top15.get(stufe, []))
        for person in by_stufe.get(stufe, []):
            if int(person["matrikelnummer"]) in wanted:
                place(person, room, stufe)

    # 2) Stufen 5/6: gemeinsame Räume je 15 + 15, klassenbalanciert
    def buckets_of(members: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        buckets: dict[str, list[dict[str, Any]]] = {}
        for person in members:
            buckets.setdefault(person.get("klasse") or "", []).append(person)
        for key in sorted(buckets):
            rng.shuffle(buckets[key])
        return buckets

    def take_round_robin(buckets: dict[str, list[dict[str, Any]]], count: int) -> list[dict[str, Any]]:
        order = sorted(buckets)
        rng.shuffle(order)
        taken: list[dict[str, Any]] = []
        index = 0
        while len(taken) < count and any(buckets.values()):
            key = order[index % len(order)]
            index += 1
            if buckets[key]:
                taken.append(buckets[key].pop(0))
        return taken

    buckets5 = buckets_of([p for p in by_stufe.get("5", []) if not assigned(p)])
    buckets6 = buckets_of([p for p in by_stufe.get("6", []) if not assigned(p)])
    for room in plan.paired_rooms:
        for stufe, buckets in (("5", buckets5), ("6", buckets6)):
            for person in take_round_robin(buckets, rules.max_per_room):
                place(person, room, stufe)

    # 3) Rest je Stufe klassenbalanciert auf die Zielräume
    leftovers_all: list[dict[str, Any]] = []
    for stufe in sorted(by_stufe, key=int):
        members = [p for p in by_stufe[stufe] if not assigned(p)]
        targets = [r for r in rules.grade_rooms.get(stufe, []) if not (r in paired and stufe not in ("5", "6"))]
        if not targets:
            if members:
                plan.warnings.append(f"Stufe {stufe}: keine Zielräume in den Raumregeln, {len(members)} Teilnehmende offen")
            leftovers_all.extend(members)
            continue
        buckets = buckets_of(members)
        classes = sorted(buckets)
        rng.shuffle(classes)
        class_index = room_index = 0
        left: list[dict[str, Any]] = []
        while any(buckets.values()):
            room = targets[room_index % len(targets)]
            room_index += 1
            chosen = None
            for _ in range(len(classes)):
                key = classes[class_index % len(classes)]
                class_index += 1
                if buckets[key]:
                    chosen = buckets[key].pop(0)
                    break
            if chosen is None:
                break
            if not place(chosen, room, stufe):
                left.append(chosen)
        for key in classes:
            left.extend(buckets.get(key, []))
        leftovers_all.extend(left)

    # 4) Überlauf in den Reserveraum
    for person in leftovers_all:
        if not (rules.reserve and place(person, rules.reserve, str(person["stufe"]))):
            plan.unassigned.append(person)

    plan.per_room = {room: count for room, count in usage.items() if count > 0}
    return plan


# ---------------------------------------------------------------- Datenbank

def _ident(name: str, label: str) -> str:
    if not IDENT.fullmatch(name or "") or name.startswith("DEMO_"):
        raise RoomError(f"{label} fehlt oder ist ein DEMO-Platzhalter: tables.ini prüfen")
    return name


def load_room_data(cur: Any, tables: Mapping[str, str]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]],
                                                                    list[tuple[int, int, int | None]]]:
    orga = _ident(tables.get("orga_table", ""), "orga_table")
    rooms_table = _ident(tables.get("rooms_table", ""), "rooms_table")
    view = _ident(tables.get("points_last_year_view", ""), "points_last_year_view")
    cur.execute(f"SELECT id, raumcode, kapazitaet_wettbewerb, aktiv FROM `{rooms_table}`")
    rooms: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for room_id, code, capacity, active in cur.fetchall():
        code = str(code)
        if code in rooms:
            duplicates.add(code)
        rooms[code] = {"id": int(room_id), "capacity": None if capacity is None else int(capacity),
                       "active": active is None or bool(active)}
    if duplicates:
        raise RoomError("Raumcodes nicht eindeutig (mehrere Gebäude?): " + ", ".join(sorted(duplicates)))
    by_id = {info["id"]: code for code, info in rooms.items()}
    cur.execute(f"SELECT matrikelnummer, stufe, klasse, raum_id FROM `{orga}`")
    participants: list[dict[str, Any]] = []
    for matrikel, stufe, klasse, raum_id in cur.fetchall():
        if matrikel is None:
            continue
        level = int(stufe) if stufe not in (None, "") else grade(klasse)
        participants.append({"matrikelnummer": int(matrikel), "stufe": level, "klasse": klasse or "",
                             "raum": by_id.get(int(raum_id)) if raum_id is not None else None,
                             "raum_id": raum_id})
    cur.execute(f"SELECT Matrikelnummer, Klasse, Punkte FROM `{view}`")
    points: list[tuple[int, int, int | None]] = []
    for matrikel, klasse, punkte in cur.fetchall():
        value = parse_points(punkte)
        if matrikel is not None and value is not None:
            points.append((int(matrikel), value, grade(klasse)))
    return participants, rooms, points


def review(rules: RoomRules, rooms: Mapping[str, Mapping[str, Any]], participants: list[dict[str, Any]]) -> list[str]:
    """Blockierende Probleme sammeln (unbekannte Räume, leere Tabelle)."""
    issues: list[str] = []
    if not participants:
        issues.append("Keine Teilnehmenden in der Organisationstabelle (erst import-gf ausführen)")
    unknown = sorted(r for r in rules.all_rooms() if r not in rooms)
    if unknown:
        issues.append("Raumcodes aus raumregeln.yaml fehlen in der Raumtabelle: " + ", ".join(unknown))
    return issues


def assign_rooms(db: Any, config_dir: Path, tables: Mapping[str, str], *, dry_run: bool, seed: int,
                 overwrite: bool = False, report: dict[str, Any] | None = None) -> dict[str, int]:
    rules = load_room_rules(config_dir / "raumregeln.yaml")
    cnx = db.conn
    if cnx is None:
        raise RoomError("Keine Datenbankverbindung")
    cur = cnx.cursor()
    lock_name = ""
    try:
        participants, rooms, points = load_room_data(cur, tables)
        issues = review(rules, rooms, participants)
        if report is not None:
            report["seed"] = seed
            report["issues"] = issues
        if issues:
            raise RoomError("; ".join(issues))
        capacities = {code: info["capacity"] for code, info in rooms.items()}
        working = [{**p, "raum": None} for p in participants] if overwrite else participants
        plan = plan_rooms(working, capacities, rules, points, seed=seed)
        for code in sorted(plan.per_room):
            if not rooms[code]["active"]:
                plan.warnings.append(f"Raum {code} ist in der Raumtabelle als inaktiv markiert")
        for code in plan.paired_rooms:
            db_cap = capacities.get(code)
            if db_cap is not None and db_cap < rules.paired_capacity:
                plan.warnings.append(f"Raum {code}: Kapazität laut Datenbank {db_cap} (Regel für 5/6: {rules.paired_capacity})")
        stats = {"participants": len(participants), "kept": plan.kept, "assigned": len(plan.assignments),
                 "unassigned": len(plan.unassigned), "seed": seed}
        if report is not None:
            report["summary"] = stats
            report["rooms"] = [{"raum": code, "teilnehmende": plan.per_room[code], "kapazitaet": plan.capacities.get(code),
                                "stufen": plan.per_room_grade.get(code, {})} for code in sorted(plan.per_room)]
            report["unassigned"] = [{"matrikelnummer": p["matrikelnummer"], "stufe": p["stufe"], "klasse": p["klasse"]}
                                    for p in plan.unassigned]
            report["warnings"] = plan.warnings
            report["top15"] = {key: len(values) for key, values in plan.top15.items()}
        for note in plan.warnings:
            LOGGER.warning("Raumzuteilung | %s", note)
        _log_plan(plan, stats, overwrite)
        if not dry_run:
            lock_name = f"mao-rooms-{tables['orga_table']}"
            _persist(cnx, cur, tables["orga_table"], rooms, plan, overwrite, lock_name)
            stats["written"] = len(plan.assignments)
        else:
            stats["written"] = 0
        if report is not None:
            report["status"] = "applied" if not dry_run else "ready"
        return stats
    finally:
        if lock_name:
            try:
                cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                cur.fetchall()
            except Exception:
                LOGGER.warning("Konnte Raumsperre nicht regulär freigeben (DB-Disconnect gibt sie frei)")
        cur.close()


def _log_plan(plan: RoomPlan, stats: Mapping[str, int], overwrite: bool) -> None:
    LOGGER.info("Raumzuteilung | Teilnehmende: %s | bereits mit Raum: %s | neu zugeteilt: %s | offen: %s | Seed: %s%s",
                stats["participants"], stats["kept"], stats["assigned"], stats["unassigned"], stats["seed"],
                " | alle Zuordnungen werden ersetzt" if overwrite else "")
    for room in sorted(plan.per_room, key=lambda r: (_floor(r), r)):
        parts = ", ".join(f"{count}× Stufe {stufe}" for stufe, count in sorted(
            plan.per_room_grade.get(room, {}).items(), key=lambda item: int(item[0])))
        LOGGER.info("  Raum %-8s %3d / %-4s %s", room, plan.per_room[room],
                    plan.capacities.get(room, "") if plan.capacities.get(room, 0) < 10 ** 9 else "∞", parts)
    for person in plan.unassigned[:20]:
        LOGGER.warning("  Ohne Raum: Matrikelnummer %s | Stufe %s | Klasse %s",
                       person["matrikelnummer"], person["stufe"], person["klasse"])
    if len(plan.unassigned) > 20:
        LOGGER.warning("  … und %d weitere (vollständig mit --report)", len(plan.unassigned) - 20)


def _floor(room: str) -> int:
    match = re.match(r"^(\d)", room)
    return int(match.group(1)) if match else 999


def _persist(cnx: Any, cur: Any, orga_table: str, rooms: Mapping[str, Mapping[str, Any]], plan: RoomPlan,
             overwrite: bool, lock_name: str) -> None:
    _ident(orga_table, "orga_table")
    cur.execute("SELECT GET_LOCK(%s, 10)", (lock_name,))
    if cur.fetchall()[0][0] != 1:
        raise RoomError("Konkurrierende Raumzuteilung oder Sperr-Timeout")
    if cnx.in_transaction:
        cnx.rollback()
    cnx.start_transaction()
    try:
        if overwrite:
            cur.execute(f"UPDATE `{orga_table}` SET raum_id = NULL")
        guard = "" if overwrite else " AND raum_id IS NULL"
        rows = [(rooms[room]["id"], matrikel) for matrikel, room in sorted(plan.assignments.items())]
        if rows:
            cur.executemany(f"UPDATE `{orga_table}` SET raum_id = %s WHERE matrikelnummer = %s{guard}", rows)
        cur.execute(f"SELECT COUNT(*) FROM `{orga_table}` WHERE raum_id IS NOT NULL")
        total = int(cur.fetchall()[0][0])
        if total != plan.kept + len(plan.assignments):
            raise RoomError(f"Zählabgleich fehlgeschlagen: {total} mit Raum, erwartet {plan.kept + len(plan.assignments)}")
        cnx.commit()
    except Exception:
        cnx.rollback()
        raise


def write_room_report(workspace: Path, config_dir: Path, filename: Path, report: Mapping[str, Any]) -> Path:
    target = report_target(workspace, config_dir, filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8", newline="") as file:
            if target.suffix.lower() == ".json":
                json.dump(report, file, ensure_ascii=False, indent=2, sort_keys=True)
                file.write("\n")
            else:
                writer = csv.writer(file)
                writer.writerow(["raum", "teilnehmende", "kapazitaet", "stufen"])
                for item in report.get("rooms", []):
                    stufen = "; ".join(f"{k}: {v}" for k, v in sorted(item["stufen"].items(), key=lambda i: int(i[0])))
                    writer.writerow([item["raum"], item["teilnehmende"], item["kapazitaet"], stufen])
                writer.writerow(["OHNE RAUM", len(report.get("unassigned", [])), "", ""])
    except FileExistsError as exc:
        raise ConfigurationError(f"Bericht existiert bereits und wird nicht überschrieben: {target}") from exc
    return target
