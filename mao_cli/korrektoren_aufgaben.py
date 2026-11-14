"""Aufgabenzuweisung an Korrektor:innen (2. Stufe): Kandidaten je Aufgabennummer, Prüfung Kind in der Klassenstufe, Menü, Schreiben.

Aufgabennummer = Jahrgang + Klassenstufe (2 Stellen, 12 für 11/12) + Runde (2) + Aufgabe, z. B. 660521.
Zuordnungen liegen in ``korrektoren_zuordnung_table`` (Spalten korrektor_id, mao_nummer, bemerkung); der Jahrgang steckt in mao_nummer.
Es werden nur Zuordnungen des aktuellen Jahrgangs angezeigt und geändert; frühere Jahrgänge zählen nur als „übernommen“.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .korrektoren import KorrektorenError, _ident, column_limits

LOGGER = logging.getLogger("mao_cli.korrektoren_aufgaben")

STUFEN = {5: "kl_5", 6: "kl_6", 7: "kl_7", 8: "kl_8", 9: "kl_9", 10: "kl_10", 12: "kl_11_12"}
AUFGABEN = (1, 2, 3, 4)
RUNDE = 2
TABLE_KEYS = ("korrektoren_table", "korrektoren_prefs_table", "korrektoren_angebote_table", "korrektoren_angehoerige_table",
              "korrektoren_zuordnung_table", "students_table")
DEFAULT_REMARK_LIMIT = 20


def make_nummer(jahrgang_nr: int, stufe: int, aufgabe: int) -> int:
    return jahrgang_nr * 10000 + stufe * 100 + RUNDE * 10 + aufgabe


def split_nummer(nummer: int) -> tuple[int, int, int, int]:
    """(Jahrgang, Stufencode, Runde, Aufgabe)."""
    return nummer // 10000, (nummer // 100) % 100, (nummer // 10) % 10, nummer % 10


def parse_nummer(text: str, jahrgang_nr: int) -> int:
    """660521, 0521 oder 5-1 (Stufe-Aufgabe) -> vollständige Aufgabennummer des aktuellen Jahrgangs."""
    text = text.strip()
    found = re.fullmatch(r"(\d{1,2})\s*[-./]\s*(\d)", text)
    if found:
        number = make_nummer(jahrgang_nr, int(found.group(1)), int(found.group(2)))
    elif re.fullmatch(r"\d{4}", text):
        number = jahrgang_nr * 10000 + int(text)
    elif re.fullmatch(r"\d{6}", text):
        number = int(text)
    else:
        raise KorrektorenError("Aufgabe nicht lesbar: 660521, 0521 oder 5-1 eingeben")
    year, stufe, runde, aufgabe = split_nummer(number)
    if year != jahrgang_nr:
        raise KorrektorenError(f"Aufgabe gehört nicht zum aktuellen Jahrgang {jahrgang_nr}")
    if stufe not in STUFEN or runde != RUNDE or aufgabe not in AUFGABEN:
        raise KorrektorenError("Unbekannte Aufgabe (Stufen 5 bis 10 und 12, Aufgaben 1 bis 4, Runde 2)")
    return number


def stufe_of_class(klasse: Any) -> int | None:
    found = re.match(r"\s*0?(\d{1,2})", str(klasse or ""))
    return int(found.group(1)) if found else None


def kind_matches(code: int, klasse: Any) -> bool:
    """Passt die Klasse eines Kindes zur Klassenstufe der Aufgabe (Code 12 = Klassen 11 und 12)?"""
    stufe = stufe_of_class(klasse)
    return stufe is not None and (stufe in (11, 12) if code == 12 else stufe == code)


@dataclass
class ZState:
    jahrgang_id: int
    jahrgang_nr: int
    korrektoren: dict[int, dict[str, Any]]
    prefs: dict[int, dict[str, Any]]
    angebote: dict[int, set[int]]
    zuordnungen: list[dict[str, Any]]
    kinder: dict[int, list[str]]
    remark_limit: int = DEFAULT_REMARK_LIMIT


def load_zstate(cur: Any, tables: Mapping[str, str]) -> ZState:
    names = {key: _ident(tables, key) for key in TABLE_KEYS}
    years_t, sy_t = _ident(tables, "jahrgaenge_table"), _ident(tables, "schuljahre_table")
    cur.execute(f"SELECT j.id, j.jahrgang_nr FROM `{years_t}` j JOIN `{sy_t}` s ON s.id = j.schuljahr_id "
                "WHERE s.aktiv = 1 ORDER BY j.id DESC LIMIT 1")
    found = cur.fetchall()
    if not found:
        raise KorrektorenError("Kein aktiver Jahrgang in der Datenbank gefunden")
    jahrgang_id, jahrgang_nr = (int(x) for x in found[0])
    cur.execute(f"SELECT id, titel, vorname, nachname, aktiv FROM `{names['korrektoren_table']}`")
    korrektoren = {int(i): {"titel": t, "vorname": v or "", "nachname": n or "", "aktiv": a}
                   for i, t, v, n, a in cur.fetchall()}
    cols = ", ".join(f"`{c}`" for c in STUFEN.values())
    cur.execute(f"SELECT korrektor_id, {cols} FROM `{names['korrektoren_prefs_table']}`")
    prefs = {int(r[0]): dict(zip(STUFEN.values(), r[1:])) for r in cur.fetchall()}
    cur.execute(f"SELECT mao_jahrgang_id, korrektor_id FROM `{names['korrektoren_angebote_table']}`")
    angebote: dict[int, set[int]] = {}
    for year_id, korrektor_id in cur.fetchall():
        angebote.setdefault(int(korrektor_id), set()).add(int(year_id))
    cur.execute(f"SELECT id, korrektor_id, mao_nummer, bemerkung FROM `{names['korrektoren_zuordnung_table']}`")
    zuordnungen = [{"id": int(i), "korrektor_id": int(k), "mao_nummer": int(n), "bemerkung": b}
                   for i, k, n, b in cur.fetchall()]
    cur.execute(f"SELECT matrikelnummer, klasse FROM `{names['students_table']}`")
    klassen = {m: kl for m, kl in cur.fetchall()}
    cur.execute(f"SELECT korrektor_id, hertzkind_matrikelnummer FROM `{names['korrektoren_angehoerige_table']}` WHERE aktiv = 1")
    kinder: dict[int, list[str]] = {}
    for korrektor_id, matrikel in cur.fetchall():
        klasse = klassen.get(matrikel)
        if klasse:
            kinder.setdefault(int(korrektor_id), []).append(str(klasse))
    limit = column_limits(cur, names["korrektoren_zuordnung_table"]).get("bemerkung") or DEFAULT_REMARK_LIMIT
    return ZState(jahrgang_id, jahrgang_nr, korrektoren, prefs, angebote, zuordnungen, kinder, int(limit))


def display_name(info: Mapping[str, Any]) -> str:
    return " ".join(x for x in (info.get("titel"), info.get("vorname"), info.get("nachname")) if x)


@dataclass
class Candidate:
    korrektor_id: int
    name: str
    angebote: int
    uebernommen: int
    kinder_in_stufe: list[str]
    zugeordnet: bool
    bemerkung: str
    andere_aufgaben: list[int]
    bietet_stufe_an: bool
    angebot_aktuell: bool = True


class Work:
    """Gewünschte Zuordnungen des aktuellen Jahrgangs (zunächst wie in der Datenbank) und der Abgleich damit."""

    def __init__(self, state: ZState):
        self.state = state
        self.reset()

    def reset(self) -> None:
        nr = self.state.jahrgang_nr
        self.rows = {(z["korrektor_id"], z["mao_nummer"]): z for z in self.state.zuordnungen if z["mao_nummer"] // 10000 == nr}
        self.want: dict[tuple[int, int], str | None] = {key: z["bemerkung"] for key, z in self.rows.items()}

    # ---- Abfragen
    def uebernommen(self, korrektor_id: int) -> int:
        years = {z["mao_nummer"] // 10000 for z in self.state.zuordnungen
                 if z["korrektor_id"] == korrektor_id and z["mao_nummer"] // 10000 < self.state.jahrgang_nr}
        return len(years)

    def candidates(self, nummer: int, *, alle: bool = False) -> list[Candidate]:
        state = self.state
        _, stufe, _, _ = split_nummer(nummer)
        column = STUFEN[stufe]
        result = []
        for korrektor_id, info in state.korrektoren.items():
            assigned = (korrektor_id, nummer) in self.want
            offers = state.jahrgang_id in state.angebote.get(korrektor_id, set())
            bietet = bool((state.prefs.get(korrektor_id) or {}).get(column))
            if not assigned and not (info.get("aktiv") and offers and (bietet or alle)):
                continue
            result.append(Candidate(
                korrektor_id, display_name(info), len(state.angebote.get(korrektor_id, set())), self.uebernommen(korrektor_id),
                [kl for kl in state.kinder.get(korrektor_id, []) if kind_matches(stufe, kl)], assigned,
                self.want.get((korrektor_id, nummer)) or "",
                sorted(n for (kid, n) in self.want if kid == korrektor_id and n != nummer), bietet, offers))
        result.sort(key=lambda c: (not c.zugeordnet, state.korrektoren[c.korrektor_id]["nachname"].casefold(),
                                   state.korrektoren[c.korrektor_id]["vorname"].casefold()))
        return result

    def assigned(self, nummer: int) -> list[int]:
        return [kid for (kid, n) in self.want if n == nummer]

    # ---- Änderungen
    def set(self, korrektor_id: int, nummer: int, on: bool, bemerkung: str | None = None) -> None:
        if on:
            self.want[(korrektor_id, nummer)] = bemerkung if bemerkung is not None else self.want.get((korrektor_id, nummer))
        else:
            self.want.pop((korrektor_id, nummer), None)

    def set_remark(self, korrektor_id: int, nummer: int, text: str) -> None:
        if (korrektor_id, nummer) not in self.want:
            raise KorrektorenError("Die Bemerkung gehört zu einer Zuordnung: erst zuordnen")
        text = text.strip()
        if len(text) > self.state.remark_limit:
            raise KorrektorenError(f"Bemerkung zu lang (höchstens {self.state.remark_limit} Zeichen)")
        self.want[(korrektor_id, nummer)] = text or None

    def diff(self) -> dict[str, list[Any]]:
        added = sorted(k for k in self.want if k not in self.rows)
        removed = sorted(k for k in self.rows if k not in self.want)
        changed = sorted(k for k in self.want if k in self.rows and (self.want[k] or None) != (self.rows[k]["bemerkung"] or None))
        return {"neu": added, "entfernt": removed, "bemerkung": changed}

    def pending(self) -> int:
        return sum(len(v) for v in self.diff().values())


# ------------------------------------------------------------------ Ausgabe

def _short(info: Mapping[str, Any]) -> str:
    return f"{info['nachname']}, {info['vorname'][:1]}." if info["vorname"] else str(info["nachname"])


def overview_lines(work: Work) -> list[str]:
    state = work.state
    lines = [f"Aufgaben Jahrgang {state.jahrgang_nr} (Angebote für die Stufe / zugeordnet)"]
    for stufe in STUFEN:
        for aufgabe in AUFGABEN:
            nummer = make_nummer(state.jahrgang_nr, stufe, aufgabe)
            offers = len([c for c in work.candidates(nummer) if c.bietet_stufe_an])
            names = ", ".join(_short(state.korrektoren[k]) for k in sorted(work.assigned(nummer)))
            label = "11/12" if stufe == 12 else str(stufe)
            lines.append(f"  {nummer}  Stufe {label:>5}  Angebote {offers:>3}  {names or '-'}")
    without = [k for k, info in state.korrektoren.items()
               if info.get("aktiv") and state.jahrgang_id in state.angebote.get(k, set())
               and not any(kid == k for (kid, _) in work.want)]
    lines.append(f"Korrektor:innen mit Angebot ohne Aufgabe: {len(without)}")
    return lines


def candidate_lines(work: Work, nummer: int, *, alle: bool = False) -> list[str]:
    year, stufe, _, aufgabe = split_nummer(nummer)
    label = "11/12" if stufe == 12 else str(stufe)
    cands = work.candidates(nummer, alle=alle)
    lines = [f"Aufgabe {nummer} (Klassenstufe {label}, Aufgabe {aufgabe}) – zugeordnet: {len(work.assigned(nummer))}",
             " Nr  Z  Korrektor:in                        Angebote  Übernommen  Kind in Stufe  Weitere Aufgaben       Bemerkung"]
    for number, cand in enumerate(cands, 1):
        other = ", ".join(str(n) for n in cand.andere_aufgaben) or "-"
        kind = ", ".join(cand.kinder_in_stufe) if cand.kinder_in_stufe else "-"
        extra = "" if cand.bietet_stufe_an else "  (Stufe nicht angeboten)"
        if not cand.angebot_aktuell:
            extra += f"  (KEIN Angebot {year})"
        lines.append(f"{number:>3}  {'*' if cand.zugeordnet else ' '}  {cand.name[:34]:<34}{cand.angebote:>9}{cand.uebernommen:>12}  "
                     f"{kind:<13}  {other:<21}  {cand.bemerkung}{extra}")
    if not cands:
        lines.append("  Keine Korrektor:innen mit Angebot für diese Klassenstufe.")
    return lines


def change_lines(work: Work) -> list[str]:
    state = work.state
    diff = work.diff()
    lines = []
    for label, key in (("+ zuordnen", "neu"), ("- entfernen", "entfernt"), ("~ Bemerkung", "bemerkung")):
        for kid, nummer in diff[key]:
            lines.append(f"{label}: {nummer} {display_name(state.korrektoren[kid])}"
                         + (f" ({work.want[(kid, nummer)]})" if key != "entfernt" and work.want.get((kid, nummer)) else ""))
    return lines


# ------------------------------------------------------------------ Schreiben

class AufgabenSession:
    def __init__(self, db: Any, tables: Mapping[str, str], expected_nr: int | None = None):
        self.db = db
        self.tables = tables
        self.expected_nr = expected_nr
        self.work: Work = None  # type: ignore[assignment]
        self.reload()

    def reload(self) -> None:
        if self.db.conn is None:
            raise KorrektorenError("Keine Datenbankverbindung")
        cur = self.db.conn.cursor()
        try:
            state = load_zstate(cur, self.tables)
        finally:
            cur.close()
        if self.expected_nr is not None and state.jahrgang_nr != self.expected_nr:
            raise KorrektorenError(f"Aktiver Jahrgang der Datenbank ({state.jahrgang_nr}) passt nicht zum erwarteten ({self.expected_nr}); "
                                   "Angebote werden nicht verwendet")
        self.work = Work(state)

    def write(self) -> dict[str, int]:
        """Änderungen in einer Transaktion schreiben, danach neu laden."""
        work = self.work
        state = work.state
        diff = work.diff()
        table = _ident(self.tables, "korrektoren_zuordnung_table")
        cnx = self.db.conn
        cur = cnx.cursor()
        lock_name = f"mao-korrektoren-aufgaben-{table}"
        locked = False
        written = {"neu": 0, "entfernt": 0, "bemerkung": 0}
        try:
            cur.execute("SELECT GET_LOCK(%s, 10)", (lock_name,))
            if cur.fetchall()[0][0] != 1:
                raise KorrektorenError("Konkurrierende Änderung oder Sperr-Timeout")
            locked = True
            if cnx.in_transaction:
                cnx.rollback()
            cnx.start_transaction()
            try:
                cur.execute(f"SELECT COUNT(*) FROM `{table}`")
                before = int(cur.fetchall()[0][0])
                if before != len(state.zuordnungen):
                    raise KorrektorenError("Die Zuordnungen haben sich während der Bearbeitung geändert; bitte neu laden")
                for key in diff["entfernt"]:
                    row = work.rows[key]
                    cur.execute(f"DELETE FROM `{table}` WHERE id = %s AND korrektor_id = %s AND mao_nummer = %s",
                                (row["id"], key[0], key[1]))
                    if cur.rowcount != 1:
                        raise KorrektorenError("Zuordnung konnte nicht entfernt werden; nichts geschrieben")
                    written["entfernt"] += 1
                for key in diff["bemerkung"]:
                    row = work.rows[key]
                    cur.execute(f"UPDATE `{table}` SET bemerkung = %s WHERE id = %s AND korrektor_id = %s AND mao_nummer = %s",
                                (work.want[key] or None, row["id"], key[0], key[1]))
                    written["bemerkung"] += 1
                for key in diff["neu"]:
                    cur.execute(f"INSERT INTO `{table}` (korrektor_id, mao_nummer, bemerkung) VALUES (%s, %s, %s)",
                                (key[0], key[1], work.want[key] or None))
                    written["neu"] += 1
                cur.execute(f"SELECT COUNT(*) FROM `{table}`")
                if int(cur.fetchall()[0][0]) != before + written["neu"] - written["entfernt"]:
                    raise KorrektorenError("Zählabgleich fehlgeschlagen; nichts geschrieben")
                cnx.commit()
            except Exception:
                cnx.rollback()
                raise
        finally:
            if locked:
                try:
                    cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                    cur.fetchall()
                except Exception:
                    LOGGER.warning("Konnte Sperre nicht regulär freigeben (DB-Disconnect gibt sie frei)")
            cur.close()
        self.reload()
        return written


# ------------------------------------------------------------------ Menü

MENU = """
  1) Interaktive Tabelle (Pfeiltasten)
  2) Übersicht der Aufgaben (Text)
  3) Aufgabe bearbeiten per Eingabe ({pending} offene Änderungen)
  4) Änderungen anzeigen und in die Datenbank schreiben
  5) Änderungen verwerfen und neu laden
  q) Beenden"""

EDIT_HELP = ("  Nummern (z. B. 1 3) = zuordnen bzw. entfernen | a = auch ohne Angebot für diese Stufe zeigen | "
             "b 2 Text = Bemerkung | Enter = zurück")


# ------------------------------------------------------------------ Interaktive Tabelle (Pfeiltasten)

def read_key() -> str:
    """Eine Taste lesen: up, down, left, right, enter, space, esc oder das Zeichen selbst."""
    import os
    if os.name == "nt":  # pragma: no cover - Windows
        import msvcrt
        char = msvcrt.getwch()  # type: ignore[attr-defined,unused-ignore]
        if char in ("\x00", "\xe0"):
            code = msvcrt.getwch()  # type: ignore[attr-defined,unused-ignore]
            return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(code, "")
        return {"\r": "enter", " ": "space", "\x1b": "esc", "\x03": "esc"}.get(char, char)
    import sys
    import termios
    import tty
    fd = sys.stdin.fileno()  # pragma: no cover - Terminal
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        char = sys.stdin.read(1)
        if char == "\x1b":
            import select
            if select.select([sys.stdin], [], [], 0.05)[0]:
                rest = sys.stdin.read(2)
                return {"[A": "up", "[B": "down", "[D": "left", "[C": "right"}.get(rest, "")
            return "esc"
        return {"\r": "enter", "\n": "enter", " ": "space", "\x03": "esc"}.get(char, char)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def draw_screen(lines: list[str], cursor: int | None = None) -> None:  # pragma: no cover - Terminal
    """Bildschirm löschen und Zeilen ausgeben; die Zeile `cursor` invertiert."""
    import os
    if os.name == "nt":
        os.system("")  # ANSI-Sequenzen in der Windows-Konsole aktivieren
    text = "\n".join(f"\x1b[7m{x}\x1b[0m" if i == cursor else x for i, x in enumerate(lines))
    print("\x1b[2J\x1b[H" + text, flush=True)


ALL_TASKS = [(stufe, aufgabe) for stufe in STUFEN for aufgabe in AUFGABEN]


def grid_screen(work: Work, cursor: int) -> tuple[list[str], int]:
    """Übersichtstabelle aller Aufgaben; Rückgabe (Zeilen, Index der Cursorzeile)."""
    state = work.state
    lines = [f"Aufgabenzuweisung Jahrgang {state.jahrgang_nr}  ({work.pending()} offene Änderungen)",
             "↑/↓ Aufgabe | ←/→ Klassenstufe | Enter öffnen | s Schreiben/Änderungen | q zurück", "",
             "  Aufgabe  Stufe  Angebote  Zugeordnet"]
    for index, (stufe, aufgabe) in enumerate(ALL_TASKS):
        nummer = make_nummer(state.jahrgang_nr, stufe, aufgabe)
        offers = len([c for c in work.candidates(nummer) if c.bietet_stufe_an])
        names = ", ".join(_short(state.korrektoren[k]) for k in sorted(work.assigned(nummer)))
        label = "11/12" if stufe == 12 else str(stufe)
        lines.append(f"  {nummer}  {label:>5}  {offers:>8}  {names or '-'}")
    return lines, 4 + cursor


def task_screen(work: Work, nummer: int, cursor: int, alle: bool) -> tuple[list[str], int, int]:
    """Kandidatentabelle einer Aufgabe; Rückgabe (Zeilen, Cursorzeile, Anzahl Kandidaten)."""
    table = candidate_lines(work, nummer, alle=alle)
    cands = work.candidates(nummer, alle=alle)
    lines = ["↑/↓ wählen | Leertaste/Enter zuordnen bzw. entfernen | b Bemerkung | a auch ohne Stufenangebot | "
             "←/→ andere Aufgabe | q zurück", ""] + table
    return lines, 4 + cursor, len(cands)


def interactive_table(work: Work, *, key: Callable[[], str] = read_key,
                      draw: Callable[[list[str], int | None], Any] = draw_screen,
                      ask: Callable[[str], str] = input) -> bool:
    """Tabelle mit Pfeiltasten; True, wenn der Nutzer zum Schreiben (s) wechseln möchte."""
    pos = 0
    while True:
        lines, row = grid_screen(work, pos)
        draw(lines, row)
        pressed = key()
        if pressed == "up":
            pos = (pos - 1) % len(ALL_TASKS)
        elif pressed == "down":
            pos = (pos + 1) % len(ALL_TASKS)
        elif pressed == "left":
            pos = max(0, (pos // 4 - 1) * 4)
        elif pressed == "right":
            pos = min(len(ALL_TASKS) - 4, (pos // 4 + 1) * 4)
        elif pressed == "s":
            return True
        elif pressed in ("q", "esc"):
            return False
        elif pressed == "enter":
            pos = _interactive_task(work, pos, key=key, draw=draw, ask=ask)


def _interactive_task(work: Work, pos: int, *, key: Callable[[], str], draw: Callable[[list[str], int | None], Any],
                      ask: Callable[[str], str]) -> int:
    row, alle, message = 0, False, ""
    while True:
        stufe, aufgabe = ALL_TASKS[pos]
        nummer = make_nummer(work.state.jahrgang_nr, stufe, aufgabe)
        lines, cursor_row, count = task_screen(work, nummer, row, alle)
        row = min(row, max(count - 1, 0))
        if message:
            lines.append("")
            lines.append(message)
        draw(lines, cursor_row if count else None)
        message = ""
        pressed = key()
        cands = work.candidates(nummer, alle=alle)
        if pressed == "up" and count:
            row = (row - 1) % count
        elif pressed == "down" and count:
            row = (row + 1) % count
        elif pressed in ("left", "right"):
            pos = (pos + (1 if pressed == "right" else -1)) % len(ALL_TASKS)
            row = 0
        elif pressed == "a":
            alle = not alle
            row = 0
        elif pressed in ("q", "esc"):
            return pos
        elif pressed in ("space", "enter") and count:
            cand = cands[row]
            if cand.zugeordnet:
                work.set(cand.korrektor_id, nummer, False)
            elif cand.kinder_in_stufe:
                label = "11/12" if stufe == 12 else str(stufe)
                draw(lines + ["", f"{cand.name} hat ein Kind in Klassenstufe {label} ({', '.join(cand.kinder_in_stufe)}). "
                                  "Trotzdem zuordnen? [j/N]"], None)
                if key().lower() in ("j", "y"):
                    work.set(cand.korrektor_id, nummer, True)
            else:
                work.set(cand.korrektor_id, nummer, True)
        elif pressed == "b" and count:
            try:
                work.set_remark(cands[row].korrektor_id, nummer, ask("Bemerkung (leer = löschen): "))
            except KorrektorenError as exc:
                message = f"Nicht möglich: {exc}"


def edit_task(work: Work, nummer: int, *, ask: Callable[[str], str], out: Callable[[str], Any]) -> None:
    alle = False
    _, stufe, _, _ = split_nummer(nummer)
    label = "11/12" if stufe == 12 else str(stufe)
    while True:
        for line in candidate_lines(work, nummer, alle=alle):
            out(line)
        out(EDIT_HELP)
        answer = ask("Eingabe: ").strip()
        if not answer:
            return
        cands = work.candidates(nummer, alle=alle)
        try:
            if answer.lower() == "a":
                alle = not alle
            elif answer.lower().startswith("b"):
                parts = answer[1:].strip().split(None, 1)
                if not parts or not parts[0].isdigit() or not 1 <= int(parts[0]) <= len(cands):
                    raise KorrektorenError("Bemerkung: b <Nr> <Text>")
                work.set_remark(cands[int(parts[0]) - 1].korrektor_id, nummer, parts[1] if len(parts) > 1 else "")
            else:
                numbers = [int(x) for x in answer.replace(",", " ").split()]
                if not numbers or any(not 1 <= n <= len(cands) for n in numbers):
                    raise KorrektorenError(f"Bitte Nummern von 1 bis {len(cands)} eingeben")
                for number in dict.fromkeys(numbers):
                    cand = cands[number - 1]
                    if cand.zugeordnet:
                        work.set(cand.korrektor_id, nummer, False)
                        continue
                    if cand.kinder_in_stufe and ask(
                            f"{cand.name} hat ein Kind in Klassenstufe {label} ({', '.join(cand.kinder_in_stufe)}). "
                            "Trotzdem zuordnen? [j/N] ").strip().lower() not in ("j", "ja"):
                        continue
                    work.set(cand.korrektor_id, nummer, True)
        except ValueError:
            out("Bitte Nummern, a oder b <Nr> <Text> eingeben.")
        except KorrektorenError as exc:
            out(f"Nicht möglich: {exc}")


def run_menu(session: AufgabenSession, *, apply: bool, ask: Callable[[str], str] = input,
             out: Callable[[str], Any] = print, key: Callable[[], str] = read_key,
             draw: Callable[[list[str], int | None], Any] = draw_screen) -> dict[str, int]:
    written_total: dict[str, int] = {}
    want_write = False
    while True:
        work = session.work
        if want_write:
            choice, want_write = "4", False
        else:
            out(MENU.format(pending=work.pending()))
            try:
                choice = ask("Auswahl: ").strip().lower()
            except EOFError:
                choice = "q"
        try:
            if choice == "1":
                want_write = interactive_table(work, key=key, draw=draw, ask=ask)
            elif choice == "2":
                for line in overview_lines(work):
                    out(line)
            elif choice == "3":
                nummer = parse_nummer(ask("Aufgabe (660521, 0521 oder 5-1): "), work.state.jahrgang_nr)
                edit_task(work, nummer, ask=ask, out=out)
            elif choice == "4":
                lines = change_lines(work)
                if not lines:
                    out("Nichts zu schreiben.")
                    continue
                for line in lines:
                    out("  " + line)
                if not apply:
                    out("DRY-RUN: nichts geschrieben. Zum Schreiben mit: mao korrektoren-aufgaben --apply "
                        "(die Änderungen gehen beim Beenden verloren)")
                elif ask("In die Datenbank schreiben? [j/N] ").strip().lower() in ("j", "ja"):
                    written = session.write()
                    for name, value in written.items():
                        written_total[name] = written_total.get(name, 0) + value
                    out("Geschrieben: " + ", ".join(f"{k} {v}" for k, v in written.items()))
            elif choice == "5":
                session.reload()
                out("Änderungen verworfen, neu geladen.")
            elif choice in ("q", "quit", "exit"):
                if work.pending() and apply:
                    out(f"Hinweis: {work.pending()} Änderungen sind nicht geschrieben und gehen verloren.")
                return written_total
            elif choice:
                out("Bitte 1 bis 5 oder q eingeben.")
        except KorrektorenError as exc:
            out(f"Nicht möglich: {exc}")


def run_korrektoren_aufgaben(db: Any, tables: Mapping[str, str], *, apply: bool, menu: bool = True,
                             expected_nr: int | None = None, ask: Callable[[str], str] = input,
                             out: Callable[[str], Any] = print, **menu_kw: Any) -> dict[str, int]:
    """Menü (Standard) oder ohne Menü: nur Übersicht mit Kandidaten je Aufgabe ausgeben (schreibt nie)."""
    session = AufgabenSession(db, tables, expected_nr)
    if menu:
        return run_menu(session, apply=apply, ask=ask, out=out, **menu_kw)
    for line in overview_lines(session.work):
        out(line)
    for stufe in STUFEN:
        for aufgabe in AUFGABEN:
            nummer = make_nummer(session.work.state.jahrgang_nr, stufe, aufgabe)
            out("")
            for line in candidate_lines(session.work, nummer):
                out(line)
    return {}
