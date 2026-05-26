from collections.abc import Callable

import pytest
import z3

from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._proxies import StrSort
from clauz3._vendor.deal_solver._proxies._row import RowSort, z3_datatype_for_row
from clauz3.prover import prove_text
from clauz3.row import ColumnRef, Row


def test_column_ref_is_frozen_dataclass() -> None:
    c = ColumnRef(schema=int, field="x")
    assert c.schema is int
    assert c.field == "x"
    with pytest.raises(AttributeError):
        c.field = "y"  # type: ignore[misc]  # frozen


class UserRow(Row):
    name: str
    email: str
    consented: bool


def test_class_attribute_returns_column_ref() -> None:
    ref = UserRow.email
    assert isinstance(ref, ColumnRef)
    assert ref.schema is UserRow
    assert ref.field == "email"


def test_dunder_attrs_dont_become_column_refs() -> None:
    # __class__, __mro__, __init__, etc. must still work normally
    assert UserRow.__name__ == "UserRow"


def test_row_instance_construction_and_field_access() -> None:
    row = UserRow(name="Bob", email="bob@x", consented=True)
    assert row.name == "Bob"
    assert row.email == "bob@x"
    assert row.consented is True


def test_row_instance_is_immutable() -> None:
    row = UserRow(name="Bob", email="bob@x", consented=True)
    with pytest.raises(AttributeError):
        row.name = "Eve"


def test_row_instance_equality() -> None:
    a = UserRow(name="Bob", email="bob@x", consented=True)
    b = UserRow(name="Bob", email="bob@x", consented=True)
    c = UserRow(name="Ann", email="ann@x", consented=True)
    assert a == b
    assert a != c


def test_row_only_supports_str_int_bool_fields() -> None:
    with pytest.raises(TypeError, match="v1 only supports str/int/bool"):

        class BadRow(Row):
            x: list  # type: ignore[type-arg]


def test_clauz3_top_level_exports_row_and_columnref() -> None:
    import clauz3

    assert clauz3.Row is Row
    assert clauz3.ColumnRef is ColumnRef


def test_z3_datatype_for_row_caches_per_class() -> None:
    dt1 = z3_datatype_for_row(UserRow)
    dt2 = z3_datatype_for_row(UserRow)
    assert dt1 is dt2


def test_z3_datatype_field_sorts_match_annotations() -> None:
    dt = z3_datatype_for_row(UserRow)
    assert dt.num_constructors() == 1
    name_sort = dt.accessor(0, 0).range()
    email_sort = dt.accessor(0, 1).range()
    consented_sort = dt.accessor(0, 2).range()
    assert name_sort == z3.StringSort()
    assert email_sort == z3.StringSort()
    assert consented_sort == z3.BoolSort()


def test_z3_datatype_for_row_rejects_unsupported_field_type() -> None:
    class _NotARow:
        __annotations__ = {"x": list}

    with pytest.raises(TypeError, match="unsupported field type"):
        z3_datatype_for_row(_NotARow)


def test_row_sort_field_returns_typed_proxy(ctx_factory: Callable[[], Context]) -> None:
    ctx = ctx_factory()
    dt = z3_datatype_for_row(UserRow)
    bob = dt.constructor(0)(
        z3.StringVal("Bob"), z3.StringVal("bob@x"), z3.BoolVal(True)
    )
    row = RowSort(schema=UserRow, expr=bob)
    email = row.field("email", ctx=ctx)
    assert isinstance(email, StrSort)


def test_row_sort_rejects_unknown_field(ctx_factory: Callable[[], Context]) -> None:
    ctx = ctx_factory()
    dt = z3_datatype_for_row(UserRow)
    bob = dt.constructor(0)(
        z3.StringVal("Bob"), z3.StringVal("bob@x"), z3.BoolVal(True)
    )
    row = RowSort(schema=UserRow, expr=bob)
    with pytest.raises(AttributeError, match="unknown field"):
        row.field("address", ctx=ctx)


def test_query_result_sort_at_returns_rowsort(
    ctx_factory: Callable[[], Context],
) -> None:
    from clauz3._vendor.deal_solver._proxies._row import QueryResultSort

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


def test_row_supports_postponed_annotations() -> None:
    """PEP 563: from __future__ import annotations turns field annotations
    into strings. The Row metaclass must normalize them."""
    import types as _types

    mod = _types.ModuleType("_test_postponed_module")
    code = compile(
        "from __future__ import annotations\n"
        "from clauz3 import Row\n"
        "class PostponedRow(Row):\n"
        "    name: str\n"
        "    count: int\n"
        "    flag: bool\n",
        filename="<test>",
        mode="exec",
    )
    exec(code, mod.__dict__)
    PostponedRow = mod.PostponedRow
    instance = PostponedRow(name="alice", count=3, flag=True)
    assert instance.name == "alice"
    assert instance.count == 3
    assert instance.flag is True
    # ColumnRef access must still work
    from clauz3.row import ColumnRef

    assert isinstance(PostponedRow.name, ColumnRef)
    assert PostponedRow.name.field == "name"


def test_row_postponed_annotation_with_unsupported_type_raises() -> None:
    """PEP 563: unsupported string annotations should still raise TypeError."""
    import types as _types

    code = compile(
        "from __future__ import annotations\n"
        "from clauz3 import Row\n"
        "class BadRow(Row):\n"
        "    bad: list\n",
        filename="<test>",
        mode="exec",
    )
    mod = _types.ModuleType("_test_bad_postponed_module")
    with pytest.raises(TypeError, match="v1 only supports str/int/bool"):
        exec(code, mod.__dict__)


def test_trusted_call_returning_list_row_materializes_query_result() -> None:
    """A trusted function annotated -> list[UserRow] should return a QueryResultSort.

    The agent code below uses `rows[0]` and `first.email` — operations that
    work only if `rows` is a materialized QueryResultSort, not bool.True.
    """
    source = """
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
    first = rows[0]   # forces indexing on rows
    _ = first.email   # forces a field access
"""
    results = prove_text(source)
    assert all(r.ok for r in results), [r.proof.error for r in results]
