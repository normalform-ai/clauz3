# Email example

The email example is the canonical worked case. The trusted module exposes
one side-effecting function (`send_email`) and a small vocabulary of
domain contracts (`only`, `none`, `unique_recipients`, `content_length_at_most`,
`same_content`, `no_guarantees`). Agent code in `cases/` uses
`@clauz3.guarantee(...)` to express what should be true about the program's
effects.

## Trusted module

The trusted effect is a stub function decorated with `@clauz3.trusted`.
Its `@clauz3.requires(...)` precondition is an obligation at every call site.

{{ include_file("examples/email/tools/email/trusted/effects.py") }}

The contract module defines the domain vocabulary on top of the
`effect(send_email)` relation, passing the imported trusted function directly.
The string form `effect("send_email")` also names this effect.

{{ include_file("examples/email/tools/email/trusted/contracts.py") }}

## A passing case

Sending one email to an allow-listed address discharges
`emails.only([...])`.

{{ include_file("examples/email/cases/only_bob_pass.py") }}

Of course, this case is trivial. The contract is somewhat superfluous here
as it's easy for a person to see the one-line `main()` function and infer it
only emails the one person. But it serves to illustrate the simplest case
where a user need only read the contract and not the program.

In a real world scenario, the agent may author a more complex program that
is harder for a person to quickly reason over.

## A failing case

The same guarantee with a non-allow-listed recipient is rejected by the
prover.

{{ include_file("examples/email/cases/only_bob_fail.py") }}

## The runtime layer

The static prover is only the first layer. The `@clauz3.requires` on
`send_email` (`addr` must contain `"@"`) is also a *runtime* check. The two
layers act on the same artifact at different times.

Statically, `precondition_fail.py` is rejected by the prover before anything
runs, because it cannot discharge that precondition for the call below:

{{ include_file("examples/email/cases/precondition_fail.py") }}

Choosing `no_guarantees()` removes the email-effect guarantee; it does not
remove preconditions. The prover still rejects a call whose address lacks
`"@"`. The same precondition is also enforced at runtime, including when the
trusted function is called directly without a proof:

```pycon
>>> from tools.email.trusted.effects import send_email
>>> send_email("bob@example.com", "hi")        # precondition holds: runs
>>> send_email("not-an-email", "hi")           # precondition violated
PreContractError: addr must contain "@" (where addr='not-an-email', msg='hi')
```

Same check, different time: the prover discharges it ahead of execution, the
boundary enforces it at execution. See
[Concepts: static proof vs runtime](../explanation/concepts.md#static-proof-vs-runtime).

## All cases

Every file under `cases/` is a small program plus its declared guarantee.
Cases with `_pass` suffix should prove; cases with `_fail` should be
rejected. Browse the full set on the [all-cases page](email-all-cases.md).

## How they run

The example `Justfile` invokes `clauz3 prove` against each case. `_fail`
cases are wrapped so a non-zero exit is the expected outcome.

{{ include_file("examples/email/Justfile") }}
