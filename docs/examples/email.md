# Email example

The email example is the canonical worked case. The trusted module exposes
one side-effecting function (`send_email`) and a small vocabulary of
domain contracts (`only`, `none`, `unique_recipients`, `content_length_at_most`,
`same_content`, `no_guarantees`). Agent code in `cases/` uses
`@clauz3.guarantee(...)` to express what should be true about the program's
effects.

## Trusted module

The trusted effect is just a stub function decorated with `@deal.has(...)`
and a precondition.

{{ include_file("examples/email/tools/email/trusted/effects.py") }}

The contract module defines the domain vocabulary on top of the generic
`effect("send_email")` relation.

{{ include_file("examples/email/tools/email/trusted/contracts.py") }}

## A passing case

Sending one email to an allow-listed address discharges
`emails.only([...])`.

{{ include_file("examples/email/cases/only_bob_pass.py") }}

## A failing case

The same guarantee with a non-allow-listed recipient is rejected by the
prover.

{{ include_file("examples/email/cases/only_bob_fail.py") }}

## All cases

Every file under `cases/` is a small program plus its declared guarantee.
Cases with `_pass` suffix should prove; cases with `_fail` should be
rejected. Browse the full set on the [all-cases page](email-all-cases.md).

## How they run

The example `Justfile` invokes `clauz3 prove` against each case. `_fail`
cases are wrapped so a non-zero exit is the expected outcome.

{{ include_file("examples/email/Justfile") }}
