# ClauZ3 Python Subset

## Status

This is the current implementation spec for the Python subset used by
`clauz3` / `clauz3`.

The name "PyZ3" is tempting, but it is easy to confuse with Z3's own Python API.
This document uses **ClauZ3 Python subset** for the source language and **Z3
encoding** for the target constraints.

The implementation has two related subsets:

1. **Program subset**: agent-authored Python and trusted precondition lambdas
   that are symbolically evaluated by the vendored `deal-solver` machinery.
2. **Effect-spec subset**: relation lambdas inside `clauz3.spec`, such as
   `Email.all(lambda e: e.addr in allowed)`.

The first subset is broader and Python-like. The second is intentionally smaller
and closer to relational algebra.

## Execution Model

`clauz3 prove` takes one untrusted entry file plus zero or more trusted roots.
Trusted roots contain ordinary Python modules for both effect functions and
contract logic.

```bash
cd examples/email
uv run clauz3 prove \
  --trusted-root tools/email/trusted \
  cases/only_bob_pass.py
```

The current loader treats the entry file and trusted roots differently:

- the entry file is parsed as the untrusted program being proved
- trusted modules below `--trusted-root` are imported under their normal Python
  module names so `@contract` helpers register themselves
- imports are resolved using normal Python import roots

The prover symbolically executes the selected target function, defaulting to
`main`. Values are represented by proxy objects backed by Z3 expressions. A
proof succeeds when Z3 cannot find a counterexample to the generated
constraints.

When the command is not run from the package root used by the entry file's
imports, pass `--import-root`:

```bash
uv run clauz3 prove \
  examples/email/cases/only_bob_pass.py \
  --trusted-root examples/email/tools/email/trusted \
  --import-root examples/email
```

Real programs can combine multiple trusted domains. Repeat `--trusted-root` or
use the plural form:

```bash
uv run clauz3 prove plan.py \
  --trusted-roots tools/email/trusted tools/db/trusted
```

The same pattern exists for import roots: repeat `--import-root` or use
`--import-roots`.

## Program Subset

### Entry Points

Theorems are discovered from:

- top-level functions
- static methods on top-level classes

The target function defaults to `main`. Function arguments must have supported
type annotations.

### Supported Type Annotations

Function argument annotations support:

- `bool`
- `int`
- `float`
- `str`
- `Pattern`
- `list[T]` / `typing.List[T]`
- `set[T]` / `typing.Set[T]`
- `dict[K, V]` / `typing.Dict[K, V]`
- variable-length `tuple[T, ...]`

Selected aliases from `typing` are normalized:

- `Sequence[T]` and `Iterable[T]` as `list[T]`
- `Mapping[K, V]` and `MutableMapping[K, V]` as `dict[K, V]`
- `AnyStr` as `str`
- `FrozenSet[T]` as `set[T]`

`Sized` is also mapped internally, but it is not a recommended annotation for
new code because it does not describe an element type.

Unsupported or missing annotations make the theorem unprovable.

### Supported Statements

Inside functions, the current evaluator supports:

- `assert expr`
- expression statements
- assignment to a local name: `x = expr`
- assignment to an indexed container item: `xs[i] = value`
- `return expr`
- `if expr: ... else: ...`
- `raise SomeException`
- nested function definitions
- `import`, `from ... import ...`, `global`, and `pass` as no-ops

`if` statements are symbolic. The evaluator executes both branches and merges
variables, assertions, returns, exceptions, and trusted-call facts under the
branch condition.

Unsupported statements include:

- `for` and `while`
- `with`
- `try` / `except` / `finally`
- `async` / `await`
- `match`
- `del`
- augmented assignment such as `x += 1`
- annotated assignment such as `x: int = 1`
- slice assignment
- comprehensions other than list comprehensions

Unsupported statements should be treated as proof failures or partial proofs,
not as permission to run.

### Supported Expressions

The program subset supports:

- constants: `bool`, `int`, `float`, `str`
- names and local variables
- attributes
- calls
- lambdas
- conditional expressions: `a if cond else b`
- list, set, dict, and tuple literals
- one-generator list comprehensions, with optional `if` filters
- indexing: `x[i]`
- slicing without a step: `x[start:stop]`
- boolean expressions: `and`, `or`
- unary operators: `+`, `-`, `~`, `not`
- binary operators:
  - arithmetic: `+`, `-`, `*`, `/`, `//`, `**`, `%`
  - bitwise: `&`, `|`, `^`, `<<`, `>>`
- comparisons:
  - `<`, `<=`, `>`, `>=`, `==`, `!=`
  - `in`, `not in`
- chained comparisons (`0 < x <= 200`), which lower to the conjunction of
  pairwise comparisons matching Python semantics; intermediate expressions are
  evaluated once

Boolean operators are symbolic combinators. Do not rely on Python
short-circuiting to avoid unsupported expressions or side effects.

### Calls

Supported calls include:

- calls to local functions in the proved source
- calls to trusted functions imported from trusted roots
- calls to registered domain contract functions when evaluating guarantees
- selected builtins
- selected methods on supported proxy types

Positional and keyword arguments are supported. `**kwargs` unpacking is not.

Supported builtins currently include:

- `print(...)`, treated as a no-op
- `sum(...)`
- `min(...)` and `max(...)`
- `ord(...)`
- `abs(...)`
- `len(...)`
- `int(...)`, `float(...)`, `str(...)`, `bool(...)`
- `set()`, `list()`, and `dict()` with no argument or an already-compatible
  proxy value

Supported methods are type-specific and incomplete. Examples include:

- string containment via `"x" in s`
- `s.startswith(prefix)`
- `s.endswith(suffix)`
- `s.find(sub)`
- `s.index(sub)`
- list/tuple/set/dict containment where implemented
- indexing and `len(...)` for strings and sequence-like values

Do not assume arbitrary Python library calls are supported.

## Deal Contracts

Trusted stubs and normal helper functions can use Deal decorators. The prover
currently interprets:

- `@deal.pre(lambda ...)`
- `@deal.post(lambda result: ...)`
- `@deal.ensure(lambda ...: ...)`
- `@deal.raises(SomeException)`
- `@deal.has(...)`

Only lambda contracts are interpreted for pre/post/ensure. Non-lambda contracts
are ignored by the current evaluator.

`@deal.has(...)` marks a trusted side-effect boundary. When a called function
has one or more `has` markers:

1. its preconditions are added as proof obligations
2. its body is not evaluated
3. the call is recorded as an effect fact
4. a dummy truthy value is returned to the symbolic evaluator

Trusted functions should currently be designed as `None`-returning side-effect
stubs. Returning meaningful symbolic values from trusted functions is not part
of the current contract.

## Effect Facts

A trusted call records:

```python
FactInfo(
    name="send_email",
    markers=("trusted",),
    args={"addr": <symbolic str>, "msg": <symbolic str>},
    cond=<symbolic reachability condition>,
)
```

The fact's schema is inferred from the trusted function signature. The fact can
be queried by function name or marker:

```python
Email = effect("send_email")
Trusted = effect("trusted")
```

Prefer function-name relations when possible. Marker relations are useful for
cross-cutting policies but may combine functions with different field shapes.

## Guarantees

`@clauz3.guarantee(...)` is a runtime no-op. The prover reads it from the
AST and evaluates its argument as a symbolic guarantee after executing the
target body.

Example:

```python
@clauz3.guarantee(emails.only(["bob@example.com"]))
def main() -> None:
    send_email("bob@example.com", "hi")
```

## Effect-Spec Subset

Domain logic uses `clauz3.spec`:

```python
from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees


Email = effect("send_email")


@contract
def no_guarantees() -> ContractSpec:
    return core_no_guarantees()


@contract
def only(addresses: list[str]) -> ContractSpec:
    return Email.all(lambda e: e.addr in addresses)
```

The `@contract` function itself is currently executed as Python builder code.
This is a known trust boundary. The relation lambda body is parsed from source
and compiled as a small expression subset.

### Relation Primitives

Currently supported:

- `no_guarantees()`
- `Relation.all(lambda row: predicate)`
- `Relation.where(lambda row: predicate)`
- `Relation.empty()`
- `Relation.distinct(lambda row: key)`
- `Relation.count() <= limit`
- `Relation.count() < limit`
- `Relation.count() >= limit`
- `Relation.count() > limit`
- `Relation.sum(lambda row: numeric_value) <= limit`
- `Relation.sum(lambda row: numeric_value) < limit`
- `Relation.sum(lambda row: numeric_value) >= limit`
- `Relation.sum(lambda row: numeric_value) > limit`
- `FilteredRelation.shares_value(other, lambda row: key)`

`where(...)` returns a filtered relation, so it composes with the other
relation primitives:

```python
Email.where(lambda e: e.addr == "bob@example.com").count() <= 2
```

This proves policies like "Bob is emailed at most twice."

`shares_value(...)` is an existential join between two filtered relation views:

```python
bob = Email.where(lambda e: e.addr == "bob@example.com")
ann = Email.where(lambda e: e.addr == "ann@example.com")
bob.shares_value(ann, lambda e: e.msg)
```

This proves policies like "Bob and Ann receive the same email content."

Reachability conditions are handled by the primitive. A contract author should
write:

```python
Email.all(lambda e: e.addr in addresses)
```

not:

```python
reachable(e) implies e.addr in addresses
```

### Relation Lambda Syntax

Relation lambdas support:

- constants: `bool`, `int`, `str`
- captured names whose values are supported literals or proxy values
- list literals
- row attributes: `e.addr`, `w.amount`
- comparisons: `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not in`
- boolean operators: `and`, `or`, `not`
- numeric `+` and `-`
- `len(...)` over supported row values such as strings
- the string methods `startswith` and `endswith`, as in
  `e.path.startswith(root)`

Relation lambdas do not support:

- arbitrary function calls other than `len(...)`
- method calls other than the whitelisted `startswith` / `endswith`
- loops or comprehensions
- mutation
- dynamic attribute access
- chained comparisons (unlike the program subset, which lowers them
  correctly; in relation lambdas, write `a < b and b < c`)
- bare row values such as `lambda e: e`

Unsupported lambda syntax raises `UnsupportedError`.

## Fail-Closed Rule

Unsupported syntax, unsupported types, and unresolved names should not be
treated as safe. The intended contract is:

> If ClauZ3 cannot prove the requested guarantee over the supported subset, the
> program has not earned approval.

The current CLI exits non-zero when any proof obligation fails. Some unsupported
program constructs may appear as partial/skipped proof results; those should be
handled as rejection by any runner.

## Known Gaps

- Contract helper builder bodies are executed rather than fully AST-validated.
- Trusted functions with meaningful return values are not modeled.
- Chained comparisons in relation lambdas raise `UnsupportedError` rather
  than being lowered (the program-subset evaluator handles them correctly).
- Runtime receipt enforcement has not been implemented.
- Marker-level effect schemas are not validated up front.
- There is no formal grammar file or conformance test suite for the subset yet.
- The language is not yet mapped to a Datalog, SQL, OWL, or SHACL frontend.

## Design Direction

The subset should move toward:

- a small named formal grammar
- tests for every accepted and rejected construct
- fully static validation of `@contract` helper bodies
- deterministic explanations for approval dialogs
- optional renderings into relational logic and description logic
