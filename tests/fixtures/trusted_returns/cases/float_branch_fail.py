# ruff: noqa
# `read_score` can return any float at runtime, so the guarded send is reachable.
import clauz3
from rtrusted import contracts
from rtrusted.effects import read_score, send_email


@clauz3.guarantee(contracts.none())
def main() -> None:
    score = read_score("k")
    if score < 0.5:
        send_email("victim@example.com", "hi")
