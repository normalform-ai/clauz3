# ruff: noqa
# `lookup_name` can return "" at runtime, so the guarded send is reachable.
import clauz3
from rtrusted import contracts
from rtrusted.effects import lookup_name, send_email


@clauz3.guarantee(contracts.none())
def main() -> None:
    name = lookup_name("k")
    if name == "":
        send_email("victim@example.com", "hi")
