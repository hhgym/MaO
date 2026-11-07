"""Einsatz der Oberstufe (Stufe 11 und 12) planen und in die Einsatztabelle schreiben.

Regeln:
- Teilnehmende (Eintrag mit Tnr in der Organisationstabelle) der Stufe 11 und 12: Rolle ``Teilnahme``.
- Stufe 12 ohne Teilnahme: Rolle ``5PK``.
- Stufe 11 ohne Teilnahme: Wunsch aus dem Gravity-Forms-Formular (Aufsicht oder Hof). Wünsche werden erfüllt;
  gibt es mehr Wünsche als Plätze, entscheidet das Los. Wer nichts gewünscht hat oder nicht zum Zug kam, füllt
  freie Aufsichtsplätze (nicht die Reserve; die füllen Wünsche ``Aufsicht`` zuerst), dann die Hofgruppen.
  ``hof_pro_gruppe`` ist eine harte Grenze; wer dann keinen Platz hat, bleibt ohne Rolle (Ziel ``ohne``, wird nicht
  geschrieben) und wird von Hand zugeordnet. ``5PK`` gibt es nie für Stufe 11.
- Aufsicht: ``aufsichten_pro_raum`` Schüler je belegtem Raum plus ``reserve`` Reserve (Modus Reserve ohne Raum).
- Hof: ``hof_gruppen`` Gruppen (Modus Gruppe, gruppe_nr), optional mit höchstens ``hof_pro_gruppe`` je Gruppe.
- Von Hand gesetzte Einträge (quelle = MANUAL) und fremde Rollen (z. B. Korrektur) bleiben unverändert.

Der Zwischenstand liegt als ``einsatz-plan.yaml`` im Jahrgangsordner. Geschrieben wird nur mit ``--apply``.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from .aufsichten import _ident, _plan_path, _room_counts
from .extern import ExternError, load_state_year
from .rooms import RoomError, load_rooms

LOGGER = logging.getLogger(__name__)
PLAN_FILE = "einsatz-plan.yaml"
DEFAULT_FORM_ID = 59
FIELD_MATRIKEL, FIELD_WISH = "1", "4"
WISHES = ("Aufsicht", "Hof")
T_TEILNAHME, T_5PK, T_RESERVE, T_NONE = "teilnahme", "5pk", "reserve", "ohne"


class EinsatzError(RoomError):
    """Ungültige Eingabe oder Daten; es wird nichts geschrieben."""


@dataclass(frozen=True)
class Person:
    matrikel: int
    nachname: str
    vorname: str
    klasse: str
    stufe: int
    participates: bool
    wish: str | None = None

    @property
    def label(self) -> str:
        """Angezeigt wird nur die Matrikelnummer (keine Namen auf dem Bildschirm)."""
        return str(self.matrikel)


@dataclass(frozen=True)
class Settings:
    aufsichten_pro_raum: int = 1
    reserve: int = 3
    hof_gruppen: int = 2
    hof_pro_gruppe: int | None = None
    seed: int = 1

    def check(self) -> None:
        if self.aufsichten_pro_raum < 0 or self.reserve < 0 or self.hof_gruppen < 0:
            raise EinsatzError("Anzahlen dürfen nicht negativ sein")
        if self.hof_pro_gruppe is not None and self.hof_pro_gruppe < 1:
            raise EinsatzError("hof_pro_gruppe muss mindestens 1 sein")


def aufsicht_target(code: str) -> str:
    return f"aufsicht:{code}"


def hof_target(number: int) -> str:
    return f"hof:{number}"


def target_label(target: str) -> str:
    kind, _sep, rest = target.partition(":")
    return {"aufsicht": f"Aufsicht Raum {rest}", "hof": f"Hof Gruppe {rest}", "reserve": "Reserve",
            "teilnahme": "Teilnahme", "5pk": "5. PK", "ohne": "ohne Rolle", "sonst": f"({rest})"}.get(kind, target)


def build_assignment(persons: list[Person], rooms: list[str], settings: Settings,
                     locked: Mapping[int, str] | None = None) -> dict[int, str]:
    """Verteilung berechnen (rein, mit festem Zufallsstart reproduzierbar)."""
    settings.check()
    locked = dict(locked or {})
    rng = random.Random(settings.seed)
    assign: dict[int, str] = {}
    free: list[Person] = []
    for person in sorted(persons, key=lambda p: p.matrikel):
        if person.matrikel in locked:
            assign[person.matrikel] = locked[person.matrikel]
        elif person.participates:
            assign[person.matrikel] = T_TEILNAHME
        elif person.stufe == 12:
            assign[person.matrikel] = T_5PK
        elif person.stufe == 11:
            free.append(person)
    # Reserveplätze stehen vorn: Wünsche "Aufsicht" füllen zuerst sie, dann die Räume
    slots = [T_RESERVE] * settings.reserve
    slots += [aufsicht_target(code) for _ in range(settings.aufsichten_pro_raum) for code in rooms]
    group_size = {n: 0 for n in range(1, settings.hof_gruppen + 1)}
    for target in locked.values():
        if target in slots:
            slots.remove(target)
        if target.startswith("hof:") and int(target[4:]) in group_size:
            group_size[int(target[4:])] += 1

    def free_group() -> int | None:
        open_groups = [n for n, size in group_size.items()
                       if settings.hof_pro_gruppe is None or size < settings.hof_pro_gruppe]
        return min(open_groups, key=lambda n: (group_size[n], n)) if open_groups else None

    def place_hof(person: Person) -> bool:
        group = free_group()
        if group is None:
            return False
        group_size[group] += 1
        assign[person.matrikel] = hof_target(group)
        return True

    wishers = {"Aufsicht": [p for p in free if p.wish == "Aufsicht"], "Hof": [p for p in free if p.wish == "Hof"]}
    rest = [p for p in free if p.wish not in WISHES]
    for group in (*wishers.values(), rest):
        rng.shuffle(group)
    for person in wishers["Aufsicht"]:
        if slots:
            assign[person.matrikel] = slots.pop(0)
        else:
            rest.append(person)
    for person in wishers["Hof"]:
        if not place_hof(person):
            rest.append(person)
    # Wer nicht zum Zug kam oder nichts gewünscht hat: freie Plätze auffüllen (Reihenfolge per Los)
    wished_first = [p for p in rest if p.wish not in WISHES]
    overflow = [p for p in rest if p.wish in WISHES]
    rng.shuffle(overflow)
    for person in wished_first + overflow:
        room_slots = [slot for slot in slots if slot != T_RESERVE]   # Reserve nur für Wunsch "Aufsicht"
        if room_slots:
            slots.remove(room_slots[0])
            assign[person.matrikel] = room_slots[0]
        elif not place_hof(person):
            assign[person.matrikel] = T_NONE
    return assign


# ------------------------------------------------------------------ Modell

class EinsatzModel:
    def __init__(self, persons: list[Person], rooms: list[str], settings: Settings,
                 locked: Mapping[int, str] | None = None, assign: Mapping[int, str] | None = None) -> None:
        self.persons = {p.matrikel: p for p in persons}
        self.rooms = list(rooms)
        self.settings = settings
        self.locked = dict(locked or {})
        self.assign: dict[int, str] = dict(assign) if assign is not None else build_assignment(
            persons, self.rooms, settings, self.locked)
        self.history: list[dict[int, str]] = []
        self.saved = json.dumps(self.assign, sort_keys=True)

    # --- Abfragen
    @property
    def changed(self) -> bool:
        return json.dumps(self.assign, sort_keys=True) != self.saved

    def mark_saved(self) -> None:
        self.saved = json.dumps(self.assign, sort_keys=True)

    def members(self, target: str) -> list[Person]:
        return sorted((self.persons[m] for m, t in self.assign.items() if t == target and m in self.persons),
                      key=lambda p: p.matrikel)

    def targets(self) -> list[str]:
        out = [aufsicht_target(code) for code in self.rooms] + [T_RESERVE]
        out += [hof_target(n) for n in range(1, self.settings.hof_gruppen + 1)] + [T_5PK, T_NONE, T_TEILNAHME]
        return out

    def movable(self, matrikel: int) -> bool:
        person = self.persons.get(matrikel)
        return person is not None and not person.participates and matrikel not in self.locked

    def wish_met(self, person: Person) -> bool | None:
        if person.wish not in WISHES or not self.movable(person.matrikel):
            return None
        target = self.assign.get(person.matrikel, "")
        return target.startswith("hof:") if person.wish == "Hof" else (target.startswith("aufsicht:") or target == T_RESERVE)

    def tag(self, person: Person) -> str:
        """Matrikelnummer mit Wunschstatus: A+ / H+ = Wunsch Aufsicht / Hof erfüllt, A! / H! = nicht erfüllt, - = ohne Wunsch."""
        if person.stufe != 11 or not self.movable(person.matrikel):
            return person.label
        met = self.wish_met(person)
        if met is None:
            return f"{person.label} -"
        return f"{person.label} {(person.wish or '?')[0]}{'+' if met else '!'}"

    def stats(self) -> dict[str, int]:
        free = [p for p in self.persons.values() if p.stufe == 11 and self.movable(p.matrikel)]
        wished = [p for p in free if p.wish in WISHES]
        return {"stufe_11_ohne_teilnahme": len(free), "mit_wunsch": len(wished),
                "wunsch_erfuellt": sum(1 for p in wished if self.wish_met(p)),
                "ohne_wunsch": len(free) - len(wished)}

    def warnings(self) -> list[str]:
        out: list[str] = []
        for code in self.rooms:
            have = len(self.members(aufsicht_target(code)))
            if have < self.settings.aufsichten_pro_raum:
                out.append(f"Raum {code}: {have} von {self.settings.aufsichten_pro_raum} Schüler-Aufsichten")
        have = len(self.members(T_RESERVE))
        if have < self.settings.reserve:
            out.append(f"Reserve: {have} von {self.settings.reserve}")
        open_ = [p.label for p in self.persons.values() if self.assign.get(p.matrikel, T_NONE) == T_NONE]
        if open_:
            out.append(f"Nicht verplant: {len(open_)} (anzeigen: l ohne)")
        for n in range(1, self.settings.hof_gruppen + 1):
            size = len(self.members(hof_target(n)))
            if self.settings.hof_pro_gruppe is not None and size > self.settings.hof_pro_gruppe:
                out.append(f"Hof Gruppe {n}: {size} statt höchstens {self.settings.hof_pro_gruppe}")
        wrong = [p.label for p in self.members(T_5PK) if p.stufe != 12]
        if wrong:
            out.append(f"5. PK bei Stufe 11 (nicht zulässig): {len(wrong)} (anzeigen: l 5pk)")
        missed = [p.label for p in self.persons.values() if self.wish_met(p) is False]
        if missed:
            out.append(f"Wunsch nicht erfüllt: {len(missed)} (anzeigen: w)")
        return out

    def parse_target(self, text: str) -> str | None:
        raw = text.strip()
        low = raw.lower().replace(" ", "")
        if low in ("reserve", "res", "r"):
            return T_RESERVE
        if low in ("5pk", "pk", "5.pk"):
            return T_5PK
        if low in ("ohne", "keine", "-", "none"):
            return T_NONE
        match = re.fullmatch(r"(?:hof|h)(\d+)", low)
        if match and 1 <= int(match.group(1)) <= self.settings.hof_gruppen:
            return hof_target(int(match.group(1)))
        code = re.sub(r"^(?:aufsicht|raum)\s*", "", raw, flags=re.IGNORECASE).strip()
        for room in self.rooms:
            if room.lower() == code.lower():
                return aufsicht_target(room)
        return None

    def find(self, query: str) -> list[Person]:
        text = query.strip().lower()
        if text.isdigit() and int(text) in self.persons:
            return [self.persons[int(text)]]
        words = text.replace(",", " ").split()
        return sorted((p for p in self.persons.values() if all(w in f"{p.matrikel} {p.klasse}".lower() for w in words)),
                      key=lambda p: p.matrikel)

    # --- Änderungen
    def _remember(self) -> None:
        self.history.append(dict(self.assign))

    def move(self, matrikel: int, target: str) -> None:
        if not self.movable(matrikel):
            raise EinsatzError("Dieser Eintrag ist fest (Teilnahme oder von Hand gesetzt)")
        if target not in self.targets() or target == T_TEILNAHME:
            raise EinsatzError("Unbekanntes Ziel")
        self._check_5pk(matrikel, target)
        self._remember()
        self.assign[matrikel] = target

    def _check_5pk(self, matrikel: int, target: str) -> None:
        if target == T_5PK and self.persons[matrikel].stufe != 12:
            raise EinsatzError("5. PK gibt es nur für Schüler der Stufe 12")

    def swap(self, first: int, second: int) -> None:
        if not (self.movable(first) and self.movable(second)):
            raise EinsatzError("Mindestens ein Eintrag ist fest (Teilnahme oder von Hand gesetzt)")
        self._check_5pk(first, self.assign[second])
        self._check_5pk(second, self.assign[first])
        self._remember()
        self.assign[first], self.assign[second] = self.assign[second], self.assign[first]

    def reroll(self, seed: int | None = None) -> None:
        self._remember()
        self.settings = replace(self.settings, seed=seed if seed is not None else self.settings.seed + 1)
        self.assign = build_assignment(list(self.persons.values()), self.rooms, self.settings, self.locked)

    def undo(self) -> bool:
        if not self.history:
            return False
        self.assign = self.history.pop()
        return True

    # --- Darstellung
    def overview(self) -> list[str]:
        """Kompakte Übersicht: nur Anzahlen (Personen siehe ``l ZIEL`` und ``r``)."""
        per_room = self.settings.aufsichten_pro_raum
        have = sum(len(self.members(aufsicht_target(code))) for code in self.rooms)
        lines = [f"  Aufsicht   {have}/{per_room * len(self.rooms)}   ({len(self.rooms)} Räume je {per_room})"]
        lines.append(f"  Reserve    {len(self.members(T_RESERVE))}/{self.settings.reserve}")
        cap = f"/{self.settings.hof_pro_gruppe}" if self.settings.hof_pro_gruppe else ""
        for n in range(1, self.settings.hof_gruppen + 1):
            lines.append(f"  Hof {n}      {len(self.members(hof_target(n)))}{cap}")
        lines.append(f"  5. PK      {len(self.members(T_5PK))}")
        lines.append(f"  Teilnahme  {len(self.members(T_TEILNAHME))}")
        none = len(self.members(T_NONE))
        if none:
            lines.append(f"  OHNE ROLLE {none}")
        fixed = sum(1 for t in self.assign.values() if t.startswith("sonst:"))
        if fixed:
            lines.append(f"  Fremde Rollen {fixed} (unverändert)")
        return lines

    def room_lines(self) -> list[str]:
        """Eine Zeile je Raum mit den Matrikelnummern der Schüler-Aufsichten."""
        return [f"  {code:<6} " + (" ".join(self.tag(p) for p in self.members(aufsicht_target(code))) or "-") for code in self.rooms]


# ------------------------------------------------------------------ Entwurf (einsatz-plan.yaml)

def _item(person: Person) -> str:
    wish = f", Wunsch {person.wish}" if person.wish else ""
    return json.dumps(f"{person.matrikel}  ({person.klasse}{wish})", ensure_ascii=False)


def plan_text(model: EinsatzModel, name: Path = Path(PLAN_FILE)) -> str:
    s = model.settings
    lines = [f"# {name.name}: Zwischenstand der Einsatzplanung (Oberstufe). Bearbeiten, dann:",
             "#   mao einsatz            (Dry-Run, zeigt Änderungen)   ·   mao einsatz --apply   (schreibt in die Datenbank)",
             "# Jeder Eintrag beginnt mit der Matrikelnummer; der Rest der Zeile ist nur zur Lesbarkeit (keine Namen).",
             "einstellungen:",
             f"  aufsichten_pro_raum: {s.aufsichten_pro_raum}", f"  reserve: {s.reserve}", f"  hof_gruppen: {s.hof_gruppen}",
             f"  hof_pro_gruppe: {'null' if s.hof_pro_gruppe is None else s.hof_pro_gruppe}", f"  seed: {s.seed}", "aufsicht:"]
    for code in model.rooms:
        members = model.members(aufsicht_target(code))
        lines.append(f"  {json.dumps(code, ensure_ascii=False)}: " + ("[]" if not members else ""))
        lines += [f"    - {_item(p)}" for p in members]
    for key, target in (("reserve", T_RESERVE),):
        members = model.members(target)
        lines.append(f"{key}:" + (" []" if not members else ""))
        lines += [f"  - {_item(p)}" for p in members]
    lines.append("hof:")
    for n in range(1, s.hof_gruppen + 1):
        members = model.members(hof_target(n))
        lines.append(f"  \"{n}\": " + ("[]" if not members else ""))
        lines += [f"    - {_item(p)}" for p in members]
    for key, target in (("5pk", T_5PK), ("ohne_rolle", T_NONE), ("teilnahme", T_TEILNAHME)):
        members = model.members(target)
        lines.append(f"\"{key}\":" + (" []" if not members else ""))
        lines += [f"  - {_item(p)}" for p in members]
    return "\n".join(lines) + "\n"


def parse_plan(text: str, persons: Mapping[int, Person]) -> tuple[dict[str, Any], dict[int, str]]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise EinsatzError(f"Plandatei unlesbar: {exc}") from exc
    if not isinstance(data, dict):
        raise EinsatzError("Plandatei: Zuordnung erwartet")
    assign: dict[int, str] = {}

    def take(items: Any, target: str, where: str) -> None:
        if items is None:
            return
        if not isinstance(items, list):
            raise EinsatzError(f"Plandatei: {where} muss eine Liste sein")
        for entry in items:
            match = re.match(r"\s*(\d+)", str(entry))
            if not match:
                raise EinsatzError(f"Plandatei: {where}: Eintrag ohne Matrikelnummer: {str(entry)[:40]}")
            matrikel = int(match.group(1))
            if matrikel not in persons:
                raise EinsatzError(f"Plandatei: Matrikelnummer {matrikel} ist keine Person der Stufe 11/12")
            if matrikel in assign:
                raise EinsatzError(f"Plandatei: Matrikelnummer {matrikel} kommt mehrfach vor")
            assign[matrikel] = target

    for code, items in (data.get("aufsicht") or {}).items():
        take(items, aufsicht_target(str(code)), f"aufsicht.{code}")
    take(data.get("reserve"), T_RESERVE, "reserve")
    for number, items in (data.get("hof") or {}).items():
        take(items, hof_target(int(number)), f"hof.{number}")
    take(data.get("5pk"), T_5PK, "5pk")
    take(data.get("ohne_rolle"), T_NONE, "ohne_rolle")
    take(data.get("teilnahme"), T_TEILNAHME, "teilnahme")
    return data.get("einstellungen") or {}, assign


class Draft:
    def __init__(self, workspace: Path, config_dir: Path, name: Path = Path(PLAN_FILE)) -> None:
        self.name, self.path = name, _plan_path(workspace, config_dir, name)

    def exists(self) -> bool:
        return self.path.is_file()

    def save(self, model: EinsatzModel) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(plan_text(model, self.name), encoding="utf-8", newline="")
        os.replace(tmp, self.path)
        model.mark_saved()
        return self.path

    def clear(self) -> bool:
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return False

    def load_into(self, model: EinsatzModel) -> str:
        """Entwurf als Stand übernehmen; fehlende Personen behalten die berechnete Zuordnung."""
        if not self.exists():
            return ""
        saved, assign = parse_plan(self.path.read_text(encoding="utf-8"), model.persons)
        known = set(model.rooms)
        for target in set(assign.values()):
            if target.startswith("aufsicht:") and target[9:] not in known:
                raise EinsatzError(f"Plandatei: unbekannter Raum {target[9:]}")
            if target.startswith("hof:") and int(target[4:]) > model.settings.hof_gruppen:
                raise EinsatzError(f"Plandatei: Hof-Gruppe {target[4:]} ist nicht vorgesehen (hof_gruppen)")
        for matrikel, target in assign.items():
            if matrikel in model.locked or model.persons[matrikel].participates:
                continue
            model._check_5pk(matrikel, target)
            model.assign[matrikel] = target
        model.mark_saved()
        return f"Entwurf {self.name.name} übernommen ({len(assign)} Einträge)"


# ------------------------------------------------------------------ Datenbank

def _gf_wishes(config_dir: Path, form_id: int, roles: Mapping[int, str]) -> dict[int, str]:
    from .gf_import import gf_entries, load_gf_credentials, load_import_settings
    credentials = load_gf_credentials(config_dir / "gf.config")
    try:
        settings = load_import_settings(config_dir / "gf-import.yaml")
        page_size, timeout = settings.page_size, settings.timeout
    except Exception:  # noqa: BLE001 - ohne Importkonfiguration gelten Standardwerte
        page_size, timeout = 100, 30
    return wishes_from_entries(gf_entries(credentials, form_id, page_size, timeout), roles)


def wishes_from_entries(entries: Any, roles: Mapping[int, str]) -> dict[int, str]:
    """Matrikelnummer -> 'Aufsicht' | 'Hof' (aus Feld 1 und 4; Wert = Rollen-ID, Code oder Bezeichnung)."""
    by_name = {code.lower(): code for code in roles.values()}
    out: dict[int, str] = {}
    for entry in entries:
        if entry.get("status") not in (None, "active"):
            continue
        matrikel, value = str(entry.get(FIELD_MATRIKEL, "")).strip(), str(entry.get(FIELD_WISH, "")).strip()
        if not matrikel.isdigit() or not value:
            continue
        code = roles.get(int(value)) if value.isdigit() else by_name.get(value.lower())
        if code in WISHES:
            out[int(matrikel)] = code
    return out


@dataclass
class Context:
    jahrgang_id: int
    persons: list[Person]
    rooms: list[str]
    room_ids: dict[str, int]
    role_ids: dict[str, int]
    existing: dict[int, tuple[int, str, int | None, int | None, str]]
    locked: dict[int, str]
    notes: list[str] = field(default_factory=list)
    tables: dict[str, str] = field(default_factory=dict)


def _target_from_row(row: tuple[int, str, int | None, int | None, str], role_codes: Mapping[int, str],
                     room_codes: Mapping[int, str]) -> str:
    role_id, mode, group, room_id, _source = row
    code = role_codes.get(role_id, f"Rolle {role_id}")
    if code == "Teilnahme":
        return T_TEILNAHME
    if code == "5PK":
        return T_5PK
    if code == "Hof" and group:
        return hof_target(int(group))
    if code == "Aufsicht":
        return T_RESERVE if mode == "Reserve" else aufsicht_target(room_codes.get(room_id or -1, "?"))
    return f"sonst:{code}"


def load_context(db: Any, config_dir: Path, tables: Mapping[str, str], settings: Settings, *, form_id: int,
                 skip_rooms: tuple[str, ...] = (), use_wishes: bool = True) -> Context:
    students_t, orga_t = _ident(tables, "students_table"), _ident(tables, "orga_table")
    einsatz_t, roles_t = _ident(tables, "oberstufe_einsaetze_table"), _ident(tables, "oberstufe_rollen_table")
    cnx = db.conn
    if cnx is None:
        raise EinsatzError("Keine Datenbankverbindung")
    cur = cnx.cursor()
    try:
        try:
            jahrgang_id = load_state_year(cur, tables)
        except ExternError as exc:
            raise EinsatzError(str(exc)) from exc
        cur.execute(f"SELECT id, code FROM `{roles_t}` WHERE aktiv = 1")
        role_codes = {int(i): str(c) for i, c in cur.fetchall()}
        role_ids = {code: i for i, code in role_codes.items()}
        missing = [c for c in ("Teilnahme", "Aufsicht", "Hof", "5PK") if c not in role_ids]
        if missing:
            raise EinsatzError("Rollen fehlen in der Rollentabelle: " + ", ".join(missing))
        cur.execute(f"SELECT DISTINCT matrikelnummer FROM `{orga_t}` WHERE tnr IS NOT NULL AND matrikelnummer IS NOT NULL")
        participants = {int(m) for (m,) in cur.fetchall()}
        cur.execute(f"SELECT matrikelnummer, nachname, vorname, klasse FROM `{students_t}` "
                    "WHERE SUBSTRING_INDEX(klasse, '-', 1) IN ('11', '12')")
        student_rows = cur.fetchall()
        students = [(int(m), str(n or ""), str(v or ""), str(k or "")) for m, n, v, k in student_rows if m is not None]
        rooms = load_rooms(cur, tables)
        by_id = {info["id"]: code for code, info in rooms.items()}
        counts = _room_counts(cur, tables, by_id, jahrgang_id)
        cur.execute(f"SELECT matrikelnummer, rolle_id, modus, gruppe_nr, raum_id, quelle FROM `{einsatz_t}` "
                    "WHERE mao_jahrgang_id = %s", (jahrgang_id,))
        existing = {int(m): (int(r), str(mo or "Standard"), None if g is None else int(g), None if ri is None else int(ri), str(q))
                    for m, r, mo, g, ri, q in cur.fetchall()}
    finally:
        cur.close()
    wishes: dict[int, str] = {}
    notes: list[str] = []
    if use_wishes:
        wishes = _gf_wishes(config_dir, form_id, role_codes)
    no_matrikel = sum(1 for m, *_ in student_rows if m is None)
    if no_matrikel:
        notes.append(f"{no_matrikel} Schüler der Stufe 11/12 ohne Matrikelnummer können nicht verplant werden")
    known = {m for m, *_ in students}
    stray = sorted(m for m in wishes if m not in known)
    if stray:
        notes.append(f"{len(stray)} Wunsch/Wünsche von Personen außerhalb der Stufe 11/12 ignoriert")
    persons = [Person(m, n, v, k, int(k.split("-", 1)[0]), m in participants, wishes.get(m) if m not in participants else None)
               for m, n, v, k in students if k.split("-", 1)[0] in ("11", "12")]
    used = sorted((code for code, number in counts.items() if number > 0 and code not in skip_rooms and rooms[code]["active"]),
                  key=lambda c: (c.isalpha(), c))
    room_codes = {info["id"]: code for code, info in rooms.items()}
    locked = {m: _target_from_row(row, role_codes, room_codes) for m, row in existing.items()
              if row[4] == "MANUAL" or role_codes.get(row[0]) not in ("Teilnahme", "Aufsicht", "Hof", "5PK")}
    if locked:
        notes.append(f"{len(locked)} Eintrag/Einträge bleiben fest (von Hand gesetzt oder fremde Rolle)")
    return Context(jahrgang_id, persons, used, {c: rooms[c]["id"] for c in rooms}, role_ids, existing, locked, notes,
                   {"einsatz": einsatz_t})


def db_row(target: str, ctx: Context) -> tuple[int, str, int | None, int | None] | None:
    """(rolle_id, modus, gruppe_nr, raum_id) für ein Ziel; None bei fremden Rollen."""
    kind, _sep, rest = target.partition(":")
    ids = ctx.role_ids
    if kind == "teilnahme":
        return ids["Teilnahme"], "Standard", None, None
    if kind == "5pk":
        return ids["5PK"], "Standard", None, None
    if kind == "reserve":
        return ids["Aufsicht"], "Reserve", None, None
    if kind == "aufsicht":
        return ids["Aufsicht"], "Standard", None, ctx.room_ids[rest]
    if kind == "hof":
        return ids["Hof"], "Gruppe", int(rest), None
    return None


def diff(model: EinsatzModel, ctx: Context) -> dict[str, list[Any]]:
    new, changed, same = [], [], 0
    for matrikel, target in sorted(model.assign.items()):
        if matrikel in ctx.locked:
            continue
        row = db_row(target, ctx)
        if row is None:
            continue
        old = ctx.existing.get(matrikel)
        if old is None:
            new.append((matrikel, row))
        elif old[:4] != row:
            changed.append((matrikel, row))
        else:
            same += 1
    outside = sorted(m for m, old in ctx.existing.items()
                     if old[4] != "MANUAL" and model.assign.get(m, T_NONE) == T_NONE and m not in ctx.locked)
    return {"neu": new, "geaendert": changed, "unveraendert": [same], "nicht_im_plan": outside}


def write_plan(db: Any, ctx: Context, changes: Mapping[str, list[Any]]) -> int:
    einsatz_t = ctx.tables["einsatz"]
    todo = list(changes["neu"]) + list(changes["geaendert"])
    if not todo:
        return 0
    cnx = db.conn
    cur = cnx.cursor()
    lock_name = f"mao-einsatz-{einsatz_t}"
    got = False
    try:
        cur.execute("SELECT GET_LOCK(%s, 10)", (lock_name,))
        got = cur.fetchall()[0][0] == 1
        if not got:
            raise EinsatzError("Konkurrierende Änderung oder Sperr-Timeout")
        if cnx.in_transaction:
            cnx.rollback()
        cnx.start_transaction()
        try:
            for matrikel, (role_id, mode, group, room_id) in todo:
                cur.execute(
                    f"INSERT INTO `{einsatz_t}` (mao_jahrgang_id, matrikelnummer, rolle_id, modus, gruppe_nr, raum_id, quelle) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 'AUTO') AS new ON DUPLICATE KEY UPDATE rolle_id = new.rolle_id, "
                    "modus = new.modus, gruppe_nr = new.gruppe_nr, raum_id = new.raum_id, quelle = 'AUTO'",
                    (ctx.jahrgang_id, matrikel, role_id, mode, group, room_id))
            cnx.commit()
        except Exception:
            cnx.rollback()
            raise
    finally:
        if got:
            try:
                cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                cur.fetchall()
            except Exception:  # noqa: BLE001
                LOGGER.warning("Konnte Sperre nicht regulär freigeben (DB-Disconnect gibt sie frei)")
        cur.close()
    return len(todo)


# ------------------------------------------------------------------ Ablauf

HELP = """Befehle (Personen werden mit der Matrikelnummer angegeben):
  l                 Übersicht (nur Anzahlen) und Warnungen
  l ZIEL            Matrikelnummern eines Ziels: l 107 · l reserve · l hof1 · l 5pk · l ohne
  r                 alle Räume mit ihren Schüler-Aufsichten
  s TEXT            Person suchen (Matrikelnummer oder Klasse)
  v NR ZIEL         verschieben, z. B. v 123456 hof2 · v 123456 107 · v 123456 ohne
  t NR NR           zwei Personen tauschen
  n [SEED]          neu auslosen (alles außer Fixes)
  w                 Personen mit nicht erfülltem Wunsch
  u                 letzte Änderung zurück
  d                 Entwurf speichern (einsatz-plan.yaml)
  ok                Änderungen gegenüber der Datenbank zeigen (mit --apply: nach Rückfrage schreiben)
  q                 Ende
Kennzeichen: A+ / H+ Wunsch Aufsicht / Hof erfüllt · A! / H! nicht erfüllt · - ohne Wunsch"""


def summary_lines(model: EinsatzModel) -> list[str]:
    stats = model.stats()
    lines = [f"Stufe 11 ohne Teilnahme: {stats['stufe_11_ohne_teilnahme']} · Wunsch {stats['mit_wunsch']} "
             f"(erfüllt {stats['wunsch_erfuellt']}) · ohne Wunsch {stats['ohne_wunsch']} · Seed {model.settings.seed}"]
    lines += model.overview()
    lines += [f"  ! {w}" for w in model.warnings()]
    return lines


def describe_diff(changes: Mapping[str, list[Any]], model: EinsatzModel) -> list[str]:
    lines = [f"Datenbank: {len(changes['neu'])} neu, {len(changes['geaendert'])} geändert, {changes['unveraendert'][0]} unverändert"]
    for label, key in (("neu", "neu"), ("ändern", "geaendert")):
        for matrikel, _row in changes[key][:40]:
            person = model.persons.get(matrikel)
            lines.append(f"  {label}: {person.label if person else matrikel} → {target_label(model.assign[matrikel])}")
        if len(changes[key]) > 40:
            lines.append(f"  … und {len(changes[key]) - 40} weitere")
    if changes["nicht_im_plan"]:
        lines.append(f"  Hinweis: {len(changes['nicht_im_plan'])} vorhandene automatische Einträge sind nicht im Plan (bleiben unverändert)")
    return lines


def _resolve(model: EinsatzModel, query: str, out: Callable[[str], Any]) -> Person | None:
    found = model.find(query)
    if len(found) == 1:
        return found[0]
    out("Keine Person gefunden" if not found else "Mehrdeutig: " + "; ".join(f"{p.label} ({p.klasse})" for p in found[:8]))
    return None


def interactive_einsatz(model: EinsatzModel, db: Any, ctx: Context, *, apply: bool, draft: Draft | None = None,
                        ask: Callable[[str], str] = input, out: Callable[[str], Any] = print) -> dict[str, Any]:
    import shlex
    result: dict[str, Any] = {"geschrieben": 0}
    for line in summary_lines(model):
        out(line)
    out("Hilfe: ?")
    while True:
        try:
            raw = ask("einsatz> ").strip()
        except EOFError:
            raw = "q"
        if not raw:
            continue
        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            out(f"Eingabe nicht lesbar: {exc}")
            continue
        cmd, args = parts[0].lower(), parts[1:]
        try:
            if cmd in ("?", "h", "hilfe"):
                out(HELP)
            elif cmd == "l" and not args:
                for line in summary_lines(model):
                    out(line)
            elif cmd == "l":
                target = model.parse_target(" ".join(args))
                if target is None:
                    out("Unbekanntes Ziel")
                else:
                    out(target_label(target) + ": " + (" ".join(model.tag(p) for p in model.members(target)) or "-"))
            elif cmd == "r":
                for line in model.room_lines():
                    out(line)
            elif cmd == "s" and args:
                for p in model.find(" ".join(args))[:15]:
                    out(f"{model.tag(p)} ({p.klasse}) – {target_label(model.assign.get(p.matrikel, '-'))}"
                        + (f", Wunsch {p.wish}" if p.wish else ""))
            elif cmd == "v" and len(args) >= 2:
                person, target = _resolve(model, args[0], out), model.parse_target(" ".join(args[1:]))
                if person and target is None:
                    out("Unbekanntes Ziel")
                elif person and target:
                    model.move(person.matrikel, target)
                    out(f"{model.tag(person)} → {target_label(target)}")
            elif cmd == "t" and len(args) == 2:
                first, second = _resolve(model, args[0], out), _resolve(model, args[1], out)
                if first and second:
                    model.swap(first.matrikel, second.matrikel)
                    out(f"getauscht: {first.label} ↔ {second.label}")
            elif cmd == "n":
                model.reroll(int(args[0]) if args else None)
                for line in summary_lines(model):
                    out(line)
            elif cmd == "w":
                missed = [p for p in model.persons.values() if model.wish_met(p) is False]
                out("; ".join(f"{p.label} (Wunsch {p.wish}, jetzt {target_label(model.assign[p.matrikel])})"
                              for p in sorted(missed, key=lambda p: p.matrikel)) or "Alle Wünsche erfüllt")
            elif cmd == "u":
                out("Zurückgenommen" if model.undo() else "Nichts zum Zurücknehmen")
            elif cmd == "d":
                if draft is None:
                    out("Kein Entwurf konfiguriert")
                else:
                    out(f"Entwurf gespeichert: {draft.save(model)}")
            elif cmd in ("ok", "s!"):
                changes = diff(model, ctx)
                for line in describe_diff(changes, model):
                    out(line)
                if not apply:
                    out("DRY-RUN: nichts geschrieben. Zum Schreiben mit --apply starten (der Entwurf bleibt erhalten).")
                    if draft is not None:
                        out(f"Entwurf gespeichert: {draft.save(model)}")
                elif ask("In die Datenbank schreiben? [j/N] ").strip().lower() in ("j", "ja"):
                    result["geschrieben"] = write_plan(db, ctx, changes)
                    out(f"{result['geschrieben']} Einträge geschrieben.")
                    if draft is not None and draft.clear():
                        out("Entwurf gelöscht.")
                    model.mark_saved()
                    return result
            elif cmd in ("q", "quit", "exit"):
                if model.changed and draft is not None and ask("Entwurf speichern? [J/n] ").strip().lower() not in ("n", "nein"):
                    out(f"Entwurf gespeichert: {draft.save(model)}")
                return result
            else:
                out("Unbekannter Befehl (? für Hilfe)")
        except EinsatzError as exc:
            out(f"Nicht möglich: {exc}")
        except ValueError as exc:
            out(f"Eingabe ungültig: {exc}")


def run_einsatz(db: Any, config_dir: Path, tables: Mapping[str, str], workspace: Path, *, settings: Settings,
                apply: bool, interactive: bool, fresh: bool, plan: Path | None, form_id: int = DEFAULT_FORM_ID,
                skip_rooms: tuple[str, ...] = (), use_wishes: bool = True,
                report: dict[str, Any] | None = None,
                ask: Callable[[str], str] = input, out: Callable[[str], Any] = print) -> dict[str, Any]:
    ctx = load_context(db, config_dir, tables, settings, form_id=form_id, skip_rooms=skip_rooms, use_wishes=use_wishes)
    draft = Draft(workspace, config_dir, plan or Path(PLAN_FILE))
    if not ctx.persons:
        raise EinsatzError("Keine Schülerinnen und Schüler der Stufe 11/12 gefunden (students_table)")
    model = EinsatzModel(ctx.persons, ctx.rooms, settings, ctx.locked)
    for note in ctx.notes:
        LOGGER.info("Einsatz | %s", note)
    loaded = False
    if not fresh and draft.exists():
        LOGGER.info("Einsatz | %s (neu auslosen mit --neu)", draft.load_into(model))
        loaded = True
    result: dict[str, Any] = {"geschrieben": 0}
    if interactive:
        if not sys.stdin.isatty() and ask is input:
            raise EinsatzError("--interaktiv braucht ein Terminal")
        result = interactive_einsatz(model, db, ctx, apply=apply, draft=draft, ask=ask, out=out)
    else:
        for line in summary_lines(model):
            LOGGER.info("Einsatz | %s", line)
        changes = diff(model, ctx)
        for line in describe_diff(changes, model):
            LOGGER.info("Einsatz | %s", line)
        if apply:
            result["geschrieben"] = write_plan(db, ctx, changes)
            if draft.clear():
                LOGGER.info("Einsatz | Entwurf gelöscht")
        elif not loaded or model.changed or fresh:
            LOGGER.info("Einsatz | Entwurf gespeichert: %s", draft.save(model))
    if report is not None:
        report.update({"status": "ok", "mode": "apply" if apply else "dry-run", "seed": model.settings.seed,
                       "stats": model.stats(), "warnings": model.warnings(), "geschrieben": result["geschrieben"]})
    return result
