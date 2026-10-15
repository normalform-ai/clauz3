"""Approval-gate contracts for the trading-desk example.

These are the clauz3 rendering of Dogwood's approval-gate policy:

    permit ( principal, action == AgentCore::Action::"SellShares", resource )
    when temporal {
        formerly within 1h AgentCore::Action::"ApproveSale"::response{
            input.stock:     context.input.stock,
            input.shares:    context.input.shares,
            output.approved: true
        }
    };

Dogwood asks whether a matching approval *event* occurred recently.
These contracts ask a question with no time in it: does every order's
argument provably *come from* an approved row?

- ``approvals_only_where`` constrains the **source**: every approval query must
  carry a specific filter, which is where ``output.approved: true`` lands.
- ``stock_from`` and ``shares_from`` constrain the **data flow**: the values
  handed to ``sell_shares`` must symbolically be columns of a queried approval
  row -- not literals the agent typed, and not a quantity of its own choosing.

  These are two independent clauses, and each pins its argument to *some*
  approved row rather than to a common one. Column-ref equality is a
  structural match on ``<field>(Select(<approval array>, ?))`` that ignores the
  index, so ``sell_shares(row.stock, rows[0].shares)`` satisfies both while
  mixing rows. That is weaker than Dogwood's
  ``formerly ... {input.stock: ..., input.shares: ...}``, which matches one
  event. Row-correlated binding needs row identity the prover does not carry
  today; see ``docs/examples/trading.md``.

What has no rendering here is ``within 1h``. See ``docs/examples/trading.md``.
"""

from tools.trading.trusted.effects import list_approvals, sell_shares

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Approvals = effect(list_approvals)
Sell = effect(sell_shares)


@contract
def approvals_only_where(filter_dict: dict[str, object]) -> ContractSpec:
    """Guarantee every ``list_approvals`` query uses exactly this filter.

    Source provenance: it fixes which approval rows may enter the plan at all.
    A query that omits ``{"approved": True}`` reads rejected requests too.
    """
    return Approvals.all(lambda e: e.where == filter_dict)


@contract
def approvals_only_desk(desk: str) -> ContractSpec:
    """Guarantee every ``list_approvals`` query reads this desk's record.

    The quoted Dogwood policy does not scope the desk either, but unlike the
    holes in the walk-through this one is closable with an ordinary provenance
    clause, so it is closed rather than caveated.
    """
    return Approvals.all(lambda e: e.desk == desk)


@contract
def stock_from(schema: type, field: str) -> ContractSpec:
    """Guarantee every sold stock is ``schema``.``field`` of a queried approval."""
    column = getattr(schema, field)
    return Sell.all(lambda e: e.stock == column)


@contract
def shares_from(schema: type, field: str) -> ContractSpec:
    """Guarantee every sold quantity is ``schema``.``field`` of a queried approval.

    This is the half that catches "approved to sell 10, sold 500". Pinning the
    stock alone is not the policy Dogwood writes.
    """
    column = getattr(schema, field)
    return Sell.all(lambda e: e.shares == column)


@contract
def orders_at_most(n: int) -> ContractSpec:
    """Guarantee at most n sell orders are placed."""
    return Sell.count() <= n


@contract
def none() -> ContractSpec:
    """Guarantee no sell order is placed."""
    return Sell.empty()


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about trading effects."""
    return core_no_guarantees()
