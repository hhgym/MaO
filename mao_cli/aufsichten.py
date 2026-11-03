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


def _room_counts(cur: Any, tables: Mapping[str, str], by_id: Mapping[int, str], jahrgang_id: int) -> dict[str, int]:
    """Teilnehmende je belegtem Raum (Hauptwettbewerb und Externe)."""
    orga, extern = _ident(tables, "orga_table"), _ident(tables, "extern_teilnahmen_table")
    counts: dict[str, int] = {}
    for sql, params in ((f"SELECT raum_id, COUNT(*) FROM `{orga}` WHERE raum_id IS NOT NULL GROUP BY raum_id", None),
                        (f"SELECT raum_id, COUNT(*) FROM `{extern}` WHERE raum_id IS NOT NULL AND mao_jahrgang_id = %s "
                         "GROUP BY raum_id", (jahrgang_id,))):
        cur.execute(sql, params)
        for room_id, number in cur.fetchall():
            code = by_id.get(int(room_id))
            if code is not None:
                counts[code] = counts.get(code, 0) + int(number)
    return counts


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
        sup_t, teachers_t = _ident(tables, "aufsichten_table"), _ident(tables, "teachers_table")
        try:
            jahrgang_id = load_state_year(cur, tables)
        except ExternError as exc:
            raise RoomError(str(exc)) from exc
        rooms = load_rooms(cur, tables)
        by_id = {info["id"]: code for code, info in rooms.items()}
        counts = _room_counts(cur, tables, by_id, jahrgang_id)
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


ROLES = ("Standard", "Reserve", "Fachaufsicht")


PREFERRED_SUBJECT = "Mathematik"


def _teacher_list(cur: Any, tables: Mapping[str, str]) -> list[tuple[str, str, str, bool]]:
    """Aktive Lehrkräfte (Kürzel, Vorname, Nachname, unterrichtet Mathematik); Fächer sind optional konfiguriert."""
    teachers_t = _ident(tables, "teachers_table")
    try:
        links, subjects = _ident(tables, "teacher_subjects_table"), _ident(tables, "subjects_table")
    except RoomError:
        cur.execute(f"SELECT kuerzel, vorname, nachname FROM `{teachers_t}` WHERE aktiv = 1 OR aktiv IS NULL ORDER BY kuerzel")
        return [(str(k), str(v or ""), str(n or ""), False) for k, v, n in cur.fetchall() if k]
    cur.execute(f"SELECT l.kuerzel, l.vorname, l.nachname, MAX(f.Fach = %s) FROM `{teachers_t}` l "
                f"LEFT JOIN `{links}` lf ON lf.lehrkraft_id = l.id LEFT JOIN `{subjects}` f ON f.id = lf.fach_id "
                "WHERE l.aktiv = 1 OR l.aktiv IS NULL GROUP BY l.id ORDER BY l.kuerzel", (PREFERRED_SUBJECT,))
    return [(str(k), str(v or ""), str(n or ""), bool(m)) for k, v, n, m in cur.fetchall() if k]


def set_supervision(db: Any, tables: Mapping[str, str], *, room: str | None, kuerzel: list[str], role: str = "Standard",
                    remove: bool = False, dry_run: bool = True) -> dict[str, Any]:
    """Aufsichten eintragen (oder entfernen). Dry-Run ist Standard; Reserve und Fachaufsicht gehören zu keinem Raum."""
    if role not in ROLES:
        raise RoomError(f"Unbekannte Rolle {role} (erlaubt: {', '.join(ROLES)})")
    roomless = role in ROOMLESS_ROLES
    if roomless and room:
        raise RoomError(f"Rolle {role} gehört zu keinem Raum (--raum weglassen)")
    if not roomless and not room:
        raise RoomError("Für die Rolle Standard ist ein Raum nötig")
    names = [k.strip().upper() for k in kuerzel if k.strip()]
    if not names or len(set(names)) != len(names):
        raise RoomError("Lehrkräfte-Kürzel fehlen oder kommen doppelt vor")
    cnx = db.conn
    if cnx is None:
        raise RoomError("Keine Datenbankverbindung")
    cur = cnx.cursor()
    lock_name = ""
    try:
        sup_t, teachers_t = _ident(tables, "aufsichten_table"), _ident(tables, "teachers_table")
        try:
            jahrgang_id = load_state_year(cur, tables)
        except ExternError as exc:
            raise RoomError(str(exc)) from exc
        rooms = load_rooms(cur, tables)
        room_id = None
        if room:
            info = rooms.get(room)
            if info is None or not info["active"]:
                raise RoomError(f"Raum {room} fehlt in der Raumtabelle oder ist inaktiv")
            room_id = info["id"]
        cur.execute(f"SELECT id, kuerzel, aktiv FROM `{teachers_t}`")
        by_kuerzel = {str(k).upper(): (int(i), a) for i, k, a in cur.fetchall() if k}
        unknown = [n for n in names if n not in by_kuerzel]
        if unknown:
            raise RoomError("Unbekannte Lehrkräfte-Kürzel: " + ", ".join(unknown))
        inactive = [n for n in names if by_kuerzel[n][1] is not None and not int(by_kuerzel[n][1])]
        if inactive and not remove:
            raise RoomError("Lehrkräfte nicht aktiv: " + ", ".join(inactive))
        ids = {n: by_kuerzel[n][0] for n in names}
        cur.execute(f"SELECT raum_id, lehrkraft_id, rolle FROM `{sup_t}` WHERE mao_jahrgang_id = %s", (jahrgang_id,))
        existing = {(None if r is None else int(r), int(lk), str(ro)) for r, lk, ro in cur.fetchall()}
        elsewhere: dict[int, set[str]] = {}
        for r, lk, ro in existing:
            if ro == "Standard" and r != room_id:
                elsewhere.setdefault(lk, set()).add(next((c for c, i in rooms.items() if i["id"] == r), "?"))
        todo: list[str] = []
        skipped: list[str] = []
        warnings: list[str] = []
        for n in names:
            key = (room_id, ids[n], role)
            if remove:
                (todo if key in existing else skipped).append(n)
            elif key in existing:
                skipped.append(n)
            else:
                todo.append(n)
                if role == "Standard" and ids[n] in elsewhere:
                    warnings.append(f"{n} ist schon in Raum {', '.join(sorted(elsewhere[ids[n]]))} eingeteilt")
        if remove and not todo:
            raise RoomError("Nichts zu entfernen: " + ", ".join(skipped))
        stats = {"raum": room, "rolle": role, "aktion": "entfernen" if remove else "eintragen", "betroffen": todo,
                 "bereits_so": skipped, "hinweise": warnings}
        LOGGER.info("Aufsicht setzen | %s %s %s: %s | schon so: %s | Modus: %s", role, f"Raum {room}" if room else "(ohne Raum)",
                    stats["aktion"], ", ".join(todo) or "-", ", ".join(skipped) or "-", "DRY-RUN" if dry_run else "APPLY")
        for note in warnings:
            LOGGER.warning("Aufsicht setzen | %s", note)
        if dry_run or not todo:
            return stats
        lock_name = f"mao-aufsichten-{sup_t}"
        cur.execute("SELECT GET_LOCK(%s, 10)", (lock_name,))
        if cur.fetchall()[0][0] != 1:
            raise RoomError("Konkurrierende Änderung oder Sperr-Timeout")
        if cnx.in_transaction:
            cnx.rollback()
        cnx.start_transaction()
        try:
            for n in todo:
                if remove:
                    cur.execute(f"DELETE FROM `{sup_t}` WHERE mao_jahrgang_id = %s AND lehrkraft_id = %s AND rolle = %s AND "
                                + ("raum_id IS NULL" if room_id is None else "raum_id = %s"),
                                (jahrgang_id, ids[n], role) + (() if room_id is None else (room_id,)))
                else:
                    cur.execute(f"INSERT INTO `{sup_t}` (mao_jahrgang_id, raum_id, lehrkraft_id, rolle) VALUES (%s, %s, %s, %s)",
                                (jahrgang_id, room_id, ids[n], role))
            cnx.commit()
        except Exception:
            cnx.rollback()
            raise
        return stats
    finally:
        if lock_name:
            try:
                cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                cur.fetchall()
            except Exception:
                LOGGER.warning("Konnte Sperre nicht regulär freigeben (DB-Disconnect gibt sie frei)")
        cur.close()


PLAN_FILE = "aufsichten-plan.yaml"


def _plan_path(workspace: Path, config_dir: Path, name: Path) -> Path:
    if name.suffix.lower() not in (".yaml", ".yml"):
        raise RoomError("Plandatei braucht die Endung .yaml")
    base, config = workspace.resolve(), config_dir.resolve()
    target = (base / name).resolve()
    if target == base or base not in target.parents or target == config or config in target.parents:
        raise RoomError("Plandatei muss innerhalb des Workspaces liegen (nicht in der Konfiguration)")
    return target


def _load_plan_context(cur: Any, tables: Mapping[str, str]) -> dict[str, Any]:
    sup_t, teachers_t = _ident(tables, "aufsichten_table"), _ident(tables, "teachers_table")
    try:
        jahrgang_id = load_state_year(cur, tables)
    except ExternError as exc:
        raise RoomError(str(exc)) from exc
    rooms = load_rooms(cur, tables)
    by_id = {info["id"]: code for code, info in rooms.items()}
    cur.execute(f"SELECT raum_id, lehrkraft_id, rolle FROM `{sup_t}` WHERE mao_jahrgang_id = %s", (jahrgang_id,))
    current = {(None if r is None else int(r), int(lk), str(ro)) for r, lk, ro in cur.fetchall() if lk is not None}
    cur.execute(f"SELECT id, kuerzel, aktiv FROM `{teachers_t}`")
    teachers = {int(i): (str(k) if k else str(i), a) for i, k, a in cur.fetchall()}
    return {"sup_t": sup_t, "jahrgang_id": jahrgang_id, "rooms": rooms, "by_id": by_id, "current": current,
            "teachers": teachers, "counts": _room_counts(cur, tables, by_id, jahrgang_id)}


def export_plan(db: Any, config_dir: Path, tables: Mapping[str, str], workspace: Path, name: Path = Path(PLAN_FILE)) -> Path:
    """Aktuellen Stand als bearbeitbare Plandatei schreiben (überschreibt nie)."""
    target = _plan_path(workspace, config_dir, name)
    cnx = db.conn
    if cnx is None:
        raise RoomError("Keine Datenbankverbindung")
    cur = cnx.cursor()
    try:
        ctx = _load_plan_context(cur, tables)
    finally:
        cur.close()
    names = ctx["teachers"]
    standard: dict[str, list[str]] = {}
    roleless: dict[str, list[str]] = {"Fachaufsicht": [], "Reserve": []}
    for room_id, lk, role in sorted(ctx["current"], key=lambda t: (str(t[0]), t[1])):
        if role == "Standard" and room_id is not None:
            standard.setdefault(ctx["by_id"].get(room_id, "?"), []).append(names.get(lk, (str(lk), None))[0])
        elif role in roleless:
            roleless[role].append(names.get(lk, (str(lk), None))[0])
    codes = sorted(set(ctx["counts"]) | set(standard))
    lines = ["# Aufsichtenplan: Kürzel je Raum eintragen/tauschen, dann prüfen mit",
             f"#   mao aufsichten --plan {name} --dry-run   und schreiben mit   --apply",
             "# Abschnitte, die fehlen, bleiben unverändert. Ein Eintrag in der Datei = Zielzustand (Rest wird entfernt).",
             "", "raeume:"]
    for code in codes:
        who = ", ".join(standard.get(code, []))
        lines.append(f'  "{code}": [{who}]  # {ctx["counts"].get(code, 0)} Teilnehmende')
    lines += ["", "# Wettkampfleitung (ohne Raum)", f"fachaufsicht: [{', '.join(roleless['Fachaufsicht'])}]",
              "# Springer (ohne Raum)", f"reserve: [{', '.join(roleless['Reserve'])}]", ""]
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8", newline="") as file:
            file.write("\n".join(lines))
    except FileExistsError as exc:
        raise RoomError(f"Plandatei existiert bereits und wird nicht überschrieben: {target}") from exc
    return target


def apply_plan(db: Any, config_dir: Path, tables: Mapping[str, str], workspace: Path, name: Path, *,
               dry_run: bool = True) -> dict[str, Any]:
    """Plandatei lesen und mit der Datenbank abgleichen."""
    import yaml
    target = _plan_path(workspace, config_dir, name)
    try:
        plan = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RoomError(f"Plandatei nicht lesbar: {exc}") from exc
    return apply_plan_data(db, tables, plan, dry_run=dry_run)


def apply_plan_data(db: Any, tables: Mapping[str, str], plan: Any, *, dry_run: bool = True) -> dict[str, Any]:
    """Plan (raeume/fachaufsicht/reserve) mit der Datenbank abgleichen: alles auf einmal, eine Transaktion."""
    if not isinstance(plan, dict) or not set(plan) <= {"raeume", "fachaufsicht", "reserve"} or not plan:
        raise RoomError("Plandatei: erlaubt sind nur die Abschnitte raeume, fachaufsicht, reserve")

    def names_of(value: Any, where: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(x, (str, int)) for x in value):
            raise RoomError(f"Plandatei: {where} muss eine Liste von Kürzeln sein")
        out = [str(x).strip().upper() for x in value if str(x).strip()]
        if len(set(out)) != len(out):
            raise RoomError(f"Plandatei: {where} enthält ein Kürzel doppelt")
        return out

    cnx = db.conn
    if cnx is None:
        raise RoomError("Keine Datenbankverbindung")
    cur = cnx.cursor()
    lock_name = ""
    try:
        ctx = _load_plan_context(cur, tables)
        by_kuerzel = {k.upper(): (i, a) for i, (k, a) in ctx["teachers"].items()}
        rooms, current = ctx["rooms"], ctx["current"]
        desired: set[tuple[int | None, int, str]] = set()
        scope_rooms: set[int] = set()
        scope_roles: set[str] = set()
        warnings: list[str] = []
        per_teacher: dict[str, list[str]] = {}

        def lookup(n: str) -> int:
            if n not in by_kuerzel:
                raise RoomError(f"Unbekanntes Lehrkräfte-Kürzel: {n}")
            if by_kuerzel[n][1] is not None and not int(by_kuerzel[n][1]):
                raise RoomError(f"Lehrkraft {n} ist nicht aktiv")
            return by_kuerzel[n][0]

        raw_rooms = plan.get("raeume")
        if raw_rooms is not None:
            if not isinstance(raw_rooms, dict):
                raise RoomError("Plandatei: raeume muss eine Zuordnung Raumcode → Kürzel sein")
            for code, value in raw_rooms.items():
                info = rooms.get(str(code))
                if info is None or not info["active"]:
                    raise RoomError(f"Raum {code} fehlt in der Raumtabelle oder ist inaktiv")
                scope_rooms.add(info["id"])
                for n in names_of(value, f"Raum {code}"):
                    desired.add((info["id"], lookup(n), "Standard"))
                    per_teacher.setdefault(n, []).append(str(code))
        for key, role in (("fachaufsicht", "Fachaufsicht"), ("reserve", "Reserve")):
            if key in plan:
                scope_roles.add(role)
                for n in names_of(plan[key], key):
                    desired.add((None, lookup(n), role))
        for n, places in sorted(per_teacher.items()):
            if len(places) > 1:
                warnings.append(f"{n} ist in mehreren Räumen eingeteilt: {', '.join(places)}")
        scoped = {t for t in current if (t[2] == "Standard" and t[0] in scope_rooms) or t[2] in scope_roles}
        to_add, to_del = desired - scoped, scoped - desired
        label = {i: k for i, (k, _a) in ctx["teachers"].items()}

        def show(t: tuple[int | None, int, str]) -> str:
            place = ctx["by_id"].get(t[0], "?") if t[0] is not None else "ohne Raum"
            return f"{t[2]} {place}: {label.get(t[1], t[1])}"
        added, removed = sorted(show(t) for t in to_add), sorted(show(t) for t in to_del)
        stats = {"hinzu": added, "entfernt": removed, "unveraendert": len(scoped & desired), "hinweise": warnings}
        LOGGER.info("Aufsichtenplan | +%d −%d unverändert %d | Modus: %s", len(added), len(removed), stats["unveraendert"],
                    "DRY-RUN" if dry_run else "APPLY")
        for line in removed:
            LOGGER.info("Aufsichtenplan | − %s", line)
        for line in added:
            LOGGER.info("Aufsichtenplan | + %s", line)
        for note in warnings:
            LOGGER.warning("Aufsichtenplan | %s", note)
        if dry_run or not (to_add or to_del):
            return stats
        lock_name = f"mao-aufsichten-{ctx['sup_t']}"
        cur.execute("SELECT GET_LOCK(%s, 10)", (lock_name,))
        if cur.fetchall()[0][0] != 1:
            raise RoomError("Konkurrierende Änderung oder Sperr-Timeout")
        if cnx.in_transaction:
            cnx.rollback()
        cnx.start_transaction()
        try:
            for room_id, lk, role in sorted(to_del, key=str):
                cur.execute(f"DELETE FROM `{ctx['sup_t']}` WHERE mao_jahrgang_id = %s AND lehrkraft_id = %s AND rolle = %s AND "
                            + ("raum_id IS NULL" if room_id is None else "raum_id = %s"),
                            (ctx["jahrgang_id"], lk, role) + (() if room_id is None else (room_id,)))
            for room_id, lk, role in sorted(to_add, key=str):
                cur.execute(f"INSERT INTO `{ctx['sup_t']}` (mao_jahrgang_id, raum_id, lehrkraft_id, rolle) VALUES (%s, %s, %s, %s)",
                            (ctx["jahrgang_id"], room_id, lk, role))
            cnx.commit()
        except Exception:
            cnx.rollback()
            raise
        return stats
    finally:
        if lock_name:
            try:
                cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                cur.fetchall()
            except Exception:
                LOGGER.warning("Konnte Sperre nicht regulär freigeben (DB-Disconnect gibt sie frei)")
        cur.close()


SHORT_GRADES = 6  # Räume, in denen nur bis zu dieser Stufe schreibt (Stufen 5/6: 2 statt 4 Stunden)


def rotation_proposal(short_rooms: list[str], rooms_map: Mapping[str, list[str]], history: Mapping[str, list[int]],
                      per_room: int = 1) -> dict[str, Any]:
    """Kurzräume (Stufen 5/6, 2 Stunden) rotierend besetzen: wer am seltensten/längsten nicht dran war, kommt zuerst.

    Pool sind die schon für Räume eingeteilten Lehrkräfte (rooms_map). Wer in einen Kurzraum rückt, tauscht mit
    jemandem, der den Kurzraum verlässt. Reine Funktion: rooms_map Raum → Kürzel, history Kürzel → Jahrgangsnummern.
    """
    pool = sorted({k for team in rooms_map.values() for k in team})
    rank = sorted(pool, key=lambda k: (len(history.get(k, [])), max(history.get(k, []), default=0), k))
    slots = len(short_rooms) * per_room
    chosen = rank[:slots]
    new_map = {code: list(team) for code, team in rooms_map.items()}
    notes: list[str] = []
    if len(pool) < slots:
        notes.append(f"Nur {len(pool)} Lehrkräfte eingeteilt, {slots} Plätze in Kurzräumen")
    target = {code: chosen[n * per_room:(n + 1) * per_room] for n, code in enumerate(sorted(short_rooms))}
    where = {k: code for code, team in rooms_map.items() for k in team}
    leaving = sorted(k for code in short_rooms for k in rooms_map.get(code, []) if k not in chosen)
    entering = sorted((k for k in chosen if where.get(k) not in short_rooms), key=lambda k: where.get(k, ""))
    for code in short_rooms:
        new_map[code] = list(target.get(code, []))
    for k in entering:
        if where.get(k) in new_map and where[k] not in short_rooms:
            new_map[where[k]] = [x for x in new_map[where[k]] if x != k]
    moves = []
    for n, k in enumerate(entering):
        home = where.get(k)
        if n < len(leaving) and home is not None:
            new_map[home] = new_map[home] + [leaving[n]]
            moves.append(f"{k}: {home} → Kurzraum, {leaving[n]}: Kurzraum → {home}")
        else:
            moves.append(f"{k}: {home or '-'} → Kurzraum")
    for k in chosen:
        if where.get(k) in short_rooms and not any(m.startswith(f"{k}:") for m in moves):
            moves.append(f"{k}: bleibt im Kurzraum {where[k]}")
    return {"raeume": new_map, "zuteilung": target, "wechsel": moves, "hinweise": notes, "rang": rank}


def _short_rooms_and_history(cur: Any, tables: Mapping[str, str], by_id: Mapping[int, str],
                             jahrgang_id: int) -> tuple[list[str], dict[int, list[int]]]:
    """Kurzräume des aktuellen Jahrgangs und je Lehrkraft die Jahrgangsnummern früherer Kurzraum-Aufsichten."""
    orga = _ident(tables, "orga_table")
    cur.execute(f"SELECT raum_id, MAX(COALESCE(stufe, CAST(SUBSTRING_INDEX(klasse, '-', 1) AS UNSIGNED))) FROM `{orga}` "
                f"WHERE raum_id IS NOT NULL GROUP BY raum_id")
    short = sorted(by_id[int(r)] for r, m in cur.fetchall() if m is not None and int(m) <= SHORT_GRADES and int(r) in by_id)
    history: dict[int, list[int]] = {}
    try:
        archive, sup_t = _ident(tables, "archive_table"), _ident(tables, "aufsichten_table")
        years = _ident(tables, "jahrgaenge_table")
    except RoomError:
        return short, history
    cur.execute(f"SELECT a.lehrkraft_id, j.jahrgang_nr FROM `{sup_t}` a JOIN `{years}` j ON j.id = a.mao_jahrgang_id "
                f"JOIN (SELECT mao_jahrgang_id, raum_id FROM `{archive}` WHERE raum_id IS NOT NULL "
                f"GROUP BY mao_jahrgang_id, raum_id HAVING MAX(stufe) <= {int(SHORT_GRADES)}) k "
                "ON k.mao_jahrgang_id = a.mao_jahrgang_id AND k.raum_id = a.raum_id "
                "WHERE a.rolle = 'Standard' AND a.lehrkraft_id IS NOT NULL AND a.mao_jahrgang_id <> %s", (jahrgang_id,))
    for lk, nr in cur.fetchall():
        history.setdefault(int(lk), []).append(int(nr))
    return short, history


def interactive_supervision(db: Any, config_dir: Path, tables: Mapping[str, str], *, apply: bool,
                            workspace: Path | None = None, ask: Any = input, out: Any = print) -> dict[str, Any]:
    """Alle Aufsichten in einer Konsolensitzung bearbeiten (Tabelle, Tausch, Rotationsvorschlag), dann gesammelt abschicken."""
    rules = load_room_rules(resolve_rules_file(workspace, config_dir))
    cnx = db.conn
    if cnx is None:
        raise RoomError("Keine Datenbankverbindung")
    cur = cnx.cursor()
    try:
        ctx = _load_plan_context(cur, tables)
        teachers = _teacher_list(cur, tables)
        short, history_ids = _short_rooms_and_history(cur, tables, ctx["by_id"], ctx["jahrgang_id"])
    finally:
        cur.close()
    names = ctx["teachers"]
    known = {k.upper() for k, _a in names.values()}
    history = {names[i][0].upper(): sorted(nrs) for i, nrs in history_ids.items() if i in names}
    state: dict[str, Any] = {"raeume": {}, "fachaufsicht": [], "reserve": []}
    for room_id, lk, role in sorted(ctx["current"], key=lambda t: (str(t[0]), t[1])):
        who = names.get(lk, (str(lk), None))[0].upper()
        if role == "Standard" and room_id is not None:
            state["raeume"].setdefault(ctx["by_id"].get(room_id, "?"), []).append(who)
        elif role == "Fachaufsicht":
            state["fachaufsicht"].append(who)
        elif role == "Reserve":
            state["reserve"].append(who)
    for code in ctx["counts"]:
        state["raeume"].setdefault(code, [])
    if not ctx["counts"]:
        raise RoomError("Keine Teilnehmenden mit Raum (erst raumzuteilung ausführen)")
    need = rules.supervisors_per_room
    has_math = any(t[3] for t in teachers)
    stack: list[str] = []
    import json

    def table() -> None:
        out("\nRaum   Teilnehmende  Aufsicht")
        for code in sorted(state["raeume"]):
            team = state["raeume"][code]
            flag = " K" if code in short else "  "
            gap = "   <-- fehlt" if len(team) < need and ctx["counts"].get(code) else ""
            out(f"{code:<6}{flag} {ctx['counts'].get(code, 0):>5}      {' '.join(team) or '-'}{gap}")
        out(f"Fachaufsicht: {' '.join(state['fachaufsicht']) or '-'} | Reserve: {' '.join(state['reserve']) or '-'}")
        if short:
            out("(K = Kurzraum, Stufe ≤ 6: 2 Stunden Aufsicht)")

    def parse_names(tokens: list[str]) -> list[str] | None:
        found = [t.upper() for t in tokens]
        bad = [t for t in found if t not in known]
        if bad:
            out("  Unbekannte Kürzel: " + ", ".join(bad))
            return None
        return found

    def edit(current: list[str], tokens: list[str]) -> list[str] | None:
        if tokens == ["-"]:
            return []
        if all(t[:1] in "+-" and len(t) > 1 for t in tokens):
            result = list(current)
            for t in tokens:
                parsed = parse_names([t[1:]])
                if parsed is None:
                    return None
                if t[0] == "+" and parsed[0] not in result:
                    result.append(parsed[0])
                elif t[0] == "-":
                    result = [x for x in result if x != parsed[0]]
            return result
        parsed = parse_names(tokens)
        if parsed is None:
            return None
        if len(set(parsed)) != len(parsed):
            out("  Kürzel doppelt")
            return None
        return parsed

    def where_is(k: str) -> list[tuple[str, str | None]]:
        spots = [("raeume", c) for c, team in state["raeume"].items() if k in team]
        spots += [(sec, None) for sec in ("fachaufsicht", "reserve") if k in state[sec]]
        return spots

    out("Alle Aufsichten bearbeiten. Befehle:\n"
        "  <Raum> KÜRZEL...   Raum neu besetzen (+KÜ / -KÜ ändert nur, '-' leert)   z. B. 105 GER NIX\n"
        "  fach KÜRZEL... | reserve KÜRZEL...   Fachaufsicht / Reserve setzen\n"
        "  tausch <Raum> <Raum> | tausch KÜ KÜ   Teams bzw. Personen tauschen\n"
        "  kurz   Rotationsvorschlag für Kurzräume zeigen, 'kurz ok' übernimmt ihn\n"
        f"  l = {'Mathematik-Lehrkräfte' if has_math else 'Lehrkräfte'}, la = alle, z = Tabelle, u = zurück, fertig = prüfen und abschicken, q = abbrechen")
    table()
    result: dict[str, Any] = {"eintraege": 0}
    while True:
        line = str(ask("> ")).strip()
        if not line:
            continue
        cmd, *args = line.split()
        low = cmd.lower()
        if low == "q":
            out("Abgebrochen, nichts geschrieben")
            return result
        if low in ("z", "zeige"):
            table()
            continue
        if low in ("l", "la"):
            shown = [t for t in teachers if low == "la" or t[3] or not has_math]
            out("  " + "; ".join(f"{k} {v} {n}".replace("  ", " ").strip() for k, v, n, _m in shown))
            continue
        if low == "u":
            if not stack:
                out("  Nichts zum Zurücknehmen")
            else:
                state = json.loads(stack.pop())
                table()
            continue
        snapshot = json.dumps(state)
        if low == "fertig":
            plan = {"raeume": state["raeume"], "fachaufsicht": state["fachaufsicht"], "reserve": state["reserve"]}
            try:
                stats = apply_plan_data(db, tables, plan, dry_run=True)
            except RoomError as exc:
                out(f"  {exc}")
                continue
            out(f"\nÄnderungen: +{len(stats['hinzu'])} −{len(stats['entfernt'])}")
            for x in stats["entfernt"]:
                out(f"  − {x}")
            for x in stats["hinzu"]:
                out(f"  + {x}")
            for x in stats["hinweise"]:
                out(f"  Hinweis: {x}")
            result["eintraege"] = len(stats["hinzu"]) + len(stats["entfernt"])
            if not result["eintraege"]:
                out("Keine Änderungen")
                return result
            if not apply:
                out("DRY-RUN: nichts geschrieben. Schreiben mit --apply")
                return result
            if str(ask("In die Datenbank schreiben? [j/N] ")).strip().lower() != "j":
                out("Nicht geschrieben (weiter bearbeiten mit Befehlen, q = abbrechen)")
                continue
            apply_plan_data(db, tables, plan, dry_run=False)
            return result
        if low == "kurz":
            prop = rotation_proposal(short, state["raeume"], history, need)
            if not short:
                out("  Keine Kurzräume (Stufe ≤ 6) in der Raumzuteilung")
                continue
            out("  Vorschlag (wer am seltensten/längsten nicht im Kurzraum war, kommt zuerst):")
            for k in prop["rang"][:max(len(short) * need + 3, 6)]:
                past = history.get(k, [])
                out(f"    {k}: {'noch nie' if not past else f'{len(past)}× (zuletzt Jahrgang {max(past)})'}")
            for code, team in sorted(prop["zuteilung"].items()):
                out(f"  Kurzraum {code}: {' '.join(team) or '-'}")
            for m in prop["wechsel"]:
                out(f"    {m}")
            for h in prop["hinweise"]:
                out(f"  Hinweis: {h}")
            if args[:1] == ["ok"]:
                stack.append(snapshot)
                state["raeume"] = prop["raeume"]
                out("  Vorschlag übernommen (u = zurück)")
                table()
            else:
                out("  Übernehmen mit: kurz ok")
            continue
        if low == "tausch" and len(args) == 2:
            a, b = args
            rooms_ = state["raeume"]
            if a in rooms_ and b in rooms_:
                stack.append(snapshot)
                rooms_[a], rooms_[b] = rooms_[b], rooms_[a]
            else:
                parsed = parse_names(args)
                if parsed is None:
                    continue
                x, y = parsed
                if not where_is(x) or not where_is(y):
                    out("  Beide Lehrkräfte müssen schon eingeteilt sein")
                    continue
                stack.append(snapshot)
                for sec, code in where_is(x) + where_is(y):
                    team = state[sec][code] if code else state[sec]
                    team[:] = [y if t == x else x if t == y else t for t in team]
            table()
            continue
        if low in ("fach", "fachaufsicht", "reserve"):
            key = "reserve" if low == "reserve" else "fachaufsicht"
            new = edit(state[key], args) if args else None
            if new is None:
                out("  Kürzel angeben (z. B. fach MEI)")
                continue
            stack.append(snapshot)
            state[key] = new
            out(f"  {key}: {' '.join(new) or '-'}")
            continue
        if cmd in state["raeume"] and args:
            new = edit(state["raeume"][cmd], args)
            if new is None:
                continue
            stack.append(snapshot)
            state["raeume"][cmd] = new
            dup = [k for k in new for c, t in state["raeume"].items() if c != cmd and k in t]
            if dup:
                out(f"  Hinweis: {', '.join(sorted(set(dup)))} ist auch in einem anderen Raum eingeteilt")
            out(f"  {cmd}: {' '.join(new) or '-'}")
            continue
        if cmd in state["raeume"]:
            out(f"  {cmd}: {' '.join(state['raeume'][cmd]) or '-'}")
            continue
        out("  Unbekannter Befehl oder Raum (Räume ohne Teilnehmende gibt es hier nicht)")
