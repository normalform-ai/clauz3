# ruff: noqa
# Both branches are live, and both of them respect the guarantee.
import clauz3
from rtrusted import contracts
from rtrusted.effects import is_allowed, send_email


@clauz3.guarantee(contracts.only_bob())
def main() -> None:
    if is_allowed("bob@example.com"):
        send_email("bob@example.com", "hi")
    else:
        send_email("bob@example.com", "denied")
