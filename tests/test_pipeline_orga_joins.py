"""Die mitgelieferte Pipeline-SQL muss zur Basis-View der ID-basierten Organisationstabelle passen."""
import sqlite3
from pathlib import Path

import pytest
import yaml

from mao_cli.config import resolve_sql_table_tokens

DEFAULTS = Path(__file__).resolve().parents[1] / "mao_cli" / "defaults"
TABLES = {"orga_view": "t_basis", "courses_table": "t_info"}


@pytest.fixture
def db():
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE t_teachers (id INTEGER PRIMARY KEY, kuerzel TEXT);
        CREATE TABLE t_rooms (id INTEGER PRIMARY KEY, raumcode TEXT);
        CREATE TABLE t_prizes (id INTEGER PRIMARY KEY, bezeichnung TEXT);
        CREATE TABLE t_lg (id INTEGER PRIMARY KEY, lerngruppe_kennung TEXT);
        CREATE TABLE t_lgs (id INTEGER PRIMARY KEY, lerngruppe_id INTEGER);
        CREATE TABLE t_info (lerngruppe TEXT, kl TEXT, stellvkl TEXT, mathel TEXT);
        CREATE TABLE t_orga (id INTEGER PRIMARY KEY, tnr INT, nachname TEXT, vorname TEXT, geschlecht TEXT,
            klasse TEXT, matrikelnummer INT, stufe INT, lehrkraft_id INT, kurs_id INT, raum_id INT,
            anwesenheit INT, bemerkung TEXT, aufgabe_1 INT, aufgabe_2 INT, aufgabe_3 INT, aufgabe_4 INT,
            punkte INT, preis_id INT, preisgeld REAL, delegierung INT);
        -- Nachbildung der Basis-View: ID- und Klartext-Spalten nebeneinander
        CREATE VIEW t_basis AS
          SELECT o.*, r.raumcode AS raum, t.kuerzel AS lehrkraft,
                 lg.lerngruppe_kennung AS kurs, p.bezeichnung AS preis
          FROM t_orga o
          LEFT JOIN t_rooms r ON r.id = o.raum_id
          LEFT JOIN t_teachers t ON t.id = o.lehrkraft_id
          LEFT JOIN t_lgs lgs ON lgs.id = o.kurs_id
          LEFT JOIN t_lg lg ON lg.id = lgs.lerngruppe_id
          LEFT JOIN t_prizes p ON p.id = o.preis_id;
        INSERT INTO t_teachers VALUES (1, 'ABC');
        INSERT INTO t_rooms VALUES (1, '202');
        INSERT INTO t_prizes VALUES (1, '1. Preis');
        INSERT INTO t_lg VALUES (7, 'M-GK1');
        INSERT INTO t_lgs VALUES (5, 7);
        INSERT INTO t_info VALUES ('11-1', 'KL', 'SKL', 'ML');
        INSERT INTO t_orga VALUES (1, 1, 'Muster', 'Max', 'm', '11-1', 1001, 11, 1, 5, 1, 1, NULL, 1, 2, 3, 4, 10, 1, 25.0, 0);
        INSERT INTO t_orga VALUES (2, 2, 'Beispiel', 'Eva', 'w', '7-1', 1002, 7, NULL, NULL, NULL, 0, NULL, NULL, NULL, NULL, NULL, 0, NULL, NULL, NULL);
    """)
    con.row_factory = sqlite3.Row
    return con


def dataset_sql(name: str) -> str:
    cfg = yaml.safe_load((DEFAULTS / "pipeline.yaml").read_text(encoding="utf-8"))
    ds = next(item for item in cfg["datasets"] if item["name"] == name)
    return resolve_sql_table_tokens(ds["sql"], TABLES, label=name)


def test_orga_dataset_selects_text_columns_without_ids(db):
    rows = {r["matrikelnummer"]: dict(r) for r in db.execute(dataset_sql("orga"))}
    assert len(rows) == 2  # LEFT JOIN: Teilnehmende ohne Kurs/Raum/Preis bleiben erhalten
    assert (rows[1001]["lehrkraft"], rows[1001]["kurs"], rows[1001]["raum"], rows[1001]["preis"]) == (
        "ABC", "M-GK1", "202", "1. Preis")
    assert rows[1002]["kurs"] == ""            # leer statt NULL: Filter kurs != "" greift
    assert rows[1002]["preis"] is None and rows[1002]["lehrkraft"] is None
    assert not any(column.endswith("_id") for column in rows[1001])


def test_orga_preis_filters_on_preis_id(db):
    rows = list(db.execute(dataset_sql("orga_preis")))
    assert [r["matrikelnummer"] for r in rows] == [1001]
    assert rows[0]["preis"] == "1. Preis"


def test_klassenleitungslisten_returns_prize_name(db):
    rows = list(db.execute(dataset_sql("klassenleitungslisten")))
    assert rows[0]["preis"] == "1. Preis" and rows[0]["kl"] == "KL"


def test_pipeline_reads_organisation_only_through_the_view():
    cfg = yaml.safe_load((DEFAULTS / "pipeline.yaml").read_text(encoding="utf-8"))
    for name in ("orga", "orga_preis", "klassenleitungslisten"):
        raw = next(item for item in cfg["datasets"] if item["name"] == name)["sql"]
        assert "{{table.orga_view}}" in raw and "{{table.orga_table}}" not in raw
