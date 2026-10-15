# db

A trusted `clauz3` layer for read-only tabular database access. It is intended
for agents that need to loop over rows from CSV, Parquet, DuckDB, or a folder of
CSV/Parquet tables, while proving which source path and table they will read.

Unlike the layers under `examples/`, this effect is **real**, not a mock. The
prover never runs the body — it records each call as a fact, materializes a
symbolic `list[RowT]`, and proves the contract. The body runs only under
`clauz3 run`, after a program is proved and approved.

## Install

```bash
clauz3 install stdlib:db
```

The runtime body uses DuckDB. Install `duckdb` in the environment that runs
proved programs. Proof-only tests do not require DuckDB because effect bodies
are skipped by the prover.

## Effects

Import with `from tools.db.trusted.effects import read_rows`:

- `read_rows(schema, path, table, where) -> list[RowT]` — read up to 100 rows
  from `path` and instantiate each row as `schema`. `schema` must be a
  `clauz3.Row` subclass. `path` may be a DuckDB database file, a CSV file, a
  Parquet file, or a directory containing `<table>.csv` / `<table>.parquet`.
  For a single CSV or Parquet file, `table` must match the file stem. CSV and
  Parquet inputs are queried through DuckDB in memory.

The schema argument is the type-system hook. The function is generic at runtime,
but the prover uses the concrete `Row` subclass passed at the call site to type
`row.email`, `row.id`, and other field accesses inside a loop.

## Contracts

Import with `from tools.db.trusted import contracts as db`:

- `db.no_guarantees()` — explicit null contract.
- `db.no_db_reads()` — no database reads occur.
- `db.reads_at_most(count)` — at most `count` database reads occur.
- `db.only_read_under(root)` — every database source path starts with `root`.
- `db.never_read_under(prefix)` — no database source path starts with `prefix`.
- `db.only_table(table)` — every read targets exactly `table`.
- `db.only_tables(tables)` — every read targets one of `tables`.
- `db.only_where(filter_dict)` — every read uses exactly `filter_dict`.

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

This first stdlib version is read-only. A write surface should be separate and
explicit about table, path, and schema because the proof system needs concrete
row shapes to type loop variables safely.

## Tests

```bash
clauz3 test stdlib:db
```

`tests/cases/*_pass.py` must prove; `tests/cases/*_fail.py` must not. The
Justfile also recreates `tests/runtime-data/` on each run. It always writes a
small CSV folder fixture and, when DuckDB is installed, also writes Parquet and
DuckDB fixtures before running a runtime smoke test. Without DuckDB, the smoke
test skips after fixture setup.
