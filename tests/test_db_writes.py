"""Structured DuckDB writes preserve data and obey the declared destinations."""

import importlib
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import deal
import duckdb
import pytest

import clauz3
from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import _selected_layer_context, prove_text

LIBRARY = Path(__file__).resolve().parents[1] / "src/clauz3/stdlib/db"
ROOTS = [LIBRARY / "tools/db/trusted"]


class Transaction(clauz3.Row):
    id: int
    merchant: str
    amount_cents: int
    fictional: bool


@pytest.fixture
def db_effects() -> Iterator[ModuleType]:
    with _selected_layer_context(ROOTS, [LIBRARY]):
        yield importlib.import_module("tools.db.trusted.effects")


def test_create_insert_read_and_preserve_existing_data(
    db_effects: ModuleType, tmp_path: Path
) -> None:
    path = str(tmp_path / "transactions.duckdb")
    db_effects.create_table(Transaction, path, "transactions")
    text = "Coffee'); DROP TABLE transactions; --"
    db_effects.insert_row(
        path,
        "transactions",
        Transaction(id=1, merchant=text, amount_cents=450, fictional=True),
    )
    db_effects.insert_row(
        path,
        "transactions",
        Transaction(
            id=2, merchant="Fictional Books", amount_cents=-200, fictional=False
        ),
    )
    rows = db_effects.read_rows(Transaction, path, "transactions", {})
    assert [(r.id, r.merchant, r.amount_cents, r.fictional) for r in rows] == [
        (1, text, 450, True),
        (2, "Fictional Books", -200, False),
    ]
    with pytest.raises(duckdb.CatalogException):
        db_effects.create_table(Transaction, path, "transactions")
    assert len(db_effects.read_rows(Transaction, path, "transactions", {})) == 2


def test_identifiers_are_quoted(db_effects: ModuleType, tmp_path: Path) -> None:
    path = str(tmp_path / "quoted.duckdb")
    db_effects.create_table(Transaction, path, "keep")
    name = 'other"; DROP TABLE keep; --'
    db_effects.create_table(Transaction, path, name)
    db_effects.insert_row(
        path,
        name,
        Transaction(id=1, merchant="fiction", amount_cents=1, fictional=True),
    )
    assert len(db_effects.read_rows(Transaction, path, name, {})) == 1
    assert db_effects.read_rows(Transaction, path, "keep", {}) == []


def test_bad_values_fail_before_writing(db_effects: ModuleType, tmp_path: Path) -> None:
    path = str(tmp_path / "bad.duckdb")
    db_effects.create_table(Transaction, path, "transactions")
    for value in (True, "1", 1.5, None):
        with pytest.raises(TypeError, match="column 'id'"):
            db_effects.insert_row(
                path,
                "transactions",
                Transaction(
                    id=value, merchant="fiction", amount_cents=1, fictional=True
                ),
            )
    assert db_effects.read_rows(Transaction, path, "transactions", {}) == []


def test_failed_insert_is_atomic(db_effects: ModuleType, tmp_path: Path) -> None:
    path = str(tmp_path / "atomic.duckdb")
    with duckdb.connect(path) as con:
        con.execute(
            "CREATE TABLE transactions (id BIGINT PRIMARY KEY, merchant VARCHAR, "
            "amount_cents BIGINT, fictional BOOLEAN)"
        )
    row = Transaction(id=1, merchant="fiction", amount_cents=5, fictional=True)
    db_effects.insert_row(path, "transactions", row)
    with pytest.raises(duckdb.ConstraintException):
        db_effects.insert_row(path, "transactions", row)
    assert len(db_effects.read_rows(Transaction, path, "transactions", {})) == 1


def test_missing_database_and_non_database_paths_refused(
    db_effects: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "missing.duckdb"
    with pytest.raises(FileNotFoundError):
        db_effects.insert_row(
            str(path),
            "transactions",
            Transaction(id=1, merchant="fiction", amount_cents=1, fictional=True),
        )
    assert not path.exists()
    for name in ("data.csv", "data.parquet", ":memory:", "bad//data.db"):
        with pytest.raises((TypeError, ValueError, deal.PreContractError)):
            db_effects.create_table(
                Transaction,
                str(tmp_path) + "/" + name if name != ":memory:" else name,
                "transactions",
            )
    assert not (tmp_path / "data.csv").exists()


def test_write_connections_disable_external_access(
    db_effects: ModuleType, tmp_path: Path
) -> None:
    csv = tmp_path / "secret.csv"
    csv.write_text("secret\nvalue\n")
    con = db_effects._write_connection(str(tmp_path / "limited.duckdb"))
    try:
        with pytest.raises(duckdb.PermissionException):
            con.execute("SELECT * FROM read_csv_auto(?)", [str(csv)])
        with pytest.raises(duckdb.PermissionException):
            con.execute("COPY (SELECT 1) TO ?", [str(tmp_path / "out.csv")])
        assert con.execute("SELECT current_setting('temp_directory')").fetchone() == (
            "",
        )
    finally:
        con.close()
    assert not (tmp_path / "out.csv").exists()


PREAMBLE = """import clauz3
from tools.db.trusted import contracts as db
from tools.db.trusted.effects import create_table, insert_row
class Transaction(clauz3.Row):
    id: int
    merchant: str
    amount_cents: int
    fictional: bool
"""
ROW = 'Transaction(id=1, merchant="Fictional Coffee", amount_cents=450, fictional=True)'


@pytest.mark.parametrize(
    "guarantee,body,allowed",
    [
        (
            'only_writes_databases([clauz3.FilePath("data.duckdb")])',
            'create_table(Transaction, "./data.duckdb", "transactions")',
            True,
        ),
        (
            'only_writes_databases([clauz3.FilePath("data.duckdb")])',
            'create_table(Transaction, "data.duckdb.bak", "transactions")',
            False,
        ),
        (
            'only_writes_databases([clauz3.FilePath("data.duckdb")])',
            f'insert_row("elsewhere.duckdb", "transactions", {ROW})',
            False,
        ),
        (
            "only_writes_databases([])",
            f'insert_row("data.duckdb", "transactions", {ROW})',
            False,
        ),
        (
            'only_writes_tables(["transactions"])',
            f'insert_row("data.duckdb", "other", {ROW})',
            False,
        ),
        (
            "no_db_writes()",
            'create_table(Transaction, "data.duckdb", "transactions")',
            False,
        ),
        ("no_db_writes()", f'insert_row("data.duckdb", "transactions", {ROW})', False),
        (
            "writes_at_most(1)",
            'create_table(Transaction, "data.duckdb", "transactions")\n    '
            f'insert_row("data.duckdb", "transactions", {ROW})',
            False,
        ),
        (
            "rows_inserted_at_most(1)",
            'create_table(Transaction, "data.duckdb", "transactions")\n    '
            f'insert_row("data.duckdb", "transactions", {ROW})',
            True,
        ),
        (
            "rows_inserted_at_most(1)",
            f'insert_row("data.duckdb", "transactions", {ROW})\n    '
            f'insert_row("data.duckdb", "transactions", {ROW})',
            False,
        ),
        ("no_db_reads()", f'insert_row("data.duckdb", "transactions", {ROW})', True),
    ],
)
def test_write_guarantees(guarantee: str, body: str, allowed: bool) -> None:
    source = (
        PREAMBLE
        + f"@clauz3.guarantee(db.{guarantee})\ndef main() -> None:\n    {body}\n"
    )
    results = prove_text(source, trusted_roots=ROOTS, import_paths=[LIBRARY])
    assert results[0].proof.conclusion is (
        Conclusion.OK if allowed else Conclusion.FAIL
    ), results[0].proof


def test_query_rows_can_be_copied_with_a_proved_count_bound() -> None:
    source = (
        PREAMBLE
        + """from tools.db.trusted.effects import read_rows
@clauz3.guarantee(db.only_writes_databases([clauz3.FilePath("dest.duckdb")]))
@clauz3.guarantee(db.rows_inserted_at_most(100))
def main() -> None:
    for row in read_rows(Transaction, "source.duckdb", "transactions", {}):
        insert_row("dest.duckdb", "transactions", row)
"""
    )
    results = prove_text(source, trusted_roots=ROOTS, import_paths=[LIBRARY])
    assert results[0].proof.conclusion is Conclusion.OK, results[0].proof
