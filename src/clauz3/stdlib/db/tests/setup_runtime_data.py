"""Create fresh runtime fixtures for the stdlib:db smoke test."""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "runtime-data"
CSV = DATA / "csv"
PARQUET = DATA / "parquet"
DUCKDB = DATA / "duckdb"

USERS_CSV = """name,email,consented
Bob,bob@example.com,true
Ann,ann@example.com,true
Carol,carol@example.com,false
"""


def main() -> None:
    shutil.rmtree(DATA, ignore_errors=True)
    CSV.mkdir(parents=True)
    (CSV / "users.csv").write_text(USERS_CSV, encoding="utf-8")

    try:
        import duckdb
    except ImportError:
        print("created CSV runtime fixtures; duckdb not installed")
        return

    PARQUET.mkdir()
    DUCKDB.mkdir()
    csv_path = CSV / "users.csv"
    parquet_path = PARQUET / "users.parquet"
    duckdb_path = DUCKDB / "users.duckdb"

    con = duckdb.connect(":memory:")
    try:
        con.execute(
            "COPY (SELECT * FROM read_csv_auto($source)) "
            "TO $destination (FORMAT PARQUET)",
            {"source": str(csv_path), "destination": str(parquet_path)},
        )
    finally:
        con.close()

    con = duckdb.connect(str(duckdb_path))
    try:
        con.execute(
            "CREATE TABLE users AS SELECT * FROM read_csv_auto(?)",
            [str(csv_path)],
        )
    finally:
        con.close()

    print("created CSV, Parquet, and DuckDB runtime fixtures")


if __name__ == "__main__":
    main()
