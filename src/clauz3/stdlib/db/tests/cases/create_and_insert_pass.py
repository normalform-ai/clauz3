from tools.db.trusted import contracts as db
from tools.db.trusted.effects import create_table, insert_row

import clauz3


class Transaction(clauz3.Row):
    id: int
    merchant: str
    amount_cents: int


@clauz3.guarantee(
    db.only_writes_databases([clauz3.FilePath("/tmp/transactions.duckdb")])
)
@clauz3.guarantee(db.only_writes_tables(["transactions"]))
@clauz3.guarantee(db.writes_at_most(3))
@clauz3.guarantee(db.rows_inserted_at_most(2))
def main() -> None:
    create_table(Transaction, "/tmp/transactions.duckdb", "transactions")
    insert_row(
        "/tmp/transactions.duckdb",
        "transactions",
        Transaction(id=1, merchant="Fictional Coffee", amount_cents=450),
    )
    insert_row(
        "/tmp/transactions.duckdb",
        "transactions",
        Transaction(id=2, merchant="Fictional Books", amount_cents=1800),
    )
