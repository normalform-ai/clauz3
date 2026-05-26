"""Contracts for quantified relation tests."""

from clauz3.spec import ContractSpec, contract, effect

Emails = effect("send_email")


@contract
def no_emails() -> ContractSpec:
    """Guarantee that no email is sent."""
    return Emails.empty()


@contract
def all_trivial() -> ContractSpec:
    """Guarantee: all emails satisfy trivially-true predicate."""
    return Emails.all(lambda e: True)


@contract
def at_most(n: int) -> ContractSpec:
    """Guarantee at most n emails are sent."""
    return effect("send_email").count() <= n


@contract
def total_amount_under(limit: int) -> ContractSpec:
    """Guarantee sum of `amount` is under limit. Used for sum-over-loop tests."""
    return effect("charge").sum(lambda e: e.amount) <= limit


@contract
def unique_recipients() -> ContractSpec:
    """Guarantee no recipient is emailed twice."""
    return effect("send_email").distinct(lambda e: e.addr)


@contract
def same_content_two_bobs() -> ContractSpec:
    """Two paths to bob receive identical messages — uses shares_value."""
    Email = effect("send_email")
    left = Email.where(lambda e: e.addr == "bob@a")
    right = Email.where(lambda e: e.addr == "bob@b")
    return left.shares_value(right, lambda e: e.msg)


@contract
def addresses_from(schema: type, field: str) -> ContractSpec:
    """Guarantee every email recipient came from `schema`.`field`."""
    column = getattr(schema, field)
    return effect("send_email").all(lambda e: e.addr == column)
