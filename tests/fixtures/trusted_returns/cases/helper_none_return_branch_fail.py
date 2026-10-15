# ruff: noqa
# An untrusted helper with no return statement evaluates to None, so the
# guarded send is reachable at runtime.
import clauz3
from rtrusted import contracts
from rtrusted.effects import send_email


def note(msg: str) -> None:
    """Untrusted helper that returns None."""
    pass


@clauz3.guarantee(contracts.none())
def main() -> None:
    if not note("hi"):
        send_email("victim@example.com", "hi")
