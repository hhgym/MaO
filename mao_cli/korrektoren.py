"""Korrekturangebote (Gravity Forms) in die Korrektoren-Tabellen übernehmen.

Pro Eintrag des aktuellen Jahrgangs werden angelegt oder aktualisiert: Korrektor:in (MaO_Korrektoren), Präferenzen
(…_Prefs, vollständig aus dem Formular, weil bekannte Korrektor:innen es mit ihren Daten vorbefüllt bekommen), Angebot
des Jahrgangs (…_Angebote) und bei Übergabe per Post die Anschrift (…_Adressen).
Zuordnung: uid, dann E-Mail, dann Name. Sichere Treffer (uid, eindeutige E-Mail) werden automatisch übernommen,
alles andere wird gemeldet bzw. mit --interaktiv entschieden. Hertz-Kinder (…_Angehoerige) werden nur auf
ausdrückliche Entscheidung (interaktiv) geschrieben. Berichte und Entscheidungsdatei enthalten nur IDs, keine
Namen, Adressen oder E-Mail-Adressen.
"""
from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import yaml

from .config import ConfigurationError
from .gf_import import IDENT, gf_entries, load_gf_credentials

LOGGER = logging.getLogger(__name__)
FIELD_ID = re.compile(r"^\d+(\.\d+)?$")
FIELD_KEYS = ("uid", "jahrgang_nr", "schuljahr", "name", "geschlecht", "email", "kinder_bekannt", "weitere_kinder", "kinder",
              "stufen", "wuensche", "erfahrung", "erfahrung_text", "uebergabe", "anschrift", "uebergabe_sonst", "anmerkungen")
OPTIONAL_KEYS = ("uid_neu",)
TABLE_KEYS = ("korrektoren_table", "korrektoren_prefs_table", "korrektoren_angebote_table",
              "korrektoren_adressen_table", "korrektoren_angehoerige_table", "schueler_alle_table")
STUFEN_COLUMNS = ("kl_5", "kl_6", "kl_7", "kl_8", "kl_9", "kl_10", "kl_11_12")
ERFAHRUNG_COLUMNS = ("keine_erfahrung", "korrexp", "selbst_teilgenommen")
GENDERS = {"männlich": "m", "weiblich": "w", "divers": "d", "möchte nicht antworten": "k", "m": "m", "w": "w", "d": "d", "k": "k"}
UEBERGABE = ("Hertzkind", "Sekretariat", "Post", "Sonstiges")
DECISIONS_FILE = "korrektoren-entscheidungen.yaml"
KORREKTOR_COLUMNS = {"titel": "titel", "vorname": "vorname", "nachname": "nachname", "geschlecht": "geschlecht", "email": "email"}


class KorrektorenError(ValueError):
    """Unbrauchbare Daten oder Tabellen. Es wird nichts geschrieben."""


@dataclass(frozen=True)
class KorrSettings:
    form_id: int
    page_size: int
    timeout: int
    fields: Mapping[str, str]


def load_korr_settings(path: Path) -> KorrSettings:
    if not path.is_file():
        raise ConfigurationError(f"Konfiguration für Korrektoren fehlt: {path}; bitte mao config init ausführen")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"gf-korrektoren.yaml unlesbar: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) - {"form_id", "page_size", "timeout", "fields"}:
        raise ConfigurationError("gf-korrektoren.yaml: ungültige oder unbekannte Schlüssel")
    fields = raw.get("fields")
    if (not isinstance(fields, dict) or not set(FIELD_KEYS) <= set(fields) or set(fields) - set(FIELD_KEYS + OPTIONAL_KEYS)
            or not all(isinstance(v, str) and FIELD_ID.fullmatch(v) for v in fields.values())):
        raise ConfigurationError("gf-korrektoren.yaml: fields benötigt " + ", ".join(FIELD_KEYS)
                                 + " (optional: uid_neu); Feldnummern als Zeichenketten")

    def number(key: str, default: int, maximum: int) -> int:
        value = raw.get(key, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ConfigurationError(f"gf-korrektoren.yaml: {key} außerhalb zulässiger Grenzen")
        return value

    return KorrSettings(number("form_id", 3, 100000), number("page_size", 100, 500), number("timeout", 60, 300), dict(fields))


# ------------------------------------------------------------------ Hilfsfunktionen

def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def fold(value: Any) -> str:
    """Namen vergleichbar machen: ohne Akzente/Umlaute, klein, einfache Leerzeichen."""
    text = clean(value).casefold().replace("ß", "ss")
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", text).strip()


def norm_class(value: Any) -> str | None:
    """Klassenbezeichnung vereinheitlichen: 8_1, 8.1, 8a, 8-1 → 8-1 (a=1, b=2 …)."""
    text = clean(value).casefold()
    match = re.fullmatch(r"(\d{1,2})\s*[-_./ ]?\s*(\d{1,2}|[a-z])?", text)
    if not match:
        return None
    level, part = match.group(1), match.group(2)
    if part is None:
        return level
    if part.isalpha():
        part = str(ord(part) - ord("a") + 1)
    return f"{int(level)}-{int(part)}"


def php_unserialize(text: str) -> Any:
    """Minimaler PHP-unserialize für Listenfelder (Arrays, Zeichenketten, Zahlen)."""
    data = text.encode("utf-8")
    pos = 0

    def read_until(char: bytes) -> bytes:
        nonlocal pos
        end = data.index(char, pos)
        chunk, pos = data[pos:end], end + 1
        return chunk

    def parse() -> Any:
        nonlocal pos
        kind = data[pos:pos + 1]
        pos += 2
        if kind == b"N":
            return None
        if kind in (b"i", b"d", b"b"):
            value = read_until(b";").decode()
            return int(value) if kind != b"d" else float(value)
        if kind == b"s":
            length = int(read_until(b":"))
            value = data[pos + 1:pos + 1 + length].decode("utf-8")
            pos += length + 3
            return value
        if kind == b"a":
            count = int(read_until(b":"))
            pos += 1
            result = {}
            for _ in range(count):
                key = parse()
                result[key] = parse()
            pos += 1
            return result
        raise ValueError("unbekannter Typ")

    return parse()


def parse_list_field(value: Any) -> list[dict[str, str]]:
    """Listenfeld (Name, Klasse) als Liste von Zeilen; akzeptiert Listen, JSON-Listen und PHP-serialisierte Werte."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            if text.startswith("a:"):
                value = php_unserialize(text)
            else:
                import json
                value = json.loads(text)
        except (ValueError, IndexError):
            return []
    if isinstance(value, dict):
        value = list(value.values())
    rows = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            rows.append({"name": clean(item.get("Name", item.get("name"))), "klasse": clean(item.get("Klasse", item.get("klasse")))})
        elif isinstance(item, str) and clean(item):
            rows.append({"name": clean(item), "klasse": ""})
    return [r for r in rows if r["name"] or r["klasse"]]


def parse_type_limit(column_type: str) -> int | None:
    text = str(column_type).lower()
    match = re.match(r"(?:var)?char\((\d+)\)", text)
    if match:
        return int(match.group(1))
    return {"tinytext": 255, "text": 65535, "mediumtext": 16777215, "longtext": 4294967295}.get(text.split("(")[0])


# ------------------------------------------------------------------ Formular → Zeilen

def parse_entries(entries: Iterable[Mapping[str, Any]], fields: Mapping[str, str]
                  ) -> tuple[list[dict[str, Any]], list[str], int]:
    """Einträge in Zeilen umwandeln. Rückgabe: Zeilen, Hinweise, Zahl inaktiver Einträge."""
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    inactive = 0

    def get(entry: Mapping[str, Any], key: str, sub: int | None = None) -> str:
        base = fields.get(key)
        if base is None:
            return ""
        return clean(entry.get(base if sub is None else f"{base}.{sub}"))

    for entry in entries:
        entry_id = clean(entry.get("id"))
        if entry.get("status") not in (None, "active"):
            inactive += 1
            continue
        if not entry_id.isdigit():
            issues.append("Eintrag ohne gültige ID übersprungen")
            continue
        name_base = fields["name"]
        first = clean(entry.get(f"{name_base}.3"))
        middle = clean(entry.get(f"{name_base}.4"))
        gender_raw = get(entry, "geschlecht").casefold()
        uebergabe = get(entry, "uebergabe")
        address_base = fields["anschrift"]
        weitere = get(entry, "weitere_kinder").casefold() == "ja"
        rows.append({
            "eintrag_id": int(entry_id),
            "erstellt": clean(entry.get("date_created")),
            "uid": get(entry, "uid") or get(entry, "uid_neu"),
            "uid_bekannt": bool(get(entry, "uid")),
            "jahrgang_nr": get(entry, "jahrgang_nr"),
            "schuljahr": get(entry, "schuljahr"),
            "titel": clean(entry.get(f"{name_base}.2")),
            "vorname": clean(f"{first} {middle}"),
            "nachname": clean(entry.get(f"{name_base}.6")),
            "geschlecht": GENDERS.get(gender_raw),
            "geschlecht_roh": gender_raw,
            "email": get(entry, "email"),
            "stufen": {col: int(bool(get(entry, "stufen", i))) for i, col in enumerate(STUFEN_COLUMNS, 1)},
            "wuensche": get(entry, "wuensche"),
            "erfahrung": {col: int(bool(get(entry, "erfahrung", i))) for i, col in enumerate(ERFAHRUNG_COLUMNS, 1)},
            "erfahrung_text": get(entry, "erfahrung_text"),
            "uebergabe": uebergabe or "Hertzkind",
            "uebergabe_sonst": get(entry, "uebergabe_sonst"),
            "anmerkungen": get(entry, "anmerkungen"),
            "adresse": {col: clean(entry.get(f"{address_base}.{sub}")) for col, sub in
                        (("strasse", 1), ("zusatz", 2), ("ort", 3), ("bundesland", 4), ("plz", 5), ("land", 6))},
            "kinder_bekannt": sum(1 for i in (1, 2, 3, 4, 5, 6) if get(entry, "kinder_bekannt", i)),
            "kinder": parse_list_field(entry.get(fields["kinder"])) if weitere else [],
        })
    return rows, issues, inactive


def is_current(row: Mapping[str, Any], jahrgang_nr: int, schuljahr_id: int) -> bool | None:
    """True/False, wenn der Jahrgang erkennbar ist (Jahrgangsnummer und/oder Schuljahr-ID); None, wenn nicht."""
    checks = []
    if row["jahrgang_nr"]:
        checks.append(row["jahrgang_nr"] == str(jahrgang_nr))
    if row["schuljahr"]:
        checks.append(row["schuljahr"] == str(schuljahr_id))
    return all(checks) if checks else None


# ------------------------------------------------------------------ Abgleich und Planung

def student_candidates(name: str, klasse: str, students: Iterable[Mapping[str, Any]], limit: int = 5
                       ) -> list[dict[str, Any]]:
    """Wahrscheinliche Schüler:innen zu Name und Klasse (auch inaktive und ohne Klasse), beste zuerst."""
    wanted = fold(name)
    wanted_rev = " ".join(reversed(wanted.split()))
    want_class = norm_class(klasse)
    scored = []
    for s in students:
        full = fold(f"{s['vorname']} {s['nachname']}")
        score = max(difflib.SequenceMatcher(None, wanted, full).ratio(),
                    difflib.SequenceMatcher(None, wanted_rev, full).ratio())
        same_class = bool(want_class) and norm_class(s.get("klasse")) == want_class
        if same_class:
            score = min(1.0, score + 0.1)
        if score >= 0.6:
            scored.append({"matrikelnummer": int(s["matrikelnummer"]), "vorname": s["vorname"], "nachname": s["nachname"],
                           "klasse": s.get("klasse"), "aktiv": s.get("aktiv"), "score": round(score, 2), "gleiche_klasse": same_class})
    scored.sort(key=lambda c: (-c["score"], c["matrikelnummer"]))
    return scored[:limit]


@dataclass
class Item:
    row: dict[str, Any]
    status: str = "ok"                    # ok | unklar | uebersprungen
    korrektor_id: int | None = None       # None und status ok: neu anlegen
    treffer: str = ""                     # uid | email | name | entscheidung | neu
    kandidaten: list[dict[str, Any]] = field(default_factory=list)
    aenderungen: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    gruende: list[str] = field(default_factory=list)
    kinder_offen: list[dict[str, Any]] = field(default_factory=list)
    kinder_neu: list[int] = field(default_factory=list)
    angebot_vorhanden: bool = False
    prefs_vorhanden: bool = False
    adresse_vorhanden: bool = False
    reaktiviert: bool = False


@dataclass
class State:
    jahrgang_id: int
    jahrgang_nr: int
    schuljahr_id: int
    korrektoren: list[dict[str, Any]]
    prefs: set[int]
    angebote: set[tuple[int, int]]
    adressen: set[int]
    angehoerige: set[tuple[int, int]]
    students: list[dict[str, Any]]
    limits: Mapping[str, Mapping[str, int | None]]


@dataclass
class Plan:
    items: list[Item] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)
    anderes_jahr: int = 0
    jahr_unbekannt: int = 0
    doppelt: int = 0


def _identity(row: Mapping[str, Any]) -> str:
    if row["uid"] and row["uid_bekannt"]:
        return "uid:" + row["uid"]
    if row["email"]:
        return "mail:" + row["email"].casefold()
    return "name:" + fold(row["vorname"]) + "|" + fold(row["nachname"])


def _length_problems(row: Mapping[str, Any], limits: Mapping[str, Mapping[str, int | None]]) -> list[str]:
    checks = [("korrektoren", "titel", row["titel"]), ("korrektoren", "vorname", row["vorname"]),
              ("korrektoren", "nachname", row["nachname"]), ("korrektoren", "email", row["email"]),
              ("prefs", "wuensche", row["wuensche"]), ("prefs", "korrexp_text", row["erfahrung_text"]),
              ("prefs", "anmerkungen", row["anmerkungen"]), ("prefs", "uebergabe_sonst", row["uebergabe_sonst"])]
    if row["uebergabe"] == "Post":
        checks += [("adressen", col, val) for col, val in row["adresse"].items()]
    problems = []
    for table, column, value in checks:
        limit = limits.get(table, {}).get(column)
        if limit is not None and len(value) > limit:
            problems.append(f"Spalte {column} in {table} ist auf {limit} Zeichen begrenzt, benötigt {len(value)}: Länge verlängern")
    return problems


def plan_import(rows: list[dict[str, Any]], state: State, decisions: Mapping[str, Any] | None = None) -> Plan:
    decisions = decisions or {}
    plan = Plan()
    current = []
    for row in rows:
        verdict = is_current(row, state.jahrgang_nr, state.schuljahr_id)
        if verdict is None:
            plan.jahr_unbekannt += 1
            plan.hinweise.append(f"GF-Eintrag {row['eintrag_id']}: Jahrgang nicht erkennbar (übersprungen)")
        elif not verdict:
            plan.anderes_jahr += 1
        else:
            current.append(row)
    newest: dict[str, dict[str, Any]] = {}
    for row in current:
        key = _identity(row)
        old = newest.get(key)
        if old is None or (row["erstellt"], row["eintrag_id"]) > (old["erstellt"], old["eintrag_id"]):
            if old is not None:
                plan.doppelt += 1
            newest[key] = row
        else:
            plan.doppelt += 1
    by_uid = {k["gf_uid"]: k for k in state.korrektoren if k.get("gf_uid")}
    by_mail: dict[str, list[dict[str, Any]]] = {}
    by_name: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for k in state.korrektoren:
        if k.get("email"):
            by_mail.setdefault(k["email"].casefold(), []).append(k)
        by_name.setdefault((fold(k["vorname"]), fold(k["nachname"])), []).append(k)
    by_id = {k["id"]: k for k in state.korrektoren}
    student_ids = {int(s["matrikelnummer"]) for s in state.students}
    for row in sorted(newest.values(), key=lambda r: r["eintrag_id"]):
        item = Item(row)
        plan.items.append(item)
        for problem in _length_problems(row, state.limits):
            item.gruende.append(problem)
        if not row["nachname"] or not row["vorname"]:
            item.gruende.append("Name unvollständig")
        if row["geschlecht_roh"] and row["geschlecht"] is None:
            plan.hinweise.append(f"GF-Eintrag {row['eintrag_id']}: Geschlecht unbekannt (ohne Angabe übernommen)")
        if row["uebergabe"] not in UEBERGABE:
            item.gruende.append("Übergabe unbekannt")
        if item.gruende:
            item.status = "uebersprungen"
            continue
        decision = (decisions.get(str(row["eintrag_id"])) or {}).get("korrektor")
        found: dict[str, Any] | None = None
        if decision == "skip":
            item.status = "uebersprungen"
            item.gruende.append("Entscheidung: überspringen")
            continue
        if decision == "neu":
            item.treffer = "neu"
        elif isinstance(decision, int) and decision in by_id:
            found, item.treffer = by_id[decision], "entscheidung"
        elif row["uid_bekannt"] and row["uid"] in by_uid:
            found, item.treffer = by_uid[row["uid"]], "uid"
        else:
            mail = by_mail.get(row["email"].casefold(), []) if row["email"] else []
            named = by_name.get((fold(row["vorname"]), fold(row["nachname"])), [])
            if len(mail) == 1:
                found, item.treffer = mail[0], "email"
            elif len(mail) > 1 or named:
                item.status = "unklar"
                item.kandidaten = [{"id": k["id"], "vorname": k["vorname"], "nachname": k["nachname"],
                                    "email": k.get("email"), "gf_uid": bool(k.get("gf_uid"))} for k in (mail or named)]
                continue
            else:
                item.treffer = "neu"
        if found is not None:
            item.korrektor_id = found["id"]
            for column in KORREKTOR_COLUMNS:
                new = row[column]
                if new and new != (found.get(column) or ""):
                    item.aenderungen[column] = (found.get(column), new)
            if not found.get("gf_uid") and row["uid"] and not row["uid_bekannt"]:
                item.aenderungen["gf_uid"] = (None, row["uid"])
            item.reaktiviert = found.get("aktiv") in (0, False)
            item.prefs_vorhanden = found["id"] in state.prefs
            item.angebot_vorhanden = (state.jahrgang_id, found["id"]) in state.angebote
            item.adresse_vorhanden = found["id"] in state.adressen
        # Hertz-Kinder: nur mit ausdrücklicher Entscheidung schreiben
        kid_decisions = (decisions.get(str(row["eintrag_id"])) or {}).get("kinder") or {}
        for index, kid in enumerate(row["kinder"]):
            choice = kid_decisions.get(str(index), kid_decisions.get(index))
            if isinstance(choice, int):
                if choice not in student_ids:
                    plan.hinweise.append(f"GF-Eintrag {row['eintrag_id']}: Matrikelnummer der Entscheidung für Hertz-Kind {index + 1} "
                                         "nicht in den Stammdaten (nicht geschrieben)")
                elif found is None or (found["id"], choice) not in state.angehoerige:
                    item.kinder_neu.append(choice)
            elif choice is None:
                item.kinder_offen.append({"index": index, "name": kid["name"], "klasse": kid["klasse"],
                                          "kandidaten": student_candidates(kid["name"], kid["klasse"], state.students)})
    return plan


# ------------------------------------------------------------------ Entscheidungen

def load_decisions(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"{path.name} unlesbar: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{path.name}: ungültiger Aufbau")
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


def save_decisions(path: Path, decisions: Mapping[str, Any]) -> None:
    path.write_text("# Entscheidungen von mao import-korrektoren --interaktiv (nur IDs, keine Namen)\n"
                    + yaml.safe_dump(dict(decisions), allow_unicode=True, sort_keys=True), encoding="utf-8")


def ask_decisions(plan: Plan, decisions: dict[str, Any], *, ask: Callable[[str], str] = input,
                  out: Callable[[str], Any] = print) -> bool:
    """Offene Fragen stellen. Rückgabe: True, wenn sich Entscheidungen geändert haben."""
    changed = False
    for item in plan.items:
        row = item.row
        key = str(row["eintrag_id"])
        if item.status == "unklar":
            out(f"\nGF-Eintrag {key}: {row['vorname']} {row['nachname']} <{row['email']}> – bekannte Korrektor:in unklar")
            for number, k in enumerate(item.kandidaten, 1):
                out(f"  {number}) ID {k['id']}: {k['vorname']} {k['nachname']} <{k.get('email') or 'ohne E-Mail'}>"
                    f"{'' if k['gf_uid'] else ' (ohne uid)'}")
            while True:
                answer = ask("Nummer wählen, n = neu anlegen, s = später/überspringen: ").strip().lower()
                if answer == "n":
                    decisions.setdefault(key, {})["korrektor"] = "neu"
                elif answer == "s" or answer == "":
                    decisions.setdefault(key, {})["korrektor"] = "skip"
                elif answer.isdigit() and 1 <= int(answer) <= len(item.kandidaten):
                    decisions.setdefault(key, {})["korrektor"] = item.kandidaten[int(answer) - 1]["id"]
                else:
                    out("Bitte eine Nummer, n oder s eingeben.")
                    continue
                changed = True
                break
        for kid in item.kinder_offen:
            out(f"\nGF-Eintrag {key}, Hertz-Kind {kid['index'] + 1}: {kid['name']} ({kid['klasse'] or 'ohne Klasse'})")
            for number, c in enumerate(kid["kandidaten"], 1):
                out(f"  {number}) {c['vorname']} {c['nachname']} | Klasse {c['klasse'] or '–'} | "
                    f"{'aktiv' if c['aktiv'] in (1, True) else 'inaktiv'} | Matrikel {c['matrikelnummer']} | {c['score']:.2f}")
            while True:
                answer = ask("Nummer wählen, Matrikelnummer eingeben, k = kein Kind zuordnen, Enter = offen lassen: ").strip().lower()
                kinder = decisions.setdefault(key, {}).setdefault("kinder", {})
                if answer == "":
                    break
                if answer == "k":
                    kinder[str(kid["index"])] = "keins"
                elif answer.isdigit() and 1 <= int(answer) <= len(kid["kandidaten"]):
                    kinder[str(kid["index"])] = kid["kandidaten"][int(answer) - 1]["matrikelnummer"]
                elif answer.isdigit() and int(answer) > 1000:
                    kinder[str(kid["index"])] = int(answer)
                else:
                    out("Ungültige Eingabe.")
                    continue
                changed = True
                break
    return changed


# ------------------------------------------------------------------ Datenbank

def _ident(tables: Mapping[str, str], key: str) -> str:
    name = tables.get(key, "")
    if not IDENT.fullmatch(name or "") or name.startswith("DEMO_"):
        raise KorrektorenError(f"{key} fehlt oder ist ein DEMO-Platzhalter: tables.ini prüfen")
    return name


def column_limits(cur: Any, table: str) -> dict[str, int | None]:
    cur.execute(f"SHOW COLUMNS FROM `{table}`")
    return {str(r[0]): parse_type_limit(r[1]) for r in cur.fetchall()}


def load_state(cur: Any, tables: Mapping[str, str]) -> State:
    names = {key: _ident(tables, key) for key in TABLE_KEYS}
    years_t, sy_t = _ident(tables, "jahrgaenge_table"), _ident(tables, "schuljahre_table")
    cur.execute(f"SELECT j.id, j.jahrgang_nr, j.schuljahr_id FROM `{years_t}` j JOIN `{sy_t}` s ON s.id = j.schuljahr_id "
                "WHERE s.aktiv = 1 ORDER BY j.id DESC LIMIT 1")
    found = cur.fetchall()
    if not found:
        raise KorrektorenError("Kein aktiver Jahrgang in der Datenbank gefunden")
    jahrgang_id, jahrgang_nr, schuljahr_id = (int(x) for x in found[0])
    cur.execute(f"SELECT id, gf_uid, titel, vorname, nachname, geschlecht, email, aktiv FROM `{names['korrektoren_table']}`")
    korrektoren = [{"id": int(i), "gf_uid": u, "titel": t, "vorname": v or "", "nachname": n or "", "geschlecht": g,
                    "email": m, "aktiv": a} for i, u, t, v, n, g, m, a in cur.fetchall()]
    cur.execute(f"SELECT korrektor_id FROM `{names['korrektoren_prefs_table']}`")
    prefs = {int(r[0]) for r in cur.fetchall()}
    cur.execute(f"SELECT mao_jahrgang_id, korrektor_id FROM `{names['korrektoren_angebote_table']}`")
    angebote = {(int(a), int(b)) for a, b in cur.fetchall()}
    cur.execute(f"SELECT korrektor_id FROM `{names['korrektoren_adressen_table']}`")
    adressen = {int(r[0]) for r in cur.fetchall()}
    cur.execute(f"SELECT korrektor_id, hertzkind_matrikelnummer FROM `{names['korrektoren_angehoerige_table']}`")
    angehoerige = {(int(a), int(b)) for a, b in cur.fetchall()}
    cur.execute(f"SELECT matrikelnummer, vorname, nachname, klasse, aktiv FROM `{names['schueler_alle_table']}`")
    students = [{"matrikelnummer": m, "vorname": v or "", "nachname": n or "", "klasse": k, "aktiv": a}
                for m, v, n, k, a in cur.fetchall()]
    limits = {"korrektoren": column_limits(cur, names["korrektoren_table"]), "prefs": column_limits(cur, names["korrektoren_prefs_table"]),
              "adressen": column_limits(cur, names["korrektoren_adressen_table"])}
    return State(jahrgang_id, jahrgang_nr, schuljahr_id, korrektoren, prefs, angebote, adressen, angehoerige, students, limits)


def _insert(cur: Any, table: str, values: Mapping[str, Any]) -> None:
    cols = list(values)
    cur.execute(f"INSERT INTO `{table}` ({', '.join('`' + c + '`' for c in cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                tuple(values[c] for c in cols))


def _update(cur: Any, table: str, key_col: str, key: Any, values: Mapping[str, Any]) -> None:
    cols = list(values)
    cur.execute(f"UPDATE `{table}` SET {', '.join('`' + c + '` = %s' for c in cols)} WHERE `{key_col}` = %s",
                (*(values[c] for c in cols), key))


def apply_plan(cur: Any, tables: Mapping[str, str], state: State, plan: Plan) -> dict[str, int]:
    names = {key: _ident(tables, key) for key in TABLE_KEYS}
    stats = dict.fromkeys(("korrektoren_neu", "korrektoren_aktualisiert", "prefs", "angebote", "adressen", "angehoerige"), 0)
    for item in plan.items:
        if item.status != "ok":
            continue
        row = item.row
        korrektor_id = item.korrektor_id
        if korrektor_id is None:
            _insert(cur, names["korrektoren_table"], {
                "gf_uid": row["uid"] or None, "titel": row["titel"] or None, "nachname": row["nachname"],
                "vorname": row["vorname"], "geschlecht": row["geschlecht"], "email": row["email"] or None, "aktiv": 1})
            korrektor_id = cur.lastrowid
            if not korrektor_id:
                raise KorrektorenError("Keine Korrektor-ID erhalten")
            stats["korrektoren_neu"] += 1
        else:
            changes = {col: new for col, (_, new) in item.aenderungen.items()}
            if item.reaktiviert:
                changes.update({"aktiv": 1, "deaktiviert_am": None, "deaktiviert_grund": None})
            if changes:
                _update(cur, names["korrektoren_table"], "id", korrektor_id, changes)
                stats["korrektoren_aktualisiert"] += 1
        prefs = {**row["stufen"], "wuensche": row["wuensche"] or None, **row["erfahrung"],
                 "korrexp_text": row["erfahrung_text"] or None, "uebergabe": row["uebergabe"],
                 "uebergabe_sonst": row["uebergabe_sonst"] or None, "anmerkungen": row["anmerkungen"] or None}
        if item.prefs_vorhanden:
            _update(cur, names["korrektoren_prefs_table"], "korrektor_id", korrektor_id, prefs)
        else:
            _insert(cur, names["korrektoren_prefs_table"], {"korrektor_id": korrektor_id, **prefs})
        stats["prefs"] += 1
        if not item.angebot_vorhanden:
            _insert(cur, names["korrektoren_angebote_table"], {"mao_jahrgang_id": state.jahrgang_id, "korrektor_id": korrektor_id})
            stats["angebote"] += 1
        if row["uebergabe"] == "Post":
            address = {col: val or None for col, val in row["adresse"].items()}
            if item.adresse_vorhanden:
                _update(cur, names["korrektoren_adressen_table"], "korrektor_id", korrektor_id, address)
            else:
                _insert(cur, names["korrektoren_adressen_table"], {"korrektor_id": korrektor_id, **address})
            stats["adressen"] += 1
        for matrikel in dict.fromkeys(item.kinder_neu):
            _insert(cur, names["korrektoren_angehoerige_table"],
                    {"korrektor_id": korrektor_id, "hertzkind_matrikelnummer": matrikel, "beziehung": "Kind"})
            stats["angehoerige"] += 1
    return stats


def import_korrektoren(db: Any, config_dir: Path, tables: Mapping[str, str], *, dry_run: bool, workspace: Path,
                       interactive: bool = False, report: dict[str, Any] | None = None,
                       entries: Iterable[Mapping[str, Any]] | None = None, decisions_file: Path | None = None,
                       ask: Callable[[str], str] = input, out: Callable[[str], Any] = print) -> dict[str, int]:
    settings = load_korr_settings(config_dir / "gf-korrektoren.yaml")
    cnx = db.conn
    if cnx is None:
        raise KorrektorenError("Keine Datenbankverbindung")
    if entries is None:
        entries = gf_entries(load_gf_credentials(config_dir / "gf.config"), settings.form_id, settings.page_size, settings.timeout)
    entries = list(entries)
    rows, issues, inactive = parse_entries(entries, settings.fields)
    decisions_path = decisions_file or workspace / DECISIONS_FILE
    decisions = load_decisions(decisions_path)
    cur = cnx.cursor()
    lock_name = ""
    try:
        state = load_state(cur, tables)
        plan = plan_import(rows, state, decisions)
        if interactive and ask_decisions(plan, decisions, ask=ask, out=out):
            save_decisions(decisions_path, decisions)
            out(f"Entscheidungen gespeichert: {decisions_path.name}")
            plan = plan_import(rows, state, decisions)
        ok = [i for i in plan.items if i.status == "ok"]
        unclear = [i for i in plan.items if i.status == "unklar"]
        skipped = [i for i in plan.items if i.status == "uebersprungen"]
        stats = {"gf_eintraege": len(entries), "inaktive": inactive, "anderer_jahrgang": plan.anderes_jahr,
                 "jahr_unbekannt": plan.jahr_unbekannt, "doppelt": plan.doppelt, "neu": sum(1 for i in ok if i.korrektor_id is None),
                 "bekannt": sum(1 for i in ok if i.korrektor_id is not None), "unklar": len(unclear),
                 "uebersprungen": len(skipped), "kinder_offen": sum(len(i.kinder_offen) for i in plan.items),
                 "kinder_neu": sum(len(i.kinder_neu) for i in ok)}
        LOGGER.info("Korrektoren | GF-Einträge: %d | anderer Jahrgang: %d | doppelt: %d | neu: %d | bekannt: %d | unklar: %d "
                    "| übersprungen: %d | Hertz-Kinder offen: %d | Modus: %s", stats["gf_eintraege"], plan.anderes_jahr, plan.doppelt,
                    stats["neu"], stats["bekannt"], stats["unklar"], stats["uebersprungen"], stats["kinder_offen"],
                    "DRY-RUN" if dry_run else "APPLY")
        for item in plan.items:
            if item.status == "uebersprungen":
                LOGGER.warning("  GF %s übersprungen: %s", item.row["eintrag_id"], "; ".join(item.gruende))
            elif item.status == "unklar":
                LOGGER.warning("  GF %s unklar: %d mögliche Korrektor:innen (mit --interaktiv entscheiden)",
                               item.row["eintrag_id"], len(item.kandidaten))
        for text in [*issues, *plan.hinweise][:20]:
            LOGGER.warning("  %s", text)
        if report is not None:
            report["summary"] = stats
            report["eintraege"] = [{"gf_eintrag_id": i.row["eintrag_id"], "status": i.status, "treffer": i.treffer,
                                    "korrektor_id": i.korrektor_id, "aenderungen": sorted(i.aenderungen),
                                    "reaktiviert": i.reaktiviert, "prefs_neu": not i.prefs_vorhanden,
                                    "angebot_neu": not i.angebot_vorhanden, "gruende": i.gruende,
                                    "kandidaten_ids": [k["id"] for k in i.kandidaten],
                                    "kinder_offen": [k["index"] for k in i.kinder_offen]} for i in plan.items]
            report["hinweise"] = [*issues, *plan.hinweise]
        if dry_run or not ok:
            if report is not None:
                report["status"] = "ready" if dry_run else "applied"
            return stats
        table = _ident(tables, "korrektoren_table")
        lock_name = f"mao-korrektoren-{table}"
        cur.execute("SELECT GET_LOCK(%s, 10)", (lock_name,))
        if cur.fetchall()[0][0] != 1:
            raise KorrektorenError("Konkurrierender Import oder Sperr-Timeout")
        if cnx.in_transaction:
            cnx.rollback()
        cnx.start_transaction()
        try:
            cur.execute(f"SELECT COUNT(*) FROM `{table}`")
            before = int(cur.fetchall()[0][0])
            if before != len(state.korrektoren):
                raise KorrektorenError("Die Tabelle hat sich während der Prüfung geändert; bitte erneut ausführen")
            written = apply_plan(cur, tables, state, plan)
            cur.execute(f"SELECT COUNT(*) FROM `{table}`")
            if int(cur.fetchall()[0][0]) != before + written["korrektoren_neu"]:
                raise KorrektorenError("Zählabgleich fehlgeschlagen; nichts geschrieben")
            cnx.commit()
        except Exception:
            cnx.rollback()
            raise
        stats.update({f"geschrieben_{k}": v for k, v in written.items()})
        LOGGER.info("Korrektoren | geschrieben: %s", ", ".join(f"{k} {v}" for k, v in written.items()))
        if report is not None:
            report["status"] = "applied"
            report["geschrieben"] = written
        return stats
    finally:
        if lock_name:
            try:
                cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                cur.fetchall()
            except Exception:
                LOGGER.warning("Konnte Sperre nicht regulär freigeben (DB-Disconnect gibt sie frei)")
        cur.close()
