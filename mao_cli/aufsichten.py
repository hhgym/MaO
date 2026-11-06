"""Aufsichten nach der Raumzuteilung prüfen (nur lesend).

Verglichen werden die belegten Räume (Hauptwettbewerb und Externe) mit der Aufsichtentabelle
(Jahrgang, Raum, Lehrkraft, Rolle). Die Regeln stehen in raumregeln.yaml (Abschnitt aufsichten).
Gemeldet werden fehlende und überzählige Aufsichten, Plätze ohne Lehrkraft, doppelt eingesetzte
oder nicht mehr aktive Lehrkräfte. Es wird nichts geschrieben.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from .extern import ExternError, _ident as _extern_ident, load_state_year
from .rooms import RoomError, RoomRules, load_room_rules, load_rooms, log_rules_source, resolve_rules_file

LOGGER = logging.getLogger(__name__)
# Rollen ohne Raum: Reserve springt ein, die Fachaufsicht ist die Wettkampfleitung (beantwortet Fragen zu den Aufgaben),
# Abiturerstellung: Lehrkräfte, die statt der Aufsicht eine Abituraufgabe erstellen.
ROOMLESS_ROLES = ("Reserve", "Fachaufsicht", "Abiturerstellung")
# Zeilen ohne Raum in Plandatei und Konsole: Schlüssel → Rolle
ROLE_ROWS = {"fachaufsicht": "Fachaufsicht", "reserve": "Reserve", "abitur": "Abiturerstellung"}
NOTE_MAX = 200


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


ROLES = ("Standard", "Reserve", "Fachaufsicht", "Abiturerstellung")


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
PLAN_KEYS = {"raeume", "fachaufsicht", "reserve", "abitur", "bemerkungen"}


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
    cur.execute(f"SELECT raum_id, lehrkraft_id, rolle, bemerkung FROM `{sup_t}` WHERE mao_jahrgang_id = %s "
                "AND bemerkung IS NOT NULL AND bemerkung <> ''", (jahrgang_id,))
    notes = {(None if r is None else int(r), int(lk), str(ro)): str(b).strip() for r, lk, ro, b in cur.fetchall() if lk is not None}
    return {"sup_t": sup_t, "jahrgang_id": jahrgang_id, "rooms": rooms, "by_id": by_id, "current": current, "notes": notes,
            "teachers": teachers, "counts": _room_counts(cur, tables, by_id, jahrgang_id)}


def state_from_context(ctx: Mapping[str, Any]) -> dict[str, Any]:
    """Bearbeitungsstand (Räume, Rollen ohne Raum, Bemerkungen) aus dem Datenbankstand."""
    names = ctx["teachers"]
    state: dict[str, Any] = {"raeume": {}, "fachaufsicht": [], "reserve": [], "abitur": [], "bemerkungen": {}}
    by_role = {role: key for key, role in ROLE_ROWS.items()}
    for room_id, lk, role in sorted(ctx["current"], key=lambda t: (str(t[0]), t[1])):
        who = names.get(lk, (str(lk), None))[0].upper()
        if role == "Standard" and room_id is not None:
            row = ctx["by_id"].get(room_id, "?")
            state["raeume"].setdefault(row, []).append(who)
        elif role in by_role:
            row = by_role[role]
            state[row].append(who)
        else:
            continue
        note = ctx["notes"].get((room_id, lk, role))
        if note:
            state["bemerkungen"][f"{row}|{who}"] = note
    return state


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
    state = state_from_context(ctx)
    for code in sorted(set(ctx["counts"]) | set(state["raeume"])):
        state["raeume"].setdefault(code, [])
    state["raeume"] = {c: state["raeume"][c] for c in sorted(state["raeume"])}
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8", newline="") as file:
            file.write(plan_text(state, ctx["counts"], {}, name))
    except FileExistsError as exc:
        raise RoomError(f"Plandatei existiert bereits und wird nicht überschrieben: {target}") from exc
    return target


def plan_text(state: Mapping[str, Any], counts: Mapping[str, int], levels: Mapping[str, list[str]], name: Path) -> str:
    lines = ["# Aufsichtenplan: Kürzel je Raum eintragen/tauschen, dann prüfen mit",
             f"#   mao aufsichten --plan {name} --dry-run   und schreiben mit   --apply",
             "#   (oder weiterbearbeiten mit: mao aufsichten --interaktiv)",
             "# Abschnitte, die fehlen, bleiben unverändert. Ein Eintrag in der Datei = Zielzustand (Rest wird entfernt).",
             "", "raeume:"]
    for code in sorted(state["raeume"]):
        who = ", ".join(state["raeume"][code])
        stufen = f", Stufe {', '.join(levels[code])}" if levels.get(code) else ""
        lines.append(f'  "{code}": [{who}]  # {counts.get(code, 0)} Teilnehmende{stufen}')
    lines += ["", "# Wettkampfleitung (ohne Raum)", f"fachaufsicht: [{', '.join(state['fachaufsicht'])}]",
              "# Springer (ohne Raum)", f"reserve: [{', '.join(state['reserve'])}]",
              "# Abiturerstellung statt Aufsicht (ohne Raum)", f"abitur: [{', '.join(state.get('abitur', []))}]",
              "# Bemerkungen je Eintrag (\"Raum|KÜRZEL\": Text); fehlt ein Eintrag, wird die Bemerkung gelöscht"]
    notes = state.get("bemerkungen") or {}
    lines.append("bemerkungen:" + ("" if notes else " {}"))
    lines += [f"  {json.dumps(key, ensure_ascii=False)}: {json.dumps(text, ensure_ascii=False)}" for key, text in sorted(notes.items())]
    return "\n".join(lines) + "\n"


class Draft:
    """Entwurf der Aufsichten im Workspace (aufsichten-plan.yaml): Zwischenstand bis zum Bestätigen mit --apply."""

    def __init__(self, workspace: Path, config_dir: Path, name: Path = Path(PLAN_FILE)) -> None:
        self.name, self.path = name, _plan_path(workspace, config_dir, name)

    def save(self, model: "SupervisionModel") -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(plan_text(model.state, model.counts, model.levels, self.name), encoding="utf-8", newline="")
        os.replace(tmp, self.path)
        model.mark_saved()
        return self.path

    def clear(self) -> bool:
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return False

    def load_into(self, model: "SupervisionModel") -> str:
        """Entwurf als Ausgangsstand übernehmen; liefert eine Meldung ('' = keine Datei)."""
        import yaml
        if not self.path.exists():
            return ""
        try:
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise RoomError(f"Entwurf {self.name} nicht lesbar: {exc}") from exc
        if not isinstance(data, dict) or not set(data) <= PLAN_KEYS:
            raise RoomError(f"Entwurf {self.name}: erlaubt sind nur die Abschnitte {', '.join(sorted(PLAN_KEYS))}")

        def names_of(value: Any, where: str) -> list[str]:
            if value is None:
                return []
            if not isinstance(value, list):
                raise RoomError(f"Entwurf {self.name}: {where} muss eine Liste von Kürzeln sein")
            found = [str(x).strip().upper() for x in value if str(x).strip()]
            bad = [x for x in found if x not in model.known]
            if bad:
                raise RoomError(f"Entwurf {self.name}: unbekannte Kürzel {', '.join(bad)} ({where})")
            return found

        before = json.loads(json.dumps(model.state))
        ignored = []
        if isinstance(data.get("raeume"), dict):
            for code, value in data["raeume"].items():
                if str(code) in model.state["raeume"]:
                    model.state["raeume"][str(code)] = names_of(value, f"Raum {code}")
                else:
                    ignored.append(str(code))
        for key in model.ROLE_KEYS:
            if key in data:
                model.state[key] = names_of(data[key], key)
        if "bemerkungen" in data:
            model.state["bemerkungen"] = {}
            for key, text in (data["bemerkungen"] or {}).items():
                row, _sep, who = str(key).partition("|")
                if row in model.rows() and who.upper() in model.team(row) and str(text or "").strip():
                    model.state["bemerkungen"][f"{row}|{who.upper()}"] = str(text).strip()[:NOTE_MAX]
        model.mark_saved()
        note = f"Entwurf {self.name} geladen"
        differing = [f"{model.ROLE_TITLES.get(row, row)}: Datenbank {' '.join(before_team) or '-'}, Entwurf {' '.join(model.team(row)) or '-'}"
                     for row in model.rows()
                     if (before_team := before["raeume"].get(row, []) if row in model.state["raeume"] else before.get(row, [])) != model.team(row)]
        if differing:
            note += " – weicht von der Datenbank ab: " + "; ".join(differing[:8]) + (" …" if len(differing) > 8 else "")
        return note + (f" (nicht belegte Räume ignoriert: {', '.join(ignored)})" if ignored else "")


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
    if not isinstance(plan, dict) or not set(plan) <= PLAN_KEYS or not plan:
        raise RoomError(f"Plandatei: erlaubt sind nur die Abschnitte {', '.join(sorted(PLAN_KEYS))}")

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
        for key, role in ROLE_ROWS.items():
            if key in plan:
                scope_roles.add(role)
                for n in names_of(plan[key], key):
                    desired.add((None, lookup(n), role))
        for n, places in sorted(per_teacher.items()):
            if len(places) > 1:
                warnings.append(f"{n} ist in mehreren Räumen eingeteilt: {', '.join(places)}")
        scoped = {t for t in current if (t[2] == "Standard" and t[0] in scope_rooms) or t[2] in scope_roles}
        to_add, to_del = desired - scoped, scoped - desired
        wanted_notes: dict[tuple[int | None, int, str], str] = {}
        note_changes: list[tuple[tuple[int | None, int, str], str]] = []
        if "bemerkungen" in plan:
            raw_notes = plan["bemerkungen"] or {}
            if not isinstance(raw_notes, dict):
                raise RoomError("Plandatei: bemerkungen muss eine Zuordnung \"Raum|KÜRZEL\" → Text sein")
            room_by_code = {c: i["id"] for c, i in rooms.items()}
            for key, text in raw_notes.items():
                row, _sep, who = str(key).partition("|")
                who = who.strip().upper()
                triple = ((room_by_code.get(row), lookup(who), "Standard") if row in room_by_code
                          else (None, lookup(who), ROLE_ROWS[row]) if row in ROLE_ROWS else None)
                if triple is None or triple not in desired:
                    raise RoomError(f"Plandatei: Bemerkung für {key} – diese Person ist dort nicht eingeteilt")
                text = str(text or "").strip()
                if len(text) > NOTE_MAX:
                    raise RoomError(f"Plandatei: Bemerkung für {key} ist länger als {NOTE_MAX} Zeichen")
                if text:
                    wanted_notes[triple] = text
            old_notes = ctx.get("notes", {})
            note_changes = sorted(((t, wanted_notes.get(t, "")) for t in (scoped & desired)
                                   if wanted_notes.get(t, "") != old_notes.get(t, "")), key=lambda x: str(x[0]))
        label = {i: k for i, (k, _a) in ctx["teachers"].items()}

        def show(t: tuple[int | None, int, str]) -> str:
            place = ctx["by_id"].get(t[0], "?") if t[0] is not None else "ohne Raum"
            return f"{t[2]} {place}: {label.get(t[1], t[1])}"
        added, removed = sorted(show(t) for t in to_add), sorted(show(t) for t in to_del)
        noted = [f"{show(t)}: {text or '(gelöscht)'}" for t, text in note_changes]
        stats = {"hinzu": added, "entfernt": removed, "bemerkungen": noted,
                 "unveraendert": len(scoped & desired), "hinweise": warnings}
        LOGGER.info("Aufsichtenplan | +%d −%d Bemerkungen %d unverändert %d | Modus: %s", len(added), len(removed), len(noted),
                    stats["unveraendert"], "DRY-RUN" if dry_run else "APPLY")
        for line in noted:
            LOGGER.info("Aufsichtenplan | Bemerkung %s", line)
        for line in removed:
            LOGGER.info("Aufsichtenplan | − %s", line)
        for line in added:
            LOGGER.info("Aufsichtenplan | + %s", line)
        for note in warnings:
            LOGGER.warning("Aufsichtenplan | %s", note)
        if dry_run or not (to_add or to_del or note_changes):
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
                new_note = wanted_notes.get((room_id, lk, role))
                if new_note:
                    cur.execute(f"INSERT INTO `{ctx['sup_t']}` (mao_jahrgang_id, raum_id, lehrkraft_id, rolle, bemerkung) "
                                "VALUES (%s, %s, %s, %s, %s)", (ctx["jahrgang_id"], room_id, lk, role, new_note))
                else:
                    cur.execute(f"INSERT INTO `{ctx['sup_t']}` (mao_jahrgang_id, raum_id, lehrkraft_id, rolle) VALUES (%s, %s, %s, %s)",
                                (ctx["jahrgang_id"], room_id, lk, role))
            for (room_id, lk, role), text in note_changes:
                cur.execute(f"UPDATE `{ctx['sup_t']}` SET bemerkung = %s WHERE mao_jahrgang_id = %s AND lehrkraft_id = %s "
                            "AND rolle = %s AND " + ("raum_id IS NULL" if room_id is None else "raum_id = %s"),
                            (text or None, ctx["jahrgang_id"], lk, role) + (() if room_id is None else (room_id,)))
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


def _room_levels(cur: Any, tables: Mapping[str, str], by_id: Mapping[int, str], jahrgang_id: int) -> dict[str, list[str]]:
    """Stufen je Raum: Zahlen für den Hauptwettbewerb, E3…E6 für Externe (Grundschulen)."""
    orga, extern = _ident(tables, "orga_table"), _ident(tables, "extern_teilnahmen_table")
    main: dict[str, set[int]] = {}
    ext: dict[str, set[int]] = {}
    cur.execute(f"SELECT DISTINCT raum_id, COALESCE(stufe, CAST(SUBSTRING_INDEX(klasse, '-', 1) AS UNSIGNED)) FROM `{orga}` "
                "WHERE raum_id IS NOT NULL")
    for r, level in cur.fetchall():
        if level is not None and int(r) in by_id:
            main.setdefault(by_id[int(r)], set()).add(int(level))
    cur.execute(f"SELECT DISTINCT raum_id, klassenstufe FROM `{extern}` WHERE raum_id IS NOT NULL AND mao_jahrgang_id = %s",
                (jahrgang_id,))
    for r, level in cur.fetchall():
        if level is not None and int(r) in by_id:
            ext.setdefault(by_id[int(r)], set()).add(int(level))
    return {code: [str(x) for x in sorted(main.get(code, ()))] + [f"E{x}" for x in sorted(ext.get(code, ()))]
            for code in set(main) | set(ext)}


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


class SupervisionModel:
    """Bearbeitungsstand aller Aufsichten (Räume, Fachaufsicht, Reserve) mit Rückgängig; unabhängig von der Oberfläche."""

    ROLE_KEYS = ("fachaufsicht", "reserve", "abitur")
    ROLE_TITLES = {"fachaufsicht": "Fachaufsicht", "reserve": "Reserve", "abitur": "Abiturerstellung"}

    def __init__(self, state: dict[str, Any], known: set[str], counts: Mapping[str, int], short: list[str],
                 history: Mapping[str, list[int]], need: int, teachers: list[tuple[str, str, str, bool]],
                 levels: Mapping[str, list[str]] | None = None) -> None:
        self.state, self.known, self.counts, self.short = state, known, dict(counts), list(short)
        for key in self.ROLE_KEYS:
            self.state.setdefault(key, [])
        self.state.setdefault("bemerkungen", {})
        self.levels = dict(levels or {})
        self.history, self.need, self.teachers = dict(history), need, teachers
        self.stack: list[str] = []
        self.original = json.dumps(state, sort_keys=True)

    @property
    def changed(self) -> bool:
        return json.dumps(self.state, sort_keys=True) != self.original

    def mark_saved(self) -> None:
        self.original = json.dumps(self.state, sort_keys=True)

    def level_text(self, row: str) -> str:
        return ", ".join(self.levels.get(row, [])) if row not in self.ROLE_KEYS else ""

    def rows(self) -> list[str]:
        return sorted(self.state["raeume"]) + list(self.ROLE_KEYS)

    def team(self, row: str) -> list[str]:
        return self.state[row] if row in self.ROLE_KEYS else self.state["raeume"][row]

    def _push(self) -> None:
        self.stack.append(json.dumps(self.state))

    # --- Bemerkungen (Spalte bemerkung der Aufsichtentabelle), Schlüssel "Zeile|KÜRZEL"
    def note(self, row: str, who: str) -> str:
        return str(self.state["bemerkungen"].get(f"{row}|{who}", ""))

    def notes_text(self, row: str) -> str:
        return "; ".join(f"{k}: {self.note(row, k)}" for k in self.team(row) if self.note(row, k))

    def set_note(self, row: str, who: str, text: str) -> str:
        who = who.upper()
        if row not in self.rows():
            return f"Unbekannte Zeile {row}"
        if who not in self.team(row):
            return f"{who} ist in {row} nicht eingeteilt"
        text = text.strip()
        if len(text) > NOTE_MAX:
            return f"Bemerkung zu lang (höchstens {NOTE_MAX} Zeichen)"
        self._push()
        if text:
            self.state["bemerkungen"][f"{row}|{who}"] = text
        else:
            self.state["bemerkungen"].pop(f"{row}|{who}", None)
        return ""

    def _prune_notes(self) -> None:
        self.state["bemerkungen"] = {k: v for k, v in self.state["bemerkungen"].items()
                                     if k.partition("|")[0] in self.rows() and k.partition("|")[2] in self.team(k.partition("|")[0])}

    def undo(self) -> bool:
        if not self.stack:
            return False
        self.state = json.loads(self.stack.pop())
        return True

    def parse(self, tokens: list[str]) -> tuple[list[str] | None, str]:
        found = [t.upper() for t in tokens]
        bad = [t for t in found if t not in self.known]
        return (None, "Unbekannte Kürzel: " + ", ".join(bad)) if bad else (found, "")

    def edit(self, row: str, tokens: list[str]) -> str:
        """Zeile neu besetzen (KÜRZEL…), ändern (+KÜ/-KÜ) oder leeren ('-'); liefert eine Meldung ('' = ok)."""
        if row not in self.state["raeume"] and row not in self.ROLE_KEYS:
            return f"Unbekannter Raum {row}"
        current = self.team(row)
        if tokens == ["-"] or not tokens:
            new: list[str] = []
        elif all(t[:1] in "+-" and len(t) > 1 for t in tokens):
            new = list(current)
            for t in tokens:
                parsed, err = self.parse([t[1:]])
                if parsed is None:
                    return err
                if t[0] == "+" and parsed[0] not in new:
                    new.append(parsed[0])
                elif t[0] == "-":
                    new = [x for x in new if x != parsed[0]]
        else:
            parsed, err = self.parse(tokens)
            if parsed is None:
                return err
            if len(set(parsed)) != len(parsed):
                return "Kürzel doppelt"
            new = parsed
        self._push()
        if row in self.ROLE_KEYS:
            self.state[row] = new
            self._prune_notes()
            return ""
        self.state["raeume"][row] = new
        self._prune_notes()
        dup = sorted({k for k in new for c, t in self.state["raeume"].items() if c != row and k in t})
        return f"Hinweis: {', '.join(dup)} ist auch in einem anderen Raum eingeteilt" if dup else ""

    def swap_rows(self, a: str, b: str) -> str:
        if a == b:
            return "Zwei verschiedene Zeilen wählen"
        self._push()
        ta, tb = list(self.team(a)), list(self.team(b))
        notes = self.state["bemerkungen"]
        moved = {k: v for k, v in notes.items() if k.partition("|")[0] in (a, b)}
        for k in moved:
            notes.pop(k)
        for k, v in moved.items():
            row, _sep, who = k.partition("|")
            notes[f"{b if row == a else a}|{who}"] = v
        for row, new in ((a, tb), (b, ta)):
            if row in self.ROLE_KEYS:
                self.state[row] = new
            else:
                self.state["raeume"][row] = new
        return ""

    def where(self, k: str) -> list[str]:
        return [r for r in self.rows() if k in self.team(r)]

    def swap_people(self, x: str, y: str) -> str:
        parsed, err = self.parse([x, y])
        if parsed is None:
            return err
        x, y = parsed
        if not self.where(x) or not self.where(y):
            return "Beide Lehrkräfte müssen schon eingeteilt sein"
        self._push()
        for row in set(self.where(x)) | set(self.where(y)):
            team = self.team(row)
            team[:] = [y if t == x else x if t == y else t for t in team]
        notes = self.state["bemerkungen"]
        self.state["bemerkungen"] = {f"{k.partition('|')[0]}|{y if k.partition('|')[2] == x else x if k.partition('|')[2] == y else k.partition('|')[2]}": v
                                     for k, v in notes.items()}
        return ""

    def proposal(self) -> dict[str, Any]:
        return rotation_proposal(self.short, self.state["raeume"], self.history, self.need)

    def adopt(self, prop: Mapping[str, Any]) -> None:
        self._push()
        self.state["raeume"] = {c: list(t) for c, t in prop["raeume"].items()}
        self._prune_notes()

    def plan(self) -> dict[str, Any]:
        return {"raeume": self.state["raeume"], "fachaufsicht": self.state["fachaufsicht"], "reserve": self.state["reserve"],
                "abitur": self.state["abitur"], "bemerkungen": dict(self.state["bemerkungen"])}

    def gap(self, row: str) -> bool:
        return row not in self.ROLE_KEYS and bool(self.counts.get(row)) and len(self.team(row)) < self.need

    def overview(self, everyone: bool = False) -> tuple[list[str], list[str], str]:
        """Lehrkräfte (Mathematik, sonst alle) in dieser Sitzung: (noch frei, schon verplant mit Ort, Überschrift)."""
        math = [t for t in self.teachers if t[3]]
        shown = self.teachers if everyone or not math else math
        names = {"fachaufsicht": "Fach", "reserve": "Reserve", "abitur": "Abitur"}
        free: list[str] = []
        placed: list[str] = []
        for k, v, n, _m in shown:
            spots = self.where(k.upper())
            if spots:
                placed.append(f"{k} ({', '.join(names.get(r, r) for r in spots)})")
            else:
                free.append(" ".join(x for x in (k, n) if x))
        return free, placed, ("alle Lehrkräfte" if everyone or not math else "Mathematik-Lehrkräfte")

    def history_text(self, k: str) -> str:
        past = self.history.get(k, [])
        return "noch nie" if not past else f"{len(past)}× (zuletzt Jahrgang {max(past)})"


def load_supervision_model(db: Any, config_dir: Path, tables: Mapping[str, str],
                           workspace: Path | None = None) -> SupervisionModel:
    rules = load_room_rules(resolve_rules_file(workspace, config_dir))
    cnx = db.conn
    if cnx is None:
        raise RoomError("Keine Datenbankverbindung")
    cur = cnx.cursor()
    try:
        ctx = _load_plan_context(cur, tables)
        teachers = _teacher_list(cur, tables)
        short, history_ids = _short_rooms_and_history(cur, tables, ctx["by_id"], ctx["jahrgang_id"])
        levels = _room_levels(cur, tables, ctx["by_id"], ctx["jahrgang_id"])
    finally:
        cur.close()
    if not ctx["counts"]:
        raise RoomError("Keine Teilnehmenden mit Raum (erst raumzuteilung ausführen)")
    names = ctx["teachers"]
    state = state_from_context(ctx)
    for code in ctx["counts"]:
        state["raeume"].setdefault(code, [])
    history = {names[i][0].upper(): sorted(nrs) for i, nrs in history_ids.items() if i in names}
    return SupervisionModel(state, {k.upper() for k, _a in names.values()}, ctx["counts"], short, history,
                            rules.supervisors_per_room, teachers, levels)


def finish_supervision(db: Any, tables: Mapping[str, str], model: SupervisionModel, *, apply: bool,
                       confirm: Any, out: Any) -> tuple[str, int]:
    """Diff anzeigen und (mit apply und Bestätigung) schreiben. Liefert (status, Änderungen); status: fehler|leer|dry|nein|ok."""
    try:
        stats = apply_plan_data(db, tables, model.plan(), dry_run=True)
    except RoomError as exc:
        out(f"  {exc}")
        return "fehler", 0
    out(f"\nÄnderungen: +{len(stats['hinzu'])} −{len(stats['entfernt'])} Bemerkungen {len(stats['bemerkungen'])}")
    for x in stats["entfernt"]:
        out(f"  − {x}")
    for x in stats["hinzu"]:
        out(f"  + {x}")
    for x in stats["bemerkungen"]:
        out(f"  Bemerkung {x}")
    for x in stats["hinweise"]:
        out(f"  Hinweis: {x}")
    changes = len(stats["hinzu"]) + len(stats["entfernt"]) + len(stats["bemerkungen"])
    if not changes:
        out("Keine Änderungen")
        return "leer", 0
    if not apply:
        out("DRY-RUN: nichts geschrieben. Schreiben mit --apply")
        return "dry", changes
    if not confirm():
        return "nein", changes
    apply_plan_data(db, tables, model.plan(), dry_run=False)
    return "ok", changes


def interactive_supervision(db: Any, config_dir: Path, tables: Mapping[str, str], *, apply: bool,
                            workspace: Path | None = None, ask: Any = input, out: Any = print,
                            draft: "Draft | None" = None, model: "SupervisionModel | None" = None) -> dict[str, Any]:
    """Alle Aufsichten in einer zeilenweisen Konsolensitzung bearbeiten (Tabelle, Tausch, Rotation), dann gesammelt abschicken."""
    model = model or load_supervision_model(db, config_dir, tables, workspace)
    has_math = any(t[3] for t in model.teachers)

    def table() -> None:
        out("\nRaum   Teilnehmende  Stufen          Aufsicht                  Bemerkung")
        for code in sorted(model.state["raeume"]):
            flag = " K" if code in model.short else "  "
            gap = "   <-- fehlt" if model.gap(code) else ""
            who = (' '.join(model.team(code)) or '-') + gap
            out(f"{code:<6}{flag} {model.counts.get(code, 0):>5}      {model.level_text(code):<14}  "
                f"{who:<24}  {model.notes_text(code)}".rstrip())
        for key in model.ROLE_KEYS:
            out(f"{model.ROLE_TITLES[key] + ':':<17} {' '.join(model.state[key]) or '-':<24}  {model.notes_text(key)}".rstrip())
        if model.short:
            out("(K = Kurzraum, Stufe ≤ 6: 2 Stunden Aufsicht)")

    out("Alle Aufsichten bearbeiten. Befehle:\n"
        "  <Raum> KÜRZEL...   Raum neu besetzen (+KÜ / -KÜ ändert nur, '-' leert)   z. B. 105 GER NIX\n"
        "  fach KÜRZEL... | reserve KÜRZEL... | abitur KÜRZEL...   Fachaufsicht / Reserve / Abiturerstellung setzen\n"
        "  bem <Raum|fach|reserve|abitur> KÜRZEL [Text]   Bemerkung zu einer Lehrkraft setzen (ohne Text: löschen)\n"
        "  tausch <Raum> <Raum> | tausch KÜ KÜ   Teams bzw. Personen tauschen\n"
        "  kurz   Rotationsvorschlag für Kurzräume zeigen, 'kurz ok' übernimmt ihn\n"
        f"  l = {'Mathematik-Lehrkräfte' if has_math else 'Lehrkräfte'}, la = alle, z = Tabelle, u = zurück, "
        "w = Entwurf speichern, fertig = prüfen und abschicken, q = abbrechen")
    table()
    result: dict[str, Any] = {"eintraege": 0}
    while True:
        line = str(ask("> ")).strip()
        if not line:
            continue
        cmd, *args = line.split()
        low = cmd.lower()
        if low == "q":
            if draft and model.changed and str(ask("Entwurf speichern? [j/N] ")).strip().lower() == "j":
                out(f"Entwurf gespeichert: {draft.save(model)}")
            out("Abgebrochen, nichts in die Datenbank geschrieben")
            return result
        if low == "w":
            out(f"Entwurf gespeichert: {draft.save(model)}" if draft else "  Kein Entwurf-Ziel")
            continue
        if low in ("z", "zeige"):
            table()
        elif low in ("l", "la"):
            free, placed, title = model.overview(low == "la")
            out(f"  {title}: noch nicht verplant ({len(free)}): " + ("; ".join(free) or "-"))
            out(f"  schon verplant ({len(placed)}): " + ("; ".join(placed) or "-"))
        elif low == "u":
            if model.undo():
                table()
            else:
                out("  Nichts zum Zurücknehmen")
        elif low == "fertig":
            status, changes = finish_supervision(
                db, tables, model, apply=apply, out=out,
                confirm=lambda: str(ask("In die Datenbank schreiben? [j/N] ")).strip().lower() == "j")
            result["eintraege"] = changes
            if draft and status in ("dry", "nein"):
                out(f"Entwurf gespeichert: {draft.save(model)} (weiterbearbeiten: erneut --interaktiv, bestätigen: --apply)")
            elif draft and status == "ok" and draft.clear():
                out("Entwurf gelöscht (Stand ist in der Datenbank)")
            if status == "nein":
                out("Nicht geschrieben (weiter bearbeiten mit Befehlen, q = abbrechen)")
            elif status != "fehler":
                return result
        elif low == "kurz":
            if not model.short:
                out("  Keine Kurzräume (Stufe ≤ 6) in der Raumzuteilung")
                continue
            prop = model.proposal()
            out("  Vorschlag (wer am seltensten/längsten nicht im Kurzraum war, kommt zuerst):")
            for k in prop["rang"][:max(len(model.short) * model.need + 3, 6)]:
                out(f"    {k}: {model.history_text(k)}")
            for code, team in sorted(prop["zuteilung"].items()):
                out(f"  Kurzraum {code}: {' '.join(team) or '-'}")
            for m in prop["wechsel"]:
                out(f"    {m}")
            for h in prop["hinweise"]:
                out(f"  Hinweis: {h}")
            if args[:1] == ["ok"]:
                model.adopt(prop)
                out("  Vorschlag übernommen (u = zurück)")
                table()
            else:
                out("  Übernehmen mit: kurz ok")
        elif low == "tausch" and len(args) == 2:
            a, b = args
            msg = (model.swap_rows(a, b) if a in model.state["raeume"] and b in model.state["raeume"]
                   else model.swap_people(a, b))
            if msg:
                out(f"  {msg}")
            else:
                table()
        elif low in ("fach", "fachaufsicht", "reserve", "abitur"):
            if not args:
                out("  Kürzel angeben (z. B. fach MEI)")
                continue
            key = "fachaufsicht" if low.startswith("fach") else low
            msg = model.edit(key, args)
            out(f"  {msg}" if msg else f"  {low}: {' '.join(model.state[key]) or '-'}")
        elif low in ("bem", "bemerkung") and len(args) >= 2:
            row = args[0] if args[0] in model.state["raeume"] else ("fachaufsicht" if args[0].lower().startswith("fach") else args[0].lower())
            msg = model.set_note(row, args[1], " ".join(args[2:]))
            out(f"  {msg}" if msg else f"  Bemerkung {row}/{args[1].upper()}: {model.note(row, args[1].upper()) or '(gelöscht)'}")
        elif cmd in model.state["raeume"]:
            if not args:
                out(f"  {cmd}: {' '.join(model.team(cmd)) or '-'}")
                continue
            msg = model.edit(cmd, args)
            if msg.startswith("Hinweis") or not msg:
                out(f"  {msg}" if msg else f"  {cmd}: {' '.join(model.team(cmd)) or '-'}")
            else:
                out(f"  {msg}")
        else:
            out("  Unbekannter Befehl oder Raum (Räume ohne Teilnehmende gibt es hier nicht)")
