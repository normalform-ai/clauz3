# ruff: noqa: F821
#
# Issue #123. Literal sequences and `range(N)` are unrolled, not quantified:
# concrete iteration re-evaluates the body per element, so loop-carried state
# is modelled exactly there and must keep working. 1 + 2 + 3 = 6 <= 10.
from qtrusted import contracts
from qtrusted.effects import charge

import clauz3


@clauz3.guarantee(contracts.total_amount_under(10))
def main() -> None:
    total = 0
    for step in [1, 2, 3]:
        total = total + step
    charge(total)
