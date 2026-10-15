# ruff: noqa
import clauz3
from tools.trading.trusted import contracts as trading
from tools.trading.trusted.effects import sell_shares


@clauz3.guarantee(trading.no_guarantees())
def main() -> None:
    # The explicit null contract. This sells an arbitrary stock in an arbitrary
    # quantity with no approval anywhere, and proves — because the agent is
    # openly asking for unconstrained trading authority rather than claiming a
    # gate it does not have. Weak, but not silent.
    sell_shares("ACME", 100)
