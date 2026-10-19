"""Small tutorial subset of examples/email, maintained and tested independently."""

from tools.email.trusted.effects import send_email

from clauz3.spec import ContractSpec, contract, effect

Email = effect(send_email)


@contract(description="Every simulated email targets one of {addresses}.")
def only(addresses: list[str]) -> ContractSpec:
    """Every simulated email targets one of these addresses."""
    return Email.all(lambda e: e.addr in addresses)


@contract(description="At most {count} simulated emails may be sent.")
def at_most(count: int) -> ContractSpec:
    """Send at most this many simulated emails."""
    return Email.count() <= count


@contract(description="Each address receives at most one simulated email.")
def unique_recipients() -> ContractSpec:
    """Never simulate sending to the same address twice."""
    return Email.distinct(lambda e: e.addr)
