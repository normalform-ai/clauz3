# Generic Effect Specs

This document describes the current contract-spec prototype. It is the path
away from domain-specific solver callbacks and toward reusable, auditable
contract libraries.

## Goal

Domain authors should be able to write contracts like:

```python
Email = effect(send_email)


@contract
def only(addresses: list[str]) -> ContractSpec:
    return Email.all(lambda e: e.addr in addresses)
```

without knowing about `ctx.facts`, z3 proxy objects, or the vendored
`deal-solver` internals.

The core idea is that every trusted function invocation creates a row in a
finite relation. Contracts are relational constraints over those rows.

## Trusted Functions Become Effect Facts

A trusted function is an audited side-effect boundary:

```python
import clauz3


@clauz3.requires(lambda account, amount: amount >= 0)
@clauz3.trusted
def withdraw(account: str, amount: int) -> None:
    ...
```

When the prover sees a reachable call:

```python
withdraw("checking", 3)
```

it records a symbolic fact like:

```python
name = "withdraw"
args = {"account": "checking", "amount": 3}
cond = <path condition>
```

The trusted function body is not symbolically executed. Its preconditions are
proved as assertions, then the call is recorded as a fact.

This is generic. The core does not know what a withdrawal, email, HTTP request,
or file write means. It only records the trusted function name, signature
fields, symbolic arguments, and reachability condition.

## Declaring mock implementations

Use `@clauz3.mock` alongside `@clauz3.trusted` when a library implementation
simulates an effect:

```python
import clauz3

@clauz3.mock
@clauz3.trusted
def send_email(addr: str, msg: str) -> None:
    """Example sender: no message is delivered."""
    pass
```

The decorator is an identity operation; `@clauz3.mock()` is also supported.
It does not replace the function body, grant trusted status, suppress I/O, or
bypass proofs and approvals. Preconditions and effect contracts retain their
normal meaning. Agent-authored programs cannot apply this decorator.

Tool discovery and the workspace read the declaration from source without
importing the library. Library selection and review distinguish declared mock
effects, a mixture of mock and unmarked effects, and sources whose declarations
cannot be read. Unmarked does not mean real or safe. Approval cards show mock
declarations from the operator-authorized libraries, not from claims supplied
by the agent; those declarations describe the selection rather than a complete
analysis of the submitted program's calls.

**A mock declaration is not enforced demo isolation.** An incorrectly marked
implementation, its imports, or its contract helpers can still perform real
effects. Review the code as trusted code. The bundled email and bank examples
use this marker for their mock implementations; general-purpose enforced demo
mode is separate work.

## Effect Relations

`effect(*targets)` creates a relation view over trusted facts. Name the
function itself — the import is then checked by Python, mypy and your editor's
rename:

```python
from clauz3.spec import effect
from tools.editor.trusted.effects import append_file, edit_file

EditFile = effect(edit_file)              # one effect
Edit = effect(edit_file, append_file)     # both editor effects
Any = effect()                           # every trusted call
```

A string names the same thing for a layer that cannot import its effects
(`effect("edit_file")`), and is checked against the trusted layer when the
proof loads it.

**An unresolvable relation is refused.** A name that is not a trusted effect
of the layer being proved is a configuration error, not an empty relation:

```
error: effect('db_read') in tools/db/trusted/contracts.py names 'db_read',
which is not a trusted effect. Known effects: ['read_rows']. (A name that
matches nothing would make every contract over this relation hold vacuously.
If it was a @deal.has marker, name the function that carries it.)
```

That refusal is the point. A contract over an empty relation is a conjunction
of no clauses, which is `True`, so a relation that quietly matched nothing
would make its guarantees hold however the program behaves.

**Fields come from the signature.** `send_email(addr, msg)` gives `e.addr` and
`e.msg`, and a predicate reading anything else is refused where the relation is
used, naming what is available. A relation over several effects offers the
fields they *share*, since the predicate is applied to every matching fact —
which is what makes a group like `effect(edit_file, append_file)` safe to write.
`effect()` covers whatever a program calls, so no field is guaranteed and the
check falls back to the one the expression compiler does per fact.

Grouping belongs to the contract that depends on it. A relation could once
name a `@deal.has` marker instead — `effect("read")` matched whichever
functions carried the string `read` — which made the membership of a relation
something no contract stated: a later effect, or an unrelated layer installed
alongside, silently joined or left it, and the matched facts need not even have
the same fields. A layer that wants another layer's rule applied to its own
effect says so, the way `stdlib:grep` does:

```python
# grep/trusted/contracts.py
@contract
def only_read_under(root: str) -> ContractSpec:
    return filesystem.reads_under(Grep, root)
```

## Relation Primitives

The current relation API is intentionally small.

### `no_guarantees`

`no_guarantees()` is the transparent null contract. It solves to `True` and
imposes no constraints:

```python
from clauz3.spec import no_guarantees as core_no_guarantees


@contract
def no_guarantees() -> ContractSpec:
    return core_no_guarantees()
```

This is semantically equivalent to omitting the guarantee, but it is useful in
user-facing flows because it lets an agent explicitly say, "for this domain, I
make no guarantees."

### `all`

Every reachable row must satisfy a predicate:

```python
Email.all(lambda e: e.addr in addresses)
Withdraw.all(lambda w: w.account == account)
```

The path condition is handled by the relation primitive. Authors do not write
`if reachable then ...` manually.

### `empty`

No reachable row may exist:

```python
Email.empty()
```

This is used by `emails.none()`.

### `distinct`

No two reachable rows may have the same key:

```python
Email.distinct(lambda e: e.addr)
```

This proves "never email the same person twice." Mutually exclusive branches are
allowed to produce the same key because both rows cannot be reachable in the
same execution.

### `sum`

Sum a selected numeric field over reachable rows, then compare it:

```python
Withdraw.sum(lambda w: w.amount) <= limit
```

Unreachable rows contribute zero.

### `count`

Count reachable rows, then compare the count:

```python
Email.count() <= 2
```

### Aggregates over a `for` loop

A row emitted inside `for row in db_query(...)` stands for one call per loop
index, so it contributes the loop's length rather than 1. Two consequences are
worth knowing before writing a bound:

- An upper bound must cover the loop source's whole length postcondition. A
  row-dependent guard such as `if row.consented:` does not lower it.
- A strictly positive count lower bound cannot follow from a loop whose
  source may be empty. The lower bound `count() >= 0` can still prove.

For counts and nonnegative constant-selector sums, both directions are
conservative under the supported loop-body restrictions. See
[Counting and summing over a loop](../explanation/symbolic-iteration.md#counting-and-summing-over-a-loop)
for the emitted term and a table of exact and approximate cases.

### `where`

Filter a relation before applying another relation primitive:

```python
Email.where(lambda e: e.addr == "bob@example.com").count() <= 2
```

This proves policies like "Bob is emailed at most twice." The filter is also
guarded by reachability conditions, so facts in unreachable branches do not
contribute to the filtered count.

### `shares_value`

Require two filtered relation views to have at least one reachable pair with
the same selected value:

```python
bob = Email.where(lambda e: e.addr == "bob@example.com")
ann = Email.where(lambda e: e.addr == "ann@example.com")
bob.shares_value(ann, lambda e: e.msg)
```

This proves policies like "Bob and Ann receive the same email content." It is an
existential join: at least one Bob row and at least one Ann row must be
reachable with equal selected values.

## Lambda Subset

Relation lambdas are parsed from source and compiled by `clauz3.spec`.
Supported today:

- constants: `bool`, `int`, `str`
- list literals and captured lists
- row attributes: `e.addr`, `w.amount`
- comparisons: `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not in`
- boolean operators: `and`, `or`, `not`
- numeric `+` and `-`
- `len(...)` over supported row values such as strings
- `str.startswith(prefix)` and `str.endswith(suffix)` over string row values,
  for path-prefix policies such as `e.path.startswith(root)`

A relation lambda is located by its source position, so it must be
identifiable uniquely. Two lambdas on one line are fine when they are
siblings, as in `Email.where(lambda e: ...).all(lambda e: ...)`; a lambda
nested *inside* another lambda on the same line is refused because the inner
one's instructions lie inside both spans. Write a curried predicate factory
as a `def` instead:

```python
# refused: the inner lambda cannot be told apart from the outer one
by_addr = lambda who: lambda e: e.addr == who

# fine
def by_addr(who: str):
    return lambda e: e.addr == who
```

The lambda's source file must also be readable on disk. Telling two lambdas
on one line apart needs the column positions that `-X no_debug_ranges` strips,
so under that flag put each lambda on its own line; a lambda alone on its line
is located by line number and is unaffected.

Unsupported constructs fail closed with an `UnsupportedError`. Notably,
arbitrary function calls inside relation lambdas (other than `len(...)` and the
whitelisted string methods above), loops, mutation, and dynamic attribute
access are not supported in the spec layer yet. See
[ClauZ3 Python Subset](python-subset.md) for the full current subset.

## What Is Still Trusted

This prototype reduces the trust burden, but it does not eliminate it.

Trusted today:

- trusted roots such as `tools/email/trusted/`
- the vendored and modified `deal-solver` machinery
- the `clauz3.spec` compiler and relation primitives
- domain helper builder bodies marked with `@contract`

The last item is the main remaining gap. A helper like this is easy to audit:

```python
@contract
def max_spend(limit: int) -> ContractSpec:
    return Withdraw.sum(lambda w: w.amount) <= limit
```

but it is still executed as Python. Future work should validate helper bodies by
AST before executing or compile them without executing arbitrary Python. For
the full boundary, which pieces execute, which are interpreted, and what each
may reference, see
[Concepts → Trust boundary](../explanation/concepts.md#trust-boundary-what-runs-as-python-what-is-interpreted).

## Why Not Hard-Code Email?

The effect relation is derived from trusted functions, not from domain names.
Trusted functions and domain contracts live together under trusted roots.

Email:

```python
@clauz3.trusted
def send_email(addr: str, msg: str) -> None: ...

Email = effect(send_email)
```

Bank:

```python
@clauz3.trusted
def withdraw(account: str, amount: int) -> None: ...

Withdraw = effect(withdraw)
```

Both follow the same rule: the trusted function name creates the relation, and
the function parameters create the row fields.

## Relationship To Datalog And SQL

Most useful side-effect contracts are relational:

- allowlists are universal predicates over rows
- "never twice" is a uniqueness constraint
- budgets are aggregates
- read-only is absence of write rows
- table policies are set membership over database action rows

The current Python API is deliberately small, but it is close to a relational
algebra. A later frontend could be more explicitly Datalog-like or SQL-like and
compile to the same internal relation primitives.

Useful future features:

- mypy-level typing of a relation's fields: `effect(send_email)` knows the
  parameters and their types at contract-build time, but `e.addr` is still an
  attribute on an opaque row, so a type error in a predicate (`e.addr <= 5`)
  surfaces as a Z3 sort mismatch rather than a type-check failure. A generated
  per-effect row type would close that
- richer string predicates (substring, regex) beyond the current
  prefix/suffix checks
- `exists`, `any_of`, and grouped aggregates
- ~~provenance facts to describe where an argument value came from~~ —
  the common case (value from a column of a trusted query result) is now
  addressed by symbolic iteration; see
  [symbolic-iteration.md](../explanation/symbolic-iteration.md).
- contradiction, redundancy, and subsumption checks over policies — a first,
  mention-level completeness check now exists; see
  [Coverage policies](coverage-policies.md)
- deterministic natural-language rendering for approval dialogs
- description logic / OWL renderings for contracts; see
  [OWL / Description Logic Mapping](../todos/owl-dl-mapping.md)

## Runtime Role

Static proof answers whether the whole program satisfies its contract before
any trusted side effect happens. That is important for transactional intent: the
system can reject the entire program rather than discovering halfway through
that the next effect is forbidden.

A runtime monitor can still be useful as defense in depth, but it should enforce
the already-proved contract rather than replace the static proof step.
