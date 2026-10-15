# ruff: noqa
# `is_allowed` can return False at runtime, so the guarded send is reachable.
import clauz3
from rtrusted import contracts
from rtrusted.effects import is_allowed, send_email


@clauz3.guarantee(contracts.none())
def main() -> None:
    if not is_allowed("x"):
        send_email("victim@example.com", "hi")
