# Cloud spend caps example

Simon Willison argues that pay-per-use services should ship with
[hard budget caps on by default](https://simonwillison.net/2026/Oct/3/default-hard-budget-caps/):
when spending reaches the limit the service stops, and removing the cap takes
an explicit opt-in. Coding agents make it easy to deploy things without
thinking about cost, so a runaway service can run up a large bill overnight.

The provider enforces the cap at runtime. This example checks the agent's side
before anything runs: every deploy carries a cap, the caps fit the user's
budget, and the opt-out is not used without the user agreeing to it.

## Trusted module

`deploy` requires a positive cap, so an uncapped deploy cannot be written.
`remove_spend_cap` is the opt-out. Both declare
[successor-state axioms](../reference/fluents.md) over a `Capped` fluent.
`estimate_monthly_cost` is non-negative but unbounded above.

{{ include_file("examples/cloud/tools/cloud/trusted/effects.py") }}

{{ include_file("examples/cloud/tools/cloud/trusted/contracts.py") }}

## Clamping a computed cap

A cap derived from the estimate must be clamped. Without the clamp the prover
finds a counterexample: any estimate of 50 or more pushes the cap past the
bound.

{{ include_file("examples/cloud/cases/estimate_clamped_pass.py") }}

{{ include_file("examples/cloud/cases/estimate_unclamped_fail.py") }}

## Per-service and total budgets

`cap_at_most` bounds each service. The user's budget is usually for the whole
bill, which is what `total_caps_at_most` bounds. Four services capped at 50
each pass the first and fail the second:

{{ include_file("examples/cloud/cases/total_caps_fail.py") }}

The sum counts every deploy, so redeploying a service counts its cap twice.
That over-approximates the bill, which is the safe direction.

### Cap bounds need an end-state guarantee

Bounding the caps does not stop the agent removing one afterwards. This
program proves against both cap bounds, yet `api` ends up billing without
limit:

{{ include_file("examples/cloud/cases/cap_bounds_then_removed_pass.py") }}

Adding `all_services_capped_at_end()` (or `never_remove_caps()`) rejects it.
State one of them whenever the user's budget must actually hold:

{{ include_file("examples/cloud/cases/cap_bounds_then_removed_fail.py") }}

## Removing a cap

`never_remove_caps()` forbids the opt-out anywhere in the trace.
`all_services_capped_at_end()` is a state contract: a cap may be lifted for a
one-off job as long as a later `deploy` restores one.

{{ include_file("examples/cloud/cases/capped_at_end_pass.py") }}

{{ include_file("examples/cloud/cases/capped_at_end_fail.py") }}

## Limits

The contracts cover the deploys in one program. A provider's cap applies to
spending over a billing period across many runs, which needs state carried
between runs. That is not modeled here.

## All cases

Browse the full set on the [all-cases page](cloud-all-cases.md).

## How they run

{{ include_file("examples/cloud/Justfile") }}
