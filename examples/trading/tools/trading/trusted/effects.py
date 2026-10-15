"""Trusted effects for a trading-desk agent.

This layer is the clauz3 side of a port of Dogwood's approval-gate policy
(see ``docs/examples/trading.md``). Dogwood permits ``SellShares`` only when a
matching ``ApproveSale`` *response* is found in the last hour of event history.
Here there is no event history: the agent may read the approval record and may
place orders, and the gate is a contract pinning every order's arguments to a
row of that record.

Neither capability is withheld to make the agent safe. ``list_approvals`` can
read any desk's approvals with any filter, and ``sell_shares`` can sell any
stock in any quantity. Safety comes from the contracts in ``contracts.py``.
"""

from tools.trading.trusted.schemas import ApprovalRow

import clauz3


@clauz3.trusted
@clauz3.ensures(lambda result: len(result) <= 50)
def list_approvals(desk: str, where: dict[str, object]) -> list[ApprovalRow]:
    """MOCK: return the desk's sale approvals, filtered by ``where``.

    The prover never runs this body. It records a ``list_approvals`` fact whose
    fields are ``desk`` and ``where``, and materializes a symbolic
    ``list[ApprovalRow]`` so a for-loop over the result can be reasoned about.
    The ``@deal.post`` length cap is what bounds that loop.
    """
    return []


@clauz3.trusted
def sell_shares(stock: str, shares: int) -> None:
    """MOCK: place a sell order.

    Real capability, deliberately unrestricted at the function level. *Which*
    stock and *how many* shares are contract questions, answered by
    ``stock_from`` and ``shares_from``.
    """
    pass
