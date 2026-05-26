# Symbolic Iteration Over Trusted Return Values

## Status

Drafted from brainstorming session 2026-05-25. Pending user review before
plan-writing. Single-PR delivery on an isolated worktree.

## Problem

`clauz3` proves contracts about agent-authored Python by symbolically
executing the program and emitting facts at every trusted-function boundary.
The relation algebra in `src/clauz3/spec.py` (`all`, `empty`, `where`,
`count`, `sum`, `distinct`, `shares_value`) reasons over those facts.

Today's prover handles a closed set of statement shapes — `If`, `Assign`,
`Return`, `Assert`, `Expr`, `Raise`, `Pass`, imports — and a corresponding
expression subset. There is no `astroid.For` handler in `_eval_stmt.py`, no
`ListComp` handler in `_eval_expr.py`, and no `m_iter` method on proxies.
Trusted calls in `_proxies/_func.py:71-77` always return
`types.bool.val(True)` — the return value is discarded, so a call like
`db_query("users")` symbolically evaluates to a boolean, not a list of rows.

This blocks the most natural pattern for agents working with structured data:

```python
for row in db_query("users", where={"consented": True}):
    send_email(row.email, "Newsletter")
```

The agent can't write this code at all (the for-loop fails), and even if it
could, no contract surface exists to say "every email recipient came from
the `email` column of the `users` query."

Encapsulating the pattern in a single trusted bulk operation (e.g.
`email_from_table(table, address_column, where, message)`) is provable
today but constrains the agent to fixed query shapes — a workaround, not
the goal. The goal is for agents to write idiomatic code and have the
prover follow.

## Goals

- Agents write idiomatic for-loops over trusted query returns:
  `for row in db_query(...): send_email(row.email, msg)`.
- Contracts reference structured value sources:
  `Email.all(lambda e: e.addr == UserRow.email)`.
- The prover treats the loop as ∀-quantified — no `limit` parameter, no
  bound annotation in agent code.
- Existing examples (email, bank) continue to prove without regression.

## Non-goals (v1)

- `break`, `continue`, `return` inside loops.
- Local-state accumulators across loop iterations (`total += 1`).
- Iterables other than `QueryResultSort` or literal lists (`range(n)`,
  `enumerate`, `zip`).
- `sum(lambda r: r.numeric_field)` over quantified facts (selector
  depending on bound vars). Requires Z3 recursive-function reasoning or
  bounded-unrolling fallback; deferred to v2 with a dedicated todo doc.
- `shares_value(other, key)` between two quantified relations. Deferred.
- Field types other than `str`, `int`, `bool`. `Union`, `Optional`,
  forward references, generics, and recursive types raise a clear error
  at schema-class definition time.
- `where` arguments that are computed dicts rather than literal-dict
  syntax at the call site.

## Approach

True ∀-quantification (Approach B from brainstorming). The for-loop emits
facts carrying a quantifier prefix; the relation algebra learns to read
quantified facts and emits Z3 `ForAll` formulas. The alternative — bounded
unrolling (Approach A) — was rejected because it forces an explicit
`limit` parameter into the agent's code and degrades linearly with bound
size. Approach B costs more solver work but keeps the agent-facing surface
clean.

## Architecture

Three new mechanisms, plus updates to the relation algebra:

### Trusted-return materialization

`src/clauz3/_vendor/deal_solver/_proxies/_func.py:71-77` is the trusted-call
boundary. Today it records a `FactInfo` and returns `bool.val(True)`. The
extension reads the trusted function's return annotation; if it is
`list[<Row-subclass>]`, the boundary constructs a `QueryResultSort`
(symbolic Z3 sequence) and binds it as the call's return value instead of
the boolean. Postconditions on the trusted call (e.g.
`@deal.post(lambda result: len(result) <= 100)`) become Z3 constraints on
`QueryResultSort.length_expr`.

### For-loop handler

A new `@eval_stmt.register(astroid.For)` in `_eval_stmt.py` handles
iteration. When the iterable's `ProxySort` is a `QueryResultSort`, the
handler allocates a fresh symbolic index variable, binds the loop variable
to `query_result.at(i)` (a `RowSort`), pushes a `Quantifier` frame onto
`ctx.quantifiers`, runs the body once symbolically, then pops the frame.
Every fact emitted during the body's execution snapshots the current
quantifier stack into its `quantifiers` field.

Literal-list iterables fall back to today's behavior (unroll by binding
the loop variable to each element). Any other iterable type raises
`UnsupportedError`.

### Quantifier-aware relations

Each primitive in `src/clauz3/spec.py` learns to wrap its Z3 body in
`ForAll(...)` over each fact's quantifiers:

```python
z3.ForAll(
    [q.bound_var for q in fact.quantifiers],
    z3.Implies(
        z3.And(*bounds_constraints, fact.cond),
        relation_body(fact.args),
    ),
)
```

When `fact.quantifiers` is empty, the `ForAll` is short-circuited — relations
emit their body directly, matching today's behavior. This is the no-regression
path for existing email and bank examples.

### Column references in the DSL

A new `ColumnRef(schema, field)` marker, plus a metaclass on `clauz3.Row`
that returns `ColumnRef` instances for class-level attribute access.
Compared against an arg's symbolic expression via a structural matcher that
walks the Z3 expression tree.

## Data shapes

```python
@dataclass(frozen=True)
class RowSort(ProxySort):
    """Symbolic row of a known schema."""
    schema: type                       # the clauz3.Row subclass
    expr: z3.DatatypeRef               # Z3 datatype value

    def field(self, name: str, *, ctx) -> ProxySort: ...


@dataclass(frozen=True)
class QueryResultSort(ProxySort):
    """Symbolic return of a trusted call typed list[Row]."""
    row_schema: type
    array_expr: z3.ArrayRef            # Array(Int, RowDatatype)
    length_expr: z3.ArithRef           # symbolic non-negative length
    source: tuple[str, dict[str, ProxySort]]  # (trusted_name, bound_args)

    def at(self, i: z3.ArithRef, *, ctx) -> RowSort: ...


@dataclass(frozen=True)
class Quantifier:
    """One for-loop frame's binding."""
    bound_var: z3.ArithRef             # the index symbol
    source: QueryResultSort
    lower: z3.ArithRef                 # always IntVal(0) in v1
    upper: z3.ArithRef                 # source.length_expr


@dataclass(frozen=True)
class FactInfo:
    """Extended with optional quantifier prefix."""
    name: str
    markers: tuple[str, ...]
    args: dict[str, ProxySort]         # may reference bound_vars
    cond: BoolSort
    quantifiers: tuple[Quantifier, ...] = ()


@dataclass(frozen=True)
class ColumnRef:
    """Marker for contracts referring to a column source."""
    schema: type
    field: str
```

Z3 datatypes for `RowSort` are generated lazily, one per `Row` subclass,
keyed by class identity. Field types are mapped: `str → String`,
`int → Int`, `bool → Bool`. Any other type raises at class-definition
time.

## For-loop handler semantics

When `eval_stmt` dispatches `astroid.For`:

1. Evaluate `node.iter`. If the result is not a `QueryResultSort` or a
   literal-list proxy, raise `UnsupportedError`.
2. If `node.target` is anything other than a single `astroid.AssignName`,
   raise `UnsupportedError` (no tuple-unpack in v1).
3. Allocate a fresh `z3.Int`, name `loop_idx_<n>`.
4. Construct a `RowSort` representing `query_result.at(i)`.
5. Push a fresh scope layer; bind the loop variable name to the `RowSort`.
6. Push a `Quantifier` onto `ctx.quantifiers`.
7. Run each statement in `node.body` through `eval_stmt`. Facts emitted
   inside copy `tuple(ctx.quantifiers.layer)` into their `quantifiers`
   field.
8. Pop the quantifier and scope layer.

If any restricted construct is encountered (`break`, `continue`, `return`,
name reassignment of the loop variable or any outer-scope name,
`for-else`), the handler raises `UnsupportedError` with a message naming
the construct and pointing at `docs/python-subset.md`.

Nested loops accumulate quantifiers in source order. A fact emitted inside
two nested loops has two quantifiers, outer first.

## Relation primitives under quantification

For every fact, relations wrap their body:
```python
z3.ForAll(
    [q.bound_var for q in fact.quantifiers],
    z3.Implies(
        z3.And(
            *[z3.And(q.lower <= q.bound_var, q.bound_var < q.upper)
              for q in fact.quantifiers],
            fact.cond,
        ),
        body(fact.args),
    ),
)
```

If `fact.quantifiers` is empty, the wrapper short-circuits and the body is
emitted directly (no `ForAll`, no implication chain).

### `all(pred)` and `empty()`

Already universally quantified — natural extension. `empty` is
`all(lambda _: False)` with the same wrapping.

### `where(pred)`

Composable filter via `FilteredRelation`. The predicate is conjoined with
`fact.cond` inside the `ForAll` body. No new mechanics.

### `count() <= N` (and other comparisons)

Per-fact contribution:
- *Empty quantifiers*: `If(fact.cond, 1, 0)`.
- *Quantifiers with body-cond independent of bound vars*: `If(fact.cond,
  product(q.upper for q in quantifiers), 0)`. Exact.
- *Quantifiers with body-cond depending on bound vars*: upper-bound by
  `product(q.upper for q in quantifiers)`. Sound but loose; `count <= N`
  proofs still discharge when the loose bound fits.

For `count <= N` to prove, the prover needs an upper bound on each
quantifier's source length. This comes from the trusted call's
postcondition (e.g., `@deal.post(lambda result: len(result) <= 100)`).
Without it, the proof fails with a clear pointer at the missing
postcondition.

### `sum(selector)`

v1 limited:
- *Empty quantifiers*: today's behavior.
- *Selector returns a captured constant* (`lambda r: 1`,
  `lambda r: 5`): contribution is `constant * product(q.upper for q in
  quantifiers)`. Works; supports count-via-sum idiom.
- *Selector depends on bound vars* (`lambda r: r.amount`): `UnsupportedError`
  pointing at `docs/todos/quantified-aggregates.md`.

### `distinct(key)`

Produces a `∀∀` formula:
```python
∀ fact1, fact2 (over all bound vars):
   different_iteration ∧ both_reachable → key1(args1) ≠ key2(args2)
```

For the same-fact case, this expands to
`∀ i, j, i ≠ j → key(args(i)) ≠ key(args(j))`. Only provable when the
trusted layer's postcondition establishes per-key uniqueness. Missing
postcondition → proof fails with a pointing message.

Z3 quantifier instantiation may time out on hostile inputs. v1 uses
E-matching with explicit patterns derived from the goal shape; MBQI as
fallback; per-goal solver budget (30s default, configurable). Timeout
raises a specific error rather than hanging.

### `shares_value(other, key)`

Deferred when either side is a quantified relation. Raises
`UnsupportedError` pointing at `docs/todos/quantified-shares-value.md`.

## Column references in the DSL

### Marker construction

`clauz3.Row` is a base class with a metaclass that intercepts class-level
attribute access:

```python
class _RowMeta(type):
    def __getattribute__(cls, name):
        # Class-level field access at proof time returns a ColumnRef.
        # Instance access goes through the normal field descriptor.
        annotations = type.__getattribute__(cls, '__annotations__')
        if name in annotations and not name.startswith('_'):
            return ColumnRef(schema=cls, field=name)
        return type.__getattribute__(cls, name)


class Row(metaclass=_RowMeta):
    """Base class for trusted-layer row schemas.

    Subclasses declare fields like a dataclass:
        class UserRow(Row):
            name: str
            email: str
            consented: bool

    Instances behave like immutable records. Class-level attribute access
    (e.g., UserRow.email) returns a ColumnRef marker for use in contracts.
    """
```

Instance behavior: the metaclass also wires up an underlying immutable
record type (e.g., a generated NamedTuple held by composition or
`__slots__`-based class) so trusted functions can return `UserRow(name=...,
email=...)` instances normally. `dataclass_transform` decoration tags it
for static-analyzer support.

If the metaclass approach turns out hostile during implementation
(see Risks), the design falls back to an explicit `column(schema, field)`
helper without losing the headline example.

### Compiler dispatch

`_eval_compare` in `clauz3.spec` is extended: when either operand
evaluates to a `ColumnRef`, dispatch to `_compare_column_ref` instead of
`m_eq`. `_as_proxy` recognizes `ColumnRef` and returns a wrapper that the
compare handler can detect.

### Structural matcher

`_compare_column_ref(arg_proxy, column_ref, ctx)` walks the Z3 expression
tree underlying `arg_proxy`:

1. Is it a Z3 selector application on a datatype value?
2. Is the selector the one for `column_ref.field` of `column_ref.schema`'s
   generated datatype?
3. Is the inner value an array select on a `QueryResultSort.array_expr`
   whose `row_schema` matches `column_ref.schema`?

All three conditions met → return Z3 `BoolVal(True)`. Otherwise →
`BoolVal(False)`. This is a Python-side syntactic check on the Z3
expression, not a SAT problem.

### Contract-author example

A trusted-layer contract that uses the column reference:

```python
from clauz3.spec import contract, effect, ContractSpec

@contract
def addresses_from(schema: type, field: str) -> ContractSpec:
    column = getattr(schema, field)            # returns ColumnRef via metaclass
    return effect("send_email").all(
        lambda e: e.addr == column,
    )
```

Agent-side usage is then:

```python
@clauz3.guarantee(emails.addresses_from(UserRow, "email"))
def main() -> None:
    for row in db_query("users", where={"consented": True}):
        send_email(row.email, "Newsletter")
```

### Composition

`or` and `and` over column-refs and other predicates work as expected:
```python
Email.all(lambda e: e.addr == UserRow.email or e.addr in safe_list)
```
Each side independently evaluates to a Z3 bool; the existing `BoolOp`
handler composes them.

### Mixed-source programs

```python
send_email("admin@example.com", "manual")   # literal arg
for row in db_query("users", where=W):
    send_email(row.email, "newsletter")
@clauz3.guarantee(emails.addresses_from(UserRow, "email"))
```

Fact 1's `addr` is a `StringVal`; matcher returns `BoolVal(False)`;
predicate is `False`; contract fails. Fact 2 matches; contract holds on
that fact. Overall the contract fails because of Fact 1, with the error
pointing at the literal send. This is correct behavior.

## Delivery

Single PR titled
`feat: symbolic iteration over trusted return values`, on an isolated
worktree per `using-git-worktrees`. Six commits, each independently
buildable with full CI green:

1. **Foundation**: `clauz3.Row` + metaclass, `RowSort`, `QueryResultSort`,
   return materialization in `_proxies/_func.py`, `FactInfo.quantifiers`
   field (default empty). No behavior change on existing examples.
2. **For-loop handler**: `@eval_stmt.register(astroid.For)`,
   `ctx.quantifiers` stack, fact-emission snapshot, v1 restrictions.
3. **Quantifier-aware relations**: all five primitives wrap in `ForAll`;
   `count` length-product logic; `sum` v1 limitation; `distinct` ∀∀
   formulas with E-matching patterns.
4. **Column references**: `ColumnRef` marker, `_compare_column_ref`,
   `_eval_compare` extension. The headline lights up here.
5. **Worked example**: `examples/email-from-db/` end-to-end —
   `tools/db/trusted/{effects.py,schemas.py,contracts.py}`, reuse of
   `tools/email/trusted/`, `cases/` with 8–10 pass/fail cases, `users.csv`
   sample data, `Justfile`, `AGENTS.md`, `CLAUDE.md`, `.claude/`.
6. **Docs**: `docs/symbolic-iteration.md` (design summary reflecting
   what shipped), `docs/todos/quantified-aggregates.md`,
   `docs/todos/quantified-shares-value.md`, updates to `README.md`,
   `docs/effect-specs.md`, top-level `AGENTS.md`,
   `docs/integration-testing.md` extended with a db-flavored recipe.

Total ~2000–3000 lines. ~8–14 days of focused work.

TDD per commit: tests written before implementation, red → green →
refactor, per the `test-driven-development` skill rules. Each commit's
test list is part of its pre-implementation plan.

## Testing strategy

Six test layers, narrowest to broadest:

**Proxy unit tests** (`tests/test_row_proxy.py`)
Schema parsing → Z3 datatype; field selectors return typed `ProxySort`;
`QueryResultSort.at(i)` returns `RowSort`; trusted-call postcondition on
`len(result)` flows into Z3 constraints on `length_expr`.

**For-loop handler tests** (`tests/test_for_loop.py`)
Single loop → one fact with one quantifier; nested → two quantifiers in
order; branchy body → cond correct; each v1 restriction raises with the
right message.

**Quantifier-aware relation tests** (`tests/test_quantified_relations.py`)
Per primitive: positive case, negative case, edge cases (length 0, body
cond independent vs dependent). Specifically: `count <= N` exact when
independent / upper-bound when dependent; `sum(constant)` works,
`sum(row.field)` raises; `distinct` proves with postcondition, fails
clearly without.

**Column-reference tests** (`tests/test_column_ref.py`)
Match succeeds for query-sourced args; fails for literals; fails on schema
mismatch; fails on field mismatch; `or`-composition with `in`-allowlists;
nested-loop with two schemas resolves independently; mixed-source program
fails on the literal fact with a pointing error.

**Integration tests** (`tests/test_examples.py` extended)
Parametrized over `examples/email-from-db/cases/`. 8–10 cases covering
the threat-model matrix (more recipients than asked, wrong people, wrong
column, write attempts on a read-only trusted layer, missing
postconditions).

**Property-based tests** (`tests/test_quantified_properties.py`)
Hypothesis for two payoff cases:
- Quantifier composition: random loop nesting depths 1–3, random body
  shapes; verify the emitted `ForAll` has the right bound variables in the
  right order.
- Soundness: random schemas of `str`/`int`/`bool` fields; synthesize an
  agent program that violates a randomly-chosen contract → prover
  rejects; synthesize one that satisfies → prover accepts.

**Performance gates** (CI)
- Headline example proves in < 5s.
- `distinct` contract with postcondition proves in < 10s.
- Existing email and bank examples regress no more than 20% (empty
  `quantifiers` short-circuit).
- Per-goal Z3 timeout 30s, configurable. Timeouts fail loudly, never
  hang.

## Risks and mitigations

**Z3 layer composition risk.** `Array(Int, RowDatatype) + ∀-quantifier`
in our pattern shapes might hit an incompleteness or performance cliff
we can't tune around.
*Mitigation*: spike commit 1 plus one hardcoded Z3-level test
**before** committing to the full PR scope. ~half a day. If the spike
fails, regroup on approach (bounded unrolling or hybrid).

**`clauz3.Row` metaclass turns hostile.** Custom metaclasses combined with
dataclass-like semantics are historically fragile.
*Mitigation*: avoid inheriting from `NamedTuple` directly; build our own
runtime with `@dataclass_transform` for static analyzers. If hostile,
fall back to `column(schema, field)` helper without losing the headline.

**`distinct` over symbolic-length quantified facts times out.** `∀∀` is
hard for Z3.
*Mitigation*: explicit instantiation patterns we own; per-goal budget;
documented as best-effort. Headline newsletter case does not require
`distinct`, so this risk does not block the headline.

**Performance regression on existing examples.**
*Mitigation*: short-circuit when `quantifiers` is empty; CI gate at 20%.

**Computed `where` dicts.** Symbolic-dict-equality is beyond v1.
*Mitigation*: v1 requires literal-dict syntax at the call site; computed
dicts raise. Documented in AGENTS.md.

**Schema-type edges.** Forward refs, generics, `Union`, recursive types.
*Mitigation*: v1 supports `str`, `int`, `bool` only; class-definition
time error otherwise.

**Self-referential quantifiers.** `for u in users: for u2 in users: ...`.
*Mitigation*: dedicated test for fresh-bound-var-per-frame.

**Single PR stalls in review.**
*Mitigation*: linear commit structure; headline example as a runnable
anchor; respond to feedback in place rather than restructuring.

## Open questions

- Should `count <= N` over a quantifier with body-cond dependent on the
  bound var attempt exact reasoning via Z3 recursive functions, or stay
  with the loose upper bound? Current design chooses the loose bound.
  Revisit if real examples hit the looseness as a wall.
- Does `dataclass_transform` get along with our metaclass cleanly across
  mypy / pyright / Pyre? Verify during the foundation commit's spike.
- Per-goal Z3 budget default: 30s is a guess. Calibrate against the test
  suite's slowest cases.

## References

- `docs/effect-specs.md` — current relation algebra and the (now-renamed)
  "provenance facts" future-work bullet.
- `docs/background.md` — related work (FORGE) and design framing.
- `docs/todos/user-approval-dialog.md` — execution model and import
  checking (independent but related).
- `src/clauz3/_vendor/deal_solver/` — vendored prover that this work
  extends.
- `src/clauz3/spec.py` — relation algebra extended in commit 3.
- Brainstorming transcript — recorded design rationale for B over A/C,
  NamedTuple-as-Row, idiomatic for-loop over comprehension, single PR.
