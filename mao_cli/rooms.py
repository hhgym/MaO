"""Raumzuteilung für MaO_Orga_2.raum_id: Plan berechnen, berichten, erst zuletzt schreiben.

Port der Logik des früheren Monolithen (Top-15-Räume, Stufen 5/6 gemeinsam,
klassenbalancierte Verteilung, Reserveraum). Neu: Vorhandene Zuordnungen
bleiben geschützt, die Top-15 kommen aus einer View mit den Vorjahrespunkten,
und geschrieben wird nur mit --apply.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import itertools
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
SUPERVISOR_ROLES = ("Standard", "Reserve", "Fachaufsicht")  # enum der Aufsichtentabelle


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
    fill: str = "full"  # full: Raum für Raum auffüllen (wenige Aufsichten); even: gleichmäßig verteilen
    extern_rooms: tuple[str, ...] = ()  # Räume der externen Teilnehmenden (mao raumzuteilung --extern)
    flex_max_span: int | None = None  # größte erlaubte Stufenspanne in einem gemischten Sammelraum (None = beliebig)
    flex_style: str = "rein_zuerst"  # rein_zuerst: volle Räume je Stufe, Reste gemeinsam; nachbarn: Stufenblöcke nebeneinander
    flex_rooms: tuple[str, ...] = ()  # Sammelräume: nehmen den Rest aller Stufen mit möglichst wenigen Räumen und Mischungen auf
    supervisors_per_room: int = 1  # Lehrkräfte je belegtem Raum (mao aufsichten)
    supervisor_roles: tuple[str, ...] = ("Standard",)  # Rollen, die als Aufsicht des Raums zählen

    def all_rooms(self) -> set[str]:
        rooms = {r for values in self.grade_rooms.values() for r in values}
        rooms |= set(self.top15_by_grade.values())
        for extra in (self.reserve, self.top15_oberstufe):
            if extra:
                rooms.add(extra)
        return rooms | set(self.flex_rooms)


RULES_FILE = "raumregeln.yaml"


def resolve_rules_file(workspace: Path | None, config_dir: Path) -> Path:
    """Regeln des Jahrgangs (Jahrgangsordner) haben Vorrang vor der zentralen Vorlage im Konfigurationsordner."""
    if workspace is not None and (workspace / RULES_FILE).is_file():
        return workspace / RULES_FILE
    return config_dir / RULES_FILE


def create_year_rules(workspace: Path, config_dir: Path) -> Path:
    """Regeldatei für den Jahrgang aus der zentralen Vorlage anlegen (nie überschreiben)."""
    target = workspace / RULES_FILE
    if target.exists():
        raise ConfigurationError(f"{target} existiert bereits")
    source = config_dir / RULES_FILE
    if not source.is_file():
        raise ConfigurationError(f"Zentrale Vorlage fehlt: {source} (mao config init legt sie an)")
    target.write_text(source.read_text(encoding="utf-8-sig"), encoding="utf-8")
    return target


def log_rules_source(path: Path, config_dir: Path) -> None:
    if path.parent.resolve() == config_dir.resolve():
        LOGGER.warning("Raumregeln | Für diesen Jahrgang gibt es keine eigene %s; verwendet wird die zentrale Vorlage %s. "
                       "Teilnehmerzahlen ändern sich jedes Jahr: mit mao raumzuteilung --regeln-anlegen eine Jahrgangsdatei anlegen "
                       "und anpassen.", RULES_FILE, path)
    else:
        LOGGER.info("Raumregeln | %s", path)


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
        if not isinstance(value, list) or not all(isinstance(x, (str, int)) for x in value):
            raise ConfigurationError(f"{path.name}: grade_rooms.{key} muss eine Liste von Raumcodes sein")
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
    fill = str(data.get("fill", "full"))
    if fill not in ("full", "even"):
        raise ConfigurationError(f"{path.name}: fill muss 'full' oder 'even' sein")
    reserve = rooms.get("reserve")
    ober = top15.get("oberstufe")
    extern = data.get("extern") or {}
    extern_rooms = extern.get("rooms", []) if isinstance(extern, dict) else None
    if (not isinstance(extern, dict) or set(extern) - {"rooms"} or not isinstance(extern_rooms, list)
            or not all(isinstance(x, (str, int)) and not isinstance(x, bool) for x in extern_rooms)
            or len(set(map(str, extern_rooms))) != len(extern_rooms)):
        raise ConfigurationError(f"{path.name}: extern erwartet nur 'rooms' mit einer Liste verschiedener Raumcodes")
    sup = data.get("aufsichten") or {}
    sup_roles = sup.get("zaehlende_rollen", ["Standard"]) if isinstance(sup, dict) else None
    sup_count = sup.get("lehrkraefte_pro_raum", 1) if isinstance(sup, dict) else None
    if (not isinstance(sup, dict) or set(sup) - {"lehrkraefte_pro_raum", "zaehlende_rollen"}
            or not isinstance(sup_count, int) or isinstance(sup_count, bool) or not 1 <= sup_count <= 10
            or not isinstance(sup_roles, list) or not sup_roles or not set(sup_roles) <= set(SUPERVISOR_ROLES)):
        raise ConfigurationError(f"{path.name}: aufsichten erwartet lehrkraefte_pro_raum (1–10) und zaehlende_rollen "
                                 f"(Liste aus {', '.join(SUPERVISOR_ROLES)})")
    flex = rooms.get("sammel", [])
    if (not isinstance(flex, list) or not all(isinstance(x, (str, int)) and not isinstance(x, bool) for x in flex)
            or len(set(map(str, flex))) != len(flex)):
        raise ConfigurationError(f"{path.name}: rooms.sammel erwartet eine Liste verschiedener Raumcodes")
    main_rooms = {r for values in grade_rooms.values() for r in values} | {str(v) for v in by_grade.values()}
    main_rooms |= {str(x) for x in (reserve, ober) if x not in (None, "")}
    flex_style = str(rooms.get("sammel_stil", "rein_zuerst"))
    if flex_style not in ("rein_zuerst", "nachbarn"):
        raise ConfigurationError(f"{path.name}: rooms.sammel_stil muss 'rein_zuerst' oder 'nachbarn' sein")
    span = rooms.get("sammel_max_spanne")
    if span is not None and (not isinstance(span, int) or isinstance(span, bool) or not 0 <= span <= 7):
        raise ConfigurationError(f"{path.name}: rooms.sammel_max_spanne muss eine ganze Zahl von 0 bis 7 sein")
    empty = sorted(k for k, v in grade_rooms.items() if not v)
    if empty and not flex:
        raise ConfigurationError(f"{path.name}: grade_rooms.{', '.join(empty)} ist leer; ohne rooms.sammel gäbe es für diese Stufen keinen Raum")
    clash = sorted(set(map(str, flex)) & main_rooms)
    if clash:
        raise ConfigurationError(f"{path.name}: Sammelraum {', '.join(clash)} ist zugleich ein fester Raum (grade_rooms, top15, reserve)")
    main_rooms |= set(map(str, flex))
    shared = sorted(set(map(str, extern_rooms)) & main_rooms)
    if shared:
        raise ConfigurationError(f"{path.name}: Raum {', '.join(shared)} ist für Externe und Hauptwettbewerb vorgesehen; "
                                 "Externe brauchen eigene Räume")
    return RoomRules(
        grade_rooms=grade_rooms,
        reserve=str(reserve) if reserve not in (None, "") else None,
        top15_by_grade={str(k): str(v) for k, v in by_grade.items()},
        top15_oberstufe=str(ober) if ober not in (None, "") else None,
        top_count=number("top_count", 15),
        max_per_room=number("max_per_room", 15),
        paired_capacity=number("paired_capacity", 30),
        db_capacity_rooms=tuple(str(x) for x in db_rooms),
        fill=fill,
        extern_rooms=tuple(str(x) for x in extern_rooms),
        flex_rooms=tuple(str(x) for x in flex),
        flex_style=flex_style,
        flex_max_span=span,
        supervisors_per_room=sup_count,
        supervisor_roles=tuple(str(x) for x in sup_roles),
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
    capacity_check: list[dict[str, Any]] = field(default_factory=list)
    kept: int = 0
    flex: list[dict[str, Any]] = field(default_factory=list)  # Sammelräume mit Stufenverteilung

    @property
    def plan_id(self) -> str:
        """Kurzprüfsumme der Zuteilung: erkennt Datenänderungen zwischen Dry-Run und --apply."""
        text = json.dumps(sorted(self.assignments.items()))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


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


def capacity_check(by_stufe: Mapping[str, list[dict[str, Any]]], rules: RoomRules, cap: Any,
                   usage: Mapping[str, int], plan: RoomPlan) -> list[dict[str, Any]]:
    """Je Stufe: noch zu verteilende Teilnehmende gegen freie Plätze (Zielräume und Top-Raum)."""
    rows: list[dict[str, Any]] = []
    for stufe in sorted(by_stufe, key=int):
        rooms = list(rules.grade_rooms.get(stufe, []))
        extra = rules.top15_oberstufe if stufe in ("11", "12") else rules.top15_by_grade.get(stufe)
        if extra and extra not in rooms:
            rooms.append(extra)
        free = sum(max(cap(room, stufe) - usage.get(room, 0), 0) for room in dict.fromkeys(rooms))
        rows.append({"stufe": stufe, "teilnehmende": len(by_stufe[stufe]), "freie_plaetze": free})
    reserve_free = max(cap(rules.reserve, "7") - usage.get(rules.reserve, 0), 0) if rules.reserve else 0
    if rules.flex_rooms:
        # Sammelräume nehmen den Rest aller Stufen auf: Engpass nur, wenn der Gesamtrest nicht hineinpasst.
        flex_free = sum(max(cap(room, "7") - usage.get(room, 0), 0) for room in rules.flex_rooms)
        overflow = sum(max(row["teilnehmende"] - row["freie_plaetze"], 0) for row in rows)
        if overflow > flex_free + reserve_free:
            plan.warnings.append(f"Engpass: {overflow} Teilnehmende passen nicht in ihre festen Räume, Sammelräume ({flex_free} frei) "
                                 f"und Reserve ({reserve_free} frei) reichen nicht")
        return rows
    for row in rows:
        if row["teilnehmende"] > row["freie_plaetze"]:
            missing = row["teilnehmende"] - row["freie_plaetze"]
            plan.warnings.append(f"Engpass Stufe {row['stufe']}: {row['teilnehmende']} Teilnehmende, "
                                 f"{row['freie_plaetze']} freie Plätze in den Zielräumen (Reserve: {reserve_free} frei, "
                                 f"{missing} fehlen)")
    return rows


def _flex_segments(order: tuple[int, ...], counts: Mapping[int, int], rooms: list[tuple[str, int]]
                   ) -> tuple[list[dict[int, int]], dict[int, int]]:
    """Stufenblöcke in der gegebenen Reihenfolge Raum für Raum einsortieren. Rückgabe: Belegung je Raum, nicht Untergebrachte."""
    remaining = {s: counts[s] for s in order}
    queue = list(order)
    layout: list[dict[int, int]] = []
    for _code, free in rooms:
        seg: dict[int, int] = {}
        while free > 0 and queue:
            stufe = queue[0]
            take = min(free, remaining[stufe])
            seg[stufe] = seg.get(stufe, 0) + take
            remaining[stufe] -= take
            free -= take
            if remaining[stufe] == 0:
                queue.pop(0)
        layout.append(seg)
    return layout, {s: n for s, n in remaining.items() if n > 0}


def _plan_flex_span(remaining: Mapping[int, int], chosen: list[tuple[str, int]], existing: Mapping[str, set[int]],
                    max_span: int) -> tuple[list[tuple[str, dict[int, int]]], dict[int, int]] | None:
    """Möglichst viele reine Räume, gemischte Räume nur mit Stufen innerhalb von max_span.

    Probiert, wie viele reine Räume je Stufe angelegt werden (0 bis alle möglichen), legt die Reste in aufsteigender
    Stufenfolge hintereinander und wählt: wenigste Überschreitungen der Spanne, dann wenigste gemischte Räume,
    zusätzliche Stufen und Gesamtspanne. None, wenn der Suchraum zu groß ist.
    """
    stufen = sorted(remaining)
    smallest = min(free for _code, free in chosen)
    limits = [remaining[s] // smallest for s in stufen]
    size = 1
    for limit in limits:
        size *= limit + 1
    if size > 20000:
        return None
    best: tuple[tuple[int, int, int, int], dict[str, dict[int, int]], dict[int, int]] | None = None
    for counts in itertools.product(*(range(limit + 1) for limit in limits)):
        left_over = dict(remaining)
        pure: dict[str, dict[int, int]] = {}
        free_rooms = list(chosen)
        feasible = True
        for level, number in zip(stufen, counts):
            for _ in range(number):
                options = [r for r in free_rooms if left_over[level] >= r[1] and set(existing.get(r[0], ())) <= {level}]
                if not options:
                    feasible = False
                    break
                room = max(options, key=lambda r: r[1])
                free_rooms.remove(room)
                pure[room[0]] = {level: room[1]}
                left_over[level] -= room[1]
            if not feasible:
                break
        if not feasible:
            continue
        rest = {s: n for s, n in left_over.items() if n > 0}
        layout, not_placed = _flex_segments(tuple(sorted(rest)), rest, free_rooms)
        violations = mixed = mixes = spread = 0
        for (code, _free), seg in zip(free_rooms, layout):
            if not seg:
                continue
            levels = set(seg) | set(existing.get(code, ()))
            span = max(levels) - min(levels)
            violations += max(0, span - max_span)
            mixed += len(levels) > 1
            mixes += len(levels) - 1
            spread += span
        cost = (violations + sum(not_placed.values()) * 1000, mixed, mixes, spread)
        if best is None or cost < best[0]:
            best = (cost, {**pure, **{c: s for (c, _f), s in zip(free_rooms, layout)}}, not_placed)
    if best is None:
        return None
    return [(code, best[1][code]) for code, _free in chosen if best[1].get(code)], best[2]


def plan_flex(counts: Mapping[int, int], rooms: list[tuple[str, int]],
              existing: Mapping[str, set[int]] | None = None, *,
              pure_first: bool = True, max_span: int | None = None) -> tuple[list[tuple[str, dict[int, int]]], dict[int, int]]:
    """Rest der Teilnehmenden auf die Sammelräume verteilen.

    counts: Stufe -> Anzahl; rooms: (Raumcode, freie Plätze); existing: Raumcode -> Stufen, die dort schon sitzen.
    1. Es werden so wenige Räume wie möglich gewählt (größte freie Kapazität zuerst).
    2. pure_first (Standard): Jeder Raum, den eine Stufe allein füllen kann, wird mit genau dieser Stufe gefüllt.
       Die übrig gebliebenen Reste aller Stufen kommen zusammen in die verbleibenden Räume (Sammelraum).
       Unter den Reihenfolgen der Reste gewinnt die mit den wenigsten gemischten Räumen, dann den wenigsten
       zusätzlichen Stufen, dann der geringsten Stufenspanne.
       Ohne pure_first liegen alle Stufenblöcke hintereinander; gewonnen hat die Reihenfolge mit den wenigsten
       zusätzlichen Stufen je Raum und danach der geringsten Spanne (gemischt wird nur mit Nachbarstufen).
    max_span: nur mit pure_first. Gemischte Räume sollen nur Stufen innerhalb dieser Spanne enthalten (1 = Nachbarstufen);
       dafür werden bei Bedarf auch weniger reine Räume angelegt, die Zahl der Räume bleibt gleich.
    Rückgabe: Belegung je genutztem Raum, nicht untergebrachte Teilnehmende je Stufe.
    """
    existing = existing or {}
    remaining = {s: n for s, n in counts.items() if n > 0}
    total = sum(remaining.values())
    ranked = sorted((r for r in rooms if r[1] > 0), key=lambda r: -r[1])  # stabil: Reihenfolge der Regeldatei bleibt bei Gleichstand
    chosen: list[tuple[str, int]] = []
    capacity = 0
    for room in ranked:
        if capacity >= total:
            break
        chosen.append(room)
        capacity += room[1]
    if pure_first and max_span is not None and remaining and chosen:
        found = _plan_flex_span(remaining, chosen, existing, max_span)
        if found is not None:
            return found
    pure: dict[str, dict[int, int]] = {}
    rest_rooms: list[tuple[str, int]] = []
    for code, free in chosen:
        levels = set(existing.get(code, ()))
        candidates = [s for s in sorted(remaining) if pure_first and remaining[s] >= free and levels <= {s}]
        if candidates:
            level = max(candidates, key=lambda s: (remaining[s], -s))
            pure[code] = {level: free}
            remaining[level] -= free
            if remaining[level] == 0:
                del remaining[level]
        else:
            rest_rooms.append((code, free))
    stufen = sorted(remaining)
    orders = itertools.permutations(stufen) if len(stufen) <= 8 else iter([tuple(stufen)])
    best: tuple[tuple[int, int, int], list[dict[int, int]], dict[int, int]] | None = None
    for order in orders:
        layout, left = _flex_segments(order, remaining, rest_rooms)
        mixed = mixes = spread = 0
        for (code, _free), seg in zip(rest_rooms, layout):
            if not seg:
                continue
            levels = set(seg) | set(existing.get(code, ()))
            mixed += len(levels) > 1
            mixes += len(levels) - 1
            spread += max(levels) - min(levels)
        cost = (mixed if pure_first else 0, mixes, spread)
        if best is None or cost < best[0]:
            best = (cost, layout, left)
    segments = dict(zip((code for code, _ in rest_rooms), best[1])) if best else {}
    result = [(code, pure.get(code) or segments.get(code) or {}) for code, _free in chosen]
    return [(code, seg) for code, seg in result if seg], (best[2] if best else {})


def plan_rooms(participants: list[dict[str, Any]], capacities: Mapping[str, int | None], rules: RoomRules,
               points: Mapping[int, int], *, seed: int) -> RoomPlan:
    """Reiner Planer ohne Datenbankzugriff.

    participants: matrikelnummer, stufe, klasse, raum (vorhandener Raumcode oder None).
    capacities: Raumcode -> Wettbewerbskapazität (None = unbegrenzt).
    points: matrikelnummer -> Vorjahrespunkte (nur Zahlen). Top-15 = die Besten unter den aktuell Teilnehmenden.
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

    # Kapazitätsübersicht vorab (freie Plätze, vorhandene Zuordnungen schon abgezogen)
    plan.capacity_check = capacity_check(by_stufe, rules, cap, usage, plan)

    # 1) Top-15 in ihre Räume: die Besten unter allen aktuell Teilnehmenden (auch bereits platzierten)
    ranked = [(int(p["matrikelnummer"]), points[int(p["matrikelnummer"])], p["stufe"])
              for p in participants if p.get("stufe") is not None and int(p["matrikelnummer"]) in points]
    plan.top15, notes = select_top(ranked, rules.top_count)
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
            if members and not rules.flex_rooms:
                plan.warnings.append(f"Stufe {stufe}: keine Zielräume in den Raumregeln, {len(members)} Teilnehmende offen")
            leftovers_all.extend(members)
            continue
        buckets = buckets_of(members)
        classes = sorted(buckets)
        rng.shuffle(classes)
        left = []
        if rules.fill == "full":
            # Klassen reihum mischen, dann Raum für Raum bis zur Kapazität auffüllen.
            queue = take_round_robin(buckets, len(members))
            for person in queue:
                if not any(place(person, room, stufe) for room in targets):
                    left.append(person)
        else:
            class_index = room_index = 0
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

    # 3b) Sammelräume: Rest aller Stufen mit möglichst wenigen Räumen und möglichst wenig Stufenmischung
    if rules.flex_rooms and leftovers_all:
        by_level: dict[int, list[dict[str, Any]]] = {}
        for person in leftovers_all:
            by_level.setdefault(int(person["stufe"]), []).append(person)
        flex_free = [(room, cap(room, "7") - usage.get(room, 0)) for room in rules.flex_rooms]
        existing = {room: {int(s) for s in plan.per_room_grade.get(room, {})} for room in rules.flex_rooms}
        layout, not_placed = plan_flex({s: len(m) for s, m in by_level.items()}, flex_free, existing,
                                        pure_first=rules.flex_style == "rein_zuerst", max_span=rules.flex_max_span)
        queues = {}
        for level, members in by_level.items():
            buckets = buckets_of(members)
            queues[level] = take_round_robin(buckets, len(members))
        leftovers_all = []
        for room, segment in layout:
            for level, number in segment.items():
                for _ in range(number):
                    person = queues[level].pop(0)
                    if not place(person, room, str(level)):
                        leftovers_all.append(person)
        for level in queues:
            leftovers_all.extend(queues[level])
        plan.flex = [{"raum": room, "stufen": {str(s): n for s, n in segment.items()}} for room, segment in layout]
        if rules.flex_max_span is not None:
            for room, segment in layout:
                levels = set(segment) | set(existing.get(room, ()))
                if max(levels) - min(levels) > rules.flex_max_span:
                    plan.warnings.append(f"Sammelraum {room}: Stufen {min(levels)} bis {max(levels)} gemischt "
                                         f"(erlaubte Spanne {rules.flex_max_span} nicht einzuhalten)")
        if not_placed:
            plan.warnings.append("Sammelräume reichen nicht aus: " + ", ".join(f"{n}× Stufe {s}" for s, n in sorted(not_placed.items())))

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


def load_rooms(cur: Any, tables: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    rooms_table = _ident(tables.get("rooms_table", ""), "rooms_table")
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
    return rooms


def load_room_data(cur: Any, tables: Mapping[str, str]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]],
                                                                    dict[int, int]]:
    orga = _ident(tables.get("orga_table", ""), "orga_table")
    view = _ident(tables.get("points_last_year_view", ""), "points_last_year_view")
    rooms = load_rooms(cur, tables)
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
    points: dict[int, int] = {}
    for matrikel, _klasse, punkte in cur.fetchall():
        value = parse_points(punkte)
        if matrikel is not None and value is not None:
            points[int(matrikel)] = value
    return participants, rooms, points


def review(rules: RoomRules, rooms: Mapping[str, Mapping[str, Any]], participants: list[dict[str, Any]]) -> list[str]:
    """Blockierende Probleme sammeln (unbekannte Räume, leere Tabelle)."""
    issues: list[str] = []
    if not participants:
        issues.append("Keine Teilnehmenden in der Organisationstabelle (erst import-gf ausführen)")
    unknown = sorted(r for r in rules.all_rooms() if r not in rooms)
    if unknown:
        issues.append("Raumcodes aus raumregeln.yaml fehlen in der Raumtabelle: " + ", ".join(unknown))
    inactive = sorted(r for r in rules.all_rooms() if r in rooms and not rooms[r]["active"])
    if inactive:
        issues.append("Inaktive Räume (aktiv = 0) in raumregeln.yaml: " + ", ".join(inactive))
    return issues


def assign_rooms(db: Any, config_dir: Path, tables: Mapping[str, str], *, dry_run: bool, seed: int,
                 overwrite: bool = False, plan_id: str | None = None,
                 report: dict[str, Any] | None = None, workspace: Path | None = None) -> dict[str, Any]:
    rules_path = resolve_rules_file(workspace, config_dir)
    log_rules_source(rules_path, config_dir)
    if report is not None:
        report["rules_file"] = str(rules_path)
    rules = load_room_rules(rules_path)
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
        for code in plan.paired_rooms:
            db_cap = capacities.get(code)
            if db_cap is not None and db_cap < rules.paired_capacity:
                plan.warnings.append(f"Raum {code}: Kapazität laut Datenbank {db_cap} (Regel für 5/6: {rules.paired_capacity})")
        stats = {"participants": len(participants), "kept": plan.kept, "assigned": len(plan.assignments),
                 "unassigned": len(plan.unassigned), "seed": seed, "plan_id": plan.plan_id}
        if report is not None:
            report["summary"] = stats
            report["rooms"] = [{"raum": code, "teilnehmende": plan.per_room[code], "kapazitaet": plan.capacities.get(code),
                                "stufen": plan.per_room_grade.get(code, {})} for code in sorted(plan.per_room)]
            report["unassigned"] = [{"matrikelnummer": p["matrikelnummer"], "stufe": p["stufe"], "klasse": p["klasse"]}
                                    for p in plan.unassigned]
            report["warnings"] = plan.warnings
            report["top15"] = {key: len(values) for key, values in plan.top15.items()}
            report["capacity_check"] = plan.capacity_check
            report["sammelraeume"] = plan.flex
            report["raeume_je_stufe"] = {stufe: [{"raum": room, "anzahl": count} for room, count in rooms]
                                         for stufe, rooms in rooms_by_stufe(plan).items()}
        for note in plan.warnings:
            LOGGER.warning("Raumzuteilung | %s", note)
        _log_plan(plan, stats, overwrite)
        if not dry_run and plan_id and plan_id != plan.plan_id:
            raise RoomError(f"Plan-ID {plan.plan_id} weicht von der geprüften {plan_id} ab: Daten oder Regeln haben sich "
                            "seit dem Dry-Run geändert. Dry-Run wiederholen und neu prüfen.")
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


def rooms_by_stufe(plan: RoomPlan) -> dict[str, list[tuple[str, int]]]:
    """Je Stufe die Räume mit Anzahl, nach Raum sortiert (inklusive bereits vorhandener Zuordnungen)."""
    result: dict[str, list[tuple[str, int]]] = {}
    for room in sorted(plan.per_room_grade, key=lambda r: (_floor(r), r)):
        for stufe, count in plan.per_room_grade[room].items():
            if count:
                result.setdefault(stufe, []).append((room, count))
    return dict(sorted(result.items(), key=lambda item: int(item[0])))


def _log_plan(plan: RoomPlan, stats: Mapping[str, Any], overwrite: bool) -> None:
    LOGGER.info("Raumzuteilung | Teilnehmende: %s | bereits mit Raum: %s | neu zugeteilt: %s | offen: %s | Seed: %s | Plan-ID: %s%s",
                stats["participants"], stats["kept"], stats["assigned"], stats["unassigned"], stats["seed"],
                stats["plan_id"], " | alle Zuordnungen werden ersetzt" if overwrite else "")
    for row in plan.capacity_check:
        LOGGER.info("  Stufe %-3s %3d Teilnehmende, %3d freie Plätze", row["stufe"], row["teilnehmende"], row["freie_plaetze"])
    for key in sorted(plan.top15):
        LOGGER.info("  Top-%d Stufe %s: %d Personen", len(plan.top15[key]), key, len(plan.top15[key]))
    if plan.flex:
        mixed = sum(1 for item in plan.flex if len(item["stufen"]) > 1)
        LOGGER.info("  Sammelräume: %d Räume genutzt, davon %d mit gemischten Stufen", len(plan.flex), mixed)
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
    if plan.unassigned:
        LOGGER.warning("  Ohne Raum von Hand zuteilen: mao raum-setzen --raum CODE --matrikel NUMMER [NUMMER ...] (Dry-Run, mit --apply schreiben)")
    LOGGER.info("Räume je Stufe:")
    for stufe, rooms in rooms_by_stufe(plan).items():
        LOGGER.info("  Stufe %-3s %3d in %d Räumen: %s", stufe, sum(c for _, c in rooms), len(rooms),
                    ", ".join(f"{room} ({count})" for room, count in rooms))


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


def set_room(db: Any, tables: Mapping[str, str], room_code: str, matrikels: list[int], *, dry_run: bool,
             overwrite: bool = False, allow_over_capacity: bool = False,
             report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Einzelnen Teilnehmenden von Hand einen Raum zuweisen (Dry-Run, erst mit --apply geschrieben)."""
    orga = _ident(tables.get("orga_table", ""), "orga_table")
    cnx = db.conn
    if cnx is None:
        raise RoomError("Keine Datenbankverbindung")
    if not matrikels or len(set(matrikels)) != len(matrikels):
        raise RoomError("Matrikelnummern fehlen oder kommen doppelt vor")
    cur = cnx.cursor()
    lock_name = ""
    try:
        rooms = load_rooms(cur, tables)
        info = rooms.get(room_code)
        if info is None:
            raise RoomError(f"Raum {room_code} fehlt in der Raumtabelle")
        if not info["active"]:
            raise RoomError(f"Raum {room_code} ist inaktiv (aktiv = 0)")
        by_id = {value["id"]: code for code, value in rooms.items()}
        marks = ", ".join(["%s"] * len(matrikels))
        cur.execute(f"SELECT matrikelnummer, stufe, klasse, raum_id FROM `{orga}` WHERE matrikelnummer IN ({marks})", tuple(matrikels))
        found = {int(m): (s, k, r) for m, s, k, r in cur.fetchall()}
        unknown = [m for m in matrikels if m not in found]
        if unknown:
            raise RoomError("Nicht in der Organisationstabelle: " + ", ".join(map(str, unknown)))
        taken = {m: by_id.get(int(r)) for m, (_s, _k, r) in found.items() if r is not None and by_id.get(int(r)) != room_code}
        if taken and not overwrite:
            raise RoomError("Schon einem anderen Raum zugeordnet (mit --ueberschreiben ersetzen): "
                            + ", ".join(f"{m} ({code})" for m, code in sorted(taken.items())))
        cur.execute(f"SELECT COUNT(*), COALESCE(SUM(matrikelnummer IN ({marks})), 0) FROM `{orga}` WHERE raum_id = %s",
                    (*matrikels, info["id"]))
        inside, already = (int(x) for x in cur.fetchall()[0])
        new_total = inside - already + len(matrikels)
        capacity = info["capacity"]
        warnings = []
        if capacity is not None and new_total > capacity:
            message = f"Raum {room_code}: {new_total} Teilnehmende, Kapazität {capacity}"
            if not allow_over_capacity:
                raise RoomError(message + " (mit --trotzdem trotzdem zuteilen)")
            warnings.append(message)
        levels = {int(s) if s not in (None, "") else grade(k) for s, k, _r in found.values()}
        stufen = sorted(x for x in levels if x is not None)
        stats = {"raum": room_code, "teilnehmende": len(matrikels), "im_raum_danach": new_total, "stufen": stufen}
        LOGGER.info("Raum setzen | Raum %s: %d Teilnehmende zugeteilt (danach %d im Raum, Stufen der Zugeteilten: %s) | Modus: %s",
                    room_code, len(matrikels), new_total, ", ".join(map(str, stufen)) or "?", "DRY-RUN" if dry_run else "APPLY")
        for note in warnings:
            LOGGER.warning("Raum setzen | %s", note)
        if report is not None:
            report.update({"summary": stats, "warnings": warnings})
        if dry_run:
            if report is not None:
                report["status"] = "ready"
            return stats
        lock_name = f"mao-rooms-{orga}"
        cur.execute("SELECT GET_LOCK(%s, 10)", (lock_name,))
        if cur.fetchall()[0][0] != 1:
            raise RoomError("Konkurrierende Raumzuteilung oder Sperr-Timeout")
        if cnx.in_transaction:
            cnx.rollback()
        cnx.start_transaction()
        try:
            guard = "" if overwrite else " AND raum_id IS NULL"
            cur.execute(f"UPDATE `{orga}` SET raum_id = %s WHERE matrikelnummer IN ({marks}){guard}", (info["id"], *matrikels))
            cur.execute(f"SELECT COUNT(*) FROM `{orga}` WHERE raum_id = %s AND matrikelnummer IN ({marks})", (info["id"], *matrikels))
            if int(cur.fetchall()[0][0]) != len(matrikels):
                raise RoomError("Zählabgleich fehlgeschlagen; nichts geschrieben")
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
                LOGGER.warning("Konnte Raumsperre nicht regulär freigeben (DB-Disconnect gibt sie frei)")
        cur.close()
