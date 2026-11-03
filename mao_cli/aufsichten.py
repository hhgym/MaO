"""Aufsichten nach der Raumzuteilung prüfen (nur lesend).

Verglichen werden die belegten Räume (Hauptwettbewerb und Externe) mit der Aufsichtentabelle
(Jahrgang, Raum, Lehrkraft, Rolle). Die Regeln stehen in raumregeln.yaml (Abschnitt aufsichten).
Gemeldet werden fehlende und überzählige Aufsichten, Plätze ohne Lehrkraft, doppelt eingesetzte
oder nicht mehr aktive Lehrkräfte. Es wird nichts geschrieben.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from .extern import ExternError, _ident as _extern_ident, load_state_year
from .rooms import RoomError, RoomRules, load_room_rules, load_rooms, log_rules_source, resolve_rules_file

LOGGER = logging.getLogger(__name__)
# Rollen ohne Raum: Reserve springt ein, die Fachaufsicht ist die Wettkampfleitung (beantwortet Fragen zu den Aufgaben).
ROOMLESS_ROLES = ("Reserve", "Fachaufsicht")


def _ident(tables: Mapping[str, str], key: str) -> str:
    try:
        return _extern_ident(tables, key)
    except ExternError as exc:
        raise RoomError(str(exc)) from exc


def check_supervision(rooms: Mapping[str, int], supervisions: list[dict[str, Any]],
                      teachers: Mapping[int, dict[str, Any]], rules: RoomRules,
                      today: date | None = None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Reine Prüfung. rooms: Raumcode -> Anzahl Teilnehmende; supervisions: raum (Code oder None), lehrkraft_id, rolle."""
    today = today or date.today()
    findings: list[dict[str, str]] = []
    rows: list[dict[str, Any]] = []

    def name(teacher_id: int | None) -> str:
        info = teachers.get(teacher_id) if teacher_id is not None else None
        if info is None:
            return "?"
        return str(info.get("kuerzel") or info.get("nachname") or teacher_id)

    for code in sorted(rooms):
        here = [s for s in supervisions if s["raum"] == code]
        counted = [s for s in here if s["rolle"] in rules.supervisor_roles]
        filled = [s for s in counted if s["lehrkraft_id"] is not None]
        open_slots = len(counted) - len(filled)
        rows.append({"raum": code, "teilnehmende": rooms[code], "benoetigt": rules.supervisors_per_room,
                     "eingetragen": len(filled), "plaetze_ohne_lehrkraft": open_slots,
                     "lehrkraefte": [name(s["lehrkraft_id"]) for s in filled],
                     "weitere": [f"{name(s['lehrkraft_id'])} ({s['rolle']})" for s in here if s not in counted]})
        if len(filled) < rules.supervisors_per_room:
            findings.append({"art": "fehlt", "raum": code,
                             "text": f"Raum {code} ({rooms[code]} Teilnehmende): {len(filled)} von {rules.supervisors_per_room} Aufsichten"})
        elif len(filled) > rules.supervisors_per_room:
            findings.append({"art": "hinweis", "raum": code,
                             "text": f"Raum {code}: {len(filled)} Aufsichten, vorgesehen {rules.supervisors_per_room}"})
        if open_slots:
            findings.append({"art": "hinweis", "raum": code, "text": f"Raum {code}: {open_slots} Platz/Plätze ohne Lehrkraft"})
    for s in supervisions:
        if s["raum"] is not None and rooms.get(s["raum"], 0) == 0:
            findings.append({"art": "hinweis", "raum": str(s["raum"]),
                             "text": f"Aufsicht {name(s['lehrkraft_id'])} im Raum {s['raum']}, in dem niemand schreibt"})
        if s["raum"] is not None and s["rolle"] == "Fachaufsicht":
            findings.append({"art": "hinweis", "raum": str(s["raum"]),
                             "text": f"Fachaufsicht {name(s['lehrkraft_id'])} ist dem Raum {s['raum']} zugeordnet; "
                                     "die Wettkampfleitung gehört keinem Raum an"})
        if s["raum"] is None and s["rolle"] not in ROOMLESS_ROLES:
            findings.append({"art": "hinweis", "raum": "",
                             "text": f"Aufsicht {name(s['lehrkraft_id'])} ({s['rolle']}) ohne Raum"})
    if not any(s["rolle"] == "Fachaufsicht" and s["lehrkraft_id"] is not None for s in supervisions):
        findings.append({"art": "hinweis", "raum": "", "text": "Keine Fachaufsicht (Wettkampfleitung) eingetragen"})
    by_teacher: dict[int, set[str]] = {}
    for s in supervisions:
        if s["lehrkraft_id"] is None:
            continue
        if s["raum"] is not None:
            by_teacher.setdefault(s["lehrkraft_id"], set()).add(str(s["raum"]))
        info = teachers.get(s["lehrkraft_id"])
        if info is None:
            findings.append({"art": "fehler", "raum": str(s["raum"] or ""), "text": f"Lehrkraft-ID {s['lehrkraft_id']} existiert nicht"})
        elif info.get("aktiv") == 0 or (info.get("austritt") is not None and info["austritt"] < today):
            findings.append({"art": "fehler", "raum": str(s["raum"] or ""),
                             "text": f"Lehrkraft {name(s['lehrkraft_id'])} ist nicht mehr aktiv"})
    for teacher_id, places in sorted(by_teacher.items()):
        if len(places) > 1:
            findings.append({"art": "fehler", "raum": "",
                             "text": f"Lehrkraft {name(teacher_id)} ist in mehreren Räumen eingeteilt: {', '.join(sorted(places))}"})
    return rows, findings


def check_aufsichten(db: Any, config_dir: Path, tables: Mapping[str, str], *, workspace: Path | None = None,
                     report: dict[str, Any] | None = None) -> dict[str, int]:
    rules_path = resolve_rules_file(workspace, config_dir)
    log_rules_source(rules_path, config_dir)
    rules = load_room_rules(rules_path)
    cnx = db.conn
    if cnx is None:
        raise RoomError("Keine Datenbankverbindung")
    cur = cnx.cursor()
    try:
        orga, extern = _ident(tables, "orga_table"), _ident(tables, "extern_teilnahmen_table")
        sup_t, teachers_t = _ident(tables, "aufsichten_table"), _ident(tables, "teachers_table")
        try:
            jahrgang_id = load_state_year(cur, tables)
        except ExternError as exc:
            raise RoomError(str(exc)) from exc
        rooms = load_rooms(cur, tables)
        by_id = {info["id"]: code for code, info in rooms.items()}
        counts: dict[str, int] = {}
        for sql, params in ((f"SELECT raum_id, COUNT(*) FROM `{orga}` WHERE raum_id IS NOT NULL GROUP BY raum_id", None),
                            (f"SELECT raum_id, COUNT(*) FROM `{extern}` WHERE raum_id IS NOT NULL AND mao_jahrgang_id = %s "
                             "GROUP BY raum_id", (jahrgang_id,))):
            cur.execute(sql, params)
            for room_id, number in cur.fetchall():
                code = by_id.get(int(room_id))
                if code is not None:
                    counts[code] = counts.get(code, 0) + int(number)
        cur.execute(f"SELECT raum_id, lehrkraft_id, rolle FROM `{sup_t}` WHERE mao_jahrgang_id = %s", (jahrgang_id,))
        supervisions = [{"raum": by_id.get(int(r)) if r is not None else None,
                         "lehrkraft_id": None if lk is None else int(lk), "rolle": str(role)} for r, lk, role in cur.fetchall()]
        cur.execute(f"SELECT id, kuerzel, nachname, aktiv, austrittsdatum FROM `{teachers_t}`")
        teachers = {int(i): {"kuerzel": k, "nachname": n, "aktiv": None if a is None else int(a),
                             "austritt": d if isinstance(d, date) else None} for i, k, n, a, d in cur.fetchall()}
    finally:
        cur.close()
    if not counts:
        raise RoomError("Keine Teilnehmenden mit Raum (erst raumzuteilung ausführen)")
    rows, findings = check_supervision(counts, supervisions, teachers, rules)
    errors = sum(f["art"] in ("fehlt", "fehler") for f in findings)
    stats = {"raeume": len(rows), "aufsichten": len(supervisions), "probleme": errors, "hinweise": len(findings) - errors}
    LOGGER.info("Aufsichten | belegte Räume: %d | Einträge: %d | Probleme: %d | Hinweise: %d",
                stats["raeume"], stats["aufsichten"], errors, stats["hinweise"])
    leitung = [str((teachers.get(int(s["lehrkraft_id"])) or {}).get("kuerzel") or s["lehrkraft_id"]) for s in supervisions
               if s["rolle"] == "Fachaufsicht" and s["lehrkraft_id"] is not None]
    if leitung:
        LOGGER.info("  Wettkampfleitung (Fachaufsicht): %s", ", ".join(leitung))
    for row in rows:
        LOGGER.info("  Raum %-8s %3d Teilnehmende | Aufsicht: %s", row["raum"], row["teilnehmende"],
                    ", ".join(row["lehrkraefte"]) or "–")
    for item in findings:
        (LOGGER.warning if item["art"] != "hinweis" else LOGGER.info)("  %s: %s", item["art"].upper(), item["text"])
    if report is not None:
        report.update({"summary": stats, "wettkampfleitung": leitung, "rooms": rows, "findings": findings, "rules_file": str(rules_path),
                       "status": "ok" if not errors else "probleme"})
    return stats
