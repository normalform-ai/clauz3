"""Runtime smoke test for stdlib:db over generated fixtures."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from tools.db.trusted.effects import create_table, insert_row, read_rows

import clauz3

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "runtime-data"


class UserRow(clauz3.Row):
    name: str
    email: str
    consented: bool


def main() -> None:
    if importlib.util.find_spec("duckdb") is None:
        print("runtime smoke skipped: duckdb not installed")
        return

    csv_dir_rows = read_rows(
        UserRow,
        str(DATA / "csv"),
        "users",
        {"consented": True},
    )
    assert [row.email for row in csv_dir_rows] == [
        "bob@example.com",
        "ann@example.com",
    ]

    csv_file_rows = read_rows(
        UserRow,
        str(DATA / "csv" / "users.csv"),
        "users",
        {"consented": False},
    )
    assert [row.email for row in csv_file_rows] == ["carol@example.com"]

    parquet_rows = read_rows(
        UserRow,
        str(DATA / "parquet"),
        "users",
        {"consented": True},
    )
    assert len(parquet_rows) == 2

    duckdb_rows = read_rows(
        UserRow,
        str(DATA / "duckdb" / "users.duckdb"),
        "users",
        {"consented": True},
    )
    assert len(duckdb_rows) == 2

    try:
        read_rows(UserRow, str(DATA / "csv"), "no_such_table", {})
    except FileNotFoundError as exc:
        assert "no_such_table" in str(exc)
    else:
        raise AssertionError("missing table must raise, not return an empty result")

    written = str(DATA / "duckdb" / "writes.duckdb")
    create_table(UserRow, written, "users")
    insert_row(
        written,
        "users",
        UserRow(name="Fiction", email="fiction@example.test", consented=True),
    )
    assert [row.email for row in read_rows(UserRow, written, "users", {})] == [
        "fiction@example.test"
    ]

    print("runtime smoke passed")


if __name__ == "__main__":
    main()
