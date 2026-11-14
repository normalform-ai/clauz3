"""Database read and write policies over this library's structured calls."""

from tools.db.trusted.effects import create_table, insert_row, read_rows

from clauz3 import FilePath
from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

DbRead = effect(read_rows)
DbWrite = effect(create_table, insert_row)
DbInsert = effect(insert_row)


@contract(description="No restrictions are imposed on database tools.")
def no_guarantees() -> ContractSpec:
    """Make no guarantees about database tools."""
    return core_no_guarantees()


@contract(
    description=(
        "Database tools read no rows; creating tables and adding rows are not covered."
    )
)
def no_db_reads() -> ContractSpec:
    """Forbid read_rows calls; creation and insertion are governed separately."""
    return DbRead.empty()


@contract(description="Database tools read rows at most {count} times.")
def reads_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` read_rows calls occur (each returns <= 100 rows)."""
    return DbRead.count() <= count


@contract(
    description=(
        "Database tools read rows only from paths starting with {root}; "
        "creating tables and adding rows are not covered."
    )
)
def only_read_under(root: str) -> ContractSpec:
    """Guarantee every database read uses a source path starting with ``root``.

    A literal prefix check, not resolved directory containment.
    """
    return DbRead.all(lambda e: e.path.startswith(root))


@contract(
    description=(
        "Database tools never read rows from a path starting with {prefix}; "
        "creating tables and adding rows are not covered."
    )
)
def never_read_under(prefix: str) -> ContractSpec:
    """Guarantee no database read uses a source path starting with ``prefix``."""
    return DbRead.all(lambda e: not e.path.startswith(prefix))


@contract(
    description=(
        "Database tools read rows only from the table {table}; creating tables "
        "and adding rows are not covered."
    )
)
def only_table(table: str) -> ContractSpec:
    """Guarantee every database read targets exactly ``table``.

    Compares the table name only; the database path is not constrained.
    """
    return DbRead.all(lambda e: e.table == table)


@contract(
    description=(
        "Database tools read rows only from these tables: {tables}; creating "
        "tables and adding rows are not covered."
    )
)
def only_tables(tables: list[str]) -> ContractSpec:
    """Guarantee every database read targets one of ``tables``.

    Compares the table name only; the database path is not constrained.
    """
    return DbRead.all(lambda e: e.table in tables)


@contract
def only_where(filter_dict: dict[str, object]) -> ContractSpec:
    """Guarantee every database read uses exactly ``filter_dict``."""
    return DbRead.all(lambda e: e.where == filter_dict)


@contract(description="No database tool creates tables or inserts rows.")
def no_db_writes() -> ContractSpec:
    """Forbid database write calls; reads are allowed."""
    return DbWrite.empty()


@contract(
    description=(
        "Database tools write only to databases {files} and their adjacent "
        ".wal journals (paths compared lexically)."
    )
)
def only_writes_databases(files: list[FilePath]) -> ContractSpec:
    """Restrict table creation and insertion to these exact database files.

    One or many FilePath literals; an empty list forbids all write calls.
    Leading ./ is optional for relative paths. DuckDB journal writes are
    included; temporary spill files are disabled. No symlink confinement is
    implied, and filesystem or other libraries' writes are not covered.
    """
    if type(files) is not list or any(type(path) is not FilePath for path in files):
        raise TypeError("files must be a list of FilePath literals")
    names = []
    for path in files:
        bare = path.value.removeprefix("./")
        names.append(bare)
        if not bare.startswith("/"):
            names.append("./" + bare)
    return DbWrite.all(lambda e: e.path in names)


@contract(
    description="Database tools create or insert into only these tables: {tables}."
)
def only_writes_tables(tables: list[str]) -> ContractSpec:
    """Restrict write targets to these table names; read contracts are separate."""
    return DbWrite.all(lambda e: e.table in tables)


@contract(description="Database tools make at most {count} create or insert calls.")
def writes_at_most(count: int) -> ContractSpec:
    """Limit the combined number of create_table and insert_row calls."""
    return DbWrite.count() <= count


@contract(description="Database tools insert at most {count} rows in total.")
def rows_inserted_at_most(count: int) -> ContractSpec:
    """Limit the number of rows appended by insert_row calls."""
    return DbInsert.count() <= count
