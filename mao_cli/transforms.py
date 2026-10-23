"""MaO-CLI: transforms."""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any, List, Optional
import pandas as pd
from .common import LOGGER, _as_list, atomic_write


def _apply_rename(df: pd.DataFrame, rename_map: Any) -> pd.DataFrame:
    if not rename_map:
        return df
    if not isinstance(rename_map, dict):
        raise TypeError("rename muss ein Mapping sein")
    return df.rename(columns=rename_map)



def _apply_cast(df: pd.DataFrame, cast_spec: Any) -> pd.DataFrame:
    """Cast columns to requested dtypes.

    YAML example (dataset or export level):
      cast:
        punkte: Int64        # pandas nullable integer (supports <NA>)
        anwesenheit: Int64
        note: float64
        datum: datetime64[ns]

    Notes:
      - Numeric casts use pd.to_numeric(errors="coerce") for legacy compatibility.
      - Nonempty values turning into NA are logged; impossible dtype casts fail.
    """
    if not cast_spec:
        return df
    if not isinstance(cast_spec, dict):
        raise ValueError("cast muss ein Mapping sein, z.B. {spalte: dtype}")

    for col, dtype in cast_spec.items():
        if col not in df.columns:
            raise KeyError(f"Cast-Spalte fehlt: {col}")
        if dtype is None:
            continue
        dt = str(dtype).strip()
        if not dt:
            continue

        dt_l = dt.lower()

        try:
            original = df[col].copy()
            if dt == "Int64":
                converted = pd.to_numeric(original, errors="coerce").astype("Int64")
            elif dt_l.startswith("datetime"):
                converted = pd.to_datetime(original, errors="coerce")
            elif dt_l in {"int64","int32","int16","int8","uint64","uint32","uint16","uint8","float64","float32","float","int"}:
                converted = pd.to_numeric(original, errors="coerce").astype(dt)
            else:
                converted = original.astype(dt)
            invalid = int((original.notna() & converted.isna()).sum())
            if invalid:
                LOGGER.warning("Cast '%s' -> '%s': %d nichtleere Werte wurden NA", col, dt, invalid)
            df[col] = converted
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError(f"Cast fehlgeschlagen: Spalte '{col}' -> '{dt}': {exc}") from exc

    return df

def _apply_sort(df: pd.DataFrame, sort_by: List[Any]) -> pd.DataFrame:
    """Sort DataFrame with YAML sort_by spec.

    Supported forms:
      sort_by: [colA, colB]               # asc for all
      sort_by:
        - column: punkte
          order: desc
        - column: nachname
          order: asc
    """
    if not sort_by:
        return df

    # list of strings -> asc
    if all(isinstance(x, str) for x in sort_by):
        cols = [str(x) for x in sort_by]
        missing = [c for c in cols if c not in df.columns]
        if missing:
            raise KeyError(f"Sortierspalten fehlen: {missing}")
        return df.sort_values(by=cols, ascending=[True] * len(cols), kind="mergesort")

    # list of dicts -> per-column order
    if all(isinstance(x, dict) for x in sort_by):
        cols: list[str] = []
        asc: list[bool] = []
        for item in sort_by:
            col = str(item.get("column", "")).strip()
            if not col or col not in df.columns:
                raise KeyError(f"Sortierspalte fehlt: {col!r}")
            order = str(item.get("order", "asc")).strip().lower()
            if order not in ("asc", "desc"):
                raise ValueError(f"Unbekannte Sortierrichtung für '{col}': {order}")
            cols.append(col)
            asc.append(order == "asc")
        if not cols:
            return df
        return df.sort_values(by=cols, ascending=asc, kind="mergesort")

    raise ValueError("sort_by muss eine Liste von Spaltennamen oder eine Liste von {column, order} sein.")


def _apply_columns(df: pd.DataFrame, columns_spec: Any) -> pd.DataFrame:
    if columns_spec in (None, "*"):
        return df
    cols = [str(c) for c in _as_list(columns_spec)]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise KeyError(f"Spalten fehlen: {missing}")
    return df[cols]


def _apply_filters(df: pd.DataFrame, filters: List[Any]) -> pd.DataFrame:
    for f in filters:
        if not isinstance(f, dict):
            raise TypeError("filter muss eine Liste von Mappings sein")
        col = f.get("column")
        op = str(f.get("op", "==")).lower()
        val = f.get("value")
        if col not in df.columns:
            raise KeyError(f"Filter-Spalte fehlt: {col}")

        s = df[col]
        if op == "==":
            df = df[s == val]
        elif op == "!=":
            df = df[s != val]
        elif op == ">":
            df = df[s > val]
        elif op == ">=":
            df = df[s >= val]
        elif op == "<":
            df = df[s < val]
        elif op == "<=":
            df = df[s <= val]
        elif op == "in":
            vals = set(_as_list(val))
            df = df[s.isin(vals)]
        elif op == "notin":
            vals = set(_as_list(val))
            df = df[~s.isin(vals)]
        elif op == "contains":
            df = df[s.astype(str).str.contains(str(val), na=False)]
        elif op == "isnull":
            df = df[s.isna()]
        elif op == "notnull":
            df = df[s.notna()]
        else:
            raise ValueError(f"Unbekannter Filter-Operator: {op}")
    return df


def csv_bytes(df: pd.DataFrame, sep: str = ";", float_format: Optional[str] = None) -> bytes:
    out = io.StringIO(newline="")
    df.to_csv(out, sep=sep, index=False, float_format=float_format, lineterminator="\n")
    return out.getvalue().encode("utf-8")


def write_csv(df: pd.DataFrame, path: Path, sep: str = ";", float_format: Optional[str] = None) -> None:
    atomic_write(path, csv_bytes(df, sep=sep, float_format=float_format))
