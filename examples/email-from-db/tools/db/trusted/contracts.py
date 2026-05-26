"""Contract vocabulary for the email-from-db example.

Includes contracts for both the database query effects and the email effects.
"""

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Email = effect("send_email")
DB = effect("db_query")


# ── Email contracts ───────────────────────────────────────────────────────────


@contract
def addresses_from(schema: type, field: str) -> ContractSpec:
    """Guarantee every email recipient came from `schema`.`field`."""
    column = getattr(schema, field)
    return Email.all(lambda e: e.addr == column)


@contract
def count_at_most(n: int) -> ContractSpec:
    """Guarantee at most n emails are sent."""
    return Email.count() <= n


@contract
def only(addresses: list[str]) -> ContractSpec:
    """Guarantee that every sent email targets one of these addresses."""
    return Email.all(lambda e: e.addr in addresses)


@contract
def none() -> ContractSpec:
    """Guarantee that no email is sent."""
    return Email.empty()


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about email effects."""
    return core_no_guarantees()


@contract
def unique_recipients() -> ContractSpec:
    """Guarantee that no reachable execution emails the same address twice."""
    return Email.distinct(lambda e: e.addr)


@contract
def at_most(count: int) -> ContractSpec:
    """Guarantee that at most count emails are sent."""
    return Email.count() <= count


# ── Database contracts ────────────────────────────────────────────────────────


@contract
def only_table(table: str) -> ContractSpec:
    """Guarantee every db_query reads from `table` only."""
    return DB.all(lambda e: e.table == table)


@contract
def only_where(filter_dict: dict[str, object]) -> ContractSpec:
    """Guarantee every db_query uses exactly this `where` filter."""
    return DB.all(lambda e: e.where == filter_dict)
