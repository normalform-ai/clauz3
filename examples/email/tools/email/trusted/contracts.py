"""Email-specific predicates built from generic trusted effect facts."""

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Email = effect("send_email")


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


@contract
def recipient_at_most(addr: str, count: int) -> ContractSpec:
    """Guarantee that addr is emailed at most count times."""
    return Email.where(lambda e: e.addr == addr).count() <= count


@contract
def content_length_at_most(max_chars: int) -> ContractSpec:
    """Guarantee that no sent email content is longer than max_chars."""
    return Email.all(lambda e: len(e.msg) <= max_chars)


@contract
def same_content(left_addr: str, right_addr: str) -> ContractSpec:
    """Guarantee that both addresses receive at least one identical message."""
    left_emails = Email.where(lambda e: e.addr == left_addr)
    right_emails = Email.where(lambda e: e.addr == right_addr)
    return left_emails.shares_value(right_emails, lambda e: e.msg)
