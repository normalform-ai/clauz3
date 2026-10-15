"""Contracts for quantified relation tests."""

from typing import Any

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
def fewer_than(n: int) -> ContractSpec:
    """Guarantee strictly fewer than n emails are sent."""
    return effect("send_email").count() < n


@contract
def at_least(n: int) -> ContractSpec:
    """Guarantee at least n emails are sent."""
    return effect("send_email").count() >= n


@contract
def more_than(n: int) -> ContractSpec:
    """Guarantee strictly more than n emails are sent."""
    return effect("send_email").count() > n


@contract
def greeting_at_most(n: int) -> ContractSpec:
    """Guarantee at most n emails whose message is exactly "hi"."""
    return effect("send_email").where(lambda e: e.msg == "hi").count() <= n


@contract
def unit_cost_at_most(limit: int) -> ContractSpec:
    """Guarantee a fixed per-email cost of 2 sums to at most `limit`."""
    return effect("send_email").sum(lambda e: 2) <= limit


@contract
def unit_cost_at_least(limit: int) -> ContractSpec:
    """Guarantee a fixed per-email cost of 2 sums to at least `limit`."""
    return effect("send_email").sum(lambda e: 2) >= limit


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


@contract
def bob_gets_x() -> ContractSpec:
    """Bob only ever receives message 'x'.

    Deliberately written on one line and held there with ``fmt: off``: the
    #125 regression tests only exercise the lambda-disambiguation path when
    both lambdas share a physical line.
    """
    # fmt: off
    return Emails.where(lambda e: e.addr == "bob@example.com").all(lambda e: e.msg == "x")  # noqa: E501
    # fmt: on


@contract
def unique_bulk_recipients() -> ContractSpec:
    """No two bulk sends target the same primary address."""
    return effect("send_bulk").distinct(lambda e: e.addr)


@contract
def unique_bulk_any_recipients() -> ContractSpec:
    """No two send_bulk_any calls target the same primary address."""
    return effect("send_bulk_any").distinct(lambda e: e.addr)


class _RejectedEncodingSpec(ContractSpec):
    """A spec whose encoding the solver rejects (test fixture for #133)."""

    def solve(self, *, ctx: Any) -> Any:
        import z3

        raise z3.Z3Exception("synthetic sort mismatch")


@contract
def rejected_encoding() -> ContractSpec:
    """Contract whose Z3 encoding is rejected; must surface as SKIP."""
    return _RejectedEncodingSpec()


class _FaultyEncodingSpec(ContractSpec):
    """A spec whose solve raises an ordinary exception (test fixture for #133)."""

    def solve(self, *, ctx: Any) -> Any:
        raise ValueError("synthetic encoding fault")


@contract
def faulty_encoding() -> ContractSpec:
    """Contract whose solve raises; must surface as SKIP, not a traceback."""
    return _FaultyEncodingSpec()


@contract
def bob_at_most(n: int) -> ContractSpec:
    """Bound emails to Bob, including addresses selected from a query row."""
    return (
        effect("send_email").where(lambda e: e.addr == "bob@example.com").count() <= n
    )
