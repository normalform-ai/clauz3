# Create a DuckDB table with fictional transactions

Requires the bundled `db` library and the optional DuckDB runtime package
(`pip install 'clauz3[db]'`). Adapt the database path, table name and fictional
values to the user's request. The parent folder must exist. The program creates
one table and inserts two rows; it never replaces an existing table or deletes
rows. Each call commits independently; a later error can leave earlier work in
place, so report the run result before claiming success or retrying.

```python
import clauz3
from tools.db.trusted import contracts as db
from tools.db.trusted.effects import create_table, insert_row

class Transaction(clauz3.Row):
    id: int
    merchant: str
    amount_cents: int
    fictional: bool

@clauz3.guarantee(db.only_writes_databases([clauz3.FilePath("./data/transactions.duckdb")]))
@clauz3.guarantee(db.only_writes_tables(["transactions"]))
@clauz3.guarantee(db.writes_at_most(3))
@clauz3.guarantee(db.rows_inserted_at_most(2))
@clauz3.guarantee(db.no_db_reads())
def main() -> None:
    create_table(Transaction, "./data/transactions.duckdb", "transactions")
    insert_row("./data/transactions.duckdb", "transactions", Transaction(
        id=1, merchant="Fictional Coffee Shop", amount_cents=450, fictional=True,
    ))
    insert_row("./data/transactions.duckdb", "transactions", Transaction(
        id=2, merchant="Imaginary Bookstore", amount_cents=1800, fictional=True,
    ))
    print("Created transactions.duckdb with two fictional transactions.")
```

Use `str`, `int` and `bool` fields. Amounts above are integer cents; dates can
be ISO-formatted strings. Row constructors require every declared field as a
keyword with the declared type. Pass rows individually to `insert_row`;
constructing lists of Row values is not supported yet. To copy existing query
results, iterate `read_rows` and pass each row to `insert_row` with appropriate
read and write contracts.

Database writes use quoted identifiers and parameter-bound values, with no
arbitrary SQL input. The destination guarantee includes DuckDB's adjacent
`.wal` journal. Temporary spill files and external access on write connections
are disabled. Paths remain lexical, without symlink confinement. Filesystem
contracts do not govern database writes; use `db` contracts.
