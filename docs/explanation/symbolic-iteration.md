# Symbolic Iteration

`clauz3` can now reason about for-loops over trusted query returns and prove
column-binding contracts. See
[superpowers/specs/2026-05-25-symbolic-iteration-design.md](../superpowers/specs/2026-05-25-symbolic-iteration-design.md)
for the original design rationale.

## The agent-facing surface

Trusted functions can return `list[str]` or typed lists of `clauz3.Row` subclasses.
String-list results support the same symbolic loop syntax, including nesting:

```python
for path in find("./data/", ["-type", "f"]):
    if path.startswith("./data/") and ".." not in path:
        for line in grep("hello", path):
            print(path, line)
```

The complete directory-search recipe is available through `list_skills()` and
`read_skill()`. It reports skipped paths and explains the UTF-8 and runtime-error
limitations. The proof constrains effects; it does not promise that every file
can be decoded or that external I/O will succeed.

For row queries, declare a schema:

```python
import clauz3


class UserRow(clauz3.Row):
    name: str
    email: str
    consented: bool
    role: str


@clauz3.trusted
@clauz3.ensures(lambda result: len(result) <= 100)
def db_query(table: str, where: dict[str, object]) -> list[UserRow]: ...
```

Generic trusted readers can instead take the schema as an argument and return
`list[T]`:

```python
from typing import TypeVar


RowT = TypeVar("RowT", bound=clauz3.Row)


@clauz3.trusted
@clauz3.ensures(lambda result: len(result) <= 100)
def read_rows(
    schema: type[RowT],
    path: str,
    table: str,
    where: dict[str, object],
) -> list[RowT]: ...
```

At proof time, `read_rows(UserRow, ...)` materializes a symbolic
`list[UserRow]`. That is the pattern used by `stdlib:db`.

Agents iterate with normal for-loops:

```python
for row in db_query("users", where={"consented": True}):
    send_email(row.email, "Newsletter")
```

Fixed-size loops over literal lists/tuples or `range(N)` with a literal integer
are unrolled instead of quantified:

```python
for _ in range(5):
    withdraw("checking", 10)

for addr in ["bob@example.com", "ann@example.com"]:
    send_email(addr, "hi")
```

Unrolled iterations emit ordinary concrete facts, so existing aggregates such
as `effect(withdraw).sum(lambda w: w.amount) <= limit` work without the
quantified-aggregate limitations below.

## The contract surface

Contracts can refer to columns:

```python
from tools.db.trusted.effects import send_email

from clauz3.spec import ContractSpec, contract, effect


@contract
def addresses_from(schema: type, field: str) -> ContractSpec:
    column = getattr(schema, field)
    return effect(send_email).all(lambda e: e.addr == column)
```

Agent usage:

```python
@clauz3.guarantee(emails.addresses_from(UserRow, "email"))
def main() -> None:
    for row in db_query("users", where={"consented": True}):
        send_email(row.email, "hi")
```

The prover verifies that every `send_email` fact's `addr` argument is
*symbolically* the `email` field of a row from a `UserRow` query — not a
literal address, not a different column.

## Worked example

See [`examples/email-from-db/`](../examples/email-from-db.md) for a complete
end-to-end example with multiple cases covering: headline, literal-address fail,
wrong-column fail, mixed-source fail, count bounds.

See [`examples/bank/`](../examples/bank.md) for fixed-bound loop examples that
prove or reject total withdrawal limits over `range(5)`.

## Counting and summing over a loop

`count()` and `sum()` are the two relation primitives that produce a *number*
rather than a boolean. The discussion here covers counts and nonnegative
constant-selector sums over bounded query loops. This section says what they compute and where the answer is
approximate, so that a failing `count()` bound can be read as a property of the
encoding rather than a mystery.

### What is emitted

An unquantified fact contributes `If(cond, base, 0)`, where `cond` is its
reachability condition and `base` is `1` for `count()` or the selector's value
for `sum()`. A fact carrying quantifier frames stands for one call per loop
index, so it contributes

```text
If(cond, base * product(q.upper for q in quantifiers), 0)
```

where each `q.upper` is the loop source's *symbolic* length. For a nested
query whose length depends on an outer row, `cond` also includes the relevant
outer iteration bounds. The trusted
`@deal.post(lambda result: len(result) <= 100)` reaches the solver separately,
as an assumption about that length.

When the call sits under a branch on the row, as in `if row.consented:`, `cond`
mentions the loop's bound variable `i`. That `i` is left **free**: there is no
`ForAll` around the contribution. Every obligation is discharged by checking
`given ∧ ¬guarantee` unsatisfiable, and a free variable in an unsatisfiability
check reads universally, so the guarantee must hold for *every* value of `i` —
in range or not.

### What that means for a bound

The universal reading is what keeps the term conservative in both directions.

For an **upper bound**, the solver is free to pick an `i` that makes `cond` true,
which demands that the full product fit under the bound. Unless `cond` is
unsatisfiable for every possible row, a row-dependent condition therefore buys
nothing: `count() <= N` discharges exactly when `N` covers the loop's whole
length bound. Writing `if row.consented:` around the call does not lower the
number the prover has to fit.

For a **lower bound**, the solver picks an `i` that makes `cond` false, which
collapses the contribution to zero. A quantified fact can carry no lower bound
of its own; `count() >= 1` discharges only if unquantified facts already supply
it. This is not merely an artefact — a loop source is allowed to be empty, so
"at least one email is sent" is genuinely not provable from a loop.

For these count bounds and nonnegative constant-selector sums, both directions
err away from proving too much. This relies on the loop-body restrictions
under [v1 limitations](#v1-limitations), including refusal of loop-carried
state; the encoding does not model an accumulator changing between iterations.

### Where it is exact and where it is loose

Measured against the count the program can really reach, over a loop whose
trusted length bound is 100:

| Program shape | Guarantee | Truth | Result |
| --- | --- | --- | --- |
| unconditional call in the loop | `count() <= 100` | 100 | proves; exact |
| unconditional call in the loop | `count() <= 99` | 100 | fails; correct |
| call under `if row.consented:` | `count() <= 100` | ≤ 100 | proves; loose |
| call under `if row.consented:` | `count() <= 99` | ≤ 100 | fails; loose |
| call under a contradictory condition | `count() <= 0` | 0 | proves; exact |
| one call per arm of `if`/`else` | `count() <= 100` | 100 | proves; exact |
| one arm sends once, the other twice | `count() <= 200` | 200 | proves; exact |
| two `if`s that can both fire | `count() <= 200` | 200 | proves; exact |
| two loops over the same rows, exclusive conditions | `count() <= 200` | 100 | proves; loose |
| nested loops bounded 5 and 100 | `count() <= 500` | ≤ 500 | proves; loose |
| anything quantified | `count() >= 1` | ≥ 0 | fails; correct |

Two entries deserve a word. The `if`/`else` rows are exact because every fact
from one loop shares that loop's single bound variable, so the solver sees that
the arms cannot both fire *for the same row* and charges one email per row
rather than two. The two-loop row is loose for the mirror-image reason: separate
loops get separate bound variables, so the same exclusivity between them is
invisible and the encoding charges both loops in full.

### Practical consequences

- The only lever on a `count()` or `sum()` upper bound over a loop is the loop
  source's length postcondition. A tighter bound needs a trusted query that
  promises a shorter result, not a narrower `if` in the agent's code.
- `where(...)` prunes a fact when the filter is impossible independently of
  the loop index. For example, `Email.where(lambda e: e.msg == "hi")` drops
  a call whose message is always the literal `"bye"`. A satisfiable filter
  on row-derived arguments still charges the full loop length.
- Lower bounds (`>=`, `>`) are not a useful shape over a loop. Express
  "something must happen" with an unquantified call, or with a relation
  primitive that is not an aggregate.
- Do not wrap the aggregate contribution in `ForAll` when refactoring
  `SumSpec.value`. Binding `i` would change both directions of the bound, and
  the argument above no longer applies.
  `tests/test_quantified_aggregates.py` pins this.

## v1 limitations

(see [the spec](../superpowers/specs/2026-05-25-symbolic-iteration-design.md) for full list)

- For-loop bodies cannot use `break`, `continue`, `return`.
- Symbolic loop bodies cannot mutate containers or rebind an existing loop
  target. Trusted string-list results are read-only and cannot be passed as
  container arguments to other trusted effects, which could mutate aliases.
- Symbolic loops must prove that no iteration raises. Exception reachability
  includes iteration existence, so an empty loop does not interrupt later code.
- `range(...)` loops are unrolled only for `range(N)` where `N` is a literal
  integer. Symbolic bounds, `enumerate`, `zip`, and dict iteration are not
  supported in v1.
- No state accumulators across loop iterations. A quantified loop body that
  assigns to a name bound outside the loop, or to the loop variable, is
  refused, and a name the body binds does not survive the loop. Counters,
  accumulators and post-loop variables are all this shape; the unrolled
  `range(N)` and literal-sequence loops are unaffected, because concrete
  iteration models the carried state exactly. This was enforced only in the
  spec until issue #123, so such programs used to prove against a
  single-iteration trace the program could violate.
- Row field types are `str`, `int`, `bool` only, and a field may not carry a
  class-level default (`email: str = "x"` is refused at class creation). The
  prover relies on a row field never resolving to a constant.
- `where` arguments must be literal dicts at the call site, and nothing
  proves that the rows *returned* satisfy them: a postcondition cannot
  quantify over a returned list. `only_where({...})` constrains the query the
  program issues, not the rows it gets back, which the trusted layer's audit
  is what stands behind. See
  [docs/todos/quantified-postconditions.md](../todos/quantified-postconditions.md).
- `sum(lambda r: r.numeric_field)` over a quantified fact is unsupported.
  See [docs/todos/quantified-aggregates.md](../todos/quantified-aggregates.md).
- `count()` and `sum()` with a constant selector are supported over a quantified
  fact, but a row-dependent condition does not tighten an upper bound and no
  lower bound discharges. See
  [Counting and summing over a loop](#counting-and-summing-over-a-loop).
- `shares_value` across quantified relations is unsupported.
  See [docs/todos/quantified-shares-value.md](../todos/quantified-shares-value.md).

## How it works (briefly)

The for-loop handler has two modes. For literal lists/tuples and literal
`range(N)`, it unrolls the body once per concrete item and emits ordinary facts.
For trusted query results, it binds the loop variable to `query_result.at(i)`
for a fresh symbolic index `i`, runs the body once symbolically, and snapshots a
`Quantifier` frame into every emitted fact. The boolean relation primitives in
`clauz3.spec` wrap their Z3 body in `ForAll(...)` over fact quantifiers; when no
quantifiers are present, the wrapping short-circuits and the relation behaves as
before. The aggregate primitives (`count()`, `sum()`) are the exception: they
build a numeric term, not an assertion, so they scale by the loop lengths and
leave the bound variables free. See
[Counting and summing over a loop](#counting-and-summing-over-a-loop).

Each body has its own scope and obligation layers. Preconditions and assertions
receive their branch guards before being universally quantified at loop exit.
Exceptions are possible only if an iteration exists. Trusted calls inside loops
return values indexed by all enclosing iteration indices, so nested calls can
return different lists for different outer elements.

`count()` and `sum(constant)` over a quantified fact contribute
`If(cond, base * N, 0)`, where `N` is the product of the loop lengths. When the
fact's condition depends on the row (`if row.consented: send(...)`), the loop
index stays free in that term instead of being wrapped in a `ForAll`. Because
each obligation is checked as `given ∧ ¬guarantee` unsat, a free index is
effectively universally quantified: `count() <= K` demands `N <= K` whenever
some row could satisfy the condition (an upper bound on the real count), and
`count() >= K` cannot prove for `K > 0` because some row may fail it. Both
directions over-approximate the program.

When an inner loop length depends on an outer index, its contribution is guarded
by the enclosing iteration bounds. A returned length is constrained only for
outer iterations that actually happen; arbitrary out-of-range indices cannot
supply meaningful lengths. This remains a conservative count model, not an
exact encoding of arbitrary nested sums.

Column-reference equality (`e.addr == UserRow.email`) is a structural check
on the symbolic expression tree: the prover walks the Z3 expression to verify
it has the shape `<field selector>(array_select(<query result>, ?))` with the
expected schema and field.
