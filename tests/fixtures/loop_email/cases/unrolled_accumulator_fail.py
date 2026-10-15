# ruff: noqa: F821
#
# Issue #123. The same unrolled accumulator against a bound it exceeds: the
# unrolled path still reports a real FAIL, not a skip, so the fix did not
# quietly turn concrete loops into refusals too.
from qtrusted import contracts
from qtrusted.effects import charge

import clauz3


@clauz3.guarantee(contracts.total_amount_under(5))
def main() -> None:
    total = 0
    for step in [1, 2, 3]:
        total = total + step
    charge(total)
