"""Database read policies built from trusted ``read_rows`` call facts."""

from tools.db.trusted.effects import read_rows

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

DbRead = effect(read_rows)


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about database-read effects."""
    return core_no_guarantees()


@contract
def no_db_reads() -> ContractSpec:
    """Guarantee the program performs no database reads."""
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
