"""MaO-CLI: db."""
from __future__ import annotations

import logging
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


def load_db_config(script_dir: Path) -> DBConfig:
    """Reads db.config in script dir. Format: key=value per line."""
    path = script_dir / "db.config"
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


def current_MaO_number(db: Optional[DB] = None, tables: Optional[Mapping[str, str]] = None) -> int:
    if db is not None and tables is not None:
        x = current_MaO_number_db(db, tables)
        if isinstance(x, int) and x > 0:
            return x
    return current_MaO_number_fallback()


# --------------------------------------------------------------------------------------
# Data transforms
# --------------------------------------------------------------------------------------
