# ruff: noqa
# The returned string is symbolic, but @deal.post pins it to bob's address.
import clauz3
from rtrusted import contracts
from rtrusted.effects import lookup_bob, send_email


@clauz3.guarantee(contracts.only_bob())
def main() -> None:
    send_email(lookup_bob("k"), "hi")
