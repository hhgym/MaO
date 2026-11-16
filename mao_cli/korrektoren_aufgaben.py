"""Aufgabenzuweisung an Korrektor:innen (2. Stufe): Kandidaten je Aufgabennummer, Prüfung Kind in der Klassenstufe, Menü, Schreiben.

Aufgabennummer = Jahrgang + Klassenstufe (2 Stellen, 12 für 11/12) + Runde (2) + Aufgabe, z. B. 660521.
Zuordnungen liegen in ``korrektoren_zuordnung_table`` (Spalten korrektor_id, mao_nummer, bemerkung); der Jahrgang steckt in mao_nummer.
Es werden nur Zuordnungen des aktuellen Jahrgangs angezeigt und geändert; frühere Jahrgänge zählen nur als „übernommen“.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from .korrektoren import KorrektorenError, _ident, column_limits

LOGGER = logging.getLogger("mao_cli.korrektoren_aufgaben")

STUFEN = {5: "kl_5", 6: "kl_6", 7: "kl_7", 8: "kl_8", 9: "kl_9", 10: "kl_10", 12: "kl_11_12"}
AUFGABEN = (1, 2, 3, 4)
RUNDE = 2
TABLE_KEYS = ("korrektoren_table", "korrektoren_prefs_table", "korrektoren_angebote_table", "korrektoren_angehoerige_table",
              "korrektoren_zuordnung_table", "students_table")
DEFAULT_REMARK_LIMIT = 20
PLAN_FILE = "korrektoren-aufgaben-plan.yaml"


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
    vorname: str = ""
    nachname: str = ""


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

    def candidates(self, nummer: int, *, alle: bool = False, ohne_kinder: bool = False,
                   sortierung: str = "nachname", ohne_vergebene: bool = False) -> list[Candidate]:
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
                sorted(n for (kid, n) in self.want if kid == korrektor_id and n != nummer), bietet, offers, info.get("vorname") or "", info.get("nachname") or ""))
        if ohne_vergebene:  # Personen mit anderer Aufgabe dieses Jahres ausblenden (hier Zugeordnete bleiben sichtbar)
            result = [c for c in result if c.zugeordnet or not c.andere_aufgaben]
        if ohne_kinder:  # Personen mit Kind in der Stufe ausblenden (Zugeordnete bleiben sichtbar)
            result = [c for c in result if c.zugeordnet or not c.kinder_in_stufe]
        if sortierung == "vorname":
            result.sort(key=lambda c: (c.vorname.casefold(), c.nachname.casefold(), c.korrektor_id))
        else:
            result.sort(key=lambda c: (c.nachname.casefold(), c.vorname.casefold(), c.korrektor_id))
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


def _sorted_name(cand: Candidate, sortierung: str) -> str:
    if sortierung == "vorname":
        return f"{cand.vorname} {cand.nachname}".strip()
    return f"{cand.nachname}, {cand.vorname}".strip(", ")


def candidate_lines(work: Work, nummer: int, *, alle: bool = False, cands: list[Candidate] | None = None,
                    sortierung: str = "nachname") -> list[str]:
    year, stufe, _, aufgabe = split_nummer(nummer)
    label = "11/12" if stufe == 12 else str(stufe)
    cands_given = cands is not None
    cands = work.candidates(nummer, alle=alle) if cands is None else cands
    lines = [f"Aufgabe {nummer} (Klassenstufe {label}, Aufgabe {aufgabe}) – zugeordnet: {len(work.assigned(nummer))}",
             " Nr  Z  Korrektor:in                        Angebote  Übernommen  Kind in Stufe  Weitere Aufgaben       Bemerkung"]
    for number, cand in enumerate(cands, 1):
        other = ", ".join(str(n) for n in cand.andere_aufgaben) or "-"
        kind = ", ".join(cand.kinder_in_stufe) if cand.kinder_in_stufe else "-"
        extra = "" if cand.bietet_stufe_an else "  (Stufe nicht angeboten)"
        if not cand.angebot_aktuell:
            extra += f"  (KEIN Angebot {year})"
        lines.append(f"{number:>3}  {'*' if cand.zugeordnet else ' '}  {(_sorted_name(cand, sortierung) if cands_given else cand.name)[:34]:<34}{cand.angebote:>9}{cand.uebernommen:>12}  "
                     f"{kind:<13}  {other:<21}  {cand.bemerkung}{extra}")
    if not cands:
        lines.append("  Keine Korrektor:innen mit Angebot für diese Klassenstufe.")
    return lines


def takeover_lines(work: Work, sortierung: str = "aufgabe") -> list[str]:
    """Übersicht der zugeordneten Korrektor:innen (nach Aufgabennummer sortiert): Aufgaben dieses Jahr, bisher übernommene Jahrgänge."""
    state = work.state
    members: dict[int, list[int]] = {}
    for kid, nummer in sorted(work.want, key=lambda x: (x[1], x[0])):
        members.setdefault(kid, []).append(nummer)

    def order(kid: int) -> tuple[Any, ...]:
        info = state.korrektoren[kid]
        names = (str(info["nachname"]).casefold(), str(info["vorname"]).casefold())
        if sortierung == "vorname":
            return (*names[::-1], kid)
        if sortierung == "nachname":
            return (*names, kid)
        return (members[kid][0], *names, kid)  # nach (kleinster) Aufgabennummer

    lines = [f"Übernommene Korrekturen der zugeordneten Korrektor:innen (Jahrgang {state.jahrgang_nr}, inkl. offener Änderungen)",
             "Korrektor:in                         Aufgaben dieses Jahr           Früher übernommen  Gesamt  Angebote"]
    first = 0
    for kid in sorted(members, key=order):
        info = state.korrektoren[kid]
        earlier = work.uebernommen(kid)
        total = earlier + 1
        first += total == 1
        name = _sorted_name(Candidate(kid, "", 0, 0, [], True, "", [], True, True, str(info["vorname"]), str(info["nachname"])),
                            "vorname" if sortierung == "vorname" else "nachname")
        tasks = ", ".join(str(n) for n in members[kid])
        lines.append(f"{name[:34]:<34}  {tasks:<28}{earlier:>12}{total:>11}{len(state.angebote.get(kid, set())):>10}")
    if not members:
        lines.append("  Noch keine Aufgaben zugeordnet.")
    else:
        lines.append(f"{len(members)} Korrektor:innen, davon erstmals: {first}  (Früher übernommen = Jahrgänge vor diesem mit Zuordnung)")
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


# ------------------------------------------------------------------ Entwurf (korrektoren-aufgaben-plan.yaml)

def plan_text(work: Work, name: str = PLAN_FILE) -> str:
    """Zwischenstand als YAML: nur Korrektor-IDs und Bemerkungen (keine Namen, keine Kontaktdaten)."""
    lines = [f"# {name}: Zwischenstand der Aufgabenzuweisung (nur IDs). Weiterbearbeiten mit: mao korrektoren-aufgaben",
             "# Der Entwurf ersetzt beim Laden den Stand der Datenbank für den aktuellen Jahrgang; geschrieben wird erst mit --apply.",
             f"jahrgang: {work.state.jahrgang_nr}", "zuordnungen:"]
    by_task: dict[int, list[tuple[int, str | None]]] = {}
    for (kid, nummer), remark in sorted(work.want.items(), key=lambda x: (x[0][1], x[0][0])):
        by_task.setdefault(nummer, []).append((kid, remark))
    if not by_task:
        lines[-1] += " {}"
    for nummer, members in by_task.items():
        lines.append(f'  "{nummer}":')
        for kid, remark in members:
            lines.append(f"    - {{korrektor: {kid}" + (f", bemerkung: {json.dumps(remark, ensure_ascii=False)}" if remark else "") + "}")
    return "\n".join(lines) + "\n"


def parse_plan(text: str, work: Work) -> dict[tuple[int, int], str | None]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise KorrektorenError(f"Entwurf unlesbar: {exc}") from exc
    if not isinstance(data, dict):
        raise KorrektorenError("Entwurf: Zuordnung erwartet")
    state = work.state
    if data.get("jahrgang") != state.jahrgang_nr:
        raise KorrektorenError(f"Entwurf gehört zu Jahrgang {data.get('jahrgang')}, aktuell ist {state.jahrgang_nr}")
    result: dict[tuple[int, int], str | None] = {}
    for raw_number, members in (data.get("zuordnungen") or {}).items():
        try:
            nummer = parse_nummer(str(raw_number), state.jahrgang_nr)
        except KorrektorenError as exc:
            raise KorrektorenError(f"Entwurf: Aufgabe {raw_number}: {exc}") from exc
        if not isinstance(members, list):
            raise KorrektorenError(f"Entwurf: Aufgabe {nummer} muss eine Liste sein")
        for member in members:
            if not isinstance(member, dict) or not isinstance(member.get("korrektor"), int):
                raise KorrektorenError(f"Entwurf: Aufgabe {nummer}: Einträge brauchen `korrektor: <ID>`")
            kid = member["korrektor"]
            if kid not in state.korrektoren:
                raise KorrektorenError(f"Entwurf: Korrektor-ID {kid} gibt es nicht (Aufgabe {nummer})")
            remark = str(member["bemerkung"]).strip() if member.get("bemerkung") is not None else None
            if remark and len(remark) > state.remark_limit:
                raise KorrektorenError(f"Entwurf: Bemerkung zu Aufgabe {nummer} zu lang (höchstens {state.remark_limit} Zeichen)")
            if (kid, nummer) in result:
                raise KorrektorenError(f"Entwurf: Korrektor-ID {kid} kommt bei Aufgabe {nummer} mehrfach vor")
            result[(kid, nummer)] = remark or None
    return result


class Draft:
    def __init__(self, workspace: Path, config_dir: Path | None = None, name: str = PLAN_FILE) -> None:
        if not name.lower().endswith((".yaml", ".yml")):
            raise KorrektorenError("Entwurfsdatei braucht die Endung .yaml")
        base = workspace.resolve()
        target = (base / name).resolve()
        if base not in target.parents:
            raise KorrektorenError("Entwurf muss innerhalb des Workspaces liegen")
        if config_dir is not None and (target == config_dir.resolve() or config_dir.resolve() in target.parents):
            raise KorrektorenError("Entwurf darf nicht in der Konfiguration liegen")
        self.name, self.path = name, target
        self.last: dict[tuple[int, int], str | None] | None = None  # zuletzt gespeicherter/geladener Stand

    def exists(self) -> bool:
        return self.path.is_file()

    def save(self, work: Work) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(plan_text(work, self.name), encoding="utf-8", newline="")
        os.replace(tmp, self.path)
        self.last = dict(work.want)
        return self.path

    def unsaved(self, work: Work) -> bool:
        """Gibt es Änderungen gegenüber der Datenbank, die noch nicht im Entwurf stehen?"""
        return bool(work.pending()) and work.want != self.last

    def clear(self) -> bool:
        try:
            self.path.unlink()
            self.last = None
            return True
        except FileNotFoundError:
            return False

    def load_into(self, work: Work) -> str:
        """Entwurf als Stand übernehmen (ersetzt die gewünschten Zuordnungen des Jahrgangs); '' = keine Datei."""
        if not self.exists():
            return ""
        work.want = parse_plan(self.path.read_text(encoding="utf-8"), work)
        self.last = dict(work.want)
        diff = work.diff()
        return (f"Entwurf {self.name} geladen: {len(work.want)} Zuordnungen, gegenüber der Datenbank "
                f"{len(diff['neu'])} neu, {len(diff['entfernt'])} entfernt, {len(diff['bemerkung'])} Bemerkungen geändert")


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
  6) Zwischenstand als Entwurf speichern ({draft})
  7) Übersicht: übernommene Korrekturen der zugeordneten Korrektor:innen
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
             "↑/↓ Aufgabe | ←/→ Klassenstufe | Enter öffnen | u Übernahmen-Übersicht | w Entwurf speichern | s Schreiben/Änderungen | q zurück", "",
             "  Aufgabe  Stufe  Angebote  Zugeordnet"]
    for index, (stufe, aufgabe) in enumerate(ALL_TASKS):
        nummer = make_nummer(state.jahrgang_nr, stufe, aufgabe)
        offers = len([c for c in work.candidates(nummer) if c.bietet_stufe_an])
        names = ", ".join(_short(state.korrektoren[k]) for k in sorted(work.assigned(nummer)))
        label = "11/12" if stufe == 12 else str(stufe)
        lines.append(f"  {nummer}  {label:>5}  {offers:>8}  {names or '-'}")
    return lines, 4 + cursor


CELL_WIDTH = 40
INVERSE, RESET = "\x1b[7m", "\x1b[0m"


def terminal_size() -> tuple[int, int]:
    import shutil
    size = shutil.get_terminal_size((100, 30))
    return size.columns, size.lines


def _cell(number: int, cand: Candidate, sortierung: str = "nachname") -> str:
    kind = f"K:{cand.kinder_in_stufe[0]}" if cand.kinder_in_stufe else ""
    if len(cand.kinder_in_stufe) > 1:
        kind += "+"
    flag = "*" if cand.zugeordnet else ("!" if not cand.angebot_aktuell else " ")
    name = f"{cand.vorname} {cand.nachname}".strip() if sortierung == "vorname" else f"{cand.nachname}, {cand.vorname}".strip(", ")
    text = f"{number:>3}{flag} {name[:19]:<19} {cand.angebote:>2}/{cand.uebernommen:<2} {kind:<7}"
    return text.ljust(CELL_WIDTH)[:CELL_WIDTH]


def task_screen(work: Work, nummer: int, row: int, alle: bool,
                size: tuple[int, int] = (100, 30), ohne_kinder: bool = False, sortierung: str = "nachname",
                ohne_vergebene: bool = False) -> tuple[list[str], int, int]:
    """Kandidatenliste einer Aufgabe, bei Platzmangel in zwei Spalten (spaltenweise) und mit Seiten.

    Rückgabe (Zeilen, Zeile des Cursors oder -1 bei Spaltenansicht (dort invertiert die Zelle selbst), Anzahl Kandidaten)."""
    columns, height = size
    cands = work.candidates(nummer, alle=alle, ohne_kinder=ohne_kinder, sortierung=sortierung, ohne_vergebene=ohne_vergebene)
    hidden_kinder = len(work.candidates(nummer, alle=alle, ohne_vergebene=ohne_vergebene)) - len(
        work.candidates(nummer, alle=alle, ohne_kinder=True, ohne_vergebene=ohne_vergebene))
    hidden_vergeben = len(work.candidates(nummer, alle=alle)) - len(work.candidates(nummer, alle=alle, ohne_vergebene=True))
    count = len(cands)
    row = min(max(row, 0), max(count - 1, 0))
    _, stufe, _, aufgabe = split_nummer(nummer)
    label = "11/12" if stufe == 12 else str(stufe)
    head = ["↑/↓ wählen | Leertaste/Enter zuordnen/entfernen | b Bemerkung | a auch ohne Stufenangebot | "
            "k Kinder in Stufe aus-/einblenden | v Personen mit anderer Aufgabe aus-/einblenden | n Sortierung | "
            "w Entwurf speichern | ←/→ Aufgabe | q zurück",
            f"Aufgabe {nummer} (Klassenstufe {label}, Aufgabe {aufgabe}) – zugeordnet: {len(work.assigned(nummer))}"
            + f" – sortiert nach {'Vorname' if sortierung == 'vorname' else 'Nachname'}"
            + (f" – {hidden_kinder} mit Kind in der Stufe ausgeblendet (k)" if ohne_kinder else "")
            + (f" – {hidden_vergeben} mit anderer Aufgabe ausgeblendet (v)" if ohne_vergebene else "")]
    per_col = max(5, height - 8)
    if count <= per_col or columns < 2 * CELL_WIDTH + 2:
        return candidate_screen_single(work, nummer, cands, row, alle, head, per_col, sortierung)
    capacity = 2 * per_col
    page, pages = row // capacity, (count + capacity - 1) // capacity
    first = page * capacity
    head.append(f"Nr Z Name                Ang/Übern  Kind in Stufe   (* zugeordnet, ! kein Angebot {work.state.jahrgang_nr}, "
                f"K: = Kind){'  Seite %d/%d' % (page + 1, pages) if pages > 1 else ''}")
    shown = cands[first:first + capacity]
    rows = min(per_col, len(shown))
    lines = list(head)
    for r in range(rows):
        cells = []
        for c in range(2):
            idx = c * per_col + r
            if idx < len(shown):
                text = _cell(first + idx + 1, shown[idx], sortierung)
                cells.append(f"{INVERSE}{text}{RESET}" if first + idx == row else text)
        lines.append("  ".join(cells))
    return lines, -1, count


def candidate_screen_single(work: Work, nummer: int, cands: list[Candidate], row: int, alle: bool, head: list[str],
                            per_col: int, sortierung: str = "nachname") -> tuple[list[str], int, int]:
    table = candidate_lines(work, nummer, alle=alle, cands=cands, sortierung=sortierung)  # Titelzeile, Spaltenkopf, Kandidaten
    table[0] = head[1]
    lines = [head[0], ""] + table
    if len(cands) > per_col:  # sehr schmales Fenster: nur eine Seite zeigen
        page = row // per_col
        pages = (len(cands) + per_col - 1) // per_col
        lines = [head[0], "", table[0] + f"  Seite {page + 1}/{pages}", table[1],
                 *table[2 + page * per_col:2 + (page + 1) * per_col]]
        return lines, 4 + row - page * per_col, len(cands)
    return lines, 4 + row, len(cands)


def interactive_table(work: Work, *, key: Callable[[], str] = read_key,
                      draw: Callable[[list[str], int | None], Any] = draw_screen,
                      ask: Callable[[str], str] = input, size: Callable[[], tuple[int, int]] = terminal_size,
                      draft: Draft | None = None) -> bool:
    """Tabelle mit Pfeiltasten; True, wenn der Nutzer zum Schreiben (s) wechseln möchte."""
    pos, status = 0, ""
    while True:
        lines, row = grid_screen(work, pos)
        if status:
            lines += ["", status]
            status = ""
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
        elif pressed == "w":
            status = f"Entwurf gespeichert: {draft.save(work).name}" if draft else "Kein Entwurf-Ziel"
        elif pressed == "u":
            draw(takeover_lines(work) + ["", "Beliebige Taste: zurück zur Tabelle"], None)
            key()
        elif pressed in ("q", "esc"):
            return False
        elif pressed == "enter":
            pos = _interactive_task(work, pos, key=key, draw=draw, ask=ask, size=size, draft=draft)


def _interactive_task(work: Work, pos: int, *, key: Callable[[], str], draw: Callable[[list[str], int | None], Any],
                      ask: Callable[[str], str], size: Callable[[], tuple[int, int]] = terminal_size,
                      draft: Draft | None = None) -> int:
    row, alle, message, ohne_kinder, sortierung, ohne_vergebene = 0, False, "", False, "nachname", True
    while True:
        stufe, aufgabe = ALL_TASKS[pos]
        nummer = make_nummer(work.state.jahrgang_nr, stufe, aufgabe)
        lines, cursor_row, count = task_screen(work, nummer, row, alle, size(), ohne_kinder, sortierung, ohne_vergebene)
        row = min(row, max(count - 1, 0))
        if message:
            lines.append("")
            lines.append(message)
        draw(lines, cursor_row if count and cursor_row >= 0 else None)
        message = ""
        pressed = key()
        cands = work.candidates(nummer, alle=alle, ohne_kinder=ohne_kinder, sortierung=sortierung,
                                ohne_vergebene=ohne_vergebene)
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
        elif pressed == "k":
            ohne_kinder = not ohne_kinder
            row = 0
        elif pressed == "v":
            ohne_vergebene = not ohne_vergebene
            row = 0
        elif pressed == "n":
            sortierung = "vorname" if sortierung == "nachname" else "nachname"
            if count:  # Cursor bleibt auf derselben Person
                current = cands[min(row, count - 1)].korrektor_id
                row = next((i for i, c in enumerate(work.candidates(nummer, alle=alle, ohne_kinder=ohne_kinder, sortierung=sortierung,
                                                                    ohne_vergebene=ohne_vergebene))
                            if c.korrektor_id == current), 0)
        elif pressed == "w":
            message = f"Entwurf gespeichert: {draft.save(work).name}" if draft else "Kein Entwurf-Ziel"
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
             draw: Callable[[list[str], int | None], Any] = draw_screen, draft: Draft | None = None,
             use_draft: bool = True) -> dict[str, int]:
    written_total: dict[str, int] = {}
    want_write = False
    if draft is not None and use_draft and draft.exists():
        try:
            out(draft.load_into(session.work))
        except KorrektorenError as exc:
            out(f"Entwurf nicht übernommen ({exc}); Stand der Datenbank. Mit --neu ignorieren, Datei prüfen oder löschen.")
            session.reload()
    while True:
        work = session.work
        if want_write:
            choice, want_write = "4", False
        else:
            state_text = "gespeichert" if draft is not None and draft.exists() and not draft.unsaved(work) else "noch nicht gespeichert"
            out(MENU.format(pending=work.pending(), draft=state_text if draft is not None else "kein Ziel"))
            try:
                choice = ask("Auswahl: ").strip().lower()
            except EOFError:
                choice = "q"
        try:
            if choice == "1":
                want_write = interactive_table(work, key=key, draw=draw, ask=ask, draft=draft)
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
                    out("DRY-RUN: nichts geschrieben. Zum Schreiben mit: mao korrektoren-aufgaben --apply")
                    if draft is not None:
                        out(f"Entwurf gespeichert: {draft.save(work).name} (weiterbearbeiten: erneut starten, bestätigen: --apply)")
                elif ask("In die Datenbank schreiben? [j/N] ").strip().lower() in ("j", "ja"):
                    written = session.write()
                    for name, value in written.items():
                        written_total[name] = written_total.get(name, 0) + value
                    out("Geschrieben: " + ", ".join(f"{k} {v}" for k, v in written.items()))
                    if draft is not None and draft.clear():
                        out("Entwurf gelöscht (Stand ist in der Datenbank).")
            elif choice == "5":
                session.reload()
                out("Änderungen verworfen, neu geladen.")
                if draft is not None and draft.exists() and ask("Auch den gespeicherten Entwurf löschen? [j/N] ").strip().lower() in ("j", "ja"):
                    draft.clear()
                    out("Entwurf gelöscht.")
            elif choice == "6":
                out(f"Entwurf gespeichert: {draft.save(work).name}" if draft is not None else "Kein Entwurf-Ziel")
            elif choice == "7":
                for line in takeover_lines(work):
                    out(line)
            elif choice in ("q", "quit", "exit"):
                if draft is not None and draft.unsaved(work):
                    if ask("Entwurf speichern? [J/n] ").strip().lower() not in ("n", "nein"):
                        out(f"Entwurf gespeichert: {draft.save(work).name}")
                elif work.pending() and apply:
                    out(f"Hinweis: {work.pending()} Änderungen sind nicht geschrieben und gehen verloren.")
                return written_total
            elif choice:
                out("Bitte 1 bis 7 oder q eingeben.")
        except KorrektorenError as exc:
            out(f"Nicht möglich: {exc}")


def run_korrektoren_aufgaben(db: Any, tables: Mapping[str, str], *, apply: bool, menu: bool = True,
                             expected_nr: int | None = None, workspace: Path | None = None, config_dir: Path | None = None,
                             neu: bool = False, ask: Callable[[str], str] = input,
                             out: Callable[[str], Any] = print, **menu_kw: Any) -> dict[str, int]:
    """Menü (Standard) oder ohne Menü: nur Übersicht mit Kandidaten je Aufgabe ausgeben (schreibt nie)."""
    session = AufgabenSession(db, tables, expected_nr)
    if menu:
        draft = Draft(workspace, config_dir) if workspace is not None else None
        return run_menu(session, apply=apply, ask=ask, out=out, draft=draft, use_draft=not neu, **menu_kw)
    for line in overview_lines(session.work):
        out(line)
    out("")
    for line in takeover_lines(session.work):
        out(line)
    for stufe in STUFEN:
        for aufgabe in AUFGABEN:
            nummer = make_nummer(session.work.state.jahrgang_nr, stufe, aufgabe)
            out("")
            for line in candidate_lines(session.work, nummer):
                out(line)
    return {}
