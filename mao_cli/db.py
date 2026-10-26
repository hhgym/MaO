"""MaO-CLI: db."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional, Mapping
import re

import pandas as pd
from dateutil.relativedelta import relativedelta
from .common import LOGGER
try:
    import mysql.connector
except ImportError:
    mysql = None


@dataclass(frozen=True)
class DBConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


def load_db_config(config_dir: Path) -> DBConfig:
    """Liest db.config aus MAO_CONFIG. Format: key=value pro Zeile."""
    path = config_dir / "db.config"
    if not path.exists():
        raise FileNotFoundError(f"db.config nicht gefunden: {path}")
    data: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip()
    if not data.get("user") or not data.get("database"):
        raise ValueError("db.config benötigt 'user' und 'database'.")
    return DBConfig(
        host=data.get("host", "localhost"),
        port=int(data.get("port", "3306")),
        user=data.get("user", ""),
        password=data.get("password", ""),
        database=data.get("database", ""),
    )


class DB:
    def __init__(self, cfg: DBConfig):
        self.cfg = cfg
        self.conn: Optional[mysql.connector.MySQLConnection] = None

    def __enter__(self) -> "DB":
        if mysql is None:
            raise RuntimeError("mysql-connector-python ist nicht installiert (pip install -r requirements.txt).")
        try:
            self.conn = mysql.connector.connect(
                host=self.cfg.host,
                port=self.cfg.port,
                user=self.cfg.user,
                password=self.cfg.password,
                database=self.cfg.database,
                connection_timeout=10,
            )
        except Exception as exc:
            raise ConnectionError(f"MySQL-Verbindungsaufbau gescheitert ({self.cfg.host}:{self.cfg.port}): {exc}") from exc
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.conn is not None:
                self.conn.close()
        finally:
            self.conn = None

    def query_df(self, sql: str) -> pd.DataFrame:
        if self.conn is None:
            raise RuntimeError("Keine aktive Datenbankverbindung")
        cur = self.conn.cursor(dictionary=True)
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            # Even for zero rows keep SQL column metadata so headers/validation work.
            columns = list(cur.column_names or [])
            return pd.DataFrame(rows, columns=columns)
        finally:
            cur.close()


# --------------------------------------------------------------------------------------
# MaO number
# --------------------------------------------------------------------------------------

def current_MaO_number_fallback(today: Optional[date] = None) -> int:
    start = datetime(1961, 8, 1).date()
    today = today or date.today()
    diff = relativedelta(today, start)
    return diff.years + 1


def current_MaO_number_db(db: DB, tables: Mapping[str, str]) -> Optional[int]:
    # Tabellennamen werden nur nach Identifier-Prüfung in SQL eingebettet.
    year_table = tables.get("jahrgaenge_table", "")
    school_year_table = tables.get("schuljahre_table", "")
    valid = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    if not valid.fullmatch(year_table) or not valid.fullmatch(school_year_table):
        raise ValueError("Ungültige oder fehlende Tabellennamen für die Jahrgangsermittlung")
    sql = f"""
        SELECT j.jahrgang_nr
        FROM `{year_table}` j
        JOIN `{school_year_table}` s ON s.id = j.schuljahr_id
        WHERE s.aktiv = 1
        ORDER BY j.id DESC
        LIMIT 1
    """
    try:
        df = db.query_df(sql)
        if not df.empty and pd.notna(df.iloc[0]["jahrgang_nr"]):
            return int(df.iloc[0]["jahrgang_nr"])
    except Exception as e:
        LOGGER.warning("MaO-Nummer nicht aus DB ermittelbar: %s", e)
    return None


def school_year_label(beginn: object) -> str:
    """Schuljahr als ``2026-27`` aus dem Beginndatum (Schuljahre beginnen im Sommer)."""
    start = pd.Timestamp(beginn).year  # type: ignore[arg-type]
    return f"{start}-{(start + 1) % 100:02d}"


def active_jahrgang_db(db: DB, tables: Mapping[str, str]) -> dict[str, object]:
    """Aktives Schuljahr und dessen MaO-Nummer (nur lesend).

    Liefert ``year`` (z. B. ``2026-27``), ``mao`` (Nummer oder ``None``, wenn für das
    aktive Schuljahr noch kein Jahrgang angelegt ist) und ``last_mao`` (höchste vorhandene Nummer).
    """
    year_table = tables.get("jahrgaenge_table", "")
    school_year_table = tables.get("schuljahre_table", "")
    valid = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    if (not valid.fullmatch(year_table) or not valid.fullmatch(school_year_table)
            or year_table.startswith("DEMO_") or school_year_table.startswith("DEMO_")):
        raise ValueError("Für die Jahrgangsabfrage müssen jahrgaenge_table und schuljahre_table in tables.ini echte Namen haben")
    active = db.query_df(f"SELECT id, beginn FROM `{school_year_table}` WHERE aktiv = 1 ORDER BY beginn DESC LIMIT 1")
    if active.empty:
        raise ValueError("In der Datenbank ist kein Schuljahr als aktiv markiert")
    school_year_id = int(active.iloc[0]["id"])
    year = school_year_label(active.iloc[0]["beginn"])
    nr = db.query_df(f"SELECT jahrgang_nr FROM `{year_table}` WHERE schuljahr_id = {school_year_id} "
                     "ORDER BY id DESC LIMIT 1")
    last = db.query_df(f"SELECT MAX(jahrgang_nr) AS n FROM `{year_table}`")
    mao = int(nr.iloc[0]["jahrgang_nr"]) if not nr.empty and pd.notna(nr.iloc[0]["jahrgang_nr"]) else None
    last_mao = int(last.iloc[0]["n"]) if not last.empty and pd.notna(last.iloc[0]["n"]) else None
    return {"year": year, "mao": mao, "last_mao": last_mao}


def current_MaO_number(db: Optional[DB] = None, tables: Optional[Mapping[str, str]] = None) -> int:
    if db is not None and tables is not None:
        x = current_MaO_number_db(db, tables)
        if isinstance(x, int) and x > 0:
            return x
    return current_MaO_number_fallback()


# --------------------------------------------------------------------------------------
# Data transforms
# --------------------------------------------------------------------------------------
