"""Contracts for the same-name collision fixture."""

from clauz3.spec import ContractSpec, contract, effect

Emails = effect("send_email")


@contract
def addresses_from(schema: type, field: str) -> ContractSpec:
    """Guarantee every email recipient came from ``schema``.``field``."""
    column = getattr(schema, field)
    return Emails.all(lambda e: e.addr == column)
