# ruff: noqa
# `send_email` returns None, and `not None` is True at runtime, so the second
# send is reachable and breaks the guarantee.
import clauz3
from rtrusted import contracts
from rtrusted.effects import send_email


@clauz3.guarantee(contracts.only_bob())
def main() -> None:
    if not send_email("bob@example.com", "hi"):
        send_email("victim@example.com", "hi")
