"""Database read and write policies over this library's structured calls."""

from tools.db.trusted.effects import create_table, insert_row, read_rows

from clauz3 import FilePath
from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

DbRead = effect(read_rows)
DbWrite = effect(create_table, insert_row)
DbInsert = effect(insert_row)


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about database effects."""
    return core_no_guarantees()


@contract
def no_db_reads() -> ContractSpec:
    """Forbid read_rows calls; creation and insertion are governed separately."""
    return DbRead.empty()


@contract
def reads_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` database reads occur."""
    return DbRead.count() <= count


@contract
def only_read_under(root: str) -> ContractSpec:
    """Guarantee every database source path starts with ``root``."""
    return DbRead.all(lambda e: e.path.startswith(root))


@contract
def never_read_under(prefix: str) -> ContractSpec:
    """Guarantee no database source path starts with ``prefix``."""
    return DbRead.all(lambda e: not e.path.startswith(prefix))


@contract
def only_table(table: str) -> ContractSpec:
    """Guarantee every database read targets exactly ``table``."""
    return DbRead.all(lambda e: e.table == table)


@contract
def only_tables(tables: list[str]) -> ContractSpec:
    """Guarantee every database read targets one of ``tables``."""
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
