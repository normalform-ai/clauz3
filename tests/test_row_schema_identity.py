"""Row schemas must map to Z3 sorts by class identity, not by bare name.

Regression tests for #127. Z3 interns datatype sorts by name inside one
context, so naming a Row datatype after ``schema.__name__`` made every
same-named ``Row`` subclass in the process share one sort — and made the
second declaration rewrite the first.
"""

from __future__ import annotations

import sys
import types as pytypes
from collections.abc import Callable
from pathlib import Path

import astroid
import pytest
import z3

from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._eval_expr import _resolve_row_class
from clauz3._vendor.deal_solver._proxies import types as proxy_types
from clauz3._vendor.deal_solver._proxies._row import (
    _accessor_for_field,
    z3_datatype_for_row,
)
from clauz3._vendor.deal_solver._theorem import Conclusion
from clauz3.prover import prove_path
from clauz3.row import ColumnRef, Row

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "collide_rows"


def make_row(module: str, name: str, fields: dict[str, type]) -> type:
    """Build a Row subclass with an explicit module, as an import would."""
    return type(
        name,
        (Row,),
        {"__annotations__": dict(fields), "__module__": module, "__qualname__": name},
    )


@pytest.fixture
def same_named_rows() -> tuple[type, type]:
    """Two distinct Row classes named ``UserRow`` in different modules."""
    return (
        make_row("app.db.rows", "UserRow", {"name": str, "email": str}),
        make_row("corp.ldap.rows", "UserRow", {"name": str, "email": str}),
    )


def test_same_named_row_classes_get_distinct_sorts(
    same_named_rows: tuple[type, type],
) -> None:
    db_row, ldap_row = same_named_rows
    assert db_row is not ldap_row
    assert not z3_datatype_for_row(db_row).eq(z3_datatype_for_row(ldap_row))


def test_same_qualified_name_still_gets_distinct_sorts() -> None:
    """Synthetic inline schemas share a module and qualname but are distinct.

    ``row_schema_from_class_def`` mints one synthetic class per (module, file,
    qname, fields) key, so two source files that share a module name can yield
    two distinct classes with identical ``__module__`` and ``__qualname__``.
    Qualified naming alone would collide again; sorts follow class identity.
    """
    first = make_row("case", "InlineRow", {"email": str})
    second = make_row("case", "InlineRow", {"email": str})
    assert not z3_datatype_for_row(first).eq(z3_datatype_for_row(second))


def test_second_schema_does_not_rewrite_the_first(
    same_named_rows: tuple[type, type],
) -> None:
    """The cached sort of the first class must keep its own accessors."""
    db_row, _ = same_named_rows
    dt = z3_datatype_for_row(db_row)
    other = make_row("corp.ldap.rows", "UserRow", {"secret": str})
    z3_datatype_for_row(other)

    accessors = [dt.accessor(0, i).name() for i in range(dt.constructor(0).arity())]
    assert accessors == ["name", "email"]
    assert _accessor_for_field(dt, "email").name() == "email"


def test_cached_sort_is_reused_for_one_class(
    same_named_rows: tuple[type, type],
) -> None:
    db_row, _ = same_named_rows
    assert z3_datatype_for_row(db_row) is z3_datatype_for_row(db_row)


def test_sort_names_survive_a_datatype_cache_restore() -> None:
    """Sort names must not be derived from ``_DATATYPE_CACHE``'s size.

    Per-proof isolation snapshots that cache and restores it between proofs,
    so its length is not monotonic. A size-derived serial would hand the same
    name to a later schema and Z3 would rewrite the earlier sort again.
    """
    from clauz3._vendor.deal_solver._proxies import _row

    saved = dict(_row._DATATYPE_CACHE)
    first = make_row("app.db.rows", "RestoredRow", {"name": str, "email": str})
    first_dt = z3_datatype_for_row(first)

    _row._DATATYPE_CACHE.clear()
    _row._DATATYPE_CACHE.update(saved)

    second = make_row("corp.ldap.rows", "RestoredRow", {"dn": str})
    assert not first_dt.eq(z3_datatype_for_row(second))
    assert first_dt.constructor(0).arity() == 2


def test_compare_column_ref_rejects_a_same_named_schema(
    same_named_rows: tuple[type, type],
    ctx_factory: Callable[[], Context],
) -> None:
    """An address drawn from ``corp.ldap.rows.UserRow.email`` must not satisfy
    ``addresses_from(app.db.rows.UserRow, "email")``."""
    from clauz3.spec import _compare_column_ref

    db_row, ldap_row = same_named_rows
    ldap_dt = z3_datatype_for_row(ldap_row)
    rows = z3.Array("ldap_rows", z3.IntSort(), ldap_dt)
    email = _accessor_for_field(ldap_dt, "email")(z3.Select(rows, z3.IntVal(0)))

    matched = _compare_column_ref(
        proxy_types.str(expr=email),
        ColumnRef(schema=db_row, field="email"),
        ctx=ctx_factory(),
    )
    assert z3.is_false(z3.simplify(matched.expr))


def install_module(
    monkeypatch: pytest.MonkeyPatch, name: str, **members: object
) -> None:
    """Register a throwaway module so ``_resolve_row_class`` can scan it."""
    module = pytypes.ModuleType(name)
    for key, value in members.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)


def test_resolve_row_class_finds_a_unique_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    only = make_row("only.rows", "SoleTenantRow", {"email": str})
    install_module(monkeypatch, "only.rows", SoleTenantRow=only)
    node = astroid.extract_node("SoleTenantRow")
    assert _resolve_row_class(node) is only


def test_resolve_row_class_accepts_a_re_exported_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One class bound in several modules is not ambiguous."""
    shared = make_row("shared.rows", "ReExportedRow", {"email": str})
    install_module(monkeypatch, "shared.rows", ReExportedRow=shared)
    install_module(monkeypatch, "shared.effects", ReExportedRow=shared)
    node = astroid.extract_node("ReExportedRow")
    assert _resolve_row_class(node) is shared


def test_resolve_row_class_refuses_an_ambiguous_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two trusted layers naming a schema alike must not silently pick one."""
    install_module(
        monkeypatch,
        "app.rows",
        AmbiguousRow=make_row("app.rows", "AmbiguousRow", {"email": str}),
    )
    install_module(
        monkeypatch,
        "corp.rows",
        AmbiguousRow=make_row("corp.rows", "AmbiguousRow", {"email": str}),
    )
    node = astroid.extract_node("AmbiguousRow")
    assert _resolve_row_class(node) is None


def _prove(case_name: str) -> Conclusion:
    results = prove_path(
        FIXTURES / "cases" / case_name,
        trusted_roots=[FIXTURES / "dbt", FIXTURES / "ldapt"],
        import_roots=[FIXTURES],
    )
    assert len(results) == 1
    return results[0].proof.conclusion


def test_addresses_from_passes_within_one_root() -> None:
    """Control: the same guarantee still proves when the query really is db's."""
    assert _prove("same_root_pass.py") is Conclusion.OK


def test_addresses_from_fails_across_same_named_schemas() -> None:
    """Addresses from the LDAP ``UserRow`` must not prove a guarantee written
    against the application database's ``UserRow``."""
    assert _prove("cross_root_fail.py") is Conclusion.FAIL
