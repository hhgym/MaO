"""Gravity Forms → MaO-Organisation: ausschließlich explizite, sichere Erstbefüllung.

Der Archiv-/TRUNCATE-Teil des früheren Monolithen gehört NICHT zu diesem Modul.
Alle lokalen SQL-Quellen und OAuth-Zugangsdaten stehen in MAO_CONFIG, nie im Paket.
"""
from __future__ import annotations

import configparser
import csv
import dataclasses
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, overload
from urllib.parse import urlsplit

import yaml

from .config import ConfigurationError, load_tables, resolve_sql_table_tokens

LOGGER = logging.getLogger(__name__)
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Spalten der Organisationstabelle, deren Fremdschlüssel vor dem Import geprüft werden.
FK_COLUMNS = ("kurs_id", "lehrkraft_id")


class ImportErrorGF(ValueError):
    """Unsichere oder ungültige Importdaten. Es wird nichts geschrieben."""


@dataclass(frozen=True)
class ImportSettings:
    form_id: int
    page_size: int
    timeout: int
    mandatory_classes: tuple[str, ...]
    optional_mandatory_classes: tuple[str, ...]
    fields: Mapping[str, str]
    queries: Mapping[str, str]
    # Spalte -> Tabelle, auf die ihr Fremdschlüssel zeigen muss (optional).
    foreign_keys: Mapping[str, str] = dataclasses.field(default_factory=dict)


def load_import_settings(path: Path) -> ImportSettings:
    if not path.is_file():
        raise ConfigurationError(f"GF-Importkonfiguration fehlt: {path}; bitte mao config init ausführen")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"GF-Importkonfiguration unlesbar: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) - {"form_id", "page_size", "timeout", "fields", "queries", "mandatory_classes",
                                                "optional_mandatory_classes", "foreign_keys"}:
        raise ConfigurationError("gf-import.yaml: ungültige oder unbekannte Schlüssel")
    fields = raw.get("fields")
    queries = raw.get("queries")
    if not isinstance(fields, dict) or (not {"klasse", "kurs", "lehrkraft", "teilnehmende"}.issubset(fields) or set(fields) - {"klasse", "kurs", "lehrkraft", "teilnehmende", "leermeldung"}):
        raise ConfigurationError("gf-import.yaml: fields benötigt klasse, kurs, lehrkraft und teilnehmende")
    if not all(isinstance(x, str) and re.fullmatch(r"\d+", x) for x in fields.values()):
        raise ConfigurationError("gf-import.yaml: Gravity-Forms-Feldnummern müssen Zeichenketten sein")
    if not isinstance(queries, dict) or not {"students", "teachers", "courses"}.issubset(queries) or set(queries) - {"students", "teachers", "courses", "mandatory"}:
        raise ConfigurationError("gf-import.yaml: queries benötigt students, teachers, courses; optional mandatory")
    mand = raw.get("mandatory_classes", [])
    opt_mand = raw.get("optional_mandatory_classes", [])
    for key, value in (("mandatory_classes", mand), ("optional_mandatory_classes", opt_mand)):
        if not isinstance(value, list) or not all(isinstance(x, str) and re.fullmatch(r"[0-9]{1,2}-[0-9]{1,2}", x) for x in value):
            raise ConfigurationError(f"gf-import.yaml: {key} benötigt explizite Klassenbezeichnungen")
        if len(value) != len(set(value)):
            raise ConfigurationError(f"gf-import.yaml: doppelte Klasse in {key}")
    if set(mand) & set(opt_mand):
        raise ConfigurationError("gf-import.yaml: Pflicht- und optionale Klassen überlappen")
    if (mand or opt_mand) and not queries.get("mandatory"):
        raise ConfigurationError("gf-import.yaml: Pflichtteilnahmen benötigen queries.mandatory (aktuelle Mathematik-Lerngruppen)")
    fk_raw = raw.get("foreign_keys") or {}
    if (not isinstance(fk_raw, dict) or set(fk_raw) - set(FK_COLUMNS)
            or not all(isinstance(value, str) and value.strip() for value in fk_raw.values())):
        raise ConfigurationError("gf-import.yaml: foreign_keys erlaubt nur kurs_id und lehrkraft_id mit je einem Tabellennamen")
    # Die SQL-Quellen referenzieren dieselbe zentrale tables.ini wie die Pipeline.
    # Namen aus Gravity Forms werden hier niemals als SQL-Identifier eingesetzt.
    needs_tables = any(isinstance(value, str) and "{{" in value
                       for value in (*queries.values(), *fk_raw.values()))
    tables = load_tables(path.parent / "tables.ini") if needs_tables else {}
    if "students_table" not in tables and "students_view" in tables:
        tables["students_table"] = tables["students_view"]
    resolved_queries: dict[str, str] = {}
    for key, value in queries.items():
        if not value and key == "mandatory" and not (mand or opt_mand):
            resolved_queries[key] = ""
            continue
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(f"gf-import.yaml: SELECT-Abfrage '{key}' fehlt")
        value = value.replace("{{table.students_view}}", "{{table.students_table}}")
        value = resolve_sql_table_tokens(value, tables, label=f"gf-import.yaml/queries.{key}")
        if "DEMO_" in value:
            raise ConfigurationError(f"gf-import.yaml: Tabellenkennung für '{key}' in tables.ini noch DEMO_ – bitte anpassen")
        if not re.match(r"^\s*SELECT\b", value, re.I) or ";" in value or re.search(r"\b(INTO\s+OUTFILE|FOR\s+UPDATE|LOCK\s+IN\s+SHARE\s+MODE)\b", value, re.I):
            raise ConfigurationError("gf-import.yaml: nur einfache lesende SELECT-Abfragen sind erlaubt")
        resolved_queries[key] = value
    foreign_keys: dict[str, str] = {}
    for column, value in fk_raw.items():
        table = resolve_sql_table_tokens(value.strip(), tables, label=f"gf-import.yaml/foreign_keys.{column}")
        if not IDENT.fullmatch(table):
            raise ConfigurationError(f"gf-import.yaml: foreign_keys.{column} muss genau einen Tabellennamen enthalten")
        # Noch nicht eingerichtete DEMO-Namen bedeuten: Ziel nur anzeigen, nicht erzwingen.
        if not table.startswith("DEMO_"):
            foreign_keys[column] = table
    def positive(key: str, default: int, maximum: int) -> int:
        n = raw.get(key, default)
        if type(n) is not int or not 1 <= n <= maximum:
            raise ConfigurationError(f"gf-import.yaml: {key} außerhalb zulässiger Grenzen")
        return n
    return ImportSettings(positive("form_id", 16, 100000), positive("page_size", 200, 500),
                          positive("timeout", 60, 300), tuple(mand), tuple(opt_mand), fields, resolved_queries,
                          foreign_keys)


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
    received = 0
    seen_ids: set[str] = set()
    try:
        while True:
            try:
                response = session.get(url, params={"paging[current_page]": page, "paging[page_size]": page_size},
                                       timeout=timeout, allow_redirects=False)
                response.raise_for_status()
                data = response.json()
            except Exception as exc:
                from requests.exceptions import RequestException
                if isinstance(exc, (RequestException, ValueError)):
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    detail = f"HTTP {status}" if status is not None else type(exc).__name__
                    raise ImportErrorGF(f"GF-API-Zugriff fehlgeschlagen ({detail}); API-Adresse und Berechtigungen prüfen") from None
                raise
            if not isinstance(data, dict) or not ("entries" in data or "list" in data):
                raise ImportErrorGF("GF-API lieferte keine gültige Ergebnisstruktur")
            if total is None and data.get("total_count") is not None:
                total = int(data["total_count"])
            items = data.get("entries", data.get("list", []))
            if not isinstance(items, list) or any(not isinstance(x, dict) for x in items):
                raise ImportErrorGF("Ungültige GF-Entries")
            for item in items:
                # Ändern sich Meldungen während des seitenweisen Abrufs, verschieben
                # sich die Seiten: Einträge kämen doppelt oder fehlten.
                entry_id = item.get("id")
                if entry_id is not None:
                    if str(entry_id) in seen_ids:
                        raise ImportErrorGF(f"GF-Eintrag {str(entry_id)[:64]} wurde doppelt geliefert; "
                                            "Meldungen haben sich während des Abrufs geändert, bitte erneut ausführen")
                    seen_ids.add(str(entry_id))
                received += 1
                yield item
            if len(items) < page_size or (total is not None and page * page_size >= total):
                break
            page += 1
            if page > 1000:
                raise ImportErrorGF("Import abgebrochen: mehr als 1000 Seiten")
        if total is not None and received != total:
            raise ImportErrorGF(f"Unvollständiger GF-Abruf: {received} von {total} Einträgen erhalten; "
                                "bitte erneut ausführen. Tritt das wiederholt auf, page_size in "
                                "gf-import.yaml verkleinern (die API liefert evtl. weniger pro Seite)")
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


def is_empty_report(entry: Mapping[str, Any], field: str = "10") -> bool:
    """GF-Checkbox 10 oder 10.1, deren aktivierter Wert 'Leermeldung' ist."""
    for key, value in entry.items():
        if key != field and not re.fullmatch(re.escape(field) + r"\.\d+", key):
            continue
        values = value if isinstance(value, list) else [value]
        if any(isinstance(v, str) and v.strip().casefold() == "leermeldung" for v in values):
            return True
    return False


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


@overload
def _valid_id(value: Any, source: str, *, optional: Literal[False] = False) -> int: ...
@overload
def _valid_id(value: Any, source: str, *, optional: bool) -> int | None: ...
def _valid_id(value: Any, source: str, *, optional: bool = False) -> int | None:
    """Nur positive, dezimale ID-Werte akzeptieren; niemals Labels als IDs deuten."""
    if (value is None or (isinstance(value, str) and not value.strip())) and optional:
        return None
    if type(value) is bool or not re.fullmatch(r"[1-9][0-9]*", str(value).strip()):
        raise ImportErrorGF(f"{source}: erwartet positive numerische ID, erhalten {value!r}")
    return int(str(value).strip())


def _teacher_ids(rows: list[dict]) -> set[int]:
    return {_valid_id(row["id"], "Lehrkraft-ID") for row in _cache(rows, {"id"}, "teachers")}


def _course_links(rows: list[dict]) -> dict[tuple[int, int], int]:
    """(GF-Lerngruppe-Schuljahr-ID, GF-Lehrkraft-ID) -> unveränderte Gruppen-ID.

    kurs_id erhält die unveränderte lerngruppe_schuljahr_id aus GF-Feld 5,
    nie die ID eines einzelnen Lehrkrafteinsatzes; geprüft wird nur, dass die
    Kombination aus Lerngruppe und Lehrkraft existiert.
    """
    result: dict[tuple[int, int], int] = {}
    required = {"lerngruppe_schuljahr_id", "lehrkraft_id"}
    for row in _cache(rows, required, "courses"):
        group_id = _valid_id(row["lerngruppe_schuljahr_id"], "lerngruppe_schuljahr_id")
        teacher_id = _valid_id(row["lehrkraft_id"], "Lehrkraft-ID (Lerngruppe)")
        # Mehrere Einsätze derselben Lerngruppe sind zulässig; die Ziel-ID
        # bleibt die ursprüngliche Lerngruppe-Schuljahr-ID.
        result[(group_id, teacher_id)] = group_id
    return result


def _resolve_teacher(value: Any, teacher_ids: set[int], source: str) -> int | None:
    teacher_id = _valid_id(value, source, optional=True)
    if teacher_id is not None and teacher_id not in teacher_ids:
        raise ImportErrorGF(f"{source}: ID {teacher_id} existiert nicht in der Lehrkräfte-Quelle")
    return teacher_id


def _resolve_course(value: Any, teacher_id: int | None,
                    course_links: dict[tuple[int, int], int]) -> int | None:
    group_id = _valid_id(value, "GF-Feld 5 / lerngruppe_schuljahr_id", optional=True)
    if group_id is None:
        return None
    if teacher_id is None:
        raise ImportErrorGF(f"Für Lerngruppe {group_id} fehlt die Lehrkraft-ID aus GF-Feld 3")
    key = (group_id, teacher_id)
    if key not in course_links:
        raise ImportErrorGF(f"Keine gültige Lerngruppen-/Lehrkraftzuordnung für Lerngruppe {group_id}, Lehrkraft {teacher_id}")
    # GF-Feld 5 enthält bereits die lerngruppe_schuljahr_id, die kurs_id speichert.
    return group_id


def _checked_gender(student: Mapping[str, Any], matrikel: int, warnings: list[str]) -> str | None:
    """Normalisiertes Geschlecht; nicht zuordenbare Angaben werden gemeldet statt still NULL."""
    raw = str(student.get("geschlecht") or "").strip()
    value = gender(raw)
    if raw and value is None:
        warnings.append(f"Matrikelnummer {matrikel}: Geschlecht '{raw[:20]}' nicht zuordenbar, wird leer gespeichert")
    return value


def build_participants(entries: Iterable[dict], students: list[dict], mandatory: list[dict],
                       teachers: set[int], courses: dict[tuple[int, int], int], options: ImportSettings,
                       warnings: list[str] | None = None) -> tuple[list[dict], dict[str, int]]:
    """Teilnehmende planen. Nicht blockierende Auffälligkeiten landen in ``warnings``."""
    class_notes: list[str] = []
    gender_notes: list[str] = []
    _cache(students, {"matrikelnummer", "vorname", "nachname", "geschlecht", "klasse"}, "students")
    student_map: dict[int, dict] = {}
    for item in students:
        if item.get("matrikelnummer") is None:
            continue
        matrikel = _valid_id(item["matrikelnummer"], "Matrikelnummer")
        if matrikel in student_map:
            raise ImportErrorGF(f"Doppelte Matrikelnummer {matrikel} im Schülerverzeichnis")
        student_map[matrikel] = item
    seen: dict[int, dict] = {}
    count_gf = 0
    skipped = 0
    empty_count = 0
    field = options.fields
    for entry in entries:
        if entry.get("status") not in (None, "active"):
            skipped += 1
            continue
        count_gf += 1
        chosen = selected_students(entry, field["teilnehmende"])
        if is_empty_report(entry, field.get("leermeldung", "10")):
            if chosen:
                raise ImportErrorGF(f"GF-Eintrag {entry.get('id', '(ohne ID)')}: Leermeldung und Teilnehmende gleichzeitig ausgewählt")
            empty_count += 1
            continue
        if not chosen:
            raise ImportErrorGF(f"Aktiver GF-Eintrag {entry.get('id', '(ohne ID)')} hat keine auslesbare Teilnehmerauswahl")
        for matrikel in chosen:
            if matrikel not in student_map:
                raise ImportErrorGF(f"Matrikelnummer {matrikel} aus Gravity Forms ist nicht im Schülerverzeichnis")
            if matrikel in seen:
                raise ImportErrorGF(f"Matrikelnummer {matrikel} wurde mehrfach angemeldet")
            s = student_map[matrikel]
            gf_class = str(entry.get(field["klasse"]) or "").strip()
            master_class = str(s["klasse"] or "").strip()
            # Die GF-Angabe hat wie bisher Vorrang; eine Abweichung von den
            # Stammdaten wird aber gemeldet, statt still übernommen zu werden.
            klass = gf_class or master_class
            if gf_class and master_class and gf_class.casefold() != master_class.casefold():
                class_notes.append(f"Matrikelnummer {matrikel}: Klasse laut Gravity Forms {gf_class[:20]}, "
                             f"laut Stammdaten {master_class[:20]}; übernommen wird {gf_class[:20]}")
            stufe = grade(klass)
            if stufe is None:
                raise ImportErrorGF(f"Keine Klassenstufe für Matrikelnummer {matrikel}")
            teacher_id = _resolve_teacher(entry.get(field["lehrkraft"]), teachers, "GF-Feld 3 / lehrkraft_id")
            seen[matrikel] = {
                "matrikelnummer": matrikel, "vorname": s["vorname"], "nachname": s["nachname"],
                "geschlecht": _checked_gender(s, matrikel, gender_notes), "klasse": klass, "stufe": stufe,
                "lehrkraft_id": teacher_id,
                "kurs_id": (_resolve_course(entry.get(field["kurs"]), teacher_id, courses)
                            if stufe in (11, 12) else None),
                "_source": "gf",
            }
    mandatory_count = 0
    mandatory_classes_present: set[str] = set()
    optional_classes_present: set[str] = set()
    if options.mandatory_classes or options.optional_mandatory_classes:
        # `mandatory` liefert ausschließlich *aktuelle Mathe-Lerngruppen* aus
        # einer privaten SQL-Abfrage gegen die View des aktiven Schuljahres.
        # Bei mehreren Lehrkräften pro Klasse nie zufällig eine auswählen.
        by_class: dict[str, tuple[int, int]] = {}
        for row in _cache(mandatory, {"klasse", "lehrkraft_id", "lerngruppe_schuljahr_id"}, "mandatory"):
            klass = str(row["klasse"] or "").strip()
            if klass not in set(options.mandatory_classes) | set(options.optional_mandatory_classes):
                continue
            teacher = _resolve_teacher(row["lehrkraft_id"], teachers, f"Pflichtklasse {klass}: lehrkraft_id")
            if teacher is None:
                raise ImportErrorGF(f"Pflichtklasse {klass} hat keine Mathe-Lehrkraft-ID")
            course = _resolve_course(row["lerngruppe_schuljahr_id"], teacher, courses)
            if course is None:
                raise ImportErrorGF(f"Pflichtklasse {klass} hat keine gültige Mathematik-Lerngruppe")
            if klass in by_class and by_class[klass] != (teacher, course):
                raise ImportErrorGF(f"Mehrdeutige Mathe-Lerngruppe/Lehrkraft für Pflichtklasse {klass}")
            by_class[klass] = (teacher, course)
        mandatory_classes_present = set(options.mandatory_classes) & set(by_class)
        optional_classes_present = set(options.optional_mandatory_classes) & set(by_class)
        missing_required = set(options.mandatory_classes) - mandatory_classes_present
        if missing_required:
            raise ImportErrorGF("Erforderliche Pflichtklasse(n) nicht in aktuellen Lerngruppen: " + ", ".join(sorted(missing_required)))
        # Falls in den aktuellen Schülerstammdaten bereits eine optionale
        # Klasse existiert, muss auch ihre Mathematik-Lerngruppe eindeutig
        # zugeordnet sein. Fehlende Fachgruppen nicht still übergehen.
        optional_with_students = {str(s.get("klasse") or "").strip()
                                  for s in student_map.values()} & set(options.optional_mandatory_classes)
        missing_optional_groups = optional_with_students - optional_classes_present
        if missing_optional_groups:
            raise ImportErrorGF("Optionale Klasse vorhanden, aber keine eindeutige Mathe-Lerngruppe: "
                                + ", ".join(sorted(missing_optional_groups)))
        # Eine optionale 7-4 wird nur dann ergänzt, wenn sie wirklich in
        # aktuellen Lerngruppen existiert; ansonsten gibt es keinen Fehler.
        for klass in sorted(mandatory_classes_present | optional_classes_present):
            matches = [s for s in student_map.values() if str(s.get("klasse") or "").strip() == klass]
            if not matches:
                raise ImportErrorGF(f"Keine Schülerdaten für vorhandene Pflichtklasse {klass}")
            for s in matches:
                matrikel = _valid_id(s["matrikelnummer"], "Matrikelnummer")
                if matrikel in seen:
                    # Die Daten einer expliziten GF-Meldung haben Vorrang; für
                    # fehlende IDs nutzen wir die geprüfte Pflichtlerngruppe.
                    if seen[matrikel]["klasse"] == klass:
                        if seen[matrikel]["lehrkraft_id"] is None:
                            seen[matrikel]["lehrkraft_id"] = by_class[klass][0]
                    continue
                stufe = grade(klass)
                seen[matrikel] = {
                    "matrikelnummer": matrikel, "vorname": s["vorname"], "nachname": s["nachname"],
                    "geschlecht": _checked_gender(s, matrikel, gender_notes), "klasse": klass, "stufe": stufe,
                    "lehrkraft_id": by_class[klass][0], "kurs_id": None,
                    "_source": "mandatory",
                }
                mandatory_count += 1
    def sort_key(x: dict):
        st = x["stufe"]
        return (0 if st in (11, 12) else 1, st, x["kurs_id"] or -1,
                x["klasse"].casefold(), str(x["nachname"] or "").casefold(),
                str(x["vorname"] or "").casefold(), x["matrikelnummer"])
    result = sorted(seen.values(), key=sort_key)
    for i, row in enumerate(result, 1):
        row["tnr"] = i
    if warnings is not None:
        warnings.extend(class_notes + gender_notes)
    return result, {"gf_entries": count_gf, "inactive_entries": skipped, "empty_reports": empty_count,
                    "mandatory_added": mandatory_count,
                    "mandatory_classes_present": len(mandatory_classes_present),
                    "optional_classes_present": len(optional_classes_present),
                    "class_mismatch": len(class_notes),
                    "unknown_gender": len(gender_notes)}


WRITE_COLUMNS = ("tnr", "matrikelnummer", "vorname", "nachname", "geschlecht", "klasse", "stufe", "lehrkraft_id", "kurs_id")


def _entry_id_issues(entries: list[dict], students: list[dict], field: str, empty_field: str = "10") -> list[str]:
    """Alle unbekannten/doppelten Matrikelnummern vor dem Import sammeln."""
    known = {int(s["matrikelnummer"]) for s in students if s.get("matrikelnummer") is not None
             and str(s["matrikelnummer"]).strip().isdigit()}
    seen: dict[int, str] = {}
    issues: list[str] = []
    for entry in entries:
        if entry.get("status") not in (None, "active"):
            continue
        ident = str(entry.get("id", "ohne ID"))[:64]
        try:
            selected = selected_students(entry, field)
        except ImportErrorGF as exc:
            issues.append(f"GF-Meldung {ident}: {exc}")
            continue
        if is_empty_report(entry, empty_field):
            if selected:
                issues.append(f"GF-Meldung {ident}: Leermeldung und Teilnehmende gleichzeitig ausgewählt")
            continue
        if not selected:
            issues.append(f"GF-Meldung {ident}: keine Teilnehmerauswahl")
        for number in selected:
            if number not in known:
                issues.append(f"GF-Meldung {ident}: unbekannte Matrikelnummer {number}")
            if number in seen:
                issues.append(f"Matrikelnummer {number} wurde mehrfach angemeldet (GF {seen[number]} und {ident})")
            else:
                seen[number] = ident
    return issues


def _class_summary(rows: list[dict], entries: list[dict] | None = None,
                   fields: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Zählt Klassen und Quellen ohne personenbezogene Angaben im Bericht."""
    classes: dict[str, dict[str, Any]] = {}
    for row in rows:
        klass = row["klasse"]
        item = classes.setdefault(klass, {"klasse": klass, "gf": 0, "mandatory": 0,
                                          "participants": 0, "missing_teacher": 0, "missing_course": 0,
                                           "empty_reports": 0})
        item["participants"] += 1
        item["mandatory" if row.get("_source") == "mandatory" else "gf"] += 1
        item["missing_teacher"] += int(row["lehrkraft_id"] is None)
        item["missing_course"] += int(row.get("stufe") in (11, 12) and row["kurs_id"] is None)
    if entries is not None and fields is not None:
        for entry in entries:
            if entry.get("status") not in (None, "active"):
                continue
            if not is_empty_report(entry, fields.get("leermeldung", "10")):
                continue
            klass = str(entry.get(fields["klasse"]) or "(ohne Klasse)").strip()
            item = classes.setdefault(klass, {"klasse": klass, "gf": 0, "mandatory": 0,
                                              "participants": 0, "missing_teacher": 0, "missing_course": 0,
                                              "empty_reports": 0})
            item["empty_reports"] += 1
    return [classes[key] for key in sorted(classes, key=lambda name: (grade(name) or 99, name))]


def write_import_report(workspace: Path, config_dir: Path, filename: Path,
                        report: Mapping[str, Any]) -> Path:
    """Neuen Bericht ausschließlich im Workspace schreiben; keine Überschreibungen."""
    if filename.suffix.lower() not in (".json", ".csv"):
        raise ConfigurationError("--report benötigt einen Dateinamen mit .json oder .csv")
    base = workspace.resolve()
    target = (base / filename).resolve()
    config = config_dir.resolve()
    if (target == base or base not in target.parents or
            target == config or config in target.parents):
        raise ConfigurationError("--report muss eine Datei innerhalb des Workspaces sein, außerhalb der Konfiguration")
    if any(part in (".mao-cli",) for part in target.relative_to(base).parts[:-1]):
        raise ConfigurationError("--report darf keine internen MaO-Systemdateien ersetzen")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8", newline="") as file:
            if target.suffix.lower() == ".json":
                json.dump(report, file, ensure_ascii=False, indent=2, sort_keys=True)
                file.write("\n")
            else:
                cols = ("klasse", "gf", "mandatory", "participants", "missing_teacher", "missing_course", "empty_reports",
                        "status", "issue", "warnings")
                writer = csv.DictWriter(file, fieldnames=cols, extrasaction="ignore")
                writer.writeheader()
                def safe_csv(value: Any) -> Any:
                    return "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value
                for item in report.get("classes", []):
                    writer.writerow({key: safe_csv(val) for key, val in {**item, "status": report.get("status", "")}.items()})
                summary = report.get("summary", {})
                writer.writerow({"klasse": "GESAMT", "gf": summary.get("gf_entries", ""),
                                 "mandatory": summary.get("mandatory_added", ""),
                                 "empty_reports": summary.get("empty_reports", ""),
                                 "participants": summary.get("participants", ""),
                                 "missing_teacher": summary.get("missing_teacher", ""),
                                 "missing_course": summary.get("missing_course_total", ""),
                                 "status": report.get("status", ""),
                                 "issue": safe_csv(" | ".join(map(str, report.get("issues", [])))),
                                 "warnings": safe_csv(" | ".join(map(str, report.get("warnings", []))))})
    except FileExistsError as exc:
        raise ConfigurationError(f"Bericht existiert bereits und wird nicht überschrieben: {target}") from exc
    return target


def _text(value: Any) -> str:
    """information_schema-Werte kommen je nach Treiber als str oder bytes."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8")
    return str(value)


def foreign_key_targets(cursor: Any, table: str,
                        columns: Iterable[str] = FK_COLUMNS) -> dict[str, str | None]:
    """Liest nur lesend, worauf die Fremdschlüssel der Spalten verweisen ("tabelle.spalte")."""
    cursor.execute(
        "SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
        "FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND REFERENCED_TABLE_NAME IS NOT NULL",
        (table,),
    )
    found: dict[str, str] = {}
    for column, ref_table, ref_column in cursor.fetchall():
        found.setdefault(_text(column), f"{_text(ref_table)}.{_text(ref_column)}")
    return {column: found.get(column) for column in columns}


def review_foreign_keys(targets: Mapping[str, str | None],
                        expected: Mapping[str, str]) -> tuple[list[str], list[str]]:
    """Vergleicht FK-Ziele mit gf-import.yaml/foreign_keys.

    Liefert (Diagnosezeilen, blockierende Abweichungen). Tabellennamen werden
    ohne Groß-/Kleinschreibung verglichen, weil MySQL sie unter Windows
    kleingeschrieben in information_schema ablegt.
    """
    lines: list[str] = []
    problems: list[str] = []
    for column, target in targets.items():
        wanted = expected.get(column)
        if wanted is None:
            lines.append(f"Fremdschlüssel {column} → {target or 'keiner'} "
                         "(nicht geprüft: keine Erwartung in gf-import.yaml/foreign_keys)")
        elif target is None:
            lines.append(f"WARNUNG: {column} hat keinen Fremdschlüssel; erwartet {wanted}, "
                         "die Datenbank prüft die Werte also nicht")
        elif target.split(".", 1)[0].casefold() != wanted.casefold():
            problems.append(f"Fremdschlüssel {column} verweist auf {target}, erwartet {wanted}")
        else:
            lines.append(f"Fremdschlüssel {column} → {target} (wie erwartet)")
    return lines, problems


def import_participants(db: Any, config_dir: Path, target_table: str, *, dry_run: bool,
                        no_mandatory: bool = False, report: dict[str, Any] | None = None) -> dict[str, int]:
    """Alle Datenquellen prüfen, dann berichtsfähig planen, erst zuletzt schreiben."""
    config = load_import_settings(config_dir / "gf-import.yaml")
    credentials = load_gf_credentials(config_dir / "gf.config")
    cnx = db.conn
    if cnx is None:
        raise ImportErrorGF("Keine Datenbankverbindung")
    cursor = cnx.cursor()
    try:
        students = _rows(cursor, config.queries["students"])
        mandatory = [] if no_mandatory or not (config.mandatory_classes or config.optional_mandatory_classes) else _rows(cursor, config.queries["mandatory"])
        teachers = _teacher_ids(_rows(cursor, config.queries["teachers"]))
        courses = _course_links(_rows(cursor, config.queries["courses"]))
        # GF-Daten vollständig abrufen und erst dann eine Entscheidung treffen.
        entries = list(gf_entries(credentials, config.form_id, config.page_size, config.timeout))
        active_entries = [e for e in entries if e.get("status") in (None, "active")]
        if report is not None:
            report["sources"] = {"gf_entries_total": len(entries), "gf_entries_active": len(active_entries),
                                 "students": len(students), "teachers": len(teachers), "assignments": len(courses)}
        issues = _entry_id_issues(entries, students, config.fields["teilnehmende"],
                                  config.fields.get("leermeldung", "10"))
        if issues:
            if report is not None:
                report.setdefault("issues", []).extend(issues)
            raise ImportErrorGF(f"{len(issues)} fehlerhafte Teilnehmerzuordnung(en); Einzelheiten im Prüfbericht: " + issues[0])
        options = (dataclasses.replace(config, mandatory_classes=(), optional_mandatory_classes=())
                   if no_mandatory else config)
        warnings: list[str] = []
        mapped, stats = build_participants(entries, students, mandatory, teachers, courses, options,
                                           warnings=warnings)
        if stats["class_mismatch"]:
            LOGGER.warning("Klasse laut Gravity Forms weicht von den Stammdaten ab: %d", stats["class_mismatch"])
        if stats["unknown_gender"]:
            LOGGER.warning("Geschlecht nicht zuordenbar: %d", stats["unknown_gender"])
        for note in warnings[:20]:
            LOGGER.warning("  %s", note)
        if len(warnings) > 20:
            LOGGER.warning("  … %d weitere Hinweise (vollständig mit --report)", len(warnings) - 20)
        if report is not None:
            report["classes"] = _class_summary(mapped, entries, config.fields)
            report["warnings"] = warnings
        if not mapped:
            raise ImportErrorGF("Keine Teilnehmenden ermittelt – Import aus Sicherheitsgründen abgebrochen")
        if len(mapped) != len(set(x["matrikelnummer"] for x in mapped)):
            raise ImportErrorGF("Doppelte Matrikelnummern")
        missing_teacher = sum(x["lehrkraft_id"] is None for x in mapped)
        missing_course = sum(x["stufe"] in (11, 12) and x["kurs_id"] is None for x in mapped)
        if missing_teacher:
            LOGGER.warning("Teilnehmende ohne zugeordnete Lehrkraft: %d", missing_teacher)
        if missing_course:
            LOGGER.warning("Oberstufenteilnehmende ohne Kurszuordnung: %d", missing_course)
        if report is not None:
            report["summary"] = {**stats, "participants": len(mapped), "missing_teacher": missing_teacher,
                                 "missing_course_oberstufe": missing_course,
                                 "missing_course_total": missing_course,
                                 "target_table": target_table}
        inserted = persist_import(db, target_table, mapped, dry_run=dry_run,
                                  foreign_keys=config.foreign_keys, report=report)
        if report is not None:
            report["status"] = "ready" if dry_run else "applied"
            report["summary"]["inserted"] = inserted
        return {**stats, "participants": len(mapped), "inserted": inserted,
                "missing_teacher": missing_teacher, "missing_course": missing_course}
    finally:
        cursor.close()


def persist_import(db: Any, target_table: str, participants: list[dict], *, dry_run: bool,
                   foreign_keys: Mapping[str, str] | None = None,
                   report: dict[str, Any] | None = None) -> int:
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
        # Auch im Dry-Run prüfen, damit falsche FK-Ziele vor --apply auffallen.
        targets = foreign_key_targets(cur, target_table)
        fk_lines, fk_problems = review_foreign_keys(targets, foreign_keys or {})
        for line in fk_lines:
            LOGGER.info("Schema | %s", line)
        if report is not None:
            report["schema"] = {"foreign_keys": targets, "notes": fk_lines}
        if fk_problems:
            raise ImportErrorGF("; ".join(fk_problems))
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
