"""Korrekturangebote (Gravity Forms) in die Korrektoren-Tabellen übernehmen.

Pro Eintrag des aktuellen Jahrgangs werden angelegt oder aktualisiert: Korrektor:in (MaO_Korrektoren), Präferenzen
(…_Prefs, vollständig aus dem Formular, weil bekannte Korrektor:innen es mit ihren Daten vorbefüllt bekommen), Angebot
des Jahrgangs (…_Angebote) und bei Übergabe per Post die Anschrift (…_Adressen).
Zuordnung: uid, dann E-Mail, dann Name. Sichere Treffer (uid, eindeutige E-Mail) werden automatisch übernommen,
alles andere wird im Menü (mao korrektoren) entschieden. Hertz-Kinder (…_Angehoerige) werden nur auf
ausdrückliche Entscheidung im Menü geschrieben. Berichte und Entscheidungsdatei enthalten nur IDs, keine
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
        if score >= 0.6 or (same_class and score >= 0.5):
            scored.append({"matrikelnummer": int(s["matrikelnummer"]), "vorname": s["vorname"], "nachname": s["nachname"],
                           "klasse": s.get("klasse"), "aktiv": s.get("aktiv"), "score": round(score, 2), "gleiche_klasse": same_class})
    scored.sort(key=lambda c: (not c["gleiche_klasse"], -c["score"], c["matrikelnummer"]) if c["score"] >= 0.85
                else (True, -c["score"], c["matrikelnummer"]))
    return scored[:limit]


PREFS_COLUMNS = (*STUFEN_COLUMNS, "wuensche", *ERFAHRUNG_COLUMNS, "korrexp_text", "uebergabe", "uebergabe_sonst", "anmerkungen")
ADDRESS_COLUMNS = ("strasse", "zusatz", "plz", "ort", "bundesland", "land")
INT_PREFS = (*STUFEN_COLUMNS, *ERFAHRUNG_COLUMNS)


@dataclass
class Item:
    row: dict[str, Any]
    status: str = "ok"                    # ok | unklar | uebersprungen
    korrektor_id: int | None = None       # None und status ok: neu anlegen
    treffer: str = ""                     # uid | email | entscheidung | neu
    kandidaten: list[dict[str, Any]] = field(default_factory=list)
    aenderungen: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    gruende: list[str] = field(default_factory=list)
    kinder_offen: list[dict[str, Any]] = field(default_factory=list)
    kinder_neu: list[int] = field(default_factory=list)
    angebot_vorhanden: bool = False
    prefs_vorhanden: bool = False
    prefs_geaendert: bool = True
    adresse_vorhanden: bool = False
    adresse_geaendert: bool = False
    reaktiviert: bool = False

    @property
    def unveraendert(self) -> bool:
        return (self.status == "ok" and self.korrektor_id is not None and not self.aenderungen and not self.reaktiviert
                and not self.prefs_geaendert and self.angebot_vorhanden and not self.adresse_geaendert and not self.kinder_neu)


@dataclass
class State:
    jahrgang_id: int
    jahrgang_nr: int
    schuljahr_id: int
    korrektoren: list[dict[str, Any]]
    prefs: dict[int, dict[str, Any]]
    angebote: set[tuple[int, int]]
    adressen: dict[int, dict[str, Any]]
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


def prefs_values(row: Mapping[str, Any]) -> dict[str, Any]:
    return {**row["stufen"], "wuensche": row["wuensche"] or None, **row["erfahrung"],
            "korrexp_text": row["erfahrung_text"] or None, "uebergabe": row["uebergabe"],
            "uebergabe_sonst": row["uebergabe_sonst"] or None, "anmerkungen": row["anmerkungen"] or None}


def _same(new: Mapping[str, Any], old: Mapping[str, Any] | None, ints: Iterable[str] = ()) -> bool:
    if old is None:
        return False
    ints = set(ints)
    for column, value in new.items():
        before = old.get(column)
        if column in ints:
            if int(value or 0) != int(before or 0):
                return False
        elif (value or None) != (clean(before) or None):
            return False
    return True


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
        item.gruende.extend(_length_problems(row, state.limits))
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
        if isinstance(decision, int) and decision in by_id:
            found, item.treffer = by_id[decision], "entscheidung"
        elif row["uid"] and row["uid"] in by_uid:
            # bekannte uid aus dem Link oder die beim Anlegen gespeicherte Formular-ID (Wiederholung ist dadurch sicher)
            found, item.treffer = by_uid[row["uid"]], "uid"
        elif decision == "neu":
            item.treffer = "neu"
        else:
            mail = by_mail.get(row["email"].casefold(), []) if row["email"] else []
            named = by_name.get((fold(row["vorname"]), fold(row["nachname"])), [])
            if len(mail) == 1:
                found, item.treffer = mail[0], "email"
            elif len(mail) > 1 or named:
                item.status = "unklar"
                item.kandidaten = [{"id": k["id"], "vorname": k["vorname"], "nachname": k["nachname"],
                                    "email": k.get("email"), "gf_uid": bool(k.get("gf_uid"))} for k in (mail or named)]
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
            old_prefs = state.prefs.get(found["id"])
            item.prefs_vorhanden = old_prefs is not None
            item.prefs_geaendert = not _same(prefs_values(row), old_prefs, INT_PREFS)
            item.angebot_vorhanden = (state.jahrgang_id, found["id"]) in state.angebote
            old_address = state.adressen.get(found["id"])
            item.adresse_vorhanden = old_address is not None
        if row["uebergabe"] == "Post" and item.status == "ok":
            item.adresse_geaendert = not _same(row["adresse"], state.adressen.get(item.korrektor_id) if found else None)
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
    # Mehrere Einträge, die bei derselben Korrektor:in landen: nur der neueste gilt
    latest: dict[int, Item] = {}
    for item in plan.items:
        if item.status != "ok" or item.korrektor_id is None:
            continue
        other = latest.get(item.korrektor_id)
        if other is None:
            latest[item.korrektor_id] = item
            continue
        newer, older = sorted((item, other), key=lambda i: (i.row["erstellt"], i.row["eintrag_id"]), reverse=True)
        older.status = "uebersprungen"
        older.gruende.append(f"weiterer Eintrag derselben Korrektor:in (GF {newer.row['eintrag_id']} gilt)")
        older.kinder_offen, older.kinder_neu = [], []
        latest[item.korrektor_id] = newer
        plan.doppelt += 1
    return plan


def plan_stats(plan: Plan) -> dict[str, int]:
    ok = [i for i in plan.items if i.status == "ok"]
    return {"eintraege": len(plan.items), "anderer_jahrgang": plan.anderes_jahr, "jahr_unbekannt": plan.jahr_unbekannt,
            "doppelt": plan.doppelt, "neu": sum(1 for i in ok if i.korrektor_id is None),
            "bekannt": sum(1 for i in ok if i.korrektor_id is not None),
            "unveraendert": sum(1 for i in ok if i.unveraendert),
            "unklar": sum(1 for i in plan.items if i.status == "unklar"),
            "uebersprungen": sum(1 for i in plan.items if i.status == "uebersprungen"),
            "kinder_offen": sum(len(i.kinder_offen) for i in plan.items if i.status != "uebersprungen"),
            "kinder_neu": sum(len(i.kinder_neu) for i in ok)}


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
    path.write_text("# Entscheidungen von mao korrektoren (nur IDs, keine Namen)\n"
                    + yaml.safe_dump({k: v for k, v in decisions.items() if v}, allow_unicode=True, sort_keys=True),
                    encoding="utf-8")


def _name(row: Mapping[str, Any]) -> str:
    return clean(f"{row['titel']} {row['vorname']} {row['nachname']}")


def resolve_unclear(plan: Plan, decisions: dict[str, Any], *, ask: Callable[[str], str] = input,
                    out: Callable[[str], Any] = print) -> bool:
    """Unklare Zuordnungen abfragen. Rückgabe: True, wenn sich Entscheidungen geändert haben."""
    items = [i for i in plan.items if i.status == "unklar"]
    if not items:
        out("Keine unklaren Korrektor:innen.")
        return False
    changed = False
    for item in items:
        row = item.row
        key = str(row["eintrag_id"])
        out(f"\nGF-Eintrag {key}: {_name(row)} <{row['email'] or 'ohne E-Mail'}> – welche bekannte Korrektor:in?")
        for number, k in enumerate(item.kandidaten, 1):
            out(f"  {number}) ID {k['id']}: {k['vorname']} {k['nachname']} <{k.get('email') or 'ohne E-Mail'}>"
                f"{'' if k['gf_uid'] else ' (ohne uid)'}")
        while True:
            answer = ask("Nummer, n = neu anlegen, s = überspringen, Enter = später, z = zurück zum Menü: ").strip().lower()
            if answer == "z":
                return changed
            if answer == "":
                break
            if answer == "n":
                decisions.setdefault(key, {})["korrektor"] = "neu"
            elif answer == "s":
                decisions.setdefault(key, {})["korrektor"] = "skip"
            elif answer.isdigit() and 1 <= int(answer) <= len(item.kandidaten):
                decisions.setdefault(key, {})["korrektor"] = item.kandidaten[int(answer) - 1]["id"]
            else:
                out("Bitte eine Nummer, n, s, z oder Enter eingeben.")
                continue
            changed = True
            break
    return changed


def assign_children(plan: Plan, decisions: dict[str, Any], *, ask: Callable[[str], str] = input,
                    out: Callable[[str], Any] = print) -> bool:
    """Offene Hertz-Kinder abfragen. Rückgabe: True, wenn sich Entscheidungen geändert haben."""
    items = [i for i in plan.items if i.kinder_offen and i.status != "uebersprungen"]
    if not items:
        out("Keine offenen Hertz-Kinder.")
        return False
    changed = False
    for item in items:
        key = str(item.row["eintrag_id"])
        for kid in item.kinder_offen:
            out(f"\nGF-Eintrag {key} ({_name(item.row)}), Hertz-Kind {kid['index'] + 1}: "
                f"{kid['name'] or '?'} (Klasse {kid['klasse'] or '–'})")
            for number, c in enumerate(kid["kandidaten"], 1):
                out(f"  {number}) {c['vorname']} {c['nachname']} | Klasse {c['klasse'] or '–'}"
                    f"{' (passt)' if c['gleiche_klasse'] else ''} | {'aktiv' if c['aktiv'] in (1, True) else 'inaktiv'} | "
                    f"Matrikel {c['matrikelnummer']} | Namensähnlichkeit {c['score']:.2f}")
            if not kid["kandidaten"]:
                out("  (keine wahrscheinlichen Schüler:innen gefunden)")
            while True:
                answer = ask("Nummer, Matrikelnummer, k = kein Kind, Enter = später, z = zurück zum Menü: ").strip().lower()
                if answer == "z":
                    return changed
                if answer == "":
                    break
                kinder = decisions.setdefault(key, {}).setdefault("kinder", {})
                if answer == "k":
                    kinder[str(kid["index"])] = "keins"
                elif answer.isdigit() and 1 <= int(answer) <= len(kid["kandidaten"]):
                    kinder[str(kid["index"])] = kid["kandidaten"][int(answer) - 1]["matrikelnummer"]
                elif answer.isdigit() and int(answer) > 100:
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
    cur.execute(f"SELECT korrektor_id, {', '.join('`' + c + '`' for c in PREFS_COLUMNS)} FROM `{names['korrektoren_prefs_table']}`")
    prefs = {int(r[0]): dict(zip(PREFS_COLUMNS, r[1:])) for r in cur.fetchall()}
    cur.execute(f"SELECT mao_jahrgang_id, korrektor_id FROM `{names['korrektoren_angebote_table']}`")
    angebote = {(int(a), int(b)) for a, b in cur.fetchall()}
    cur.execute(f"SELECT korrektor_id, {', '.join('`' + c + '`' for c in ADDRESS_COLUMNS)} FROM `{names['korrektoren_adressen_table']}`")
    adressen = {int(r[0]): dict(zip(ADDRESS_COLUMNS, r[1:])) for r in cur.fetchall()}
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


def apply_plan(cur: Any, tables: Mapping[str, str], state: State, plan: Plan) -> tuple[dict[str, int], dict[int, int]]:
    """Plan schreiben (ohne Transaktionssteuerung). Rückgabe: Zähler und neue Korrektor-IDs je GF-Eintrag."""
    names = {key: _ident(tables, key) for key in TABLE_KEYS}
    stats = dict.fromkeys(("korrektoren_neu", "korrektoren_aktualisiert", "prefs", "angebote", "adressen", "angehoerige"), 0)
    created: dict[int, int] = {}
    for item in plan.items:
        if item.status != "ok" or item.unveraendert:
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
            created[row["eintrag_id"]] = int(korrektor_id)
            stats["korrektoren_neu"] += 1
        else:
            changes = {col: new for col, (_, new) in item.aenderungen.items()}
            if item.reaktiviert:
                changes.update({"aktiv": 1, "deaktiviert_am": None, "deaktiviert_grund": None})
            if changes:
                _update(cur, names["korrektoren_table"], "id", korrektor_id, changes)
                stats["korrektoren_aktualisiert"] += 1
        if item.prefs_geaendert:
            if item.prefs_vorhanden:
                _update(cur, names["korrektoren_prefs_table"], "korrektor_id", korrektor_id, prefs_values(row))
            else:
                _insert(cur, names["korrektoren_prefs_table"], {"korrektor_id": korrektor_id, **prefs_values(row)})
            stats["prefs"] += 1
        if not item.angebot_vorhanden:
            _insert(cur, names["korrektoren_angebote_table"], {"mao_jahrgang_id": state.jahrgang_id, "korrektor_id": korrektor_id})
            stats["angebote"] += 1
        if row["uebergabe"] == "Post" and item.adresse_geaendert:
            address = {col: row["adresse"][col] or None for col in ADDRESS_COLUMNS}
            if item.adresse_vorhanden:
                _update(cur, names["korrektoren_adressen_table"], "korrektor_id", korrektor_id, address)
            else:
                _insert(cur, names["korrektoren_adressen_table"], {"korrektor_id": korrektor_id, **address})
            stats["adressen"] += 1
        for matrikel in dict.fromkeys(item.kinder_neu):
            _insert(cur, names["korrektoren_angehoerige_table"],
                    {"korrektor_id": korrektor_id, "hertzkind_matrikelnummer": matrikel, "beziehung": "Kind"})
            stats["angehoerige"] += 1
    return stats, created


# ------------------------------------------------------------------ Sitzung

@dataclass
class Session:
    db: Any
    tables: Mapping[str, str]
    fetch: Callable[[], list[Mapping[str, Any]]]
    fields: Mapping[str, str]
    decisions_path: Path
    decisions: dict[str, Any] = field(default_factory=dict)
    entries: int = 0
    inactive: int = 0
    issues: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    state: State | None = None
    plan: Plan = field(default_factory=Plan)

    def load_entries(self) -> None:
        entries = list(self.fetch())
        self.rows, self.issues, self.inactive = parse_entries(entries, self.fields)
        self.entries = len(entries)

    def reload(self) -> None:
        cur = self.db.conn.cursor()
        try:
            self.state = load_state(cur, self.tables)
        finally:
            cur.close()
        self.replan()

    def replan(self) -> None:
        assert self.state is not None
        self.plan = plan_import(self.rows, self.state, self.decisions)

    def save(self) -> None:
        save_decisions(self.decisions_path, self.decisions)

    def stats(self) -> dict[str, int]:
        return {"gf_eintraege": self.entries, "inaktive": self.inactive, **plan_stats(self.plan)}

    def write(self) -> dict[str, int]:
        """Plan in einer Transaktion schreiben; neue Korrektor-IDs als Entscheidung merken, danach neu laden."""
        assert self.state is not None
        cnx = self.db.conn
        cur = cnx.cursor()
        table = _ident(self.tables, "korrektoren_table")
        lock_name = f"mao-korrektoren-{table}"
        locked = False
        try:
            cur.execute("SELECT GET_LOCK(%s, 10)", (lock_name,))
            if cur.fetchall()[0][0] != 1:
                raise KorrektorenError("Konkurrierender Import oder Sperr-Timeout")
            locked = True
            if cnx.in_transaction:
                cnx.rollback()
            cnx.start_transaction()
            try:
                cur.execute(f"SELECT COUNT(*) FROM `{table}`")
                before = int(cur.fetchall()[0][0])
                if before != len(self.state.korrektoren):
                    raise KorrektorenError("Die Tabelle hat sich während der Prüfung geändert; bitte erneut laden")
                written, created = apply_plan(cur, self.tables, self.state, self.plan)
                cur.execute(f"SELECT COUNT(*) FROM `{table}`")
                if int(cur.fetchall()[0][0]) != before + written["korrektoren_neu"]:
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
        if created:
            for entry_id, korrektor_id in created.items():
                self.decisions.setdefault(str(entry_id), {})["korrektor"] = korrektor_id
            self.save()
        self.reload()
        return written


def open_session(db: Any, config_dir: Path, tables: Mapping[str, str], workspace: Path, *,
                 entries: Iterable[Mapping[str, Any]] | None = None, decisions_file: Path | None = None) -> Session:
    settings = load_korr_settings(config_dir / "gf-korrektoren.yaml")
    if db.conn is None:
        raise KorrektorenError("Keine Datenbankverbindung")
    for key in TABLE_KEYS:
        _ident(tables, key)
    if entries is None:
        credentials = load_gf_credentials(config_dir / "gf.config")

        def fetch() -> list[Mapping[str, Any]]:
            return list(gf_entries(credentials, settings.form_id, settings.page_size, settings.timeout))
    else:
        fixed = list(entries)

        def fetch() -> list[Mapping[str, Any]]:
            return fixed
    path = decisions_file or workspace / DECISIONS_FILE
    session = Session(db, tables, fetch, settings.fields, path, load_decisions(path))
    session.load_entries()
    session.reload()
    return session


# ------------------------------------------------------------------ Ausgabe

def _item_line(item: Item) -> str:
    row = item.row
    mark = {"ok": "=" if item.unveraendert else ("+" if item.korrektor_id is None else "~"), "unklar": "?",
            "uebersprungen": "!"}[item.status]
    target = (f"ID {item.korrektor_id}" if item.korrektor_id else "neu") if item.status == "ok" else item.status
    extra = []
    if item.aenderungen:
        extra.append("ändert " + ", ".join(sorted(item.aenderungen)))
    if item.reaktiviert:
        extra.append("wieder aktiv")
    if item.status == "ok" and not item.angebot_vorhanden:
        extra.append("Angebot neu")
    if item.kinder_neu:
        extra.append(f"Hertz-Kinder neu: {len(item.kinder_neu)}")
    if item.kinder_offen and item.status != "uebersprungen":
        extra.append(f"Hertz-Kinder offen: {len(item.kinder_offen)}")
    if item.gruende:
        extra.append("; ".join(item.gruende))
    return f"{mark} GF {row['eintrag_id']:>5} | {_name(row)[:32]:<32} | {item.treffer or '-':<12} | {target:<10} | " + " · ".join(extra)


def change_lines(plan: Plan) -> list[str]:
    lines = []
    for item in plan.items:
        if item.status != "ok" or item.unveraendert:
            continue
        row = item.row
        parts = []
        if item.korrektor_id is None:
            parts.append("neu anlegen")
        else:
            parts += [f"{col}: {old or '–'} → {new}" for col, (old, new) in sorted(item.aenderungen.items()) if col != "gf_uid"]
            if "gf_uid" in item.aenderungen:
                parts.append("uid ergänzen")
            if item.reaktiviert:
                parts.append("wieder aktiv")
        if item.prefs_geaendert:
            parts.append("Präferenzen" + (" neu" if not item.prefs_vorhanden else " aktualisieren"))
        if not item.angebot_vorhanden:
            parts.append("Angebot dieses Jahrgangs")
        if row["uebergabe"] == "Post" and item.adresse_geaendert:
            parts.append("Anschrift")
        if item.kinder_neu:
            parts.append("Hertz-Kinder: " + ", ".join(map(str, item.kinder_neu)))
        lines.append(f"GF {row['eintrag_id']} {_name(row)}" + (f" (ID {item.korrektor_id})" if item.korrektor_id else "")
                     + ": " + "; ".join(parts))
    return lines


def summary_line(session: Session, *, apply: bool) -> str:
    s = session.stats()
    nr = session.state.jahrgang_nr if session.state else "?"
    return (f"Korrektoren | Jahrgang {nr} | Einträge {s['eintraege']} (neu {s['neu']}, bekannt {s['bekannt']}, "
            f"davon unverändert {s['unveraendert']}, unklar {s['unklar']}, übersprungen {s['uebersprungen']}) | "
            f"Hertz-Kinder offen {s['kinder_offen']} | {'APPLY' if apply else 'DRY-RUN'}")


MENU = """
  1) Einträge anzeigen
  2) Unklare Korrektor:innen klären ({unklar})
  3) Hertz-Kinder zuordnen ({kinder_offen})
  4) Entscheidung eines Eintrags zurücksetzen
  5) Änderungen anzeigen und in die Datenbank schreiben
  6) Formular und Datenbank neu laden
  q) Beenden"""


def run_menu(session: Session, *, apply: bool, ask: Callable[[str], str] = input,
             out: Callable[[str], Any] = print) -> dict[str, int]:
    written_total: dict[str, int] = {}
    while True:
        out("\n" + summary_line(session, apply=apply))
        out(MENU.format(**session.stats()))
        try:
            choice = ask("Auswahl: ").strip().lower()
        except EOFError:
            choice = "q"
        try:
            if choice == "1":
                for item in session.plan.items:
                    out(_item_line(item))
                if not session.plan.items:
                    out("Keine Einträge für diesen Jahrgang.")
                for text in [*session.issues, *session.plan.hinweise]:
                    out(f"  Hinweis: {text}")
                out(f"  (+ neu, ~ Änderung, = unverändert, ? unklar, ! übersprungen; anderer Jahrgang: "
                    f"{session.plan.anderes_jahr}, doppelt: {session.plan.doppelt})")
            elif choice in ("2", "3"):
                step = resolve_unclear if choice == "2" else assign_children
                if step(session.plan, session.decisions, ask=ask, out=out):
                    session.save()
                    out(f"Entscheidungen gespeichert: {session.decisions_path.name}")
                    session.replan()
            elif choice == "4":
                key = ask("GF-Eintrag (Nummer): ").strip()
                if key in session.decisions:
                    del session.decisions[key]
                    session.save()
                    session.replan()
                    out(f"Entscheidung für GF {key} zurückgesetzt.")
                else:
                    out("Für diesen Eintrag gibt es keine Entscheidung.")
            elif choice == "5":
                lines = change_lines(session.plan)
                if not lines:
                    out("Nichts zu schreiben.")
                    continue
                for line in lines:
                    out("  " + line)
                if not apply:
                    out("DRY-RUN: nichts geschrieben. Zum Schreiben mit: mao korrektoren --apply "
                        "(die Entscheidungen bleiben gespeichert)")
                elif ask("In die Datenbank schreiben? [j/N] ").strip().lower() in ("j", "ja"):
                    written = session.write()
                    for key, value in written.items():
                        written_total[key] = written_total.get(key, 0) + value
                    out("Geschrieben: " + ", ".join(f"{k} {v}" for k, v in written.items()))
            elif choice == "6":
                session.load_entries()
                session.reload()
                out("Neu geladen.")
            elif choice in ("q", "quit", "exit"):
                return written_total
            elif choice:
                out("Bitte 1 bis 6 oder q eingeben.")
        except KorrektorenError as exc:
            out(f"Nicht möglich: {exc}")


def fill_report(report: dict[str, Any], session: Session) -> None:
    """Bericht nur mit IDs und Zählern (keine Namen, E-Mail-Adressen oder Anschriften)."""
    report["summary"] = session.stats()
    report["eintraege"] = [{"gf_eintrag_id": i.row["eintrag_id"], "status": i.status, "treffer": i.treffer,
                            "korrektor_id": i.korrektor_id, "aenderungen": sorted(i.aenderungen), "unveraendert": i.unveraendert,
                            "reaktiviert": i.reaktiviert, "prefs_geaendert": i.prefs_geaendert,
                            "angebot_neu": i.status == "ok" and not i.angebot_vorhanden, "gruende": i.gruende,
                            "kandidaten_ids": [k["id"] for k in i.kandidaten],
                            "kinder_offen": [k["index"] for k in i.kinder_offen]} for i in session.plan.items]
    report["hinweise"] = [*session.issues, *session.plan.hinweise]


def run_korrektoren(db: Any, config_dir: Path, tables: Mapping[str, str], *, apply: bool, workspace: Path,
                    menu: bool = True, report: dict[str, Any] | None = None,
                    entries: Iterable[Mapping[str, Any]] | None = None, decisions_file: Path | None = None,
                    ask: Callable[[str], str] = input, out: Callable[[str], Any] = print) -> dict[str, int]:
    """Menü (Standard) oder ohne Menü: Trockenlauf bzw. mit apply die sicheren Fälle und gespeicherten Entscheidungen schreiben."""
    session = open_session(db, config_dir, tables, workspace, entries=entries, decisions_file=decisions_file)
    written: dict[str, int] = {}
    if menu:
        written = run_menu(session, apply=apply, ask=ask, out=out)
    else:
        LOGGER.info("%s", summary_line(session, apply=apply))
        for item in session.plan.items:
            if item.status == "uebersprungen":
                LOGGER.warning("  GF %s übersprungen: %s", item.row["eintrag_id"], "; ".join(item.gruende))
            elif item.status == "unklar":
                LOGGER.warning("  GF %s unklar: %d mögliche Korrektor:innen (im Menü klären)", item.row["eintrag_id"],
                               len(item.kandidaten))
        for text in [*session.issues, *session.plan.hinweise][:20]:
            LOGGER.warning("  %s", text)
        if apply and change_lines(session.plan):
            written = session.write()
            LOGGER.info("Korrektoren | geschrieben: %s", ", ".join(f"{k} {v}" for k, v in written.items()))
    if report is not None:
        fill_report(report, session)
        report["geschrieben"] = written
        report["status"] = "applied" if apply and written else "ready"
    stats = session.stats()
    stats.update({f"geschrieben_{k}": v for k, v in written.items()})
    return stats
