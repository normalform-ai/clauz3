"""Contracts for the trusted-return-value tests."""

from clauz3.spec import ContractSpec, contract, effect

Emails = effect("send_email")


@contract
def none() -> ContractSpec:
    """Guarantee that no email is sent."""
    return Emails.empty()


@contract
def only_bob() -> ContractSpec:
    """Guarantee that every sent email targets bob@example.com."""
    return Emails.all(lambda e: e.addr == "bob@example.com")
