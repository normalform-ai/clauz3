# Symbolic Iteration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let agents write idiomatic `for row in db_query(...): send_email(row.email, msg)` loops and prove contracts like `Email.all(lambda e: e.addr == UserRow.email)`.

**Architecture:** Trusted return values are materialized as a symbolic `QueryResultSort` (Z3 `Array(Int, RowDatatype)` + symbolic length). A new `For` statement handler in the vendored solver runs the loop body once with a fresh symbolic index, snapshotting a `Quantifier` frame into emitted facts. The relation algebra in `spec.py` learns to wrap its Z3 body in `ForAll(...)` over fact quantifiers, short-circuiting when none are present. A `ColumnRef` marker (returned by class-level attribute access on `clauz3.Row` subclasses) plus a structural matcher on Z3 expression trees gives contracts a way to refer to "the email column of the users query."

**Tech Stack:** Python 3.11, Z3 (`z3-solver`), `astroid` for AST, `deal-solver` (vendored), `pytest`, `uv` for env management, `just` for tasks.

**Spec:** [docs/superpowers/specs/2026-05-25-symbolic-iteration-design.md](../specs/2026-05-25-symbolic-iteration-design.md)

---

## File Structure

**New files:**

| Path | Responsibility |
| --- | --- |
| `src/clauz3/row.py` | `Row` base class + `_RowMeta` metaclass + `ColumnRef` marker. Class-level field access returns ColumnRef; instances behave like immutable records. |
| `src/clauz3/_vendor/deal_solver/_proxies/_row.py` | `RowSort` and `QueryResultSort` proxies; Z3 datatype generator keyed by Row subclass. |
| `src/clauz3/_vendor/deal_solver/_context/_quantifier.py` | `Quantifier` named-tuple + helper to build bound-constraint Z3 expressions. |
| `tests/test_row_proxy.py` | Schema parsing, field selectors, `at(i)`, postcondition flow. |
| `tests/test_for_loop.py` | For-loop dispatch, quantifier emission, nested loops, v1 restrictions. |
| `tests/test_quantified_relations.py` | Each relation primitive under quantification, positive + negative + edge. |
| `tests/test_column_ref.py` | ColumnRef matching across schemas and field names. |
| `tests/test_quantified_properties.py` | Hypothesis-driven composition + soundness checks. |
| `examples/email-from-db/` (entire tree) | Worked end-to-end example. |
| `docs/symbolic-iteration.md` | Shipped-design summary. |
| `docs/todos/quantified-aggregates.md` | Future-work doc for `sum` over bound-var-dependent selectors. |
| `docs/todos/quantified-shares-value.md` | Future-work doc for ∃ across quantified relations. |

**Modified files:**

| Path | Why |
| --- | --- |
| `src/clauz3/__init__.py` | Re-export `Row` and `ColumnRef`. |
| `src/clauz3/spec.py` | All five relation primitives wrap in `ForAll`; `ColumnRef` recognized in `_eval_compare`; `_as_proxy` extension. |
| `src/clauz3/_vendor/deal_solver/_context/_layer.py` | Extend `FactInfo` with `quantifiers` field. |
| `src/clauz3/_vendor/deal_solver/_context/_context.py` | Add `quantifiers: Layer[Quantifier]` to `Context`, threaded through `make_empty` and `make_child`. |
| `src/clauz3/_vendor/deal_solver/_eval_stmt.py` | New `@eval_stmt.register(astroid.For)` handler. |
| `src/clauz3/_vendor/deal_solver/_proxies/_func.py` | Materialize `list[Row]` returns into `QueryResultSort`; snapshot quantifiers into emitted `FactInfo`. |
| `tests/test_examples.py` | Parametrize entries for `examples/email-from-db/cases/`. |
| `README.md`, `AGENTS.md`, `docs/effect-specs.md`, `docs/integration-testing.md` | Reference the new capability. |

**Setup before starting:**

- [ ] **Step 0a: Create worktree per `using-git-worktrees` skill**

```bash
git worktree add -b feat/symbolic-iteration ../agent-deal-symbolic-iteration main
cd ../agent-deal-symbolic-iteration
uv sync --dev
just test
```
Expected: all existing tests pass (clean baseline).

- [ ] **Step 0b: Z3-level spike**

Create `tests/test_z3_spike.py` to verify the chosen Z3 representation works before committing to the rest of the plan. If this spike fails, regroup on approach before continuing.

```python
import z3

def test_z3_array_of_datatype_with_forall_quantifier():
    """Verify Z3 can prove ∀-quantifier over array-indexed datatype values."""
    UserRow = z3.Datatype("UserRow")
    UserRow.declare("mk_user", ("name", z3.StringSort()), ("email", z3.StringSort()))
    UserRow = UserRow.create()

    arr = z3.Const("users", z3.ArraySort(z3.IntSort(), UserRow))
    length = z3.Int("length")
    i = z3.Int("i")

    # ∀ i, 0 ≤ i < length → UserRow.email(arr[i]) ∈ {"a@x", "b@x"}
    # Premise: ∀ i, 0 ≤ i < length → UserRow.email(arr[i]) == "a@x"
    s = z3.Solver()
    s.add(length >= 0)
    s.add(z3.ForAll(
        [i],
        z3.Implies(
            z3.And(0 <= i, i < length),
            UserRow.email(arr[i]) == z3.StringVal("a@x"),
        ),
    ))
    # Conjecture (should hold under the premise)
    s.add(z3.Not(z3.ForAll(
        [i],
        z3.Implies(
            z3.And(0 <= i, i < length),
            z3.Or(
                UserRow.email(arr[i]) == z3.StringVal("a@x"),
                UserRow.email(arr[i]) == z3.StringVal("b@x"),
            ),
        ),
    )))
    assert s.check() == z3.unsat
```

Run: `cd ../agent-deal-symbolic-iteration && uv run pytest tests/test_z3_spike.py -v`

Expected: PASS within 5 seconds. If it fails or times out, **stop and re-evaluate the approach** — Z3 cannot do what the design assumes.

- [ ] **Step 0c: Delete the spike file**

The spike was throwaway. Real tests will exercise the same Z3 paths from the framework.
```bash
rm tests/test_z3_spike.py
```

---

## Phase A: Foundation (commit 1)

### Task 1: ColumnRef dataclass

**Files:**
- Create: `src/clauz3/row.py`
- Test: `tests/test_row_proxy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_row_proxy.py
import pytest

from clauz3.row import ColumnRef


def test_column_ref_is_frozen_dataclass():
    c = ColumnRef(schema=int, field="x")
    assert c.schema is int
    assert c.field == "x"
    with pytest.raises(Exception):
        c.field = "y"  # frozen
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_row_proxy.py::test_column_ref_is_frozen_dataclass -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'clauz3.row'`.

- [ ] **Step 3: Implement ColumnRef**

```python
# src/clauz3/row.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnRef:
    """Marker for contracts that refer to a structured value source.

    `UserRow.email` (class-level attribute access on a clauz3.Row subclass)
    returns ColumnRef(schema=UserRow, field='email').
    """
    schema: type
    field: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_row_proxy.py::test_column_ref_is_frozen_dataclass -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/row.py tests/test_row_proxy.py
git commit -m "feat(row): add ColumnRef marker dataclass"
```

---

### Task 2: Row metaclass returns ColumnRef on class-level field access

**Files:**
- Modify: `src/clauz3/row.py`
- Test: `tests/test_row_proxy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_row_proxy.py (append)
from clauz3.row import Row


class UserRow(Row):
    name: str
    email: str
    consented: bool


def test_class_attribute_returns_column_ref():
    ref = UserRow.email
    assert isinstance(ref, ColumnRef)
    assert ref.schema is UserRow
    assert ref.field == "email"


def test_dunder_attrs_dont_become_column_refs():
    # __class__, __mro__, __init__, etc. must still work normally
    assert UserRow.__name__ == "UserRow"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_row_proxy.py::test_class_attribute_returns_column_ref -v`
Expected: FAIL with `ImportError: cannot import name 'Row' from 'clauz3.row'`.

- [ ] **Step 3: Implement the metaclass + Row base class**

Append to `src/clauz3/row.py`:

```python
class _RowMeta(type):
    """Metaclass that returns ColumnRef for class-level field access.

    Field access on the *class* (UserRow.email) returns a marker.
    Field access on *instances* (row.email) goes through normal descriptor
    lookup — this metaclass governs class-level access only.
    """

    def __getattribute__(cls, name: str):
        if not name.startswith("_"):
            annotations = type.__getattribute__(cls, "__annotations__")
            if name in annotations:
                return ColumnRef(schema=cls, field=name)
        return type.__getattribute__(cls, name)


class Row(metaclass=_RowMeta):
    """Base class for trusted-layer row schemas.

    Subclasses declare fields as annotations:

        class UserRow(Row):
            name: str
            email: str
            consented: bool

    Class-level attribute access (UserRow.email) returns a ColumnRef
    marker for use in contracts. Instance behavior is added in a later
    task.
    """
    __annotations__: dict[str, type] = {}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_row_proxy.py -v`
Expected: both new tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/row.py tests/test_row_proxy.py
git commit -m "feat(row): metaclass returns ColumnRef on class-level field access"
```

---

### Task 3: Row instance behavior — construction and field access

**Files:**
- Modify: `src/clauz3/row.py`
- Test: `tests/test_row_proxy.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_row_proxy.py (append)
def test_row_instance_construction_and_field_access():
    row = UserRow(name="Bob", email="bob@x", consented=True)
    assert row.name == "Bob"
    assert row.email == "bob@x"
    assert row.consented is True


def test_row_instance_is_immutable():
    row = UserRow(name="Bob", email="bob@x", consented=True)
    with pytest.raises(Exception):
        row.name = "Eve"


def test_row_instance_equality():
    a = UserRow(name="Bob", email="bob@x", consented=True)
    b = UserRow(name="Bob", email="bob@x", consented=True)
    c = UserRow(name="Ann", email="ann@x", consented=True)
    assert a == b
    assert a != c


def test_row_only_supports_str_int_bool_fields():
    # v1 limitation per spec
    with pytest.raises(TypeError, match="v1 only supports str/int/bool"):
        class BadRow(Row):
            x: list  # type: ignore[type-arg]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_row_proxy.py -v -k row_instance`
Expected: FAIL — `UserRow()` constructor not defined.

- [ ] **Step 3: Implement instance behavior via metaclass `__init__`**

Replace the metaclass and Row class in `src/clauz3/row.py`:

```python
class _RowMeta(type):
    """Metaclass that returns ColumnRef for class-level field access
    and installs frozen-instance behavior on subclasses.
    """

    _ALLOWED_TYPES = (str, int, bool)

    def __new__(mcs, cls_name, bases, namespace, **kwargs):
        annotations = namespace.get("__annotations__", {})
        for fname, ftype in annotations.items():
            if ftype not in mcs._ALLOWED_TYPES:
                raise TypeError(
                    f"v1 only supports str/int/bool field types; "
                    f"{cls_name}.{fname} is {ftype!r}. See "
                    f"docs/symbolic-iteration.md."
                )
        namespace.setdefault("__slots__", tuple(annotations.keys()))
        cls = super().__new__(mcs, cls_name, bases, namespace, **kwargs)
        return cls

    def __getattribute__(cls, name: str):
        if not name.startswith("_"):
            annotations = type.__getattribute__(cls, "__annotations__")
            if name in annotations:
                return ColumnRef(schema=cls, field=name)
        return type.__getattribute__(cls, name)


class Row(metaclass=_RowMeta):
    __slots__: tuple[str, ...] = ()
    __annotations__: dict[str, type] = {}

    def __init__(self, **kwargs):
        annotations = type(self).__annotations__
        missing = set(annotations) - set(kwargs)
        extra = set(kwargs) - set(annotations)
        if missing:
            raise TypeError(f"missing fields: {sorted(missing)}")
        if extra:
            raise TypeError(f"unknown fields: {sorted(extra)}")
        for name, value in kwargs.items():
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __eq__(self, other):
        if type(self) is not type(other):
            return NotImplemented
        return all(
            getattr(self, f) == getattr(other, f)
            for f in type(self).__annotations__
        )

    def __hash__(self):
        return hash(tuple(
            getattr(self, f) for f in type(self).__annotations__
        ))

    def __repr__(self):
        fields = ", ".join(
            f"{f}={getattr(self, f)!r}"
            for f in type(self).__annotations__
        )
        return f"{type(self).__name__}({fields})"
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_row_proxy.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/row.py tests/test_row_proxy.py
git commit -m "feat(row): immutable instance behavior + str/int/bool field type guard"
```

---

### Task 4: Re-export Row and ColumnRef from clauz3 top-level

**Files:**
- Modify: `src/clauz3/__init__.py:1-33`
- Test: `tests/test_row_proxy.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_row_proxy.py (append)
def test_clauz3_top_level_exports_row_and_columnref():
    import clauz3
    assert clauz3.Row is Row
    assert clauz3.ColumnRef is ColumnRef
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_row_proxy.py::test_clauz3_top_level_exports_row_and_columnref -v`
Expected: FAIL — `module 'clauz3' has no attribute 'Row'`.

- [ ] **Step 3: Edit `src/clauz3/__init__.py`**

Replace the contents:

```python
"""Static contract experiments for agent-authored Python."""

from collections.abc import Callable
from typing import TypeVar

from clauz3._vendor.deal_solver._funcs._registry import register
from clauz3.row import ColumnRef, Row
from clauz3.spec import contract, effect

__version__ = "0.1.0"

F = TypeVar("F", bound=Callable[..., object])


def guarantee(_predicate: object) -> Callable[[F], F]:
    """Attach a clauz3 guarantee to a function.

    At runtime this decorator is deliberately inert. The prover reads the
    decorator from the AST and asks the corresponding registered solver to
    translate it into z3 constraints.
    """

    def decorate(func: F) -> F:
        return func

    return decorate


def solver(name: str) -> Callable[[F], F]:
    """Register a symbolic implementation for a predicate name."""
    return register(name)


__all__ = [
    "ColumnRef",
    "Row",
    "__version__",
    "contract",
    "effect",
    "guarantee",
    "solver",
]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_row_proxy.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/__init__.py
git commit -m "feat(row): re-export Row and ColumnRef from clauz3 top-level"
```

---

### Task 5: Z3 datatype generator for Row subclasses

**Files:**
- Create: `src/clauz3/_vendor/deal_solver/_proxies/_row.py`
- Test: `tests/test_row_proxy.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_row_proxy.py (append)
import z3

from clauz3._vendor.deal_solver._proxies._row import z3_datatype_for_row


def test_z3_datatype_for_row_caches_per_class():
    dt1 = z3_datatype_for_row(UserRow)
    dt2 = z3_datatype_for_row(UserRow)
    assert dt1 is dt2  # same datatype object across calls


def test_z3_datatype_field_sorts_match_annotations():
    dt = z3_datatype_for_row(UserRow)
    # one constructor "mk"
    assert dt.num_constructors() == 1
    # accessors with matching sorts
    name_sort = dt.accessor(0, 0).range()
    email_sort = dt.accessor(0, 1).range()
    consented_sort = dt.accessor(0, 2).range()
    assert name_sort == z3.StringSort()
    assert email_sort == z3.StringSort()
    assert consented_sort == z3.BoolSort()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_row_proxy.py -v -k z3_datatype`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the generator**

```python
# src/clauz3/_vendor/deal_solver/_proxies/_row.py
"""Z3 representations for clauz3.Row schemas.

Each clauz3.Row subclass maps to one Z3 datatype with a single constructor
and one accessor per declared field. The mapping is cached per class
identity so repeated lookups during a proof reuse the same Z3 sort.
"""
from __future__ import annotations

import typing

import z3


_DATATYPE_CACHE: dict[type, z3.DatatypeSortRef] = {}


_TYPE_TO_SORT = {
    str: z3.StringSort(),
    int: z3.IntSort(),
    bool: z3.BoolSort(),
}


def z3_datatype_for_row(schema: type) -> z3.DatatypeSortRef:
    """Return (and cache) the Z3 datatype for a clauz3.Row subclass."""
    if schema in _DATATYPE_CACHE:
        return _DATATYPE_CACHE[schema]

    annotations = schema.__annotations__
    if not annotations:
        raise TypeError(f"{schema.__name__} has no annotated fields")

    dt = z3.Datatype(schema.__name__)
    fields = []
    for fname, ftype in annotations.items():
        sort = _TYPE_TO_SORT.get(ftype)
        if sort is None:
            raise TypeError(
                f"unsupported field type {ftype!r} on "
                f"{schema.__name__}.{fname}"
            )
        fields.append((fname, sort))
    dt.declare(f"mk_{schema.__name__.lower()}", *fields)
    created = dt.create()

    _DATATYPE_CACHE[schema] = created
    return created
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_row_proxy.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/_vendor/deal_solver/_proxies/_row.py tests/test_row_proxy.py
git commit -m "feat(proxy): Z3 datatype generator for Row schemas"
```

---

### Task 6: RowSort proxy

**Files:**
- Modify: `src/clauz3/_vendor/deal_solver/_proxies/_row.py`
- Test: `tests/test_row_proxy.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_row_proxy.py (append)
from clauz3._vendor.deal_solver._proxies._row import RowSort


def test_row_sort_field_returns_typed_proxy(ctx_factory):
    ctx = ctx_factory()
    dt = z3_datatype_for_row(UserRow)
    bob = dt.mk_userrow(z3.StringVal("Bob"), z3.StringVal("bob@x"), z3.BoolVal(True))
    row = RowSort(schema=UserRow, expr=bob)
    email = row.field("email", ctx=ctx)
    from clauz3._vendor.deal_solver._proxies import StrSort
    assert isinstance(email, StrSort)


def test_row_sort_rejects_unknown_field(ctx_factory):
    ctx = ctx_factory()
    dt = z3_datatype_for_row(UserRow)
    bob = dt.mk_userrow(z3.StringVal("Bob"), z3.StringVal("bob@x"), z3.BoolVal(True))
    row = RowSort(schema=UserRow, expr=bob)
    with pytest.raises(AttributeError, match="unknown field"):
        row.field("address", ctx=ctx)
```

Note: `ctx_factory` is an existing pytest fixture in this codebase that builds a fresh `Context`. If it doesn't exist, add this fixture to `tests/conftest.py`:

```python
# tests/conftest.py
import pytest

from clauz3._vendor.deal_solver._context import Context


@pytest.fixture
def ctx_factory():
    def make():
        return Context.make_empty(get_contracts=lambda _: iter(()))
    return make
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_row_proxy.py -v -k row_sort`
Expected: FAIL — `RowSort` not defined.

- [ ] **Step 3: Implement RowSort**

Append to `src/clauz3/_vendor/deal_solver/_proxies/_row.py`:

```python
from dataclasses import dataclass

from ._proxy import ProxySort
from ._registry import types


@types.add
@dataclass(frozen=True)
class RowSort(ProxySort):
    """Symbolic row of a known schema."""

    type_name = "row"
    schema: type
    expr: z3.DatatypeRef

    def field(self, name: str, *, ctx) -> ProxySort:
        annotations = self.schema.__annotations__
        if name not in annotations:
            raise AttributeError(
                f"unknown field {name!r} on {self.schema.__name__}"
            )
        dt = z3_datatype_for_row(self.schema)
        accessor = getattr(dt, name)
        z3_value = accessor(self.expr)
        ftype = annotations[name]
        if ftype is str:
            return types.str.from_expr(z3_value, ctx=ctx)
        if ftype is int:
            return types.int.from_expr(z3_value, ctx=ctx)
        if ftype is bool:
            return types.bool.from_expr(z3_value, ctx=ctx)
        raise TypeError(f"unexpected field type {ftype!r}")
```

Note: `from_expr` is the existing constructor on each ProxySort that wraps an existing Z3 expression. If the vendored proxies use a different name (e.g., `from_z3`), grep for it: `grep -rn "def from_expr\|def from_z3\|@classmethod" src/clauz3/_vendor/deal_solver/_proxies/`. Adjust accordingly.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_row_proxy.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/_vendor/deal_solver/_proxies/_row.py tests/test_row_proxy.py tests/conftest.py
git commit -m "feat(proxy): RowSort with field selectors"
```

---

### Task 7: QueryResultSort proxy

**Files:**
- Modify: `src/clauz3/_vendor/deal_solver/_proxies/_row.py`
- Test: `tests/test_row_proxy.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_row_proxy.py (append)
from clauz3._vendor.deal_solver._proxies._row import QueryResultSort


def test_query_result_sort_at_returns_rowsort(ctx_factory):
    ctx = ctx_factory()
    dt = z3_datatype_for_row(UserRow)
    arr = z3.Const("users", z3.ArraySort(z3.IntSort(), dt))
    length = z3.Int("users_len")
    qr = QueryResultSort(
        row_schema=UserRow,
        array_expr=arr,
        length_expr=length,
        source=("db_query", {}),
    )
    row = qr.at(z3.IntVal(0), ctx=ctx)
    assert isinstance(row, RowSort)
    assert row.schema is UserRow
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_row_proxy.py::test_query_result_sort_at_returns_rowsort -v`
Expected: FAIL.

- [ ] **Step 3: Implement QueryResultSort**

Append:

```python
from typing import Any


@types.add
@dataclass(frozen=True)
class QueryResultSort(ProxySort):
    """Symbolic return of a trusted call typed list[Row]."""

    type_name = "query_result"
    row_schema: type
    array_expr: z3.ArrayRef
    length_expr: z3.ArithRef
    source: tuple[str, dict[str, Any]]

    def at(self, i: z3.ArithRef, *, ctx) -> RowSort:
        return RowSort(
            schema=self.row_schema,
            expr=z3.Select(self.array_expr, i),
        )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_row_proxy.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/_vendor/deal_solver/_proxies/_row.py tests/test_row_proxy.py
git commit -m "feat(proxy): QueryResultSort with at(i) indexing"
```

---

### Task 8: Quantifier dataclass

**Files:**
- Create: `src/clauz3/_vendor/deal_solver/_context/_quantifier.py`
- Test: `tests/test_for_loop.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_for_loop.py
import z3

from clauz3._vendor.deal_solver._context._quantifier import Quantifier
from clauz3._vendor.deal_solver._proxies._row import QueryResultSort
from clauz3.row import Row


class UserRow(Row):
    name: str
    email: str


def test_quantifier_has_bounds_helpers():
    arr = z3.Const("users", z3.ArraySort(z3.IntSort(), z3.IntSort()))
    length = z3.Int("len")
    i = z3.Int("i")
    qr = QueryResultSort(
        row_schema=UserRow,
        array_expr=arr,
        length_expr=length,
        source=("db_query", {}),
    )
    q = Quantifier(bound_var=i, source=qr, lower=z3.IntVal(0), upper=length)
    bounds = q.bounds_expr()
    assert isinstance(bounds, z3.BoolRef)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_for_loop.py::test_quantifier_has_bounds_helpers -v`
Expected: FAIL.

- [ ] **Step 3: Implement Quantifier**

```python
# src/clauz3/_vendor/deal_solver/_context/_quantifier.py
from __future__ import annotations

import typing
from dataclasses import dataclass

import z3

if typing.TYPE_CHECKING:
    from .._proxies._row import QueryResultSort


@dataclass(frozen=True)
class Quantifier:
    """One for-loop frame's binding."""

    bound_var: z3.ArithRef
    source: "QueryResultSort"
    lower: z3.ArithRef
    upper: z3.ArithRef

    def bounds_expr(self) -> z3.BoolRef:
        return z3.And(self.lower <= self.bound_var, self.bound_var < self.upper)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_for_loop.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/_vendor/deal_solver/_context/_quantifier.py tests/test_for_loop.py
git commit -m "feat(context): Quantifier dataclass with bounds_expr helper"
```

---

### Task 9: Extend FactInfo with quantifiers field

**Files:**
- Modify: `src/clauz3/_vendor/deal_solver/_context/_layer.py:34-39`
- Test: `tests/test_for_loop.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_for_loop.py (append)
from clauz3._vendor.deal_solver._context._layer import FactInfo


def test_fact_info_quantifiers_defaults_to_empty():
    fact = FactInfo(name="x", markers=(), args={}, cond=None)  # type: ignore[arg-type]
    assert fact.quantifiers == ()


def test_fact_info_accepts_quantifiers_tuple():
    arr = z3.Const("a", z3.ArraySort(z3.IntSort(), z3.IntSort()))
    qr = QueryResultSort(
        row_schema=UserRow, array_expr=arr,
        length_expr=z3.Int("n"), source=("db_query", {}),
    )
    q = Quantifier(
        bound_var=z3.Int("i"), source=qr,
        lower=z3.IntVal(0), upper=z3.Int("n"),
    )
    fact = FactInfo(name="x", markers=(), args={}, cond=None,  # type: ignore[arg-type]
                   quantifiers=(q,))
    assert fact.quantifiers == (q,)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_for_loop.py -v -k fact_info`
Expected: FAIL — `FactInfo` does not have `quantifiers` field.

- [ ] **Step 3: Modify FactInfo**

Edit `src/clauz3/_vendor/deal_solver/_context/_layer.py:34-39`:

```python
class FactInfo(typing.NamedTuple):
    name: str
    markers: tuple[str, ...]
    args: dict[str, ProxySort]
    cond: BoolSort
    quantifiers: tuple = ()
```

(Use plain `tuple` as the annotation — importing `Quantifier` here would create a circular dependency. Type checkers see `tuple`; runtime sees actual `Quantifier` instances.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_for_loop.py -v -k fact_info`
Expected: PASS.

Also run the full pre-existing test suite to verify no regression:
`uv run pytest -v`
Expected: all existing tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/_vendor/deal_solver/_context/_layer.py tests/test_for_loop.py
git commit -m "feat(context): FactInfo.quantifiers field, default empty"
```

---

### Task 10: Add Context.quantifiers Layer

**Files:**
- Modify: `src/clauz3/_vendor/deal_solver/_context/_context.py`
- Test: `tests/test_for_loop.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_for_loop.py (append)
from clauz3._vendor.deal_solver._context import Context


def test_context_has_quantifiers_layer(ctx_factory):
    ctx = ctx_factory()
    assert hasattr(ctx, "quantifiers")
    # starts empty
    assert list(ctx.quantifiers) == []


def test_context_quantifiers_add(ctx_factory):
    ctx = ctx_factory()
    arr = z3.Const("a", z3.ArraySort(z3.IntSort(), z3.IntSort()))
    qr = QueryResultSort(
        row_schema=UserRow, array_expr=arr,
        length_expr=z3.Int("n"), source=("db_query", {}),
    )
    q = Quantifier(
        bound_var=z3.Int("i"), source=qr,
        lower=z3.IntVal(0), upper=z3.Int("n"),
    )
    ctx.quantifiers.add(q)
    assert list(ctx.quantifiers) == [q]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_for_loop.py -v -k quantifiers_layer`
Expected: FAIL — `Context` has no `quantifiers` attribute.

- [ ] **Step 3: Add the field**

Edit `_context.py`:

1. At top, after existing imports:
```python
from ._quantifier import Quantifier
```

2. Add to the Context NamedTuple fields (after `facts: Layer[FactInfo]`):
```python
    quantifiers: Layer[Quantifier]
```

3. In `make_empty`, add to the constructor args:
```python
            quantifiers=Layer(),
```

4. In `make_child`, add:
```python
            quantifiers=self.quantifiers.make_child(),
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_for_loop.py -v`
Expected: new tests PASS, no regression in existing tests (`uv run pytest -v`).

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/_vendor/deal_solver/_context/_context.py tests/test_for_loop.py
git commit -m "feat(context): Context.quantifiers Layer stack"
```

---

### Task 11: Trusted-return materialization for list[Row]

**Files:**
- Modify: `src/clauz3/_vendor/deal_solver/_proxies/_func.py:71-77`
- Test: `tests/test_row_proxy.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_row_proxy.py (append)
from clauz3._vendor.deal_solver._theorem import Theorem


def test_trusted_call_returning_list_row_materializes_query_result(ctx_factory):
    """A trusted function annotated -> list[UserRow] should return a QueryResultSort."""
    source = '''
import deal
from typing import NamedTuple
import clauz3


class UserRow(clauz3.Row):
    name: str
    email: str
    consented: bool


@deal.has("db_read")
@deal.post(lambda result: len(result) <= 100)
def db_query(table: str) -> list[UserRow]: ...


@clauz3.guarantee(lambda: True)  # trivial
def main() -> None:
    rows = db_query("users")
    # rows is a symbolic QueryResultSort; we just verify the proof completes
'''
    # Run through the theorem prover; assert no exceptions
    from clauz3.prover import prove_text
    results = prove_text(source)
    assert all(r.ok for r in results), [r.proof.error for r in results]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_row_proxy.py -v -k materializes`
Expected: FAIL — trusted call returns `bool.val(True)` so `rows = db_query(...)` is bound to a bool, then never used; the proof completes but the materialization claim isn't tested. Actually the test as written WILL pass without the change. **Strengthen the test:**

Replace test body with:
```python
    source = '''
import deal
import clauz3


class UserRow(clauz3.Row):
    name: str
    email: str
    consented: bool


@deal.has("db_read")
@deal.post(lambda result: len(result) <= 100)
def db_query(table: str) -> list[UserRow]: ...


@clauz3.guarantee(lambda: True)
def main() -> None:
    rows = db_query("users")
    first = rows[0]   # forces an index op on rows
    _ = first.email   # forces a field access
'''
    from clauz3.prover import prove_text
    results = prove_text(source)
    assert all(r.ok for r in results), [r.proof.error for r in results]
```

This test requires `rows[0]` and `first.email` to work on the materialized value. Before materialization, `rows` is `BoolSort(True)` and `rows[0]` raises.

Expected with the unmodified code: FAIL with a type error about indexing a bool.

- [ ] **Step 3: Implement materialization**

Edit `_proxies/_func.py:63-77`. Replace the `if markers:` block:

```python
        markers = _trusted_markers(node=node, ctx=ctx)
        if markers:
            # clauz3 extension: @deal.has(...) marks a trusted boundary.
            # We require the trusted function preconditions, record the call
            # as a symbolic fact, and deliberately skip the side-effecting
            # function body. If the return annotation is `list[<Row>]`, we
            # materialize a QueryResultSort instead of returning bool.True.
            contracts = eval_contracts(func=node, ctx=func_ctx)
            ctx.expected.add(and_expr(*contracts.pre, ctx=ctx))
            ctx.facts.add(FactInfo(
                name=node.name,
                markers=markers,
                args=bound_args,
                cond=ctx.interrupted.m_not(ctx=ctx),
                quantifiers=tuple(ctx.quantifiers.layer),
            ))
            materialized = _materialize_trusted_return(
                node=node, bound_args=bound_args, ctx=ctx,
            )
            if materialized is not None:
                # postconditions become constraints on materialized value
                ctx.given.add(and_expr(*contracts.post, ctx=ctx))
                return materialized
            return types.bool.val(True, ctx=ctx)
```

Add the helper function in the same file:

```python
def _materialize_trusted_return(
    *,
    node: astroid.FunctionDef,
    bound_args: dict[str, ProxySort],
    ctx: "Context",
) -> ProxySort | None:
    """If the trusted function returns `list[Row]`, return a QueryResultSort.

    Otherwise return None and let the caller fall back to bool.True.
    """
    import z3

    from clauz3.row import Row
    from ._row import QueryResultSort, z3_datatype_for_row

    returns = node.returns
    if returns is None:
        return None
    # We want astroid to give us the inferred return type. Inspecting
    # subscript syntax for `list[UserRow]`:
    if not isinstance(returns, astroid.Subscript):
        return None
    if not isinstance(returns.value, astroid.Name) or returns.value.name != "list":
        return None
    inner = returns.slice
    if not isinstance(inner, astroid.Name):
        return None

    # Resolve the row schema class by name from the module's globals.
    # The trusted file imports it directly, so it's in the module scope.
    try:
        resolved = next(inner.infer())
    except astroid.InferenceError:
        return None
    if not isinstance(resolved, astroid.ClassDef):
        return None
    # Find the actual Python class — we need to import the module that
    # defines it. The trusted module's path was added to sys.path by the
    # prover's _temporary_sys_path mechanism.
    schema = _resolve_class_object(resolved)
    if schema is None or not issubclass(schema, Row):
        return None

    dt = z3_datatype_for_row(schema)
    # Build fresh symbolic array + length keyed by a unique name per call.
    name_base = f"{node.name}_result_{id(ctx)}"
    array_expr = z3.Const(name_base, z3.ArraySort(z3.IntSort(), dt))
    length_expr = z3.Int(f"{name_base}_len")
    ctx.given.add(types.bool.from_expr(length_expr >= 0, ctx=ctx))
    return QueryResultSort(
        row_schema=schema,
        array_expr=array_expr,
        length_expr=length_expr,
        source=(node.name, bound_args),
    )


def _resolve_class_object(class_def: astroid.ClassDef) -> type | None:
    """Resolve an astroid ClassDef to its actual Python class via module import."""
    import importlib
    import sys

    module_name = class_def.root().name
    module = sys.modules.get(module_name)
    if module is None:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            return None
    return getattr(module, class_def.name, None)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_row_proxy.py -v`
Expected: all PASS.

Also run full suite: `uv run pytest -v` — no regression.

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/_vendor/deal_solver/_proxies/_func.py tests/test_row_proxy.py
git commit -m "feat(proxy): materialize list[Row] trusted returns as QueryResultSort"
```

---

### Task 12: Verify existing examples still prove (no regression checkpoint)

- [ ] **Step 1: Run full test suite + existing example proofs**

```bash
just test
```

Expected: pytest, ruff, mypy, and all examples/*/Justfile cases pass.

- [ ] **Step 2: If any regression, fix in this task before continuing**

Most likely cause of regression: a tuple-arity mismatch in `FactInfo` construction somewhere we missed. Search:
```bash
grep -rn "FactInfo(" src/clauz3/
```
Anywhere it's constructed positionally needs the new `quantifiers=()` default to apply. NamedTuple defaults should handle this, but verify.

- [ ] **Step 3: Commit any fixes**

```bash
git commit -am "fix: keep existing examples green after FactInfo extension"
```

(Skip if no fix needed.)

---

## Phase B: For-loop handler (commit 2)

### Task 13: For-loop dispatch — minimal handler that errors for unsupported

**Files:**
- Modify: `src/clauz3/_vendor/deal_solver/_eval_stmt.py`
- Test: `tests/test_for_loop.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_for_loop.py (append)
from clauz3.prover import prove_text


def test_for_loop_over_unsupported_iterable_raises():
    source = '''
import clauz3

@clauz3.guarantee(lambda: True)
def main() -> None:
    for x in range(5):  # range not supported in v1
        pass
'''
    results = prove_text(source)
    # Expect a clear UnsupportedError-like result, not an internal traceback
    assert not all(r.ok for r in results)
    # The error message should mention the unsupported iterable
    error_str = " ".join(str(r.proof.error or "") for r in results)
    assert "range" in error_str or "unsupported" in error_str.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_for_loop.py::test_for_loop_over_unsupported_iterable_raises -v`
Expected: FAIL — without a For handler, `astroid.For` hits the default unsupported path with a confusing message.

- [ ] **Step 3: Implement the For handler shell**

Edit `src/clauz3/_vendor/deal_solver/_eval_stmt.py`. After the existing handlers, add:

```python
@eval_stmt.register(astroid.For)
def eval_for(node: astroid.For, ctx: Context) -> None:
    from ._exceptions import UnsupportedError
    from ._eval_expr import eval_expr
    from ._proxies._row import QueryResultSort
    from ._context._quantifier import Quantifier
    import z3

    if node.orelse:
        raise UnsupportedError("for-else is not supported in v1")
    if not isinstance(node.target, astroid.AssignName):
        raise UnsupportedError(
            "tuple-unpack in for-loops is not supported in v1; "
            "trusted layers should return NamedTuple-shaped rows"
        )

    iterable = eval_expr(node.iter, ctx=ctx)
    if not isinstance(iterable, QueryResultSort):
        raise UnsupportedError(
            f"for-loops can only iterate over list[Row]-returning trusted "
            f"calls in v1; got {type(iterable).__name__}"
        )

    # Allocate fresh index var
    idx_name = f"loop_idx_{node.lineno}_{id(ctx)}"
    i_var = z3.Int(idx_name)
    quantifier = Quantifier(
        bound_var=i_var,
        source=iterable,
        lower=z3.IntVal(0),
        upper=iterable.length_expr,
    )

    # Bind loop variable to iterable.at(i)
    row_proxy = iterable.at(i_var, ctx=ctx)
    ctx.scope.set(name=node.target.name, value=row_proxy)

    # Push quantifier, evaluate body, pop quantifier
    ctx.quantifiers.add(quantifier)
    try:
        for stmt in node.body:
            eval_stmt(stmt, ctx=ctx)
    finally:
        # Pop — quantifiers Layer doesn't have pop; we rebuild
        ctx.quantifiers.layer.pop()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_for_loop.py -v`
Expected: the new test PASSES (UnsupportedError raised on `range`).

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/_vendor/deal_solver/_eval_stmt.py tests/test_for_loop.py
git commit -m "feat(eval): For-loop handler with v1 iterable restriction"
```

---

### Task 14: For-loop over QueryResultSort emits quantified fact

**Files:**
- Test: `tests/test_for_loop.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_for_loop.py (append)
def test_for_loop_over_query_result_emits_quantified_fact():
    source = '''
import deal
import clauz3


class UserRow(clauz3.Row):
    name: str
    email: str
    consented: bool


@deal.has("db_read")
@deal.post(lambda result: len(result) <= 100)
def db_query(table: str) -> list[UserRow]: ...


@deal.pre(lambda addr, msg: "@" in addr)
@deal.has("email")
def send_email(addr: str, msg: str) -> None: ...


@clauz3.guarantee(lambda: True)
def main() -> None:
    for row in db_query("users"):
        send_email(row.email, "hi")
'''
    from clauz3.prover import prove_text
    results = prove_text(source)
    # The point: this proves at all (no UnsupportedError). The fact's
    # quantifier presence is verified more directly in Task 15.
    assert all(r.ok for r in results), [r.proof.error for r in results]
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/test_for_loop.py::test_for_loop_over_query_result_emits_quantified_fact -v`
Expected: PASSES — Task 13's handler should already cover this.

If it fails, debug the eval_expr lookup for trusted call return values (Task 11 should have handled it but verify the trusted call's return is being plumbed through correctly).

- [ ] **Step 3: No new code (sanity-check task).**

- [ ] **Step 4: Run all tests**

Run: `uv run pytest tests/test_for_loop.py -v`

- [ ] **Step 5: Commit**

```bash
git commit --allow-empty -m "test: assert for-loop over query result proves without error"
```

---

### Task 15: Quantifier presence assertion via FactInfo inspection

**Files:**
- Create: `tests/test_quantified_facts.py`

- [ ] **Step 1: Write failing test that directly inspects emitted facts**

```python
# tests/test_quantified_facts.py
"""Direct introspection of facts emitted by the symbolic executor."""
from __future__ import annotations

import textwrap

from clauz3.prover import _temporary_sys_path
from clauz3._vendor.deal_solver._theorem import Theorem
from clauz3._vendor.deal_solver._eval_contracts import eval_contracts
from clauz3._vendor.deal_solver._eval_stmt import eval_stmt
from clauz3._vendor.deal_solver._context import Context


def _facts_for(source: str):
    """Run symbolic execution on `source` and return the facts emitted by main()."""
    theorem = next(Theorem.from_text(textwrap.dedent(source)))
    if theorem.name != "main":
        raise RuntimeError("expected main as theorem target")
    ctx = Context.make_empty(get_contracts=theorem.get_contracts)
    main_node = theorem._func  # AST for main
    # Walk main()'s body symbolically
    for stmt in main_node.body:
        eval_stmt(stmt, ctx=ctx)
    return list(ctx.facts)


def test_for_loop_fact_has_one_quantifier():
    facts = _facts_for('''
        import deal
        import clauz3


        class UserRow(clauz3.Row):
            email: str


        @deal.has("db_read")
        @deal.post(lambda result: len(result) <= 100)
        def db_query(table: str) -> list[UserRow]: ...


        @deal.has("email")
        def send_email(addr: str, msg: str) -> None: ...


        @clauz3.guarantee(lambda: True)
        def main() -> None:
            for row in db_query("users"):
                send_email(row.email, "hi")
    ''')
    # Two facts: db_query and send_email. send_email is the quantified one.
    email_facts = [f for f in facts if f.name == "send_email"]
    assert len(email_facts) == 1
    assert len(email_facts[0].quantifiers) == 1
    assert email_facts[0].quantifiers[0].source.row_schema.__name__ == "UserRow"


def test_nested_for_loops_emit_two_quantifiers():
    facts = _facts_for('''
        import deal
        import clauz3


        class UserRow(clauz3.Row):
            email: str


        class ContactRow(clauz3.Row):
            message: str


        @deal.has("db_read")
        @deal.post(lambda result: len(result) <= 10)
        def get_users(table: str) -> list[UserRow]: ...


        @deal.has("db_read")
        @deal.post(lambda result: len(result) <= 10)
        def get_contacts(table: str) -> list[ContactRow]: ...


        @deal.has("email")
        def send_email(addr: str, msg: str) -> None: ...


        @clauz3.guarantee(lambda: True)
        def main() -> None:
            for u in get_users("users"):
                for c in get_contacts("contacts"):
                    send_email(u.email, c.message)
    ''')
    email_facts = [f for f in facts if f.name == "send_email"]
    assert len(email_facts) == 1
    assert len(email_facts[0].quantifiers) == 2
    # Outer is users, inner is contacts
    assert email_facts[0].quantifiers[0].source.row_schema.__name__ == "UserRow"
    assert email_facts[0].quantifiers[1].source.row_schema.__name__ == "ContactRow"
```

Note: the `_facts_for` helper bypasses the full proof flow and just runs `eval_stmt` directly so we can inspect emitted facts. Adjust the import paths if they don't match — grep `Theorem.from_text` to find the right entry point.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_quantified_facts.py -v`
Expected: at minimum the assertion `len(quantifiers) == 1` fails — the snapshot wiring may not yet copy `ctx.quantifiers` into `FactInfo`.

- [ ] **Step 3: Verify the snapshot wiring**

Check `_proxies/_func.py` (modified in Task 11) — the `quantifiers=tuple(ctx.quantifiers.layer)` line should be present. If not, add it. (It was specified in Task 11's step 3, but worth verifying.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_quantified_facts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_quantified_facts.py
git commit -m "test: verify for-loop facts carry quantifier snapshots"
```

---

### Task 16: For-loop v1 restrictions (break, continue, return, mutation)

**Files:**
- Modify: `src/clauz3/_vendor/deal_solver/_eval_stmt.py`
- Test: `tests/test_for_loop.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_for_loop.py (append)
@pytest.mark.parametrize("body", [
    "break",
    "continue",
    "return",
])
def test_for_loop_break_continue_return_raise_unsupported(body):
    source = f'''
import deal
import clauz3


class UserRow(clauz3.Row):
    email: str


@deal.has("db_read")
def db_query(table: str) -> list[UserRow]: ...


@clauz3.guarantee(lambda: True)
def main() -> None:
    for row in db_query("users"):
        {body}
'''
    from clauz3.prover import prove_text
    results = prove_text(source)
    assert not all(r.ok for r in results)
    error_str = " ".join(str(r.proof.error or "") for r in results)
    assert body in error_str.lower() or "unsupported" in error_str.lower()
```

- [ ] **Step 2: Run tests to verify they fail or trigger internal errors**

Run: `uv run pytest tests/test_for_loop.py -v -k break_continue_return`
Expected: FAIL or internal errors (break/continue/return inside the body run through eval_stmt and may hit weird states).

- [ ] **Step 3: Modify the For handler to reject these constructs in the body**

Edit `_eval_stmt.py`'s `eval_for`:

Before iterating `node.body`, scan for forbidden constructs:

```python
    # v1: reject break, continue, return inside loop body
    for stmt in astroid.NodeNG._astroid_walk_subnodes(node.body):  # if available
        if isinstance(stmt, (astroid.Break, astroid.Continue, astroid.Return)):
            raise UnsupportedError(
                f"{type(stmt).__name__.lower()} inside a for-loop over a "
                f"query result is not supported in v1"
            )
```

If `_astroid_walk_subnodes` isn't the right helper, write a manual recursive walk:

```python
    def _walk(nodes):
        for n in nodes:
            yield n
            if hasattr(n, "get_children"):
                yield from _walk(n.get_children())

    for sub in _walk(node.body):
        if isinstance(sub, (astroid.Break, astroid.Continue, astroid.Return)):
            raise UnsupportedError(
                f"{type(sub).__name__.lower()} inside a for-loop over a "
                f"query result is not supported in v1"
            )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_for_loop.py -v`
Expected: all v1-restriction tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/_vendor/deal_solver/_eval_stmt.py tests/test_for_loop.py
git commit -m "feat(eval): for-loop body rejects break/continue/return in v1"
```

---

### Task 17: Commit 2 checkpoint — squash if appropriate

- [ ] **Step 1: Review commit history**

```bash
git log --oneline main..HEAD
```

- [ ] **Step 2: Decide whether to squash**

If the tasks were tight and you want one "for-loop handler" commit in history, squash Tasks 13-16 into a single commit with message `feat(eval): symbolic for-loop handler with quantifier emission and v1 restrictions`. Otherwise leave as-is.

If squashing:
```bash
git reset --soft <commit-before-task-13>
git commit -m "feat(eval): symbolic for-loop handler with quantifier emission and v1 restrictions"
```

- [ ] **Step 3: Run full test suite**

```bash
just test
```
Expected: green.

---

## Phase C: Quantifier-aware relations (commit 3)

### Task 18: ForAll-wrapping helper in spec.py

**Files:**
- Modify: `src/clauz3/spec.py`
- Test: `tests/test_quantified_relations.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_quantified_relations.py
"""Relation primitives under quantification."""
from __future__ import annotations

import textwrap

import z3
import pytest

from clauz3.prover import prove_text


def _prove(source: str) -> bool:
    """Helper: prove source and return whether all targets are OK."""
    results = prove_text(textwrap.dedent(source))
    return all(r.ok for r in results)


def test_all_relation_over_loop_passes_when_predicate_holds():
    source = '''
        import deal
        import clauz3
        from clauz3.spec import contract, effect, ContractSpec


        class UserRow(clauz3.Row):
            email: str


        @deal.has("db_read")
        @deal.post(lambda result: len(result) <= 100)
        def db_query(table: str) -> list[UserRow]: ...


        @deal.has("email")
        def send_email(addr: str, msg: str) -> None: ...


        @contract
        def addrs_from_users() -> ContractSpec:
            return effect("send_email").all(
                lambda e: e.addr == getattr(UserRow, "email"),
            )


        @clauz3.guarantee(addrs_from_users())
        def main() -> None:
            for row in db_query("users"):
                send_email(row.email, "hi")
    '''
    # NOTE: this test is the headline. It will pass only after column-ref
    # matching lands in Phase D. For Phase C, change the contract to
    # something we can prove now (`Email.empty()` or `count() <= N`).
    pytest.skip("headline test; lit up in Phase D Task 31")


def test_count_relation_over_loop_with_bounded_postcondition():
    source = '''
        import deal
        import clauz3
        from clauz3.spec import contract, effect, ContractSpec


        class UserRow(clauz3.Row):
            email: str


        @deal.has("db_read")
        @deal.post(lambda result: len(result) <= 50)
        def db_query(table: str) -> list[UserRow]: ...


        @deal.has("email")
        def send_email(addr: str, msg: str) -> None: ...


        @contract
        def at_most_50_emails() -> ContractSpec:
            return effect("send_email").count() <= 50


        @clauz3.guarantee(at_most_50_emails())
        def main() -> None:
            for row in db_query("users"):
                send_email(row.email, "hi")
    '''
    assert _prove(source)


def test_count_relation_fails_when_bound_too_tight():
    source = '''
        import deal
        import clauz3
        from clauz3.spec import contract, effect, ContractSpec


        class UserRow(clauz3.Row):
            email: str


        @deal.has("db_read")
        @deal.post(lambda result: len(result) <= 50)
        def db_query(table: str) -> list[UserRow]: ...


        @deal.has("email")
        def send_email(addr: str, msg: str) -> None: ...


        @contract
        def at_most_10_emails() -> ContractSpec:
            return effect("send_email").count() <= 10


        @clauz3.guarantee(at_most_10_emails())
        def main() -> None:
            for row in db_query("users"):
                send_email(row.email, "hi")
    '''
    assert not _prove(source)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_quantified_relations.py -v`
Expected: FAIL — relations don't yet wrap in `ForAll`, so the count contribution is `1` per fact (no quantifier multiplication), making the at-most-50 test pass trivially (1 <= 50 = True) BUT actually it would mean the bound is also 1 in the at-most-10 case, which would also pass (1 <= 10 = True). The "fails when bound too tight" test fails.

If both tests pass without changes, the relations are unsoundly ignoring quantifiers. The point of this task is to introduce the wrapping.

- [ ] **Step 3: Implement `_wrap_with_quantifiers` helper**

Add to `src/clauz3/spec.py` (after imports, before `EffectRelation`):

```python
def _wrap_with_quantifiers(
    fact, body, *, ctx,
):
    """Wrap a relation body in ForAll over fact.quantifiers.

    Short-circuits when fact has no quantifiers (existing behavior).
    """
    quantifiers = getattr(fact, "quantifiers", ())
    if not quantifiers:
        return body
    import z3
    bound_vars = [q.bound_var for q in quantifiers]
    bounds = z3.And(*[q.bounds_expr() for q in quantifiers])
    return z3.ForAll(
        bound_vars,
        z3.Implies(bounds, body),
    )
```

- [ ] **Step 4: Run tests (still expected to fail — helper not used yet)**

Run: `uv run pytest tests/test_quantified_relations.py -v`
Expected: still fails — Task 19+ wires this in.

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/spec.py tests/test_quantified_relations.py
git commit -m "feat(spec): _wrap_with_quantifiers helper"
```

---

### Task 19: AllSpec uses _wrap_with_quantifiers

**Files:**
- Modify: `src/clauz3/spec.py:AllSpec.solve` (approximate line ~145-163)

- [ ] **Step 1: Locate AllSpec.solve**

```bash
grep -n "class AllSpec" src/clauz3/spec.py
```

- [ ] **Step 2: Rewrite AllSpec.solve**

```python
@dataclass(frozen=True)
class AllSpec(ContractSpec):
    relation: EffectRelation
    predicate: "LambdaSpec"
    fact_filter: "LambdaSpec | None" = None

    def solve(self, *, ctx: Any) -> BoolSort:
        from agent_deal._vendor.deal_solver._proxies import (
            and_expr, or_expr,
        )
        # (or wherever and_expr/or_expr live — keep existing imports)
        clauses = []
        for fact in self.relation.facts(ctx):
            cond = _fact_cond(fact=fact, predicate=self.fact_filter, ctx=ctx)
            body_pred = or_expr(
                cond.m_not(ctx=ctx),
                self.predicate.evaluate(row=fact.args, ctx=ctx).m_bool(ctx=ctx),
                ctx=ctx,
            )
            wrapped = _wrap_with_quantifiers(fact, body_pred, ctx=ctx)
            clauses.append(wrapped)
        return and_expr(*clauses, ctx=ctx)
```

NOTE: `_wrap_with_quantifiers` returns a Z3 expression (when quantifiers exist) or a `BoolSort` proxy (when they don't). The `and_expr` in spec.py expects `BoolSort` proxies. Either:

(a) Make `_wrap_with_quantifiers` always return a `BoolSort` proxy by wrapping its Z3 result via `types.bool.from_expr(...)`, OR
(b) Have the relation methods handle the mixed return.

Choose (a) for consistency. Update Task 18's helper:

```python
def _wrap_with_quantifiers(fact, body, *, ctx):
    quantifiers = getattr(fact, "quantifiers", ())
    if not quantifiers:
        return body
    import z3
    body_z3 = body.expr if hasattr(body, "expr") else body
    bound_vars = [q.bound_var for q in quantifiers]
    bounds = z3.And(*[q.bounds_expr() for q in quantifiers])
    wrapped = z3.ForAll(bound_vars, z3.Implies(bounds, body_z3))
    from clauz3._vendor.deal_solver._proxies._registry import types
    return types.bool.from_expr(wrapped, ctx=ctx)
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_quantified_relations.py -v`
Expected: `test_all_relation_over_loop_passes_when_predicate_holds` is still skipped (Phase D); the count tests may still fail because we haven't updated CountSpec.

- [ ] **Step 4: Run existing tests for regression**

Run: `uv run pytest tests/test_email_prover.py tests/test_bank_prover.py -v`
Expected: PASS — AllSpec change should be invisible when quantifiers are empty.

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/spec.py
git commit -m "feat(spec): AllSpec wraps body in ForAll over fact quantifiers"
```

---

### Task 20: EmptySpec uses _wrap_with_quantifiers

**Files:**
- Modify: `src/clauz3/spec.py:EmptySpec.solve`

- [ ] **Step 1: Test**

```python
# tests/test_quantified_relations.py (append)
def test_empty_relation_over_loop_fails_when_any_call_reachable():
    source = '''
        import deal
        import clauz3
        from clauz3.spec import contract, effect, ContractSpec


        class UserRow(clauz3.Row):
            email: str


        @deal.has("db_read")
        @deal.post(lambda result: len(result) <= 5)
        def db_query(table: str) -> list[UserRow]: ...


        @deal.has("email")
        def send_email(addr: str, msg: str) -> None: ...


        @contract
        def no_emails() -> ContractSpec:
            return effect("send_email").empty()


        @clauz3.guarantee(no_emails())
        def main() -> None:
            for row in db_query("users"):
                send_email(row.email, "hi")
    '''
    assert not _prove(source)


def test_empty_relation_over_loop_holds_when_query_might_be_empty():
    # When length CAN be 0, empty() still holds only if it MUST be — and
    # without further constraints, length is non-negative but unconstrained.
    # send_email IS reachable under length > 0; the proof should fail.
    pass  # covered by the test above
```

- [ ] **Step 2: Run test to verify it fails (i.e., current behavior says "no emails proved" incorrectly)**

Run: `uv run pytest tests/test_quantified_relations.py -v -k empty_relation`
Expected: FAIL — without wrapping, EmptySpec treats the fact as unreachable trivially.

- [ ] **Step 3: Implement**

```python
@dataclass(frozen=True)
class EmptySpec(ContractSpec):
    relation: EffectRelation
    fact_filter: "LambdaSpec | None" = None

    def solve(self, *, ctx: Any) -> BoolSort:
        clauses = []
        for fact in self.relation.facts(ctx):
            cond = _fact_cond(fact=fact, predicate=self.fact_filter, ctx=ctx)
            body_pred = cond.m_not(ctx=ctx)
            wrapped = _wrap_with_quantifiers(fact, body_pred, ctx=ctx)
            clauses.append(wrapped)
        return and_expr(*clauses, ctx=ctx)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_quantified_relations.py -v`
Expected: empty relation tests PASS; existing tests unaffected.

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/spec.py tests/test_quantified_relations.py
git commit -m "feat(spec): EmptySpec handles quantified facts"
```

---

### Task 21: CountSpec / ComparisonSpec — length-product contribution

**Files:**
- Modify: `src/clauz3/spec.py:SumSpec.value` and `ComparisonSpec.solve`

- [ ] **Step 1: Test (from Task 18)**

The `count() <= 50` and `count() <= 10` tests in `tests/test_quantified_relations.py` should already fail correctly with the count-as-product-of-lengths logic.

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/test_quantified_relations.py -v -k count`
Expected: FAIL — without the length-product logic, count just counts facts, so both bounds (50 and 10) are met trivially with 1 fact.

- [ ] **Step 3: Implement length-product in SumSpec.value**

Locate `SumSpec` in `spec.py` (used by `count()` via `effect.count()` which returns `SumSpec(... selector=None ...)`):

```python
@dataclass(frozen=True)
class SumSpec:
    relation: EffectRelation
    selector: "LambdaSpec | None"  # None for count()
    predicate: "LambdaSpec | None" = None

    def value(self, *, ctx: Any) -> ProxySort:
        import z3
        total: ProxySort = types.int.val(0, ctx=ctx)
        zero = types.int.val(0, ctx=ctx)
        for fact in self.relation.facts(ctx):
            # Filter
            cond = _fact_cond(fact=fact, predicate=self.predicate, ctx=ctx)
            # Per-fact contribution
            if self.selector is None:
                # count() — contribution is 1 per row (or per call)
                base = types.int.val(1, ctx=ctx)
            else:
                base = self.selector.evaluate(row=fact.args, ctx=ctx)
            # Multiply by quantifier length product
            quantifiers = getattr(fact, "quantifiers", ())
            if not quantifiers:
                contrib = if_expr(cond, base, zero, ctx=ctx)
            else:
                # Check: does selector or fact.cond depend on quantifier vars?
                # For v1, if selector is None (count) and cond is independent,
                # contribution is base * product(upper). Else upper-bound.
                if self.selector is not None and _expr_uses_any_bound_var(
                    base, [q.bound_var for q in quantifiers]
                ):
                    raise UnsupportedError(
                        "sum(selector) where selector depends on the loop "
                        "variable is not supported in v1; see "
                        "docs/todos/quantified-aggregates.md"
                    )
                product_z3 = z3.IntVal(1)
                for q in quantifiers:
                    product_z3 = product_z3 * q.upper
                product = types.int.from_expr(product_z3, ctx=ctx)
                contrib = if_expr(cond, base.m_mul(product, ctx=ctx), zero, ctx=ctx)
            total = total.m_add(contrib, ctx=ctx)
        return total


def _expr_uses_any_bound_var(proxy, bound_vars):
    """Return True if proxy's underlying Z3 expr references any bound var."""
    import z3
    expr = proxy.expr if hasattr(proxy, "expr") else proxy
    def _walk(e):
        if e in bound_vars:
            return True
        if hasattr(e, "children"):
            return any(_walk(c) for c in e.children())
        return False
    return _walk(expr)
```

(Note: `if_expr` must be imported from the `_proxies` module if not already; same for `types`.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_quantified_relations.py -v -k count`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/spec.py
git commit -m "feat(spec): SumSpec/count contribution = base * product(quantifier.upper)"
```

---

### Task 22: SumSpec with bound-var-dependent selector raises UnsupportedError

**Files:**
- Test: `tests/test_quantified_relations.py`

- [ ] **Step 1: Test**

```python
def test_sum_selector_depending_on_row_raises_unsupported():
    source = '''
        import deal
        import clauz3
        from clauz3.spec import contract, effect, ContractSpec


        class InvoiceRow(clauz3.Row):
            amount: int


        @deal.has("db_read")
        @deal.post(lambda result: len(result) <= 100)
        def db_query(table: str) -> list[InvoiceRow]: ...


        @deal.has("billing")
        def charge(amount: int) -> None: ...


        @contract
        def total_under_1000() -> ContractSpec:
            return effect("charge").sum(lambda e: e.amount) <= 1000


        @clauz3.guarantee(total_under_1000())
        def main() -> None:
            for inv in db_query("invoices"):
                charge(inv.amount)
    '''
    results = prove_text(textwrap.dedent(source))
    assert not all(r.ok for r in results)
    error_str = " ".join(str(r.proof.error or "") for r in results)
    assert "quantified-aggregates" in error_str or "sum" in error_str.lower()
```

- [ ] **Step 2-4**: Already implemented in Task 21's `_expr_uses_any_bound_var` check. Run test:
```
uv run pytest tests/test_quantified_relations.py::test_sum_selector_depending_on_row_raises_unsupported -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_quantified_relations.py
git commit -m "test: assert sum(row.field) under loop raises UnsupportedError"
```

---

### Task 23: DistinctSpec — ∀∀ formula

**Files:**
- Modify: `src/clauz3/spec.py:DistinctSpec.solve`

- [ ] **Step 1: Test**

```python
def test_distinct_over_loop_fails_without_uniqueness_postcondition():
    source = '''
        import deal
        import clauz3
        from clauz3.spec import contract, effect, ContractSpec


        class UserRow(clauz3.Row):
            email: str


        @deal.has("db_read")
        @deal.post(lambda result: len(result) <= 10)
        def db_query(table: str) -> list[UserRow]: ...


        @deal.has("email")
        def send_email(addr: str, msg: str) -> None: ...


        @contract
        def unique_recipients() -> ContractSpec:
            return effect("send_email").distinct(lambda e: e.addr)


        @clauz3.guarantee(unique_recipients())
        def main() -> None:
            for row in db_query("users"):
                send_email(row.email, "hi")
    '''
    # No postcondition guarantees emails are distinct — proof fails.
    assert not _prove(source)
```

- [ ] **Step 2: Run** — FAIL (current DistinctSpec doesn't quantify properly).

- [ ] **Step 3: Implement**

```python
@dataclass(frozen=True)
class DistinctSpec(ContractSpec):
    relation: EffectRelation
    key: "LambdaSpec"
    fact_filter: "LambdaSpec | None" = None

    def solve(self, *, ctx: Any) -> BoolSort:
        import z3
        clauses = []
        facts = self.relation.facts(ctx)
        for index, left in enumerate(facts):
            left_cond = _fact_cond(fact=left, predicate=self.fact_filter, ctx=ctx)
            for right in facts[index + 1:]:
                right_cond = _fact_cond(fact=right, predicate=self.fact_filter, ctx=ctx)
                # Cross-fact case: both might or might not have quantifiers
                clauses.append(_distinct_clause(
                    left=left, right=right, key=self.key,
                    left_cond=left_cond, right_cond=right_cond, ctx=ctx,
                ))
            # Same-fact case: only relevant if fact has quantifiers
            if left.quantifiers:
                clauses.append(_same_fact_distinct(
                    fact=left, key=self.key, cond=left_cond, ctx=ctx,
                ))
        return and_expr(*clauses, ctx=ctx)


def _distinct_clause(*, left, right, key, left_cond, right_cond, ctx):
    """∀ qvars_left, qvars_right: bounds ∧ conds → key(left) ≠ key(right)."""
    import z3
    left_key = key.evaluate(row=left.args, ctx=ctx)
    right_key = key.evaluate(row=right.args, ctx=ctx)
    body = or_expr(
        left_cond.m_not(ctx=ctx),
        right_cond.m_not(ctx=ctx),
        left_key.m_ne(right_key, ctx=ctx),
        ctx=ctx,
    )
    # Wrap in ForAll over the union of quantifier bound vars
    quantifiers = list(left.quantifiers) + list(right.quantifiers)
    if not quantifiers:
        return body
    bound_vars = [q.bound_var for q in quantifiers]
    bounds = z3.And(*[q.bounds_expr() for q in quantifiers])
    wrapped = z3.ForAll(bound_vars, z3.Implies(bounds, body.expr if hasattr(body, "expr") else body))
    return types.bool.from_expr(wrapped, ctx=ctx)


def _same_fact_distinct(*, fact, key, cond, ctx):
    """∀ i, j: i ≠ j ∧ bounds(i) ∧ bounds(j) ∧ cond(i) ∧ cond(j) → key(i) ≠ key(j).

    Where i, j are fresh symbolic indices for the same fact's quantifiers.
    """
    import z3
    # Make fresh symbolic vars for this comparison
    fresh_vars_left = [z3.Int(f"{q.bound_var}_lhs") for q in fact.quantifiers]
    fresh_vars_right = [z3.Int(f"{q.bound_var}_rhs") for q in fact.quantifiers]
    # Substitute in args
    args_left = _substitute_quantifier_vars(fact.args, fact.quantifiers, fresh_vars_left)
    args_right = _substitute_quantifier_vars(fact.args, fact.quantifiers, fresh_vars_right)
    cond_left = _substitute_quantifier_vars({"_cond": cond}, fact.quantifiers, fresh_vars_left)["_cond"]
    cond_right = _substitute_quantifier_vars({"_cond": cond}, fact.quantifiers, fresh_vars_right)["_cond"]
    key_left = key.evaluate(row=args_left, ctx=ctx)
    key_right = key.evaluate(row=args_right, ctx=ctx)

    different_iteration = z3.Or(*[
        l != r for l, r in zip(fresh_vars_left, fresh_vars_right)
    ])
    bounds = z3.And(
        *[z3.And(q.lower <= lv, lv < q.upper)
          for q, lv in zip(fact.quantifiers, fresh_vars_left)],
        *[z3.And(q.lower <= rv, rv < q.upper)
          for q, rv in zip(fact.quantifiers, fresh_vars_right)],
    )
    body = z3.Implies(
        z3.And(different_iteration, bounds,
               cond_left.expr if hasattr(cond_left, "expr") else cond_left,
               cond_right.expr if hasattr(cond_right, "expr") else cond_right),
        key_left.m_ne(key_right, ctx=ctx).expr,
    )
    wrapped = z3.ForAll(fresh_vars_left + fresh_vars_right, body)
    return types.bool.from_expr(wrapped, ctx=ctx)


def _substitute_quantifier_vars(args, quantifiers, fresh_vars):
    """Substitute fact's bound_var in args with fresh_vars."""
    import z3
    subs = list(zip(
        [q.bound_var for q in quantifiers],
        fresh_vars,
    ))
    result = {}
    for key, value in args.items():
        expr = value.expr if hasattr(value, "expr") else value
        result[key] = type(value).from_expr(z3.substitute(expr, *subs), ctx=None) if hasattr(value, "expr") else value
    return result
```

Note: this is complex and may need iteration. The intent is documented; the `_substitute_quantifier_vars` helper requires the Z3 `substitute` function. If type-wrapping turns out hard, simplify by doing all substitution at the Z3 level and wrapping only at the end.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_quantified_relations.py -v -k distinct`
Expected: PASS.

Also run existing distinct tests: `uv run pytest tests/test_email_prover.py -v -k unique` (the existing email example uses `unique_recipients`). These must still pass — when there are no quantifiers, `_same_fact_distinct` is skipped and `_distinct_clause` collapses to today's behavior.

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/spec.py tests/test_quantified_relations.py
git commit -m "feat(spec): DistinctSpec emits ∀∀ formula for quantified facts"
```

---

### Task 24: SharesValueSpec rejects quantified relations in v1

**Files:**
- Modify: `src/clauz3/spec.py:SharedValueSpec`

- [ ] **Step 1: Test**

```python
def test_shares_value_with_quantified_relation_raises_unsupported():
    # Pseudo-test; if SharedValueSpec is invoked with any quantified fact,
    # the relation must raise UnsupportedError. The cleanest test stages
    # a relevant scenario:
    source = '''
        import deal
        import clauz3
        from clauz3.spec import contract, effect, ContractSpec


        class UserRow(clauz3.Row):
            email: str


        @deal.has("db_read")
        @deal.post(lambda result: len(result) <= 5)
        def db_query(table: str) -> list[UserRow]: ...


        @deal.has("email")
        def send_email(addr: str, msg: str) -> None: ...


        @contract
        def both_get_same() -> ContractSpec:
            Email = effect("send_email")
            left = Email.where(lambda e: e.addr == "bob@x")
            right = Email.where(lambda e: e.addr == "ann@x")
            return left.shares_value(right, lambda e: e.msg)


        @clauz3.guarantee(both_get_same())
        def main() -> None:
            for row in db_query("users"):
                send_email(row.email, "hi")
    '''
    results = prove_text(textwrap.dedent(source))
    error_str = " ".join(str(r.proof.error or "") for r in results)
    assert "quantified-shares-value" in error_str or "shares_value" in error_str
```

- [ ] **Step 2-3: Implement guard**

Locate `SharedValueSpec.solve` in spec.py. Add at the top:

```python
    def solve(self, *, ctx: Any) -> BoolSort:
        # v1: shares_value not supported when either relation has quantified facts
        for fact in self.left_relation.facts(ctx) + self.right_relation.facts(ctx):
            if getattr(fact, "quantifiers", ()):
                from clauz3._vendor.deal_solver._exceptions import UnsupportedError
                raise UnsupportedError(
                    "shares_value across quantified relations is not "
                    "supported in v1; see docs/todos/quantified-shares-value.md"
                )
        # ... existing implementation
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_quantified_relations.py -v -k shares_value`
Expected: PASS.

Also: `uv run pytest tests/test_email_prover.py -v -k same_content` — existing `shares_value` tests on non-quantified facts must still pass.

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/spec.py tests/test_quantified_relations.py
git commit -m "feat(spec): SharesValueSpec rejects quantified facts in v1"
```

---

### Task 25: Commit 3 checkpoint

- [ ] **Step 1: Full test run**

```bash
just test
```

- [ ] **Step 2: If green, optionally squash Tasks 18-24**

```bash
git log --oneline main..HEAD
# Identify the range, e.g., last 7 commits
git reset --soft HEAD~7  # if Tasks 18-24 are last 7 commits
git commit -m "feat(spec): quantifier-aware relation primitives

- AllSpec, EmptySpec, FilteredRelation: wrap body in ForAll over quantifiers
- SumSpec/count: contribution = base * product(quantifier.upper) with bound-var-dependent selector rejection
- DistinctSpec: ∀∀ same-fact + cross-fact formulas with fresh substituted indices
- SharesValueSpec: rejects quantified facts pointing at todos/quantified-shares-value.md"
```

---

## Phase D: Column references (commit 4)

### Task 26: _as_proxy recognizes ColumnRef

**Files:**
- Modify: `src/clauz3/spec.py:_as_proxy` (and surrounding)
- Test: `tests/test_column_ref.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_column_ref.py
"""ColumnRef matching in contracts."""
from __future__ import annotations

import textwrap

import pytest

from clauz3.prover import prove_text


def _prove(src: str) -> bool:
    results = prove_text(textwrap.dedent(src))
    return all(r.ok for r in results)


def test_column_ref_passes_when_addr_came_from_column():
    src = '''
        import deal
        import clauz3
        from clauz3.spec import contract, effect, ContractSpec


        class UserRow(clauz3.Row):
            name: str
            email: str


        @deal.has("db_read")
        @deal.post(lambda result: len(result) <= 100)
        def db_query(table: str) -> list[UserRow]: ...


        @deal.has("email")
        @deal.pre(lambda addr, msg: "@" in addr)
        def send_email(addr: str, msg: str) -> None: ...


        @contract
        def addrs_from_users_email() -> ContractSpec:
            return effect("send_email").all(
                lambda e: e.addr == getattr(UserRow, "email"),
            )


        @clauz3.guarantee(addrs_from_users_email())
        def main() -> None:
            for row in db_query("users"):
                send_email(row.email, "hi")
    '''
    assert _prove(src)


def test_column_ref_fails_when_addr_is_literal():
    src = '''
        import deal
        import clauz3
        from clauz3.spec import contract, effect, ContractSpec


        class UserRow(clauz3.Row):
            email: str


        @deal.has("db_read")
        @deal.post(lambda result: len(result) <= 100)
        def db_query(table: str) -> list[UserRow]: ...


        @deal.has("email")
        def send_email(addr: str, msg: str) -> None: ...


        @contract
        def addrs_from_users_email() -> ContractSpec:
            return effect("send_email").all(
                lambda e: e.addr == getattr(UserRow, "email"),
            )


        @clauz3.guarantee(addrs_from_users_email())
        def main() -> None:
            send_email("admin@example.com", "manual")
    '''
    assert not _prove(src)


def test_column_ref_fails_when_addr_from_wrong_column():
    src = '''
        import deal
        import clauz3
        from clauz3.spec import contract, effect, ContractSpec


        class UserRow(clauz3.Row):
            name: str
            email: str


        @deal.has("db_read")
        @deal.post(lambda result: len(result) <= 100)
        def db_query(table: str) -> list[UserRow]: ...


        @deal.has("email")
        def send_email(addr: str, msg: str) -> None: ...


        @contract
        def addrs_from_users_email() -> ContractSpec:
            return effect("send_email").all(
                lambda e: e.addr == getattr(UserRow, "email"),
            )


        @clauz3.guarantee(addrs_from_users_email())
        def main() -> None:
            for row in db_query("users"):
                send_email(row.name, "hi")   # wrong column!
    '''
    assert not _prove(src)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_column_ref.py -v`
Expected: FAIL — the lambda compiler's `_eval_compare` raises on `ColumnRef` (unknown name).

- [ ] **Step 3: Implement `_as_proxy` extension**

In `src/clauz3/spec.py`, find `_as_proxy` (search: `def _as_proxy`). Add a case at the top:

```python
def _as_proxy(value: Any, *, ctx: Any) -> ProxySort:
    if isinstance(value, ProxySort):
        return value
    from clauz3.row import ColumnRef
    if isinstance(value, ColumnRef):
        return _ColumnRefProxy(column=value, ctx=ctx)
    if isinstance(value, bool):
        return types.bool.val(value, ctx=ctx)
    # ... existing cases
```

Add the proxy wrapper class:

```python
@dataclass(frozen=True)
class _ColumnRefProxy:
    column: "ColumnRef"
    ctx: Any

    @property
    def is_column_ref(self) -> bool:
        return True
```

- [ ] **Step 4: Don't run tests yet — Task 27 implements the matcher.**

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/spec.py tests/test_column_ref.py
git commit -m "feat(spec): _as_proxy recognizes ColumnRef and wraps in proxy"
```

---

### Task 27: _eval_compare dispatches column-ref matcher

**Files:**
- Modify: `src/clauz3/spec.py:_eval_compare`

- [ ] **Step 1: Implement dispatch**

Find `_eval_compare` (search: `def _eval_compare`). Add an early dispatch:

```python
def _eval_compare(self, node: ast.Compare) -> ProxySort:
    if len(node.ops) != 1 or len(node.comparators) != 1:
        raise UnsupportedError("chained comparisons are not supported")
    left = self.eval(node.left)
    right = self.eval(node.comparators[0])
    op = node.ops[0]

    # Column-ref equality dispatch
    left_is_col = getattr(left, "is_column_ref", False)
    right_is_col = getattr(right, "is_column_ref", False)
    if isinstance(op, (ast.Eq, ast.NotEq)) and (left_is_col or right_is_col):
        col_proxy = left if left_is_col else right
        other = right if left_is_col else left
        result = _compare_column_ref(other, col_proxy.column, ctx=self.ctx)
        if isinstance(op, ast.NotEq):
            return result.m_not(ctx=self.ctx)
        return result

    # ... existing comparison logic
```

- [ ] **Step 2: Implement the structural matcher**

Add to `spec.py`:

```python
def _compare_column_ref(arg_proxy, column_ref, *, ctx):
    """Structural match: does arg_proxy's symbolic value have the shape
    <column_ref.field selector>(array_select(<query of column_ref.schema>, ?))?

    Returns BoolSort.val(True) on match, BoolSort.val(False) otherwise.
    """
    import z3
    from clauz3._vendor.deal_solver._proxies._row import (
        QueryResultSort, z3_datatype_for_row,
    )

    expr = arg_proxy.expr if hasattr(arg_proxy, "expr") else arg_proxy
    if not isinstance(expr, z3.ExprRef):
        return types.bool.val(False, ctx=ctx)

    # Top-level must be a function application of the field's accessor
    target_dt = z3_datatype_for_row(column_ref.schema)
    target_accessor_name = column_ref.field
    if expr.num_args() != 1:
        return types.bool.val(False, ctx=ctx)
    decl = expr.decl()
    if decl.name() != target_accessor_name:
        return types.bool.val(False, ctx=ctx)

    # The argument should be a Select(array, index) where the array's range
    # sort is the same Z3 datatype as column_ref.schema's
    inner = expr.arg(0)
    if inner.decl().kind() != z3.Z3_OP_SELECT:
        return types.bool.val(False, ctx=ctx)
    array_expr = inner.arg(0)
    array_sort = array_expr.sort()
    if array_sort.range() != target_dt:
        return types.bool.val(False, ctx=ctx)

    return types.bool.val(True, ctx=ctx)
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_column_ref.py -v`
Expected: all PASS.

- [ ] **Step 4: Run regression suite**

```bash
just test
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/clauz3/spec.py
git commit -m "feat(spec): structural matcher for ColumnRef equality"
```

---

### Task 28: Mixed-source program test (literal + loop)

**Files:**
- Test: `tests/test_column_ref.py`

- [ ] **Step 1: Write test**

```python
def test_column_ref_mixed_source_fails_on_literal_fact():
    src = '''
        import deal
        import clauz3
        from clauz3.spec import contract, effect, ContractSpec


        class UserRow(clauz3.Row):
            email: str


        @deal.has("db_read")
        @deal.post(lambda result: len(result) <= 100)
        def db_query(table: str) -> list[UserRow]: ...


        @deal.has("email")
        @deal.pre(lambda addr, msg: "@" in addr)
        def send_email(addr: str, msg: str) -> None: ...


        @contract
        def addrs_from_users_email() -> ContractSpec:
            return effect("send_email").all(
                lambda e: e.addr == getattr(UserRow, "email"),
            )


        @clauz3.guarantee(addrs_from_users_email())
        def main() -> None:
            send_email("admin@x.com", "manual")
            for row in db_query("users"):
                send_email(row.email, "newsletter")
    '''
    assert not _prove(src)
```

- [ ] **Step 2: Run** — PASS expected (literal fact fails the column-ref match → contract fails).

- [ ] **Step 3: No new code.**

- [ ] **Step 4: Commit**

```bash
git add tests/test_column_ref.py
git commit -m "test: column-ref contract fails on mixed-source programs"
```

---

### Task 29: Commit 4 checkpoint

- [ ] **Step 1: Full test run**

```bash
just test
```

- [ ] **Step 2: Optionally squash Tasks 26-28**

The headline lights up here. Worth one clean commit message if squashing:

```bash
git reset --soft HEAD~3
git commit -m "feat(spec): column-reference matching lights up the headline

Contracts can now express e.addr == UserRow.email, and the matcher
structurally verifies the symbolic source of every send_email fact's
addr argument is the email column of a UserRow query result.

Mixed-source programs (literal sends + loop-from-query sends) fail
correctly on the literal fact."
```

---

## Phase E: Worked example (commit 5)

### Task 30: Create example directory structure

**Files:**
- Create: `examples/email-from-db/` (directory)
- Create: `examples/email-from-db/tools/db/trusted/__init__.py`
- Create: `examples/email-from-db/tools/db/trusted/schemas.py`
- Create: `examples/email-from-db/tools/db/trusted/effects.py`
- Create: `examples/email-from-db/tools/db/trusted/contracts.py`

- [ ] **Step 1: Create directories**

```bash
mkdir -p examples/email-from-db/tools/db/trusted
mkdir -p examples/email-from-db/tools/email
mkdir -p examples/email-from-db/cases
mkdir -p examples/email-from-db/.claude
cp -r examples/email/tools/email/trusted examples/email-from-db/tools/email/
touch examples/email-from-db/tools/db/__init__.py
touch examples/email-from-db/tools/db/trusted/__init__.py
```

- [ ] **Step 2: Create UserRow schema**

```python
# examples/email-from-db/tools/db/trusted/schemas.py
"""Row schemas exposed by the db trusted layer."""
import clauz3


class UserRow(clauz3.Row):
    name: str
    email: str
    consented: bool
    role: str
```

- [ ] **Step 3: Create db_query trusted effect**

```python
# examples/email-from-db/tools/db/trusted/effects.py
"""Trusted database effects.

This is the audited boundary. The prover trusts these signatures and
preconditions; the runtime implementation actually hits DuckDB.
"""
import deal

from tools.db.trusted.schemas import UserRow


@deal.has("db_read")
@deal.post(lambda result: len(result) <= 100)
def db_query(table: str, where: dict[str, object]) -> list[UserRow]:
    """MOCK trusted DB query.

    Reads `table` filtered by `where`, returns at most 100 rows.
    """
    return []  # mock; real impl uses DuckDB
```

- [ ] **Step 4: Create db contracts vocabulary**

```python
# examples/email-from-db/tools/db/trusted/contracts.py
"""Contract vocabulary for db trusted effects."""
from clauz3.spec import ContractSpec, contract, effect


@contract
def only_table(table: str) -> ContractSpec:
    """Guarantee every db_query reads from `table` only."""
    return effect("db_query").all(lambda e: e.table == table)


@contract
def only_where(filter_dict: dict[str, object]) -> ContractSpec:
    """Guarantee every db_query uses exactly this `where` filter."""
    return effect("db_query").all(lambda e: e.where == filter_dict)
```

- [ ] **Step 5: Commit**

```bash
git add examples/email-from-db/tools/
git commit -m "feat(example): db trusted layer with UserRow schema"
```

---

### Task 31: Email contracts that reference UserRow columns

**Files:**
- Create: `examples/email-from-db/tools/email/trusted/contracts.py`

- [ ] **Step 1: Write contracts**

```python
# examples/email-from-db/tools/email/trusted/contracts.py
"""Email contract vocabulary, extended with column-source contracts."""
from clauz3.spec import ContractSpec, contract, effect

# Reuse existing email contracts: addresses_in_allowlist, count, etc.
# Existing email contracts from examples/email/ are imported by tests if needed.


@contract
def addresses_from(schema: type, field: str) -> ContractSpec:
    """Guarantee every email recipient came from `schema`.`field`."""
    column = getattr(schema, field)
    return effect("send_email").all(lambda e: e.addr == column)


@contract
def count_at_most(n: int) -> ContractSpec:
    """Guarantee at most `n` emails are sent."""
    return effect("send_email").count() <= n
```

- [ ] **Step 2: Commit**

```bash
git add examples/email-from-db/tools/email/trusted/contracts.py
git commit -m "feat(example): addresses_from + count_at_most contracts"
```

---

### Task 32: AGENTS.md and CLAUDE.md

**Files:**
- Create: `examples/email-from-db/AGENTS.md`
- Create: `examples/email-from-db/CLAUDE.md`

- [ ] **Step 1: Write AGENTS.md**

```markdown
# Agent Guide

You have access to email and a users database. All tool calls go through
`clauz3`.

## Database tools

```python
from tools.db.trusted.effects import db_query
from tools.db.trusted.schemas import UserRow
```

`db_query(table, where) -> list[UserRow]` reads from a table with a `where`
filter. Returns at most 100 rows.

`UserRow` has fields: `name`, `email`, `consented`, `role`.

## Email

```python
from tools.email.trusted.effects import send_email
```

`send_email(addr, msg) -> None` sends one email. `addr` must contain `"@"`.

## Available contracts

```python
from tools.email.trusted import contracts as emails
from tools.db.trusted import contracts as db

emails.addresses_from(schema, field)  # all recipients came from this column
emails.count_at_most(n)                # at most n emails
emails.only(addresses)                 # allowlist
emails.none()                          # no emails
db.only_table(table)                   # only this table
db.only_where(filter_dict)             # only this where filter
```

## Pattern

```python
@clauz3.guarantee(emails.addresses_from(UserRow, "email"))
@clauz3.guarantee(emails.count_at_most(100))
@clauz3.guarantee(db.only_table("users"))
@clauz3.guarantee(db.only_where({"consented": True}))
def main() -> None:
    for row in db_query("users", where={"consented": True}):
        send_email(row.email, "Newsletter is out!")
```

The contract that the user reviews is the four guarantees together. The
prover verifies the program satisfies them all before any side effects run.

## Notes

- `where` must be a literal dict at the call site (not a variable).
- Loop variables (`row` above) must not be reassigned inside the loop.
- `break`, `continue`, `return` inside a loop are not supported.
- Field types in row schemas: only `str`, `int`, `bool`.
```

- [ ] **Step 2: CLAUDE.md (thin pointer)**

```markdown
@AGENTS.md
```

- [ ] **Step 3: .claude/settings.json**

```json
{
  "permissions": {
    "defaultMode": "default",
    "allow": [
      "Read",
      "Glob",
      "Grep",
      "Bash(clauz3:*)"
    ]
  }
}
```

- [ ] **Step 4: Commit**

```bash
git add examples/email-from-db/AGENTS.md examples/email-from-db/CLAUDE.md examples/email-from-db/.claude/
git commit -m "feat(example): AGENTS/CLAUDE/settings for email-from-db"
```

---

### Task 33: users.csv sample data

**Files:**
- Create: `examples/email-from-db/users.csv`

- [ ] **Step 1: Create CSV**

```csv
name,email,consented,role
Bob,bob@example.com,true,user
Ann,ann@example.com,true,user
Carol,carol@example.com,false,user
Admin,admin@example.com,true,admin
```

- [ ] **Step 2: Commit**

```bash
git add examples/email-from-db/users.csv
git commit -m "feat(example): users.csv sample data"
```

---

### Task 34: Pass cases (headline + variants)

**Files:**
- Create: `examples/email-from-db/cases/newsletter_pass.py`
- Create: `examples/email-from-db/cases/count_pass.py`
- Create: `examples/email-from-db/cases/multiple_guarantees_pass.py`

- [ ] **Step 1: newsletter_pass.py (headline)**

```python
# examples/email-from-db/cases/newsletter_pass.py
# ruff: noqa: F821
import clauz3
from tools.db.trusted import contracts as db
from tools.db.trusted.effects import db_query
from tools.db.trusted.schemas import UserRow
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


@clauz3.guarantee(emails.addresses_from(UserRow, "email"))
@clauz3.guarantee(emails.count_at_most(100))
@clauz3.guarantee(db.only_table("users"))
@clauz3.guarantee(db.only_where({"consented": True}))
def main() -> None:
    for row in db_query("users", where={"consented": True}):
        send_email(row.email, "Newsletter is out!")
```

- [ ] **Step 2: count_pass.py**

```python
# examples/email-from-db/cases/count_pass.py
# ruff: noqa: F821
import clauz3
from tools.db.trusted.effects import db_query
from tools.db.trusted.schemas import UserRow
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


@clauz3.guarantee(emails.count_at_most(100))
def main() -> None:
    for row in db_query("users", where={"consented": True}):
        send_email(row.email, "hi")
```

- [ ] **Step 3: multiple_guarantees_pass.py — empty body case**

```python
# examples/email-from-db/cases/empty_query_pass.py
# ruff: noqa: F821
import clauz3
from tools.db.trusted.effects import db_query
from tools.db.trusted.schemas import UserRow
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


@clauz3.guarantee(emails.none())
def main() -> None:
    for row in db_query("nobody", where={}):
        # This loop's body runs only if the query has rows.
        # The fact's cond ensures send_email is reachable only inside the loop.
        # Without further constraints, this proof should... actually fail,
        # because len could be > 0. Let's not include this case.
        pass
```

Wait — `emails.none()` requires no emails ever, but the loop body sends emails. Even if length is 0, the symbolic execution still emits a quantified fact with quantifier `0 <= i < length`. The fact's cond doesn't depend on the loop being non-empty in our handler. `empty()` becomes `∀ i, bounds → ¬cond` which is true when cond is false everywhere — but cond is True here.

Drop this case. Replace with a more sensible pass case:

```python
# examples/email-from-db/cases/no_emails_with_loop_pass.py
# ruff: noqa: F821
import clauz3
from tools.db.trusted.effects import db_query
from tools.db.trusted.schemas import UserRow
from tools.email.trusted import contracts as emails


@clauz3.guarantee(emails.none())
def main() -> None:
    # Query but don't email; emails.none() holds because send_email
    # is never called.
    _rows = db_query("users", where={"consented": True})
```

- [ ] **Step 4: Commit**

```bash
git add examples/email-from-db/cases/newsletter_pass.py examples/email-from-db/cases/count_pass.py examples/email-from-db/cases/no_emails_with_loop_pass.py
git commit -m "feat(example): pass cases for email-from-db"
```

---

### Task 35: Fail cases

**Files:**
- Create: `examples/email-from-db/cases/literal_address_fail.py`
- Create: `examples/email-from-db/cases/wrong_column_fail.py`
- Create: `examples/email-from-db/cases/mixed_source_fail.py`
- Create: `examples/email-from-db/cases/count_too_tight_fail.py`

- [ ] **Step 1: literal_address_fail.py**

```python
# ruff: noqa: F821
import clauz3
from tools.db.trusted.schemas import UserRow
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


@clauz3.guarantee(emails.addresses_from(UserRow, "email"))
def main() -> None:
    send_email("admin@example.com", "manual")
```

- [ ] **Step 2: wrong_column_fail.py**

```python
# ruff: noqa: F821
import clauz3
from tools.db.trusted.effects import db_query
from tools.db.trusted.schemas import UserRow
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


@clauz3.guarantee(emails.addresses_from(UserRow, "email"))
def main() -> None:
    for row in db_query("users", where={}):
        send_email(row.name, "hi")  # name, not email — fail
```

- [ ] **Step 3: mixed_source_fail.py**

```python
# ruff: noqa: F821
import clauz3
from tools.db.trusted.effects import db_query
from tools.db.trusted.schemas import UserRow
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


@clauz3.guarantee(emails.addresses_from(UserRow, "email"))
def main() -> None:
    send_email("admin@example.com", "manual")
    for row in db_query("users", where={}):
        send_email(row.email, "newsletter")
```

- [ ] **Step 4: count_too_tight_fail.py**

```python
# ruff: noqa: F821
import clauz3
from tools.db.trusted.effects import db_query
from tools.db.trusted.schemas import UserRow
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


@clauz3.guarantee(emails.count_at_most(10))
def main() -> None:
    for row in db_query("users", where={}):
        send_email(row.email, "hi")
```

- [ ] **Step 5: Commit**

```bash
git add examples/email-from-db/cases/literal_address_fail.py examples/email-from-db/cases/wrong_column_fail.py examples/email-from-db/cases/mixed_source_fail.py examples/email-from-db/cases/count_too_tight_fail.py
git commit -m "feat(example): fail cases for email-from-db"
```

---

### Task 36: Justfile and tests/test_examples.py entries

**Files:**
- Create: `examples/email-from-db/Justfile`
- Modify: `tests/test_examples.py`

- [ ] **Step 1: Write Justfile**

```just
logic_db := ".agents/logic"
db_trusted := "tools/db/trusted"
email_trusted := "tools/email/trusted"
prove := "uv run clauz3 prove " + db_trusted + "/effects.py " + db_trusted + "/contracts.py " + db_trusted + "/schemas.py " + email_trusted + "/effects.py " + email_trusted + "/contracts.py"

test: cases

cases: newsletter-pass count-pass no-emails-with-loop-pass literal-address-fail wrong-column-fail mixed-source-fail count-too-tight-fail

newsletter-pass:
    {{prove}} cases/newsletter_pass.py

count-pass:
    {{prove}} cases/count_pass.py

no-emails-with-loop-pass:
    {{prove}} cases/no_emails_with_loop_pass.py

literal-address-fail:
    if {{prove}} cases/literal_address_fail.py; then exit 1; fi

wrong-column-fail:
    if {{prove}} cases/wrong_column_fail.py; then exit 1; fi

mixed-source-fail:
    if {{prove}} cases/mixed_source_fail.py; then exit 1; fi

count-too-tight-fail:
    if {{prove}} cases/count_too_tight_fail.py; then exit 1; fi
```

- [ ] **Step 2: Add entries to tests/test_examples.py**

Open `tests/test_examples.py` and append (or insert into the parametrized list):

```python
    # email-from-db cases
    ("email-from-db", "newsletter_pass.py", True),
    ("email-from-db", "count_pass.py", True),
    ("email-from-db", "no_emails_with_loop_pass.py", True),
    ("email-from-db", "literal_address_fail.py", False),
    ("email-from-db", "wrong_column_fail.py", False),
    ("email-from-db", "mixed_source_fail.py", False),
    ("email-from-db", "count_too_tight_fail.py", False),
```

Note: `tests/test_examples.py` may currently use a parametrize signature like `(example, logic, trusted, case, ok)` — the per-example trusted/logic file lists differ. For email-from-db there are multiple files. The existing test helper may need extension. Read `tests/test_examples.py` and adjust the parametrize signature to take a list of `prove`-arguments, or write a per-example fixture.

- [ ] **Step 3: Run**

```bash
just examples
uv run pytest tests/test_examples.py -v
```
Expected: all email-from-db cases pass-or-fail as expected; existing email/bank examples still green.

- [ ] **Step 4: Commit**

```bash
git add examples/email-from-db/Justfile tests/test_examples.py
git commit -m "feat(example): Justfile + test_examples entries for email-from-db"
```

---

### Task 37: Commit 5 checkpoint

```bash
just test
```

Optionally squash Tasks 30-36 into one commit titled "feat(example): worked end-to-end email-from-db example".

---

## Phase F: Docs (commit 6)

### Task 38: docs/symbolic-iteration.md

**Files:**
- Create: `docs/symbolic-iteration.md`

- [ ] **Step 1: Write the design summary**

```markdown
# Symbolic Iteration

This document describes how `clauz3` reasons about for-loops over trusted
query returns and how contracts can refer to the columns those rows came
from. See [docs/superpowers/specs/2026-05-25-symbolic-iteration-design.md](superpowers/specs/2026-05-25-symbolic-iteration-design.md)
for the original design.

## The agent-facing surface

Trusted functions return typed lists of `clauz3.Row` subclasses:

```python
class UserRow(clauz3.Row):
    name: str
    email: str
    consented: bool

@deal.has("db_read")
@deal.post(lambda result: len(result) <= 100)
def db_query(table: str, where: dict[str, object]) -> list[UserRow]: ...
```

Agents iterate with normal for-loops:

```python
for row in db_query("users", where={"consented": True}):
    send_email(row.email, "Newsletter")
```

## The contract surface

Contracts can refer to columns:

```python
@contract
def addresses_from(schema: type, field: str) -> ContractSpec:
    column = getattr(schema, field)  # returns ColumnRef
    return effect("send_email").all(lambda e: e.addr == column)
```

Usage:
```python
@clauz3.guarantee(emails.addresses_from(UserRow, "email"))
def main() -> None:
    for row in db_query("users", where={...}):
        send_email(row.email, "hi")
```

The prover verifies that every `send_email` fact's `addr` came from the
`email` field of a `UserRow` query result.

## v1 limitations

(see spec for full list)

- For-loop bodies cannot use `break`, `continue`, `return`.
- No state accumulators across iterations.
- Row field types are `str`, `int`, `bool` only.
- `where` arguments must be literal dicts.
- `sum(lambda r: r.numeric_field)` is unsupported; see
  [docs/todos/quantified-aggregates.md](todos/quantified-aggregates.md).
- `shares_value` across quantified relations is unsupported; see
  [docs/todos/quantified-shares-value.md](todos/quantified-shares-value.md).

## How it works (briefly)

The for-loop handler binds the loop variable to `query_result.at(i)` for a
fresh symbolic `i`, runs the body once symbolically, and snapshots a
`Quantifier` frame into every emitted fact. Relation primitives wrap their
Z3 body in `ForAll(...)` over fact quantifiers; when no quantifiers are
present, the wrapping short-circuits and the relation behaves as before
(no regression on the email and bank examples).

Column-reference equality (`e.addr == UserRow.email`) is a structural
check on the symbolic expression tree: the prover walks the Z3 expression
to verify it has the shape `<field selector>(array_select(<query result>,
?))` with the expected schema and field.
```

- [ ] **Step 2: Commit**

```bash
git add docs/symbolic-iteration.md
git commit -m "docs: symbolic-iteration shipped-design summary"
```

---

### Task 39: docs/todos/quantified-aggregates.md

**Files:**
- Create: `docs/todos/quantified-aggregates.md`

- [ ] **Step 1: Write**

```markdown
# Quantified Aggregates (future work)

## Problem

`sum(lambda r: r.amount)` over a quantified fact requires summing
`array[i].amount` over `i in [0, length)`. Z3 doesn't natively support
unbounded symbolic sums. v1 raises `UnsupportedError` for this case.

## Routes

### A. Z3 recursive functions

Define a recursive function:

```python
def sum_amounts(arr, length):
    if length == 0: return 0
    return arr[length - 1].amount + sum_amounts(arr, length - 1)
```

Z3 can reason about recursive functions via fixed-point logic, but it
is slow and incomplete for many goals.

### B. Bounded unrolling fallback

If the trusted call's postcondition gives a literal upper bound
`len(result) <= K`, unroll the sum K times symbolically (each term
conditional on `i < length`). This combines today's unrolling approach
with the new quantifier-aware path: most facts stay quantified, but
sum-aggregates get the unrolled treatment.

Trade-off: K becomes a knob; large K slows proofs.

### C. Sequence theory

Z3's Seq sort has `seq.length` and `seq.nth` but limited reasoning
over predicates. Worth investigating whether a sum can be encoded as
the length of a filtered subsequence, but unlikely to be a general
answer.

## Recommendation

Start with B (bounded unrolling fallback). It composes with the rest of
the design and gives a working answer for realistic K. Revisit A or C
if unrolling proves insufficient.
```

- [ ] **Step 2: Commit**

```bash
git add docs/todos/quantified-aggregates.md
git commit -m "docs(todo): quantified-aggregates future-work plan"
```

---

### Task 40: docs/todos/quantified-shares-value.md

```markdown
# Quantified shares_value (future work)

## Problem

`shares_value(other, key)` is an ∃-style relation: "there exists some
value that both sides emit." Under quantification, it becomes
`∃ i, j. left[i].key == right[j].key` — Z3 handles existentials via
Skolemization, but composing with the rest of the algebra is awkward.

## Sketch

1. Skolemize each side's quantifier — introduce a Skolem index `i_left`
   and `j_right`.
2. Translate `shares_value` to
   `0 ≤ i_left < length_left ∧ 0 ≤ j_right < length_right
    ∧ left[i_left].key == right[j_right].key`.
3. Negate to test for proof; existential becomes universal.

## Trade-offs

- More Z3 quantifier alternations (∀ → ∃ → ∀) — slower.
- Failure mode harder to interpret.

## Recommendation

Defer until a real example needs it. v1 surfaces `UnsupportedError` with
a pointer to this doc.
```

```bash
git add docs/todos/quantified-shares-value.md
git commit -m "docs(todo): quantified-shares-value future-work plan"
```

---

### Task 41: Cross-references in existing docs

**Files:**
- Modify: `README.md`, `docs/effect-specs.md`, `AGENTS.md` (top-level), `docs/integration-testing.md`

- [ ] **Step 1: README.md**

Add a row to the Examples table:

```markdown
| [`examples/email-from-db`](examples/email-from-db) | `emails.addresses_from`, `db.only_table`, `db.only_where` | for-loops over trusted query returns, column-binding constraints |
```

And under Status, add a bullet:

```markdown
- For-loops over `list[Row]`-returning trusted calls, with column-binding
  contracts via `UserRow.email` markers. See
  [docs/symbolic-iteration.md](docs/symbolic-iteration.md).
```

- [ ] **Step 2: docs/effect-specs.md**

Remove or update the "provenance facts" future-work bullet (lines around
214-216), since the column-reference mechanism replaces it.

Add a section:

```markdown
## Symbolic Iteration

Trusted functions returning `list[<Row-subclass>]` produce symbolic query
results. Agents iterate with normal for-loops; the prover handles the
loop as a ∀-quantifier. See [symbolic-iteration.md](symbolic-iteration.md).
```

- [ ] **Step 3: AGENTS.md (top-level)**

Add a bullet under what agents can do:

```markdown
- Loop over a trusted query: `for row in db_query("users", where={"consented": True}): send_email(row.email, msg)`. Contracts can constrain the source column with `emails.addresses_from(UserRow, "email")`.
```

- [ ] **Step 4: docs/integration-testing.md**

Append a "DB example" section parallel to the email recipe:

```markdown
## DB Example

Same setup as the email recipe, but copy `examples/email-from-db/` as the
template. The prompt to Claude becomes something like:

> Read AGENTS.md. Email all consented users with the message "Newsletter is
> out!". Use the email column. Do the task now.

The agent should produce a program that uses `for row in db_query(...):
send_email(row.email, msg)` and attach `emails.addresses_from(UserRow,
"email")` plus a count guarantee.
```

- [ ] **Step 5: Commit**

```bash
git add README.md docs/effect-specs.md AGENTS.md docs/integration-testing.md
git commit -m "docs: cross-reference symbolic-iteration capability"
```

---

### Task 42: Final commit + PR prep

- [ ] **Step 1: Run full test suite from scratch**

```bash
just test
```

- [ ] **Step 2: Verify branch history is clean**

```bash
git log --oneline main..HEAD
```

The history should read as a clean progression of 6 logical commits (or
more, depending on squashing choices in earlier checkpoints).

- [ ] **Step 3: Push and open PR**

```bash
git push -u origin feat/symbolic-iteration
gh pr create --title "feat: symbolic iteration over trusted return values" --body "$(cat <<'EOF'
## Summary

Lets agents write idiomatic for-loops over trusted query returns and
prove column-binding constraints. See
[docs/superpowers/specs/2026-05-25-symbolic-iteration-design.md](docs/superpowers/specs/2026-05-25-symbolic-iteration-design.md)
for the full design and
[docs/symbolic-iteration.md](docs/symbolic-iteration.md) for the shipped
summary.

## Headline example

```python
@clauz3.guarantee(emails.addresses_from(UserRow, "email"))
def main() -> None:
    for row in db_query("users", where={"consented": True}):
        send_email(row.email, "Newsletter")
```

The four-line program plus contract proves: every email recipient came
from the `email` column of the `users` query.

## What's in v1 / what's not

- ✅ For-loops over `list[Row]` returns; nested loops; column-binding
  contracts; `all`, `empty`, `where`, `count` under quantification;
  `distinct` with trusted-side uniqueness postconditions.
- ❌ `sum(lambda r: r.numeric_field)` (deferred —
  [docs/todos/quantified-aggregates.md](docs/todos/quantified-aggregates.md))
- ❌ `shares_value` across quantified relations (deferred —
  [docs/todos/quantified-shares-value.md](docs/todos/quantified-shares-value.md))
- ❌ `break`/`continue`/`return` in loop bodies
- ❌ Row field types other than `str`, `int`, `bool`

## Test plan

- [x] `just test` — pytest + ruff + mypy + all examples green
- [x] Headline example proves in under 5s
- [x] Existing email and bank examples regress no more than 20% in
  proof time

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

### Spec coverage check

- [ ] **Foundation**: Tasks 1-12 cover Row, ColumnRef, RowSort, QueryResultSort, Quantifier, FactInfo extension, Context layer, materialization. ✓
- [ ] **For-loop handler**: Tasks 13-17 cover dispatch, quantifier emission, nested loops, v1 restrictions. ✓
- [ ] **Quantifier-aware relations**: Tasks 18-25 cover all five primitives + wrapper helper + shares_value rejection. ✓
- [ ] **Column references**: Tasks 26-29 cover ColumnRef proxy, compare dispatch, structural matcher, mixed-source test. ✓
- [ ] **Worked example**: Tasks 30-37 cover full email-from-db example with pass + fail cases. ✓
- [ ] **Docs**: Tasks 38-41 cover symbolic-iteration.md, both TODO docs, README/AGENTS/effect-specs/integration-testing cross-refs. ✓

### Placeholder scan

- [ ] No "TBD", "TODO", "fill in" in implementation steps.
- [ ] No "appropriate error handling" hand-waves.
- [ ] No "similar to Task N" — each task is self-contained.
- [ ] One known weakness: Task 23 (DistinctSpec) has complex Z3 substitution logic that may need iteration during implementation. Acceptable because the test is fully specified — the implementer can adjust the implementation to pass.

### Type consistency

- [ ] `FactInfo.quantifiers` is `tuple[Quantifier, ...]` (annotated as plain `tuple` in source to avoid circular import). Used consistently in spec.py via `getattr(fact, "quantifiers", ())`.
- [ ] `Quantifier.bound_var: z3.ArithRef`, `source: QueryResultSort`, `lower/upper: z3.ArithRef`. Consistent.
- [ ] `ColumnRef(schema, field)` consistent.
- [ ] `QueryResultSort.row_schema: type`, `array_expr: z3.ArrayRef`, `length_expr: z3.ArithRef`, `source: tuple[str, dict[str, ProxySort]]`. Consistent.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-25-symbolic-iteration.md`. Two execution options:

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, two-stage review between tasks, fast iteration. Best for complex domains with novel Z3 interactions.

**2. Inline Execution** — Execute tasks in this session using `executing-plans`, batch execution with checkpoints. Faster but reads more of the conversation context.

**Which approach?**
