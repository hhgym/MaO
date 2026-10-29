"""Schulverzeichnis Berlin (WFS/GeoJSON) in die Tabelle der Schulen übernehmen.

Nur lesender Abruf öffentlicher Daten. Geschrieben wird nur mit --apply: neue Schulen
werden angelegt, geänderte aktualisiert, weggefallene auf aktiv = 0 gesetzt. Es wird
nie gelöscht, und manuell gepflegte Spalten (zweite_stufe_vorhanden) bleiben unberührt.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .gf_import import IDENT

LOGGER = logging.getLogger(__name__)
DEFAULT_URL = ("https://gdi.berlin.de/services/wfs/schulen?service=WFS&version=2.0.0&request=GetFeature"
               "&typeNames=schulen%3Aschulen&outputFormat=application%2Fjson")
MAX_BYTES = 50 * 1024 * 1024
MIN_SCHOOLS = 100
BSN = re.compile(r"^\d{2}[A-Za-z]\d{2}$")
# Spalte der Tabelle -> (Eigenschaft im GeoJSON, maximale Länge)
FIELDS: dict[str, tuple[str, int]] = {
    "name": ("schulname", 255), "schulart": ("schulart", 100), "schultyp": ("schultyp", 100),
    "traeger": ("traeger", 50), "bezirk": ("bezirk", 100), "ortsteil": ("ortsteil", 50),
    "plz": ("plz", 10), "strasse": ("strasse", 255), "hausnr": ("hausnr", 20),
    "telefon": ("telefon", 50), "fax": ("fax", 50), "email": ("email", 255), "internet": ("internet", 255),
    "schuljahr_quelle": ("schuljahr", 10),
}
COLUMNS = ("schulnummer", *FIELDS)


class SchoolError(ValueError):
    """Quelle oder Tabelle unbrauchbar. Es wird nichts geschrieben."""


Fetcher = Callable[[str], bytes]


def fetch_bytes(url: str, timeout: int = 60) -> bytes:
    if urlsplit(url).scheme != "https":
        raise SchoolError("Die Quelle muss eine https-Adresse sein")
    request = Request(url, headers={"User-Agent": "mao-cli", "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 (https geprüft)
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise SchoolError("Antwort der Quelle ist unerwartet groß")
    return data


def parse_schools(raw: bytes | str) -> tuple[list[dict[str, str | None]], list[str]]:
    """GeoJSON in Zeilen umwandeln. Rückgabe: Schulen, Hinweise."""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise SchoolError(f"Quelle ist kein gültiges JSON ({exc})") from exc
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        raise SchoolError("Quelle enthält keine Liste 'features'")
    notes: list[str] = []
    schools: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for feature in features:
        props = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(props, dict):
            notes.append("Eintrag ohne properties übersprungen")
            continue
        number = str(props.get("bsn") or "").strip().upper()
        name = str(props.get("schulname") or "").strip()
        if not number or not name:
            notes.append(f"Eintrag ohne Schulnummer oder Namen übersprungen ({number or '?'})")
            continue
        if not BSN.fullmatch(number):
            notes.append(f"Schulnummer {number} hat ein unerwartetes Format (trotzdem übernommen)")
        if number in seen:
            raise SchoolError(f"Schulnummer {number} kommt mehrfach in der Quelle vor")
        seen.add(number)
        row: dict[str, str | None] = {"schulnummer": number}
        for column, (prop, limit) in FIELDS.items():
            value = props.get(prop)
            text = None if value is None or str(value).strip() == "" else str(value).strip()
            if text is not None and len(text) > limit:
                notes.append(f"{number}: {column} gekürzt auf {limit} Zeichen")
                text = text[:limit]
            row[column] = text
        schools.append(row)
    for key in ("numberMatched", "totalFeatures"):
        total = data.get(key)
        if isinstance(total, int) and total != len(features):
            raise SchoolError(f"Quelle meldet {total} Schulen, geliefert wurden {len(features)} (unvollständig?)")
    return schools, notes


@dataclass
class SyncPlan:
    inserts: list[dict[str, str | None]] = field(default_factory=list)
    updates: list[tuple[dict[str, str | None], dict[str, tuple[Any, Any]]]] = field(default_factory=list)
    deactivate: list[str] = field(default_factory=list)
    unchanged: int = 0


def plan_sync(existing: Mapping[str, Mapping[str, Any]], schools: list[dict[str, str | None]]) -> SyncPlan:
    plan = SyncPlan()
    incoming = {row["schulnummer"]: row for row in schools}
    for number, row in incoming.items():
        old = existing.get(str(number))
        if old is None:
            plan.inserts.append(row)
            continue
        changes: dict[str, tuple[Any, Any]] = {column: (old.get(column), row[column]) for column in FIELDS
                   if (old.get(column) or None) != row[column]}
        if not old.get("aktiv"):
            changes["aktiv"] = (0, 1)
        if changes:
            plan.updates.append((row, changes))
        else:
            plan.unchanged += 1
    plan.deactivate = sorted(n for n, old in existing.items() if old.get("aktiv") and n not in incoming)
    return plan


def _table(tables: Mapping[str, str]) -> str:
    name = tables.get("schools_table", "")
    if not IDENT.fullmatch(name or "") or name.startswith("DEMO_"):
        raise SchoolError("schools_table fehlt oder ist ein DEMO-Platzhalter: tables.ini prüfen")
    return name


def sync_schools(db: Any, tables: Mapping[str, str], *, url: str = DEFAULT_URL, dry_run: bool = True,
                 fetcher: Fetcher = fetch_bytes, report: dict[str, Any] | None = None) -> dict[str, int]:
    table = _table(tables)
    cnx = db.conn
    if cnx is None:
        raise SchoolError("Keine Datenbankverbindung")
    schools, notes = parse_schools(fetcher(url))
    for note in notes[:20]:
        LOGGER.warning("Schulen | %s", note)
    cur = cnx.cursor()
    lock_name = ""
    try:
        cur.execute(f"SELECT {', '.join('`' + c + '`' for c in COLUMNS)}, `aktiv` FROM `{table}`")
        existing: dict[str, dict[str, Any]] = {}
        for values in cur.fetchall():
            row = dict(zip((*COLUMNS, "aktiv"), values))
            existing[str(row["schulnummer"])] = row
        active = sum(1 for row in existing.values() if row.get("aktiv"))
        if len(schools) < MIN_SCHOOLS or (active and len(schools) < active // 2):
            raise SchoolError(f"Nur {len(schools)} Schulen in der Quelle (Tabelle: {active} aktiv); "
                              "vermutlich unvollständig, nichts geschrieben")
        plan = plan_sync(existing, schools)
        stats = {"quelle": len(schools), "neu": len(plan.inserts), "geaendert": len(plan.updates),
                 "weggefallen": len(plan.deactivate), "unveraendert": plan.unchanged}
        LOGGER.info("Schulen | Quelle: %d | neu: %d | geändert: %d | weggefallen: %d | unverändert: %d | Modus: %s",
                    stats["quelle"], stats["neu"], stats["geaendert"], stats["weggefallen"], stats["unveraendert"],
                    "DRY-RUN" if dry_run else "APPLY")
        for row in plan.inserts[:10]:
            LOGGER.info("  neu: %s %s", row["schulnummer"], row["name"])
        for row, changes in plan.updates[:10]:
            LOGGER.info("  geändert: %s %s (%s)", row["schulnummer"], row["name"], ", ".join(sorted(changes)))
        for number in plan.deactivate[:10]:
            LOGGER.info("  weggefallen: %s %s", number, existing[number].get("name"))
        if report is not None:
            report["summary"] = stats
            report["warnings"] = notes
            report["neu"] = [{"schulnummer": r["schulnummer"], "name": r["name"]} for r in plan.inserts]
            report["geaendert"] = [{"schulnummer": r["schulnummer"], "name": r["name"], "felder": sorted(c)}
                                   for r, c in plan.updates]
            report["weggefallen"] = plan.deactivate
        if dry_run:
            if report is not None:
                report["status"] = "ready"
            return stats
        lock_name = f"mao-schulen-{table}"
        cur.execute("SELECT GET_LOCK(%s, 10)", (lock_name,))
        if cur.fetchall()[0][0] != 1:
            raise SchoolError("Konkurrierender Abgleich oder Sperr-Timeout")
        if cnx.in_transaction:
            cnx.rollback()
        cnx.start_transaction()
        try:
            columns = ", ".join(f"`{c}`" for c in COLUMNS)
            if plan.inserts:
                cur.executemany(f"INSERT INTO `{table}` ({columns}, `aktiv`) VALUES ({', '.join(['%s'] * len(COLUMNS))}, 1)",
                                [tuple(row[c] for c in COLUMNS) for row in plan.inserts])
            if plan.updates:
                assignments = ", ".join(f"`{c}` = %s" for c in FIELDS)
                cur.executemany(f"UPDATE `{table}` SET {assignments}, `aktiv` = 1 WHERE `schulnummer` = %s",
                                [(*(row[c] for c in FIELDS), row["schulnummer"]) for row, _ in plan.updates])
            if plan.deactivate:
                cur.executemany(f"UPDATE `{table}` SET `aktiv` = 0 WHERE `schulnummer` = %s",
                                [(number,) for number in plan.deactivate])
            cur.execute(f"SELECT COUNT(*) FROM `{table}`")
            total = int(cur.fetchall()[0][0])
            if total != len(existing) + len(plan.inserts):
                raise SchoolError(f"Zählabgleich fehlgeschlagen: {total} Zeilen, erwartet {len(existing) + len(plan.inserts)}")
            cnx.commit()
        except Exception:
            cnx.rollback()
            raise
        if report is not None:
            report["status"] = "applied"
        return stats
    finally:
        if lock_name:
            try:
                cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                cur.fetchall()
            except Exception:
                LOGGER.warning("Konnte Sperre nicht regulär freigeben (DB-Disconnect gibt sie frei)")
        cur.close()
