# Trading example (a Dogwood approval gate, ported)

This example ports one scenario from
[Dogwood](../explanation/background.md#dogwood) — AWS's temporal extension to
[Cedar](../explanation/cedar.md) — into `clauz3`, to see what survives the
translation and what does not.

Dogwood's headline example is an approval gate on share sales:

```
permit ( principal, action == AgentCore::Action::"SellShares", resource )
when temporal {
    formerly within 1h AgentCore::Action::"ApproveSale"::response{
        input.stock:     context.input.stock,
        input.shares:    context.input.shares,
        output.approved: true
    }
};
```

Read it as four separate requirements, because they port very differently:

| Clause | What it demands | Ports to `clauz3`? |
| --- | --- | --- |
| `ApproveSale::response` exists | the sale is backed by an approval | yes — as provenance |
| `output.approved: true` | it was granted, not merely requested | yes — as a source filter |
| `input.stock` / `input.shares` match | the approval was *for this sale* | partly — as column binding, per argument but not per row |
| `within 1h` | the approval is fresh | **no** |

The first three are questions about *where a value came from*. `clauz3` answers
those from the program text alone. The fourth is a question about *when
something happened*, and there is nothing in a program to answer it with.

## The trusted module

Two capabilities, neither of them starved. The agent may read the desk's
approval record with any filter, and may sell any stock in any quantity.

{{ include_file("examples/trading/tools/trading/trusted/effects.py") }}

`list_approvals`'s `@clauz3.ensures` length cap is what lets the prover bound the
loop over its result. The schema's fields become the row shape the prover binds
against — note that rows exist for *rejected* requests too, which is what makes
the query filter load-bearing rather than decorative:

{{ include_file("examples/trading/tools/trading/trusted/schemas.py") }}

The contracts are the gate. `approvals_only_desk` and `approvals_only_where` pin
the source, `stock_from` and `shares_from` pin the data flow:

{{ include_file("examples/trading/tools/trading/trusted/contracts.py") }}

## The passing cases

The agent queries its own desk's approved sales and places exactly the approved
order for each one. All five guarantees discharge on every branch, before any
order is placed:

{{ include_file("examples/trading/cases/approved_sale_pass.py") }}

Two more programs prove, for different reasons. `no_sales_pass.py` reads the
approval record and trades nothing, discharging `none()` — the read effect is
uncovered, but a query is not an order:

{{ include_file("examples/trading/cases/no_sales_pass.py") }}

And `trading_no_guarantees_pass.py` sells an unapproved stock with the explicit null
contract. It proves, because the agent is openly asking for unconstrained
trading authority rather than claiming a gate it does not have. That is the
point of `no_guarantees()`: it is semantically the same as saying nothing, and
visibly different from it.

{{ include_file("examples/trading/cases/trading_no_guarantees_pass.py") }}

## The failing cases

Each one violates a different clause of the Dogwood policy.

**No approval at all.** A literal order isn't the column of any queried row, so
`stock_from` fails. This is Dogwood's "no matching `ApproveSale::response`."

{{ include_file("examples/trading/cases/literal_sale_fail.py") }}

**Approval requested, not granted.** The loop is honest — every order does come
from an approval row — but the query pulled *every* request, including the ones
the desk turned down. `approvals_only_where` fails. This is
`output.approved: true`.

{{ include_file("examples/trading/cases/unapproved_source_fail.py") }}

**Right stock, wrong quantity.** Approved to sell whatever the desk signed off
on; sells 500. `stock_from` still holds, and `shares_from` fails. This is
`input.shares: context.input.shares`, and it is the case a stock-only gate waves
through:

{{ include_file("examples/trading/cases/quantity_mismatch_fail.py") }}

**Another desk's approvals.** Every order is backed by a genuine approval, from
a record this program has no business reading. `approvals_only_desk` fails:

{{ include_file("examples/trading/cases/wrong_desk_fail.py") }}

Two more failing cases exist to pin the aggregate contracts rather than to
illustrate a Dogwood clause. `orders_too_tight_fail.py` asserts
`orders_at_most(10)` over the same 50-bounded loop, and `sells_with_none_fail.py`
asserts `none()` while placing orders. Without them, a count contract that
degenerated to a trivially-true bound would still let `approved_sale_pass.py`
prove and nothing in the suite would notice.

## What did not port, and why it matters

`within 1h` has no rendering here, and neither does anything else that needs
history. Four concrete holes remain open in the `clauz3` version:

- **Row mixing.** This is the one that is *not* about time, and the one a
  reader is most likely to assume is closed. `stock_from` and `shares_from`
  are independent clauses, and each pins its argument to *some* approved row,
  not to a common one. Column-ref equality is a structural match on
  `<field>(Select(<approval array>, ?))` that ignores the index, so:

  ```python
  for row in rows:
      sell_shares(row.stock, rows[0].shares)
  ```

  proves, and sells row 0's approved quantity for every approved stock — a
  desk approval of 10,000 shares of a penny stock authorises 10,000 shares of
  every other approved name. Dogwood's
  `formerly ... {input.stock: ..., input.shares: ...}` matches a single event
  and denies this. Closing it needs row-identity binding the prover does not
  carry today.

  This one is not left as prose. `cases/row_mixing_hole_pass.py` is the program
  above, wired into the suite as a passing case, so the hole is a tested fact:
  if a future prover change closes it, that case starts failing and forces this
  section, the README row, and the contracts docstring to be updated in the same
  commit.
- **Staleness.** An approval granted last year is as good as one granted a
  minute ago.
- **Reuse.** Nothing stops the same approval row from backing a sale on every
  run, forever. Dogwood's window bounds reuse as a side effect of bounding
  recency; provenance does not.
- **Cross-run volume.** `orders_at_most(50)` bounds *this* program. Five
  proved programs place 250 orders.

These are not incidental gaps — they are the class of property that is only
answerable from an event log, and they are exactly what Dogwood is for.

## Why the trade is not obviously in Dogwood's favour

The temptation is to read the table above as "Dogwood does more." It says
something narrower: Dogwood *asks* more, and pays for it in what it must be able
to trust.

To decide `formerly within 1h`, the monitor needs an event history that is:

- **complete** — every relevant tool call reached the gateway. A sub-agent with
  its own tool surface, a direct SDK call, or a second harness is invisible, and
  an invisible `ApproveSale` doesn't cause a false deny, it causes the *sale* to
  be denied while an invisible sale isn't seen at all.
- **consistently ordered and timestamped** — metric windows need clock
  agreement across whoever produced the events. Dogwood's own roadmap lists
  absolute-time windows as future work.
- **correctly scoped** — whose hour is it? In a multi-agent ensemble, an
  approval granted to agent A being consumed by agent B is either the feature or
  the vulnerability, and the language has to be told which. Multi-agent
  orchestration is likewise on the roadmap rather than in the language.
- **authentic and durable** — Dogwood's README is explicit that the reference
  interpreter addresses none of event authentication, timestamp integrity, or
  trace durability.

That is a substantial environment contract, and it grows with the number of
moving pieces in the system. The `clauz3` gate needs the trusted layer to be
honest and the prover to be correct; both are in front of you at proof time, and
neither gets harder when a second agent joins.

So the honest summary is not that one subsumes the other. It is that this
example converts a *temporal* question into a *provenance* question, and
provenance is answerable from the artifact in hand:

| | Dogwood's gate | This example's gate |
| --- | --- | --- |
| Question | did a matching approval event occur recently? | does every order's argument come from an approved row? |
| Evidence | the session's event log | the program text |
| Must trust | gateway completeness, clocks, scoping, log integrity | the trusted layer, the prover |
| Decides | per call, mid-run | once, before any order |
| Catches staleness and reuse | yes | no |
| Degrades with system complexity | yes | no |

Both halves are worth having, and the composition — proving the contract, then
metering the *proved bound* against a windowed budget at approval time — is
sketched in [Background](../explanation/background.md#dogwood).

## All cases

Browse the full set on the [all-cases page](trading-all-cases.md).

## How they run

{{ include_file("examples/trading/Justfile") }}
