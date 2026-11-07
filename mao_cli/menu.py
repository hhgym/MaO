"""Pfeiltasten-Menü zum Auswählen von LaTeX-Jobs und Exporten (``mao run`` / ``latex`` / ``plan``).

Bedienung: ↑/↓ wählen, Leertaste setzt/entfernt das Häkchen, a = alle der Gruppe, n = keine der Gruppe,
Enter = ausführen, q oder Esc = abbrechen. Exporte, die ein gewählter Job braucht, erscheinen als ``[+]``
(automatisch) und müssen nicht angekreuzt werden.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .aufsichten_tui import DIM, RESET, REVERSE, terminal, terminal_size
from .common import _as_list
from .config import ConfigurationError
from .jobs import export_id, job_exports

HELP = "↑↓ wählen · Leertaste an/aus · a alle · n keine · Enter ausführen · q abbrechen"


@dataclass
class MenuState:
    jobs: list[str]
    exports: list[str]
    needs: dict[str, set[str] | None]          # Job → benötigte Exporte (None = nicht erkennbar)
    details: dict[str, str] = field(default_factory=dict)
    with_exports: bool = True
    chosen_jobs: set[str] = field(default_factory=set)
    chosen_exports: set[str] = field(default_factory=set)
    cursor: int = 0
    message: str = ""

    def rows(self) -> list[tuple[str, str]]:
        rows = [("job", name) for name in self.jobs]
        if self.with_exports:
            rows += [("export", name) for name in self.exports]
        return rows

    def derived(self) -> tuple[set[str], bool]:
        """(durch Jobs benötigte Exporte, Zuordnung unvollständig?)"""
        needed: set[str] = set()
        unknown = False
        for name in self.chosen_jobs:
            ids = self.needs.get(name)
            if ids is None:
                unknown = True
            else:
                needed |= ids
        return needed, unknown

    def toggle(self) -> None:
        kind, name = self.rows()[self.cursor]
        target = self.chosen_jobs if kind == "job" else self.chosen_exports
        target.symmetric_difference_update({name})
        self.message = ""

    def set_group(self, value: bool) -> None:
        kind, _ = self.rows()[self.cursor]
        names = self.jobs if kind == "job" else self.exports
        target = self.chosen_jobs if kind == "job" else self.chosen_exports
        target.clear()
        if value:
            target.update(names)
        self.message = ""


def render(state: MenuState, height: int, width: int, title: str) -> str:
    rows = state.rows()
    needed, unknown = state.derived()
    lines: list[str] = []
    body: list[tuple[str, int | None]] = []  # (Text, Zeilenindex in rows oder None)
    last_kind = None
    for index, (kind, name) in enumerate(rows):
        if kind != last_kind:
            body.append((("LaTeX-Jobs" if kind == "job" else "Exporte (nur Daten, ohne PDF)"), None))
            last_kind = kind
        if kind == "job":
            mark = "x" if name in state.chosen_jobs else " "
        else:
            mark = "x" if name in state.chosen_exports else "+" if name in needed else " "
        detail = state.details.get(f"{kind}:{name}", "")
        body.append((f"[{mark}] {name:<34} {detail}".rstrip(), index))
    visible = max(3, height - 6)
    cursor_line = next(i for i, (_t, idx) in enumerate(body) if idx == state.cursor)
    top = min(max(0, cursor_line - visible + 2), max(0, len(body) - visible))
    top = max(0, min(top, cursor_line))
    lines.append(f"{title}")
    lines.append(f"{DIM}{HELP}{RESET}")
    lines.append("")
    for text, idx in body[top:top + visible]:
        if idx is None:
            lines.append(f"{DIM}{text}{RESET}")
        elif idx == state.cursor:
            lines.append(f"{REVERSE}> {text}{RESET}"[: width + 8])
        else:
            lines.append(f"  {text}"[:width])
    lines.append("")
    n_exports = len(needed | state.chosen_exports)
    extra = " · Zuordnung bei mindestens einem Job nicht erkennbar: dazu alle aktivierten Exporte" if unknown else ""
    lines.append(f"Gewählt: {len(state.chosen_jobs)} Job(s) · {n_exports} Export(e){extra}" if state.with_exports
                 else f"Gewählt: {len(state.chosen_jobs)} Job(s)")
    lines.append(state.message)
    return "\x1b[H\x1b[J" + "\r\n".join(lines)


def run_menu(state: MenuState, *, title: str, read: Callable[[], str], write: Callable[[str], Any],
             size: Callable[[], tuple[int, int]] = terminal_size) -> MenuState | None:
    while True:
        height, width = size()
        write(render(state, height, width, title))
        key = read()
        count = len(state.rows())
        if key in ("q", "esc"):
            return None
        if key == "up":
            state.cursor = (state.cursor - 1) % count
        elif key == "down":
            state.cursor = (state.cursor + 1) % count
        elif key == "home":
            state.cursor = 0
        elif key == "end":
            state.cursor = count - 1
        elif key == "pgup":
            state.cursor = max(0, state.cursor - 10)
        elif key == "pgdn":
            state.cursor = min(count - 1, state.cursor + 10)
        elif key == "space":
            state.toggle()
        elif key == "a":
            state.set_group(True)
        elif key == "n":
            state.set_group(False)
        elif key == "enter":
            if state.chosen_jobs or state.chosen_exports:
                return state
            state.message = "Nichts ausgewählt (Leertaste setzt ein Häkchen, q bricht ab)."


def build_state(cfg: Mapping[str, Any], workspace: Any, tex_dir: Any, *, with_exports: bool) -> MenuState:
    jobs = [j for j in _as_list((cfg.get("latex") or {}).get("jobs")) if j.get("name")]
    exports = _as_list(cfg.get("exports"))
    needs = {str(j["name"]): job_exports(cfg, j, workspace, tex_dir) for j in jobs}
    details: dict[str, str] = {}
    for job in jobs:
        ids = needs[str(job["name"])]
        details[f"job:{job['name']}"] = ("Daten: ?" if ids is None else f"Daten: {len(ids)} Export(e)")
    for export in exports:
        details[f"export:{export_id(export)}"] = str(export.get("from_dataset", ""))
    return MenuState([str(j["name"]) for j in jobs], [export_id(e) for e in exports], needs, details, with_exports)


def choose(cfg: Mapping[str, Any], workspace: Any, tex_dir: Any, *, command: str,
           with_exports: bool = True) -> tuple[list[str], list[str]] | None:
    """Menü im Terminal; ``None`` = abgebrochen. Liefert (Job-Namen, zusätzlich gewählte Export-IDs)."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise ConfigurationError(f"'mao {command}' ohne Angabe braucht ein Terminal für das Auswahlmenü; "
                                 "nichtinteraktiv: --pipeline (Auswahl aus pipeline.yaml) oder --job NAME")
    state = build_state(cfg, workspace, tex_dir, with_exports=with_exports)
    if not state.rows():
        raise ConfigurationError("Weder LaTeX-Jobs noch Exporte definiert")

    def write(text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    with terminal() as read:
        result = run_menu(state, title=f"mao {command}: Was soll ausgeführt werden?", read=read, write=write)
    if result is None:
        return None
    return sorted(result.chosen_jobs, key=state.jobs.index), sorted(result.chosen_exports, key=state.exports.index)
