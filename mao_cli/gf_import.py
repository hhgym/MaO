"""Gravity Forms → MaO-Organisation: ausschließlich explizite, sichere Erstbefüllung.

Der Archiv-/TRUNCATE-Teil des früheren Monolithen gehört NICHT zu diesem Modul.
Alle lokalen SQL-Quellen und OAuth-Zugangsdaten stehen in MAO_CONFIG, nie im Paket.
"""
from __future__ import annotations

import configparser
import fnmatch
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

import yaml

from .config import ConfigurationError

LOGGER = logging.getLogger(__name__)
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ImportErrorGF(ValueError):
    """Unsichere oder ungültige Importdaten. Es wird nichts geschrieben."""


@dataclass(frozen=True)
class ImportSettings:
    form_id: int
    page_size: int
    timeout: int
    mandatory_classes: tuple[str, ...]
    fields: Mapping[str, str]
    queries: Mapping[str, str]


def load_import_settings(path: Path) -> ImportSettings:
    if not path.is_file():
        raise ConfigurationError(f"GF-Importkonfiguration fehlt: {path}; Vorlage gf-import.yaml.example verwenden")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"GF-Importkonfiguration unlesbar: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) - {"form_id", "page_size", "timeout", "fields", "queries", "mandatory_classes"}:
        raise ConfigurationError("gf-import.yaml: ungültige oder unbekannte Schlüssel")
    fields = raw.get("fields")
    queries = raw.get("queries")
    if not isinstance(fields, dict) or set(fields) != {"klasse", "kurs", "lehrkraft", "teilnehmende"}:
        raise ConfigurationError("gf-import.yaml: fields benötigt klasse, kurs, lehrkraft und teilnehmende")
    if not all(isinstance(x, str) and re.fullmatch(r"\d+", x) for x in fields.values()):
        raise ConfigurationError("gf-import.yaml: Gravity-Forms-Feldnummern müssen Zeichenketten sein")
    if not isinstance(queries, dict) or not {"students", "teachers", "courses"}.issubset(queries) or set(queries) - {"students", "teachers", "courses", "mandatory"}:
        raise ConfigurationError("gf-import.yaml: queries benötigt students, teachers, courses; optional mandatory")
    mand = raw.get("mandatory_classes")
    if not isinstance(mand, list) or not all(isinstance(x, str) and re.fullmatch(r"[0-9*-]+", x) for x in mand):
        raise ConfigurationError("gf-import.yaml: mandatory_classes muss eine Liste von Klassenmustern sein")
    if mand and not queries.get("mandatory"):
        raise ConfigurationError("gf-import.yaml: Pflichtteilnahmen benötigen queries.mandatory")
    for key, value in queries.items():
        if not value and key == "mandatory" and not mand:
            continue
        if not isinstance(value, str) or not value.strip() or "DEMO_" in value:
            raise ConfigurationError(f"gf-import.yaml: lokale SELECT-Abfrage '{key}' muss angepasst sein")
        if not re.match(r"^\s*SELECT\b", value, re.I) or ";" in value or re.search(r"\b(INTO\s+OUTFILE|FOR\s+UPDATE|LOCK\s+IN\s+SHARE\s+MODE)\b", value, re.I):
            raise ConfigurationError("gf-import.yaml: nur einfache lesende SELECT-Abfragen sind erlaubt")
    def positive(key: str, default: int, maximum: int):
        n = raw.get(key, default)
        if type(n) is not int or not 1 <= n <= maximum:
            raise ConfigurationError(f"gf-import.yaml: {key} außerhalb zulässiger Grenzen")
        return n
    return ImportSettings(positive("form_id", 16, 100000), positive("page_size", 200, 500),
                          positive("timeout", 60, 300), tuple(mand), fields, queries)


def load_gf_credentials(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ConfigurationError(f"Gravity-Forms-Zugangsdaten fehlen: {path}")
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(path, encoding="utf-8")
    except configparser.Error as exc:
        raise ConfigurationError(f"gf.config: {exc}") from exc
    if not cp.has_section("oauth1"):
        raise ConfigurationError("gf.config benötigt den Abschnitt [oauth1]")
    data = {k: cp.get("oauth1", k, fallback="").strip() for k in ("api_base", "consumer_key", "consumer_secret")}
    if not all(data.values()):
        raise ConfigurationError("gf.config benötigt api_base, consumer_key und consumer_secret")
    parts = urlsplit(data["api_base"])
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ConfigurationError("api_base muss eine HTTPS-Adresse ohne Benutzerinfo/Query sein")
    data["api_base"] = data["api_base"].rstrip("/")
    return data


def gf_entries(config: Mapping[str, str], form_id: int, page_size: int, timeout: int) -> Iterable[dict]:
    """OAuth1/HMAC-SHA1 und paginierte GF-REST-API wie im Originalskript."""
    try:
        from requests_oauthlib import OAuth1Session
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
    except ImportError as exc:
        raise ConfigurationError("requests-oauthlib fehlt; MaO mit aktuellen Abhängigkeiten installieren") from exc
    session = OAuth1Session(client_key=config["consumer_key"], client_secret=config["consumer_secret"], signature_method="HMAC-SHA1")
    retry = Retry(total=5, connect=5, read=5, status=5, backoff_factor=0.5,
                  status_forcelist=[429, 500, 502, 503, 504], allowed_methods=frozenset({"GET"}),
                  respect_retry_after_header=True)
    session.mount("https://", HTTPAdapter(max_retries=retry))
    url = f"{config['api_base']}/forms/{form_id}/entries"
    page = 1
    total = None
    try:
        while True:
            response = session.get(url, params={"paging[current_page]": page, "paging[page_size]": page_size},
                                   timeout=timeout, allow_redirects=False)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ImportErrorGF("GF-API lieferte keine gültige Ergebnisstruktur")
            if total is None and data.get("total_count") is not None:
                total = int(data["total_count"])
            items = data.get("entries", data.get("list", []))
            if not isinstance(items, list) or any(not isinstance(x, dict) for x in items):
                raise ImportErrorGF("Ungültige GF-Entries")
            yield from items
            if len(items) < page_size or (total is not None and page * page_size >= total):
                break
            page += 1
            if page > 1000:
                raise ImportErrorGF("Import abgebrochen: mehr als 1000 Seiten")
    finally:
        session.close()


def grade(klass: Any) -> int | None:
    m = re.match(r"^\s*(\d{1,2})(?:\b|\s*-)", str(klass or ""))
    return int(m.group(1)) if m else None


def gender(value: Any) -> str | None:
    v = str(value or "").strip().casefold()
    for normalized, variants in {
        "m": ("m", "male", "männlich", "mann", "junge"),
        "w": ("w", "weiblich", "female", "frau", "mädchen"),
        "d": ("d", "divers", "x", "nonbinary", "non-binär", "nb"),
    }.items():
        if v in variants:
            return normalized
    return None


def selected_students(entry: Mapping[str, Any], field: str) -> list[int]:
    """GF Checkbox-Felder 7.1, 7.2 ... bzw. ein Listenfeld 7."""
    values = [v for k, v in entry.items() if k.startswith(field + ".") and v]
    if not values and entry.get(field):
        raw = entry[field]
        values = raw if isinstance(raw, list) else re.split(r"[;,]\s*|\s+", str(raw))
    out: list[int] = []
    for v in values:
        match = re.search(r"\d+", str(v))
        if not match:
            raise ImportErrorGF(f"Ausgewählter GF-Checkboxwert ohne Matrikelnummer in Feld {field}")
        out.append(int(match.group()))
    return list(dict.fromkeys(out))


def _rows(cursor: Any, sql: str) -> list[dict[str, Any]]:
    cursor.execute(sql)
    columns = list(cursor.column_names or [])
    return [dict(zip(columns, values)) for values in cursor.fetchall()]


def _cache(rows: list[dict], required: set[str], source: str) -> list[dict]:
    if not rows:
        return rows
    if not required.issubset(rows[0]):
        raise ImportErrorGF(f"SQL-Quelle '{source}' benötigt Spalten: {', '.join(sorted(required))}")
    return rows


def _mapping(rows: list[dict], source: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in _cache(rows, {"id", "token"}, source):
        token = str(item["token"] or "").strip().casefold()
        if not token:
            continue
        if token in out and out[token] != int(item["id"]):
            raise ImportErrorGF(f"Mehrdeutige {source}-Zuordnung für {token!r}")
        out[token] = int(item["id"])
    return out


def _resolve(value: Any, mapping: dict[str, int], source: str) -> int | None:
    token = str(value or "").strip().casefold()
    if not token:
        return None
    if token not in mapping:
        raise ImportErrorGF(f"Keine eindeutige {source}-ID für {value!r}; SQL-Lookup in gf-import.yaml prüfen")
    return mapping[token]


def build_participants(entries: Iterable[dict], students: list[dict], mandatory: list[dict],
                       teachers: dict[str, int], courses: dict[str, int], options: ImportSettings) -> tuple[list[dict], dict[str, int]]:
    _cache(students, {"matrikelnummer", "vorname", "nachname", "geschlecht", "klasse"}, "students")
    student_map: dict[int, dict] = {}
    for item in students:
        if item.get("matrikelnummer") is None:
            continue
        matrikel = int(item["matrikelnummer"])
        if matrikel in student_map:
            raise ImportErrorGF(f"Doppelte Matrikelnummer {matrikel} im Schülerverzeichnis")
        student_map[matrikel] = item
    seen: dict[int, dict] = {}
    count_gf = 0
    skipped = 0
    field = options.fields
    for entry in entries:
        if entry.get("status") not in (None, "active"):
            skipped += 1
            continue
        count_gf += 1
        chosen = selected_students(entry, field["teilnehmende"])
        if not chosen:
            raise ImportErrorGF(f"Aktiver GF-Eintrag {entry.get('id', '(ohne ID)')} hat keine auslesbare Teilnehmerauswahl")
        for matrikel in chosen:
            if matrikel not in student_map:
                raise ImportErrorGF(f"Matrikelnummer {matrikel} aus Gravity Forms ist nicht im Schülerverzeichnis")
            if matrikel in seen:
                raise ImportErrorGF(f"Matrikelnummer {matrikel} wurde mehrfach angemeldet")
            s = student_map[matrikel]
            klass = str(entry.get(field["klasse"]) or s["klasse"] or "").strip()
            stufe = grade(klass)
            if stufe is None:
                raise ImportErrorGF(f"Keine Klassenstufe für Matrikelnummer {matrikel}")
            seen[matrikel] = {
                "matrikelnummer": matrikel, "vorname": s["vorname"], "nachname": s["nachname"],
                "geschlecht": gender(s["geschlecht"]), "klasse": klass, "stufe": stufe,
                "lehrkraft_id": _resolve(entry.get(field["lehrkraft"]), teachers, "Lehrkraft"),
                "kurs_id": _resolve(entry.get(field["kurs"]), courses, "Kurs") if stufe in (11, 12) else None,
            }
    mandatory_count = 0
    if options.mandatory_classes:
        for s in _cache(mandatory, {"matrikelnummer", "vorname", "nachname", "geschlecht", "klasse", "lehrkraft_token"}, "mandatory"):
            klass = str(s["klasse"] or "").strip()
            if not any(fnmatch.fnmatchcase(klass, pattern) for pattern in options.mandatory_classes):
                continue
            matrikel = int(s["matrikelnummer"])
            if matrikel in seen:
                continue
            if matrikel not in student_map:
                raise ImportErrorGF(f"Pflichtteilnehmer {matrikel} fehlt im Schülerverzeichnis")
            stufe = grade(klass)
            if stufe is None:
                raise ImportErrorGF(f"Keine Klassenstufe für Pflichtteilnehmer {matrikel}")
            seen[matrikel] = {
                "matrikelnummer": matrikel, "vorname": s["vorname"], "nachname": s["nachname"],
                "geschlecht": gender(s["geschlecht"]), "klasse": klass, "stufe": stufe,
                "lehrkraft_id": _resolve(s.get("lehrkraft_token"), teachers, "Lehrkraft"),
                "kurs_id": None,
            }
            mandatory_count += 1
    def sort_key(x: dict):
        st = x["stufe"]
        # Oberstufe zuerst, wie im bisherigen Skript.
        return (0 if st in (11, 12) else 1, st, x["kurs_id"] or -1,
                x["klasse"].casefold(), str(x["nachname"] or "").casefold(),
                str(x["vorname"] or "").casefold(), x["matrikelnummer"])
    result = sorted(seen.values(), key=sort_key)
    for i, row in enumerate(result, 1):
        row["tnr"] = i
    return result, {"gf_entries": count_gf, "inactive_entries": skipped, "mandatory_added": mandatory_count}


WRITE_COLUMNS = ("tnr", "matrikelnummer", "vorname", "nachname", "geschlecht", "klasse", "stufe", "lehrkraft_id", "kurs_id")


def import_participants(db: Any, config_dir: Path, target_table: str, *, dry_run: bool, no_mandatory: bool = False) -> dict[str, int]:
    """Erstbefüllung: alle Prüfungen VOR einem INSERT, nur leere Zieltabelle."""
    config = load_import_settings(config_dir / "gf-import.yaml")
    credentials = load_gf_credentials(config_dir / "gf.config")
    cnx = db.conn
    if cnx is None:
        raise ImportErrorGF("Keine Datenbankverbindung")
    cursor = cnx.cursor()
    try:
        # SELECT-Queries stehen in einer privaten Datei; kein untrusted SQL aus GF.
        students = _rows(cursor, config.queries["students"])
        mandatory = [] if no_mandatory or not config.mandatory_classes else _rows(cursor, config.queries["mandatory"])
        teachers = _mapping(_rows(cursor, config.queries["teachers"]), "Lehrkräfte")
        courses = _mapping(_rows(cursor, config.queries["courses"]), "Kurse")
        mapped, stats = build_participants(gf_entries(credentials, config.form_id, config.page_size, config.timeout),
                                            students, mandatory, teachers, courses,
                                            config if not no_mandatory else ImportSettings(config.form_id, config.page_size, config.timeout, (), config.fields, config.queries))
        if not mapped:
            raise ImportErrorGF("Keine Teilnehmenden ermittelt – Import aus Sicherheitsgründen abgebrochen")
        if len(mapped) != len(set(x["matrikelnummer"] for x in mapped)):
            raise ImportErrorGF("Doppelte Matrikelnummern")
        # Zieltabellenname ist privat in tables.ini, über geprüftes Mapping der CLI.
        missing_teacher = sum(x["lehrkraft_id"] is None for x in mapped)
        missing_course = sum(x["stufe"] in (11, 12) and x["kurs_id"] is None for x in mapped)
        if missing_teacher:
            LOGGER.warning("Teilnehmende ohne zugeordnete Lehrkraft: %d", missing_teacher)
        if missing_course:
            LOGGER.warning("Oberstufenteilnehmende ohne Kurszuordnung: %d", missing_course)
        inserted = persist_import(db, target_table, mapped, dry_run=dry_run)
        return {**stats, "participants": len(mapped), "inserted": inserted,
                "missing_teacher": missing_teacher, "missing_course": missing_course}
    finally:
        cursor.close()


def persist_import(db: Any, target_table: str, participants: list[dict], *, dry_run: bool) -> int:
    if not IDENT.fullmatch(target_table) or target_table.startswith("DEMO_"):
        raise ImportErrorGF("Ungültiger privater Zieltabellenname")
    cnx = db.conn
    if cnx is None:
        raise ImportErrorGF("Keine DB-Verbindung")
    cur = cnx.cursor()
    lock_name = f"mao-gf-import-{target_table}"
    lock_held = False
    try:
        if not dry_run:
            cur.execute("SELECT GET_LOCK(%s, 10)", (lock_name,))
            if cur.fetchone()[0] != 1:
                raise ImportErrorGF("Konkurrierender Import oder Sperr-Timeout")
            lock_held = True
        cur.execute(f"SHOW COLUMNS FROM `{target_table}`")
        columns = {row[0] for row in cur.fetchall()}
        if not set(WRITE_COLUMNS).issubset(columns):
            raise ImportErrorGF(f"Zieltabelle benötigt neue Spalten: {sorted(set(WRITE_COLUMNS) - columns)}")
        cur.execute(f"SELECT COUNT(*) FROM `{target_table}`")
        existing = int(cur.fetchone()[0])
        if existing:
            raise ImportErrorGF(f"{target_table} enthält bereits {existing} Zeilen; Import nur in leere Tabelle erlaubt")
        if dry_run:
            return 0
        # Transaktion mit Advisory Lock schützt vor parallelen MaO-Importaufrufen.
        if cnx.in_transaction:
            cnx.rollback()
        cnx.start_transaction()
        try:
            cur.execute(f"SELECT COUNT(*) FROM `{target_table}`")
            if int(cur.fetchone()[0]):
                raise ImportErrorGF("Zieltabelle wurde zwischenzeitlich befüllt")
            sql = (f"INSERT INTO `{target_table}` ({', '.join('`' + col + '`' for col in WRITE_COLUMNS)}) "
                   f"VALUES ({', '.join(['%s'] * len(WRITE_COLUMNS))})")
            cur.executemany(sql, [tuple(row.get(key) for key in WRITE_COLUMNS) for row in participants])
            cur.execute(f"SELECT COUNT(*) FROM `{target_table}`")
            if int(cur.fetchone()[0]) != len(participants):
                raise ImportErrorGF("Import-Count-Check fehlgeschlagen")
            cnx.commit()
            return len(participants)
        except Exception:
            cnx.rollback()
            raise
    finally:
        if lock_held:
            try:
                cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                cur.fetchone()
            except Exception:
                LOGGER.warning("Konnte Importsperre nicht regulär freigeben (DB-Disconnect gibt sie frei)")
        cur.close()
