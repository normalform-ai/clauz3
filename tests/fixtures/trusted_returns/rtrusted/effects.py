"""Trusted effects whose return values the prover has to model.

Every body here is a mock; the prover never runs one. What matters is the
return annotation, because that is all the solver has to go on when it
materializes the value a caller branches on.
"""

import deal


class Session:
    """An opaque handle: the solver has no sort that can represent it."""


@deal.has("email")
def send_email(addr: str, msg: str) -> None:
    """Trusted email sender."""
    pass


@deal.has("check")
def is_allowed(addr: str) -> bool:
    """Trusted policy check. Returns False for some addresses at runtime."""
    return True


@deal.has("lookup")
def lookup_name(key: str) -> str:
    """Trusted lookup. Returns "" for a missing key at runtime."""
    return ""


@deal.has("lookup")
@deal.post(lambda result: result == "bob@example.com")
def lookup_bob(key: str) -> str:
    """Trusted lookup whose postcondition pins the address it returns."""
    return "bob@example.com"


@deal.has("meter")
def read_score(key: str) -> float:
    """Trusted score reader. Returns any float at runtime."""
    return 0.0


@deal.has("session")
def open_session(user: str) -> Session:
    """Trusted call whose return type the solver cannot model."""
    return Session()
