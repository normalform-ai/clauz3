# Bank example

The bank example shows the relation algebra's numeric aggregation side. The
trusted module exposes a single side-effecting `withdraw(account, amount)`
function, and the contract vocabulary covers two domain predicates:
`max_spend(limit)` (a sum over all withdrawals) and `only_account(account)`
(a universal over the `account` field).

## Trusted module

The trusted effect is a stub with a non-negativity precondition. The
prover lifts that precondition into a proof obligation at every call site.

{{ include_file("examples/bank/tools/bank/trusted/effects.py") }}

Two contract helpers wrap the generic `effect("withdraw")` relation.

{{ include_file("examples/bank/tools/bank/trusted/contracts.py") }}

## A passing case

The sum of two withdrawals is exactly the spending limit, so
`bank.max_spend(5)` is discharged.

{{ include_file("examples/bank/cases/max_spend_pass.py") }}

## A failing case

Bumping one of the amounts above the limit causes the proof to fail.

{{ include_file("examples/bank/cases/max_spend_fail.py") }}

## All cases

Browse the full set on the [all-cases page](bank-all-cases.md).

## How they run

{{ include_file("examples/bank/Justfile") }}
