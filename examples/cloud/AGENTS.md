# Agent Guide

You are a deployment agent with access to cloud deploy tools. All tool calls
go through `clauz3`.

This example follows Simon Willison's argument that pay-per-use services
should have [hard budget caps on by default](https://simonwillison.net/2026/Oct/3/default-hard-budget-caps/).
The provider enforces a cap at runtime. The contracts below let a user check
your program before it runs: every deploy carries a cap, the caps stay within
the user's budget, and no cap is removed without the user saying so.

## Tools

```python
from tools.cloud.trusted.effects import (
    deploy, estimate_monthly_cost, remove_spend_cap,
)
```

- `estimate_monthly_cost(service) -> int` — estimated USD per month.
  Trusted postcondition: non-negative. It has no upper bound.
- `deploy(service, spend_cap_usd) -> None` — deploy with a hard monthly spend
  cap; the provider halts the service when it is reached. Trusted
  precondition: `spend_cap_usd > 0`. Marks the service as capped.
- `remove_spend_cap(service) -> None` — the explicit opt-out: the service
  bills without limit. Marks the service as uncapped.

## Available contracts

```python
from tools.cloud.trusted import contracts as cloud

cloud.cap_at_most(max_usd)            # every deploy's cap within max_usd
cloud.total_caps_at_most(max_usd)     # deploy caps sum to at most max_usd
cloud.only_services(allowed)          # deploy target allowlist
cloud.never_remove_caps()             # no cap removed, even temporarily
cloud.all_services_capped_at_end()    # no service left uncapped at the end
```

## Pattern

A cap computed from an estimate must be clamped, because the estimate is
unbounded:

```python
@clauz3.guarantee(cloud.cap_at_most(100))
def main() -> None:
    headroom = 2 * estimate_monthly_cost("api")
    if headroom > 100:
        deploy("api", 100)
    elif headroom > 0:
        deploy("api", headroom)
    else:
        deploy("api", 1)
```

This bounds the cap on each deploy, but it says nothing about a later
`remove_spend_cap`. Add `never_remove_caps()` or
`all_services_capped_at_end()` whenever the user's budget must actually hold.

## Notes

- `cap_at_most` bounds each service; `total_caps_at_most` bounds the sum of
  deploy caps across all of them. A user's monthly budget usually needs the
  total. The sum counts every deploy, so a redeploy counts twice.
- Cap bounds alone do not stop a cap being removed afterwards. Pair them with
  `never_remove_caps()` or `all_services_capped_at_end()`.
- `never_remove_caps()` forbids the opt-out event. `all_services_capped_at_end()`
  is a state contract: it allows lifting a cap mid-program (say, for a
  backfill) as long as a later `deploy` restores one.
- The caps cover the deploys in one program, not spending accumulated across
  runs or billing periods.
