# db

A trusted `clauz3` layer for tabular reads and structured DuckDB writes. Read
rows from CSV, Parquet, DuckDB, or a folder of CSV/Parquet tables. Create DuckDB
tables and insert typed rows, with separate read and write guarantees.

Unlike the layers under `examples/`, these effects are **real**, not mocks. The
prover never runs the body — it records each call as a fact, materializes a
symbolic `list[RowT]`, and proves the contract. The body runs only under
`clauz3 run`, after a program is proved and approved.

## Install

```bash
clauz3 install stdlib:db
```

The runtime bodies use DuckDB. Install `clauz3[db]` (or `duckdb>=1.4,<2`) in the
environment that runs proved programs. Proof-only tests do not require DuckDB because effect bodies
are skipped by the prover.

## Effects

Import effects from `tools.db.trusted.effects`:

- `read_rows(schema, path, table, where) -> list[RowT]` — read up to 100 rows
  from `path` and instantiate each row as `schema`. `schema` must be a
  `clauz3.Row` subclass. `path` may be a DuckDB database file, a CSV file, a
  Parquet file, or a directory containing `<table>.csv` / `<table>.parquet`.
  For a single CSV or Parquet file, `table` must match the file stem. CSV and
  Parquet inputs are queried through DuckDB in memory.

- `create_table(schema, path, table) -> None` — create a new table with columns
  from a `clauz3.Row` schema; create the DuckDB file if needed. Fields are
  `str`/`int`/`bool` (VARCHAR/BIGINT/BOOLEAN), all NOT NULL. Existing tables
  cause an error and are never replaced.
- `insert_row(path, table, row) -> None` — append one typed Row value. The file
  and table must already exist. Each call commits atomically and independently;
  a later failure does not roll back earlier successful calls.

Writes support `.duckdb`, `.ddb` and `.db` files with existing parent folders.
There is no arbitrary SQL API: identifiers are quoted and values are bound
parameters. Write connections disable external access, extension loading and
temporary spill files. DuckDB may write an adjacent `.wal` journal. Paths are
lexical, without tilde expansion or physical symlink confinement.

The schema argument is the type-system hook. The function is generic at runtime,
but the prover uses the concrete `Row` subclass passed at the call site to type
`row.email`, `row.id`, and other field accesses inside a loop.

## Contracts

Import with `from tools.db.trusted import contracts as db`:

- `db.no_guarantees()` — explicit null contract.
- `db.no_db_reads()` — no `read_rows` calls; writes are governed separately.
- `db.reads_at_most(count)` — at most `count` database reads occur.
- `db.only_read_under(root)` — every database source path starts with `root`.
- `db.never_read_under(prefix)` — no database source path starts with `prefix`.
- `db.only_table(table)` — every read targets exactly `table`.
- `db.only_tables(tables)` — every read targets one of `tables`.
- `db.only_where(filter_dict)` — every read uses exactly `filter_dict`.

Write contracts:

- `db.no_db_writes()` — no table creation or insertion.
- `db.only_writes_databases(files: list[FilePath])` — exact destination database
  file names, including their adjacent journals. Import `FilePath` from
  `clauz3`. Use the same list for one or many databases.
- `db.only_writes_tables(tables: list[str])` — creation and insertion target only
  these table names. Existing `only_table` / `only_tables` govern reads only.
- `db.writes_at_most(count)` — maximum combined number of create/insert calls.
- `db.rows_inserted_at_most(count)` — maximum number of inserted rows.

Database reads are governed by this layer's own path policies
(`only_read_under`, `never_read_under`). They were once covered by
`filesystem.only_read_under` as well, through a shared `read` marker; an
effect is addressed by name now, so name `db.only_read_under` for database
sources and `filesystem.only_read_under` for file reads.

## Example

```python
import clauz3
from tools.db.trusted import contracts as db
from tools.db.trusted.effects import read_rows


class UserRow(clauz3.Row):
    name: str
    email: str
    consented: bool


@clauz3.guarantee(db.only_read_under("/repo/data"))
@clauz3.guarantee(db.only_table("users"))
@clauz3.guarantee(db.reads_at_most(1))
def main() -> None:
    for row in read_rows(
        UserRow,
        "/repo/data",
        "users",
        {"consented": True},
    ):
        _ = row.email
```

## Creating a database

Sessions offer the tested `create-duckdb` recipe when the installed `db` library
matches the bundled version. It defines a Transaction row, creates a table and
inserts fictional transactions. Use integer cents for amounts and strings for
ISO dates; Row fields currently support str, int and bool.

Construct a row with all fields as keywords, then pass it to `insert_row`.
Lists of constructed Row values are not supported by the prover yet. To copy
query results, iterate `read_rows` and insert each returned row individually.
Write scope is independent of read scope, and filesystem contracts do not
cover database writes. There are no update, delete, drop, or SQL execution
effects in this interface.

After updating clauz3, refresh an unmodified installed library with
`clauz3 install --force --skills stdlib:db` and restart the running session.
Preserve local library customizations before using `--force`.

## Tests

```bash
clauz3 test stdlib:db
```

`tests/cases/*_pass.py` must prove; `tests/cases/*_fail.py` must not. The
Justfile also recreates `tests/runtime-data/` on each run. It always writes a
small CSV folder fixture and, when DuckDB is installed, also writes Parquet and
DuckDB fixtures before running a runtime smoke test. Without DuckDB, the smoke
test skips after fixture setup.
