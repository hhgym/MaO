"""Tabellenansicht mit Cursor zum Bearbeiten aller Aufsichten (ohne Zusatzpaket: ANSI-Ausgabe, msvcrt/termios).

Bedienung: Pfeiltasten wählen die Zeile, Enter bearbeitet sie (Kürzel, Tab vervollständigt), Leertaste markiert eine Zeile
und tauscht sie mit der nächsten markierten, r = Rotationsvorschlag für Kurzräume, u = zurück, l/L = Lehrkräfte,
s = prüfen und abschicken, q = beenden. Die Logik steckt in SupervisionModel; hier liegen nur Tasten und Darstellung.
"""
from __future__ import annotations

import os
import shutil
import sys
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping

from .aufsichten import Draft, SupervisionModel, apply_plan_data
from .rooms import RoomError

REVERSE, RESET, DIM = "\x1b[7m", "\x1b[0m", "\x1b[2m"
WIN_KEYS = {"H": "up", "P": "down", "K": "left", "M": "right", "S": "delete", "G": "home", "O": "end", "I": "pgup", "Q": "pgdn"}
CSI_KEYS = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end", "1~": "home", "4~": "end",
            "3~": "delete", "5~": "pgup", "6~": "pgdn"}
PLAIN_KEYS = {"\r": "enter", "\n": "enter", "\x1b": "esc", "\x7f": "backspace", "\x08": "backspace", "\t": "tab", " ": "space"}


def _plain(ch: str) -> str:
    if ch == "\x03":
        raise KeyboardInterrupt
    return PLAIN_KEYS.get(ch, ch)


def _read_char(fd: int) -> str:
    first = os.read(fd, 1)
    need = 3 if first[0] >= 0xF0 else 2 if first[0] >= 0xE0 else 1 if first[0] >= 0xC0 else 0
    return (first + (os.read(fd, need) if need else b"")).decode("utf-8", "replace")


def read_key_posix(fd: int) -> str:
    import select
    ch = _read_char(fd)
    if ch != "\x1b":
        return _plain(ch)
    if not select.select([fd], [], [], 0.05)[0]:
        return "esc"
    if _read_char(fd) not in ("[", "O"):
        return "esc"
    code = ""
    while True:
        c = _read_char(fd)
        code += c
        if c.isalpha() or c == "~":
            return CSI_KEYS.get(code, "")


def read_key_windows() -> str:
    import msvcrt
    ch = msvcrt.getwch()  # type: ignore[attr-defined,unused-ignore]
    if ch in ("\x00", "\xe0"):
        return WIN_KEYS.get(msvcrt.getwch(), "")  # type: ignore[attr-defined,unused-ignore]
    return _plain(ch)


@contextmanager
def terminal() -> Iterator[Callable[[], str]]:
    """Alternativen Bildschirm öffnen, Tasten ohne Enter lesen; stellt das Terminal sicher wieder her."""
    out = sys.stdout
    if os.name == "nt":
        os.system("")  # aktiviert ANSI-Sequenzen in der Windows-Konsole
        reader: Callable[[], str] = read_key_windows
        restore: Callable[[], None] = lambda: None
    else:
        import termios
        import tty
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        reader = lambda: read_key_posix(fd)
        restore = lambda: termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    out.write("\x1b[?1049h\x1b[?25l")
    out.flush()
    try:
        yield reader
    finally:
        out.write("\x1b[?25h\x1b[?1049l")
        out.flush()
        restore()


class Ui:
    def __init__(self) -> None:
        self.cursor, self.top, self.mode = 0, 0, "browse"
        self.message, self.panel = "", []  # type: str, list[str]
        self.buffer, self.pos, self.mark = "", 0, None  # type: str, int, str | None
        self.confirm_text, self.confirm_actions = "", {}  # type: str, dict[str, Callable[[], None]]


def terminal_size() -> tuple[int, int]:
    """(Zeilen, Spalten) – get_terminal_size liefert (Spalten, Zeilen)."""
    columns, lines = shutil.get_terminal_size((100, 30))
    return lines, columns


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[:max(1, width - 1)] + "…"


def render(model: SupervisionModel, ui: Ui, height: int, width: int) -> str:
    rows = model.rows()
    body_h = max(4, height - 9 - len(ui.panel))
    ui.top = min(max(ui.top, ui.cursor - body_h + 1), ui.cursor) if len(rows) > body_h else 0
    # Stufen-Spalte passt sich dem Inhalt an (Externe: E3, E4 …), lässt aber Platz für die Aufsicht
    wanted = max([len(model.level_text(r)) for r in rows if r not in model.ROLE_KEYS] + [6])
    level_w = max(8, min(wanted, 30, width - 45)) + 2
    lines = ["Aufsichten  ↑↓ wählen · Enter bearbeiten · Leertaste tauschen · r Rotation · u zurück · l Lehrkräfte · w Entwurf · s Diff/abschicken · q Ende",
             f"{'':2}{'Raum':<12}{'Tn':>4}  {'Stufen':<{level_w}}Aufsicht"]
    for index in range(ui.top, min(len(rows), ui.top + body_h)):
        row = rows[index]
        if row in model.ROLE_KEYS:
            name, count = ("Fachaufsicht" if row == "fachaufsicht" else "Reserve"), ""
        else:
            name, count = row + ("  K" if row in model.short else ""), str(model.counts.get(row, 0))
        gap = "  <-- fehlt" if model.gap(row) else ""
        text = f"{'*' if ui.mark == row else ' '} {name:<12}{count:>4}  {_clip(model.level_text(row), level_w - 2):<{level_w}}{' '.join(model.team(row)) or '-'}{gap}"
        text = text[:max(20, width - 1)]
        lines.append(f"{REVERSE}{text:<{max(20, width - 1)}}{RESET}" if index == ui.cursor and ui.mode != "edit" else text)
    lines.append(f"{DIM}K = Kurzraum (Stufe ≤ 6, 2 Stunden){RESET}" if model.short else "")
    lines += ui.panel
    lines.append(ui.message)
    if ui.mode == "edit":
        row = rows[ui.cursor]
        before, here, after = ui.buffer[:ui.pos], ui.buffer[ui.pos:ui.pos + 1] or " ", ui.buffer[ui.pos + 1:]
        lines.append(f"{row} › {before}{REVERSE}{here}{RESET}{after}   (Enter ok · Esc abbrechen · Tab ergänzt · +KÜ/-KÜ ändert)")
    elif ui.mode == "confirm":
        lines.append(ui.confirm_text)
    else:
        lines.append("")
    return "\x1b[H\x1b[J" + "\r\n".join(lines)


def _complete(model: SupervisionModel, ui: Ui) -> None:
    head, _sep, token = ui.buffer[:ui.pos].rpartition(" ")
    sign = token[:1] if token[:1] in "+-" else ""
    prefix = token[len(sign):].upper()
    found = sorted(k for k in model.known if k.startswith(prefix))
    if not found:
        ui.message = "Kein passendes Kürzel"
        return
    common = os.path.commonprefix(found)
    if len(found) > 1:
        ui.message = "Möglich: " + " ".join(found[:12])
    fill = (found[0] if len(found) == 1 else common)
    sep = " " if head or _sep else ""
    ui.buffer = f"{head}{sep}{sign}{fill}" + ui.buffer[ui.pos:]
    ui.pos = len(f"{head}{sep}{sign}{fill}")


def run_tui(model: SupervisionModel, db: Any, tables: Mapping[str, str], *, apply: bool, read: Callable[[], str],
            draft: Draft | None = None,
            write: Callable[[str], Any], size: Callable[[], tuple[int, int]] = lambda: terminal_size(),
            ) -> dict[str, Any]:
    ui = Ui()
    rows = model.rows
    result: dict[str, Any] = {"eintraege": 0, "geschrieben": False}

    def wrap(label: str, items: list[str], style: str = "") -> list[str]:
        lines, line = [], f"{label}: "
        for item in items or ["-"]:
            if len(line) + len(item) > 96:
                lines.append(line.rstrip("; "))
                line = "    "
            line += item + "; "
        lines.append(line.rstrip("; "))
        return [f"{style}{x}{RESET if style else ''}" for x in lines]

    def teacher_panel(everyone: bool) -> list[str]:
        free, placed, title = model.overview(everyone)
        return ([f"{title}: noch frei {len(free)}, verplant {len(placed)}"] + wrap("  Noch nicht verplant", free)
                + wrap("  Schon verplant", placed, DIM))[:12]

    def prompt(text: str, action: Callable[[], None], extra: dict[str, Callable[[], None]] | None = None) -> None:
        ui.mode, ui.confirm_text, ui.confirm_actions = "confirm", text, {"j": action, **(extra or {})}

    def save_draft() -> str:
        return f"Entwurf gespeichert: {draft.save(model)}" if draft else "Kein Entwurf-Ziel"

    def save() -> None:
        try:
            stats = apply_plan_data(db, tables, model.plan(), dry_run=True)
        except RoomError as exc:
            ui.message = str(exc)
            return
        lines = [f"Änderungen: +{len(stats['hinzu'])} −{len(stats['entfernt'])}"]
        lines += [f"  − {x}" for x in stats["entfernt"]] + [f"  + {x}" for x in stats["hinzu"]]
        lines += [f"  Hinweis: {x}" for x in stats["hinweise"]]
        ui.panel = lines[:8] + (["  …"] if len(lines) > 8 else [])
        changes = len(stats["hinzu"]) + len(stats["entfernt"])
        result["eintraege"] = changes
        if not changes:
            ui.message = "Keine Änderungen"
        elif not apply:
            ui.message = "DRY-RUN: nichts in die Datenbank geschrieben. " + (
                save_draft() + " – weiterbearbeiten: erneut --interaktiv, bestätigen: --apply" if draft else "")
        else:
            def commit() -> None:
                apply_plan_data(db, tables, model.plan(), dry_run=False)
                result["geschrieben"] = True
                result["entwurf_geloescht"] = bool(draft and draft.clear())
                ui.mode = "done"
            prompt("In die Datenbank schreiben? [j/n]", commit)

    while ui.mode != "done":
        height, width = size()
        write(render(model, ui, height, width))
        key = read()
        if ui.mode == "confirm":
            action = ui.confirm_actions.get(key.lower())
            ui.mode, ui.confirm_actions = "browse", {}
            if action:
                action()
            else:
                ui.message = "Abgebrochen"
            continue
        if ui.mode == "edit":
            row = rows()[ui.cursor]
            if key == "esc":
                ui.mode, ui.message = "browse", ""
            elif key == "enter":
                msg = model.edit(row, ui.buffer.replace(",", " ").split())
                if msg and not msg.startswith("Hinweis"):
                    ui.message = msg
                else:
                    ui.mode, ui.message = "browse", msg
            elif key == "backspace" and ui.pos:
                ui.buffer, ui.pos = ui.buffer[:ui.pos - 1] + ui.buffer[ui.pos:], ui.pos - 1
            elif key == "delete":
                ui.buffer = ui.buffer[:ui.pos] + ui.buffer[ui.pos + 1:]
            elif key == "left":
                ui.pos = max(0, ui.pos - 1)
            elif key == "right":
                ui.pos = min(len(ui.buffer), ui.pos + 1)
            elif key == "home":
                ui.pos = 0
            elif key == "end":
                ui.pos = len(ui.buffer)
            elif key == "tab":
                _complete(model, ui)
            elif key == "space":
                ui.buffer, ui.pos = ui.buffer[:ui.pos] + " " + ui.buffer[ui.pos:], ui.pos + 1
            elif len(key) == 1 and key.isprintable():
                ui.buffer, ui.pos = ui.buffer[:ui.pos] + key + ui.buffer[ui.pos:], ui.pos + 1
            continue
        ui.message = ""
        count = len(rows())
        if key == "up":
            ui.cursor = (ui.cursor - 1) % count
        elif key == "down":
            ui.cursor = (ui.cursor + 1) % count
        elif key == "pgup":
            ui.cursor = max(0, ui.cursor - 10)
        elif key == "pgdn":
            ui.cursor = min(count - 1, ui.cursor + 10)
        elif key == "home":
            ui.cursor = 0
        elif key == "end":
            ui.cursor = count - 1
        elif key in ("enter", "e"):
            ui.mode, ui.buffer = "edit", " ".join(model.team(rows()[ui.cursor]))
            ui.pos = len(ui.buffer)
        elif key == "space":
            row = rows()[ui.cursor]
            if ui.mark is None:
                ui.mark, ui.message = row, f"{row} markiert – Leertaste auf einer anderen Zeile tauscht"
            elif ui.mark == row:
                ui.mark = None
            else:
                msg = model.swap_rows(ui.mark, row)
                ui.message = msg or f"{ui.mark} ↔ {row} getauscht"
                ui.mark = None
        elif key == "u":
            ui.message = "Zurückgenommen" if model.undo() else "Nichts zum Zurücknehmen"
        elif key in ("l", "L"):
            ui.panel = teacher_panel(key == "L")
        elif key == "r":
            if not model.short:
                ui.message = "Keine Kurzräume (Stufe ≤ 6) in der Raumzuteilung"
            else:
                prop = model.proposal()
                ui.panel = ["Rotation (wer am seltensten/längsten nicht im Kurzraum war, kommt zuerst):"]
                ui.panel += [f"  {k}: {model.history_text(k)}" for k in prop["rang"][:max(len(model.short) * model.need + 2, 5)]]
                ui.panel += [f"  Kurzraum {c}: {' '.join(t) or '-'}" for c, t in sorted(prop["zuteilung"].items())]
                ui.panel += [f"  {m}" for m in prop["wechsel"]] + [f"  Hinweis: {h}" for h in prop["hinweise"]]
                ui.panel = ui.panel[:12]
                def adopt() -> None:
                    model.adopt(prop)
                    ui.panel = []
                prompt("Vorschlag übernehmen? [j/n]", adopt)
        elif key == "w":
            ui.message = save_draft()
        elif key == "s":
            save()
        elif key in ("q", "esc"):
            if model.changed and not result["geschrieben"]:
                def leave() -> None:
                    ui.mode = "done"
                    result["eintraege"] = 0

                def keep() -> None:
                    result["entwurf"] = save_draft()
                    leave()
                if draft:
                    prompt("Ungespeicherte Änderungen: Entwurf speichern und beenden (j) · verwerfen und beenden (v) · zurück (Esc)",
                           keep, {"v": leave})
                else:
                    prompt("Änderungen verwerfen und beenden? [j/n]", leave)
            else:
                ui.mode = "done"
    write("")
    return result


def tui_supervision(model: SupervisionModel, db: Any, tables: Mapping[str, str], *, apply: bool,
                    draft: Draft | None = None) -> dict[str, Any]:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise RoomError("Die Tabellenansicht braucht ein Terminal (sonst --einfach verwenden)")
    with terminal() as read:
        def write(text: str) -> None:
            sys.stdout.write(text)
            sys.stdout.flush()
        return run_tui(model, db, tables, apply=apply, read=read, write=write, draft=draft)
