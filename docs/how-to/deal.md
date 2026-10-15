# Extra runtime enforcement with deal

Use `@clauz3.trusted` to declare an effect boundary and `@clauz3.requires`
and `@clauz3.ensures` for its preconditions and postconditions. Contract
helpers use `@contract`, and agent programs state `@clauz3.guarantee(...)`.
None of these declarations requires a Deal import.

[Deal](https://deal.readthedocs.io/) supplies the runtime checks behind
`@clauz3.requires` and `@clauz3.ensures`. A trusted layer can also use
`@deal.has(...)` directly to declare implementation side effects for Deal's
marker lint checks and runtime guards. Those extra declarations are optional;
the trusted body remains the layer author's responsibility.

## Why a second layer exists at all

The prover never executes a trusted body. It records the call as a fact,
requires the preconditions at the call site, and invents a return value from
the annotation. So a proof cannot tell the difference between

```python
@clauz3.trusted
def read_file(path: str) -> str:
    return Path(path).read_text()
```

and the same function that also opens a socket and posts the file somewhere.
Both record one `read_file` fact. Every guarantee an agent states still holds;
what the *trusted layer itself* does inside the boundary is outside what a
proof about the agent's program can say.

That is not a gap in the proof — it is the definition of the trusted base. The
trusted layer is audited code, and the audit is the guarantee. deal is how you
get a machine to help with that audit.

## Declaring what a body may do

`@deal.has(...)` declares side effects of the implementation. Deal's built-in
markers include `io`, `network`, `read`, `write`, `syscall`, `stdout`,
`stderr`, `global`, `import`, `random`, and `time`:

```python
from pathlib import Path

import clauz3
import deal


@clauz3.requires(lambda path: len(path) > 0)
@deal.has("read", "io")
@clauz3.trusted
def read_file(path: str) -> str:
    return Path(path).read_text()
```

These markers enable two checks of the trusted body:

**At runtime.** Deal patches `socket`, `stdout` and `stderr` for the duration
of each call. A body that opens a socket without declaring `network` or `io`
raises `OfflineContractError` before making the connection.

**Statically, over the body.** `clauz3 lint` (deal's linter) walks the body,
infers its side effects, and reports the markers that are missing:

```console
$ clauz3 lint tools/gh/trusted/effects.py
tools/gh/trusted/effects.py:42:16 DEL050 missed marker (syscall)
```

That is a `subprocess.run` inside an effect that declared only `io`. Bundled
stdlib tools run `clauz3 lint` from their `tests/Justfile`; worked examples
run it from their example `Justfile`.

A function carrying only `@clauz3.trusted` declares no Deal markers, so it
does not receive these marker checks. Markers do not select ClauZ3 effect
relations: use `effect(read_file)` to constrain calls to the function above.

The linter also checks native conditions without requiring `@deal.has`.
For example, a body that returns `-1` under
`@clauz3.ensures(lambda result: result > 0)` produces `DEL012 post contract
error`. A call that violates a callee's `@clauz3.requires` can produce
`DEL011 pre contract error`.

`clauz3 lint` checks the original file and, when needed, a temporary copy with
native conditions and aliased Deal decorators rewritten to Deal's spelling
and `predicate=` renamed to `validator=`. Results are deduplicated and refer
to the original source lines. This rewrite applies to the file being checked;
native or aliased
preconditions on imported callees can still be missed
([known limitation](https://github.com/cmungall/agent-deal/issues/173)).

## The rest of deal

A trusted layer can use deal's other contracts directly; the prover reads them
as it reads `@clauz3.requires` and `@clauz3.ensures`:

| deal | ClauZ3 | Meaning |
|---|---|---|
| `@deal.pre(lambda ...)` | `@clauz3.requires(...)` | obligation at every call site |
| `@deal.post(lambda result: ...)` | `@clauz3.ensures(lambda result: ...)` | assumption about the return value |
| `@deal.ensure(lambda ..., result: ...)` | `@clauz3.ensures(lambda ..., result: ...)` | a postcondition that also reads the arguments |
| `@deal.raises(SomeError)` | — | the exceptions a body may raise |
| `@deal.has(...)` | — | the side effects a body may have |

## Checks when no proof runs

When trusted functions are called without a proof, their runtime checks still
apply: a violated precondition or postcondition raises, as does a socket call
forbidden by the declared markers.

An agent choosing `emails.no_guarantees()` removes its email-effect guarantee.
It does not disable preconditions: the prover still checks those obligations,
and the trusted function still enforces them at runtime.

See [Concepts](../explanation/concepts.md#static-proof-vs-runtime) for how the
two layers relate and what the user is agreeing to when they accept a weaker
contract.
