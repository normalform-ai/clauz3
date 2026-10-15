---
name: clauz3-authoring
description: Use when an agent needs to act in some domain — lab control, mail, file system, finance, anything with real side effects — and the project has no trusted layer for it yet. Instead of handing the agent unrestricted access, design the trust boundary: which side-effecting functions exist, which preconditions guard them, which contracts the agent will state guarantees in, which fluents track state. This is the root-of-trust role; if this layer is wrong, every proof against it is wrong. Use this skill to design and ship a new trusted layer (effects.py + contracts.py + optional fluents + tests + installable packaging).
---

# Authoring a clauz3 trusted layer

There's a domain — labs, mail, files, finance, your own system —
where an agent needs to act, and either (a) no trusted layer for it
exists, or (b) the existing layer doesn't yet express the safety
properties this use case requires. Without a trusted layer, the only
options are to hand the agent unrestricted access (rarely safe) or to
not act at all (often unhelpful). Authoring a trusted layer is the
third option: a small audited boundary the agent passes through, with
a contract vocabulary rich enough to let the user say yes.

You're in the **Trusted Layer Engineer** role
(see [concepts](../../docs/explanation/concepts.md)). You're not writing
agent code or proving anything; you're writing the *thing the prover
trusts*. If your effects are wrongly marked, or your contracts admit
predicates they shouldn't, every proof against this layer is wrong.
Treat this as security-critical work — closer to writing a Linux
syscall than writing a library.

For *using* an installed trusted layer (writing agent plans against
one), see the companion **clauz3** skill.

## Anatomy of a trusted layer

A complete library has this shape:

```
<library>/
├── tools/<domain>/
│   └── trusted/
│       ├── effects.py     # trusted side-effecting functions
│       └── contracts.py   # contract vocabulary over the effect facts
├── tests/
│   ├── Justfile           # proof recipes for `clauz3 test`
│   └── cases/             # *_pass.py must prove; *_fail.py must not
├── README.md
└── AGENTS.md              # consumer-facing guide
```

`tools/<domain>/trusted/` is the *only* directory that ships across
`clauz3 install`. Everything else (`tests/`, READMEs) is for the
library's own development and CI.

## effects.py: the side-effecting boundary

Trusted effects are decorated with `@clauz3.trusted` and optionally
`@clauz3.requires(...)` preconditions:

```python
import clauz3


@clauz3.requires(lambda addr, msg: "@" in addr, message='addr must contain "@"')
@clauz3.trusted
def send_email(addr: str, msg: str) -> None:
    """The body runs only under `clauz3 run`, after proof + approval."""
    # ... real implementation, or `pass` for a mock layer
```

Three things to internalise:

1. **`@clauz3.trusted` takes no arguments.** A function either is the
   boundary or it is not, and its *name* is how a contract addresses it:
   `effect(send_email)` with the function imported. To span several, name them —
   `effect(edit_file, append_file)` — and to span every trusted call
   in a program, `effect()`. Keep the grouping in the contract that needs
   it rather than in a string on the declaration, so a later effect cannot
   silently join a relation the contract already constrains.

2. **`@clauz3.requires(lambda ...)` is the per-call precondition.** The
   prover lifts it into a proof obligation at every call site, so the
   agent program fails closed if it passes (say) a negative volume.
   Write the predicate as a lambda in the supported Python subset; a named
   predicate function is refused by the prover. Chained comparisons such as
   `0 < x <= 200` are supported here, unlike in relation lambdas below.

3. **The body can do real work, or be a mock `pass`.** The prover
   never executes it under `clauz3 prove`; it runs only under
   `clauz3 run` after approval. Stdlib layers ship real bodies; example
   / fixture layers ship mocks.

**Optional second layer: `@deal.has(...)`.** deal's markers (`"io"`,
`"network"`, `"read"`, `"write"`, `"syscall"`, `"stdout"`, ...) are deal's
own vocabulary, and declaring them buys two things clauz3 does not do: deal
patches `socket`/`stdout`/`stderr` per call, so an undeclared side effect
raises rather than happens, and `clauz3 lint` (deal's linter) checks the
markers against what the body actually does. A layer whose effects do real
IO should declare them and run the linter in its `tests/Justfile`; a mock
layer can omit the markers. They play no part in what a contract can say.

Run `clauz3 lint` either way: its remaining rules are about contracts, not
markers, and they read `@clauz3.requires` / `@clauz3.ensures` too — a body
that contradicts its own postcondition is reported whether or not the layer
declares a marker.

## contracts.py: the predicate vocabulary

Contracts are `@contract`-decorated builder functions that return
`ContractSpec` values composed from a relation algebra over the
recorded effect facts.

```python
from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

from tools.email.trusted.effects import send_email

Email = effect(send_email)


@contract
def no_guarantees() -> ContractSpec:
    return core_no_guarantees()


@contract
def only(addresses: list[str]) -> ContractSpec:
    """Guarantee every email recipient is in `addresses`."""
    return Email.all(lambda e: e.addr in addresses)


@contract
def at_most(count: int) -> ContractSpec:
    return Email.count() <= count


@contract
def unique_recipients() -> ContractSpec:
    return Email.distinct(lambda e: e.addr)


@contract
def must_not_contain(substring: str) -> ContractSpec:
    return Email.all(lambda e: substring not in e.msg)
```

For a layer with several effects, import the functions the relation covers.
For example, these declarations belong to the editor layer's `contracts.py`:

```python
from clauz3.spec import effect
from tools.editor.trusted.effects import append_file, edit_file

EditFile = effect(edit_file)              # one effect
Edit = effect(edit_file, append_file)     # both editor effects
Any = effect()                           # every trusted call
```

### Relation primitives

| Form | Meaning |
|---|---|
| `R.all(lambda e: pred(e))` | universal over reachable facts |
| `R.where(lambda e: pred).all(...)` | filtered universal |
| `R.empty()` / `R.where(...).empty()` | no fact satisfies / no fact matches |
| `R.count() <= n` (or `<`, `>=`, `>`) | bounded cardinality |
| `R.sum(lambda e: e.field) <= n` | bounded numeric aggregate |
| `R.distinct(lambda e: key(e))` | no two facts share the same key |
| `R1.shares_value(R2, lambda e: key)` | existential join between two filtered relations |

The lambda inside `.all` / `.where` is a *relation lambda* — a small
expression subset that compiles to Z3 constraints. It supports row
attributes (`e.addr`), comparisons (`==`, `!=`, `<`, `<=`, etc.),
boolean ops (`and`, `or`, `not`), `in` / `not in`, `len(...)`,
`str.startswith`/`str.endswith`, and a handful of other primitives.
**It does not yet support chained comparisons** (write `a < b and b < c`,
not `a < b < c`). It does *not* support arbitrary function calls;
attempting `e.addr.lower()` will raise `UnsupportedError`.

**The body runs as trusted Python; the lambda is compiled.** The helper body
runs as ordinary Python at prove time, its behavior is not proved, and the
`ContractSpec` it *returns* is what gets solved. The relation lambda
is never called: it is parsed and compiled into Z3 constraints, and
fails closed on anything outside the subset. So keep every helper
body to a one-line spec builder. A body that reads `os.environ`,
branches on anything but its arguments, or touches the file system
can silently return `no_guarantees()`, removing the intended restriction. Review `contracts.py` the way you review `effects.py`. See
[the trust boundary](../docs/explanation/concepts.md#trust-boundary-what-runs-as-python-what-is-interpreted).

### Naming conventions

Match the existing stdlib / examples pattern:

- `only_X(allowlist)` — universal over a field
- `never_X(prefix_or_list)` — universal negation
- `Xs_at_most(n)` — count cap
- `must_contain(s)` / `must_not_contain(s)` — content guard
- `no_X()` — explicit emptiness
- `no_guarantees()` — explicit null contract

## Fluents: stateful (final-state) contracts

When the trusted layer needs to talk about *order* — e.g. "the door is
locked at the end" — relations alone are insufficient (they're a
multiset over facts, with no order). Add a fluent:

```python
import clauz3
from clauz3.fluent import effect, fluent

DoorLocked = fluent("door_locked", key=str, value=bool, initial=True)


@clauz3.trusted
@effect(lambda door: DoorLocked.set(door, False))
def unlock_door(door: str) -> None: ...


@clauz3.trusted
@effect(lambda door: DoorLocked.set(door, True))
def lock_door(door: str) -> None: ...
```

Then in `contracts.py`:

```python
from .effects import DoorLocked


@contract
def all_doors_locked_at_end() -> ContractSpec:
    return DoorLocked.final.all(lambda d: d.value == True)


@contract
def door_locked_at_end(door: str) -> ContractSpec:
    return DoorLocked.final[door] == True
```

Fluents encode a Z3 array threaded through the recorded fact sequence
in program order. Each `@effect` lambda is applied as a guarded store.
Contracts query the *final* valuation after all reachable calls have
been folded in — order matters (`unlock; lock` is different from
`lock; unlock`).

Fluent caveats in v1:

- `@effect`-bearing trusted calls cannot appear inside loops.
- Keys and values must be `bool`, `int`, or `str`.
- Queries are over `.final` only (no intermediate / quantified-over-time).

## Packaging as an installable library

Add a `tests/Justfile` so `clauz3 test gh:org/repo` can run your suite:

```just
clauz3 := env_var_or_default("CLAUZ3", "uv run clauz3")

trusted := "../tools/<domain>/trusted"
effects := trusted + "/effects.py"
prove := clauz3 + " prove --trusted-root " + trusted + " --import-root .."

test: lint cases

lint:
    {{clauz3}} lint {{effects}}

cases: only-X-pass only-X-fail no-X-pass ...

only-X-pass:
    {{prove}} cases/only_X_pass.py

only-X-fail:
    if {{prove}} cases/only_X_fail.py; then exit 1; fi

# ... etc
```

The `CLAUZ3` env var indirection lets the suite run both
from a source checkout (`uv run`) and from an installed wheel under
`clauz3 test`.

Every contract should have *both* a `_pass.py` and a `_fail.py` case
under `tests/cases/`. The `_fail.py` form proves the contract correctly
rejects programs that violate it — without it you're not testing that
the guarantee is enforced, only that there's no error path.

## Common pitfalls

- **Defaults are not in facts.** A trusted-function keyword default
  like `dry_run: bool = True` is *not* reflected into the recorded
  effect fact. A contract querying `e.dry_run` against a call that
  relied on the default fails closed. This is the intentional
  pattern behind `dry_run_only()` in `clauz3-tools-assistant`; if you
  want it, force agents to pass the kwarg explicitly. If you want
  defaults reflected, that needs a clauz3-core change.

- **Forgetting `@clauz3.requires` means the prover infers
  unconstrained inputs.** A symbolic `int` argument can be negative
  or huge unless a precondition bounds it. Bound where possible.

- **Incomplete effect groups.** A relation covers only the functions it
  names. When adding an effect, update each group that should constrain it;
  deal's `read` or `write` markers do not add it to another layer's policies.

- **Relation lambda gotchas.** `e.addr.lower()` raises
  `UnsupportedError`. Chained comparisons (`0 < x <= 200`) inside a
  relation lambda raise `UnsupportedError`. Stick to the supported
  subset; if you need more, the contract is probably trying to do too
  much and should be split.

- **A quiet no-match is a proven no-op.** If an effect resolves an
  identifier (room, device id, path, table, address) against external
  state, it must raise when nothing matches. A proof over
  `only_rooms(["Dining Room"])` says nothing about whether the bridge
  has such a room; wrapping a library whose `get_*_by_name` logs and
  returns `None` turns the proof into a guaranteed no-op. See
  [Avoid vacuous proofs](../../docs/how-to/vacuous-proofs.md).

## Reference layers to read

For working examples in increasing order of complexity:

- `examples/email/` in the `clauz3` repo — minimal mock, single effect.
- `src/clauz3/stdlib/filesystem/` — real reads and writes with path policies.
- `src/clauz3/stdlib/editor/` — an explicit group over edit and append effects.
- `examples/home-automation/` — relational + fluent contracts in one
  layer.
- `normalform-ai/clauz3-tools-autolabs` — production LIMS layer with
  state via fluents.
- `normalform-ai/clauz3-tools-assistant` — `dry_run_only()` safety
  pattern with subprocess shell-out.

## When done

- All `_pass.py` cases prove cleanly.
- All `_fail.py` cases are correctly rejected (non-zero exit from
  `clauz3 prove`).
- `clauz3 lint tools/<domain>/trusted/effects.py` is clean.
- Every identifier parameter either raises on no-match at run time
  (covered by a runtime smoke test), or its quiet semantics are
  documented so guarantees over it read as intent-only.
- `README.md` shows the install command and the headline contracts.
- `AGENTS.md` documents the consumer-side discipline (which contracts
  to combine, how to pass kwargs, sandbox conventions if any).
- Listed in the registry (`docs/registry.md` in the `clauz3` repo) once
  the layer is intended for shared use.
