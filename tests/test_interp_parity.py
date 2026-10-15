"""The interpreter's subset, pinned against the prover's.

The claim this design rests on is that "what was proved" and "what runs" are
two readings of one grammar. That is only true if the two dispatch tables stay
aligned, so the alignment is asserted here rather than assumed: a node the
prover learns to reason about but the interpreter cannot run is a gap, and a
node the interpreter will run but the prover cannot reason about is a hole.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

import astroid
import pytest

from clauz3._vendor.deal_solver._eval_expr import eval_expr as prover_expr
from clauz3._vendor.deal_solver._eval_stmt import eval_stmt as prover_stmt
from clauz3.interp import eval_expr as interp_expr
from clauz3.interp import eval_stmt as interp_stmt

#: Statements the interpreter runs that the prover has no handler for, with the
#: reason each is sound to execute unproved.
STMT_INTERPRETER_ONLY = {
    # Declarative only: the interpreter reads a Row schema out of the class
    # body without executing it. The prover resolves Row schemas separately,
    # via ``_proxies._row.row_schema_from_class_def``.
    astroid.ClassDef,
    # ``LIMIT: int = 10`` at module level, and ``Row`` fields. Neither is
    # inside a proved body: module level is declaration-only by the soundness
    # gate, and a Row body is read structurally by ``_stmt_class_def`` without
    # dispatching here. ``_stmt_ann_assign`` refuses the in-body form, which
    # the prover cannot model.
    astroid.AnnAssign,
}

# ``AugAssign`` is deliberately absent from both. An earlier draft registered
# it here and justified it as "desugared to ``x = x + 1``, tracked prover-side
# as issue #109" -- but the prover registers no handler at all, so ``x += 1``
# was runnable and unprovable, exactly the ``sorted`` mistake in the statement
# table. Removed until the prover models it.


#: Statements the prover reasons about that the interpreter does not run.
STMT_PROVER_ONLY = {
    # ``global x`` is a no-op for the prover's single-scope model. The
    # interpreter has real scopes, so honouring it would mean implementing
    # rebinding semantics the proof does not model -- refusing is the
    # conservative side.
    astroid.Global,
}


def _names(node_types: Collection[type[Any]]) -> set[str]:
    return {node_type.__name__ for node_type in node_types}


def test_statement_subsets_agree() -> None:
    prover = set(prover_stmt._handlers)
    interp = interp_stmt.node_types

    assert _names(interp - prover) == _names(STMT_INTERPRETER_ONLY), (
        "the interpreter would execute a statement the prover cannot reason "
        "about; either add a prover handler or remove the interpreter one"
    )
    assert _names(prover - interp) == _names(STMT_PROVER_ONLY), (
        "the prover reasons about a statement the interpreter cannot run; "
        "proved programs would fail at runtime"
    )


def test_expression_subsets_are_identical() -> None:
    prover = set(prover_expr._handlers)
    interp = interp_expr.node_types

    assert _names(interp - prover) == set()
    assert _names(prover - interp) == set()


#: Builtins the interpreter provides that the prover has no model for.
#:
#: ``range`` only ever appears as a ``for`` iterable, which the prover handles
#: structurally in ``_eval_stmt._literal_range_items`` rather than as a call.
#: Every other entry must have a ``builtins.<name>`` model.
BUILTIN_INTERPRETER_ONLY = {"range"}


def test_builtin_tables_agree() -> None:
    """The node registries are not the only place the two can drift.

    ``builtins.py``'s docstring states the rule -- "a builtin the prover cannot
    model must not be callable at runtime" -- but prose does not enforce it. An
    early draft shipped ``sorted``, which the prover does not model at all, so a
    program could call it, run fine, and be unprovable: exactly the gap this
    design exists to close, one table over from where the parity test was
    looking.
    """
    from clauz3._vendor.deal_solver._funcs import FUNCTIONS
    from clauz3.interp.builtins import BUILTINS

    modelled = {
        key.removeprefix("builtins.")
        for key in FUNCTIONS
        if key.startswith("builtins.")
    }
    assert set(BUILTINS) - modelled == BUILTIN_INTERPRETER_ONLY, (
        "the interpreter provides a builtin the prover cannot reason about"
    )
    assert modelled - set(BUILTINS) == set(), (
        "the prover models a builtin the interpreter cannot run"
    )


def test_library_surface_matches_the_prover() -> None:
    """The library half of the join, which the builtin test does not cover.

    ``test_builtin_tables_agree`` filters ``FUNCTIONS`` to ``builtins.*``, so
    ``math.*``, ``re.*`` and ``os.path.*`` went unchecked. ``re.compile`` and
    ``re.match`` are modelled by the prover but can only ever return a live
    ``Pattern``/``Match``, so they used to run and then die inside
    ``marshal_in`` -- provable but not runnable, the mirror of the gap the AST
    parity test guards. They are now refused at the name, and listed.
    """
    from clauz3._vendor.deal_solver._funcs import FUNCTIONS
    from clauz3.imports_policy import DEFAULT_STDLIB_ALLOWED, build_policy
    from clauz3.interp.interpreter import (
        LIBRARY_RETURNS_OUTSIDE_DOMAIN,
        Program,
        exposed_names,
    )

    policy = build_policy()
    for module_name in sorted(DEFAULT_STDLIB_ALLOWED):
        module = pytest.importorskip(module_name)
        prefix = module_name + "."
        modelled = {
            key[len(prefix) :]
            for key in FUNCTIONS
            if key.startswith(prefix) and "." not in key[len(prefix) :]
        }
        excluded = {
            key[len(prefix) :]
            for key in LIBRARY_RETURNS_OUTSIDE_DOMAIN
            if key.startswith(prefix)
        }
        assert exposed_names(module, module_name, policy) == modelled - excluded, (
            f"{module_name}: exposed surface must be exactly the functions the "
            "prover models, minus those that cannot return a subset value"
        )
        # Check the actual runtime resolver too: its fixed-provider path must
        # expose the same names as the public surface helper.
        resolved = Program(source_name="parity.py", policy=policy).resolve_module(
            module_name
        )
        assert resolved.allowed == modelled - excluded
        assert resolved.withheld == excluded


def test_excluded_library_functions_are_genuinely_modelled() -> None:
    """The exclusion list must name real models, not typos."""
    from clauz3._vendor.deal_solver._funcs import FUNCTIONS
    from clauz3.interp.interpreter import LIBRARY_RETURNS_OUTSIDE_DOMAIN

    assert set(FUNCTIONS) >= LIBRARY_RETURNS_OUTSIDE_DOMAIN


def _prover_modelled_methods() -> dict[str, set[str]]:
    """Public method names each proxy sort models with a real implementation.

    Most names in a proxy's ``methods`` table route to an ``unsupported`` stub
    that raises, so registration alone does not mean provable. Only the ones
    with a real impl are.
    """
    from clauz3._vendor.deal_solver._proxies import types

    surface: dict[str, set[str]] = {}
    for key in ("str", "list", "tuple", "set", "dict", "int", "float", "bool"):
        try:
            sort = getattr(types, key)
        except KeyError:  # pragma: no cover - all eight are registered
            continue
        sort.methods._finalize()
        surface[key] = {
            name
            for name, method in sort.methods._registry.items()
            if not name.startswith("__")
            and name not in {"in", "not", "not in"}
            and getattr(method.impl, "__name__", "") != "unsupported"
        }
    return surface


def test_value_method_surface_matches_the_prover() -> None:
    """Attribute access on data is the third table these two can drift on.

    ``test_expression_subsets_are_identical`` compares node *types*, so
    ``Attribute`` being registered on both sides hid a real gap: the prover
    models ``str.startswith`` and friends, and ``'sandbox/x'.startswith('...')``
    proves *and* runs under ``clauz3 run`` -- while the interpreter refused all
    attribute access on data. That is "provable but not interpretable", the
    mirror of the ``re.compile`` gap, and under migration step 5 it would have
    silently stopped a class of already-proved programs running.
    """
    from clauz3.interp.methods import MODELLED_METHODS

    interp = {
        receiver.__name__: set(names) for receiver, names in MODELLED_METHODS.items()
    }
    prover = {key: names for key, names in _prover_modelled_methods().items() if names}
    assert interp == prover


def test_attribute_receiver_pairs_cover_the_interpreter_tables() -> None:
    from clauz3._vendor.deal_solver._eval_expr import ATTRIBUTE_RECEIVER_PAIRS
    from clauz3.interp.methods import MODELLED_METHODS

    assert {runtime for _, runtime in ATTRIBUTE_RECEIVER_PAIRS} == set(MODELLED_METHODS)
    for index, (proxy, _) in enumerate(ATTRIBUTE_RECEIVER_PAIRS):
        for later, _ in ATTRIBUTE_RECEIVER_PAIRS[index + 1 :]:
            assert not issubclass(later, proxy), (proxy, later)


def test_unregistered_nodes_fail_closed() -> None:
    """The default for an unknown node is refusal, on both sides."""
    from clauz3.interp.errors import UnsupportedNode

    module = astroid.parse("x = (i for i in [1])")
    generator = next(module.nodes_of_class(astroid.GeneratorExp))
    assert astroid.GeneratorExp not in interp_expr.node_types
    assert astroid.GeneratorExp not in set(prover_expr._handlers)

    try:
        interp_expr(generator, None)  # type: ignore[arg-type]
    except UnsupportedNode as exc:
        assert "GeneratorExp" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected UnsupportedNode")


def test_operator_tables_are_a_subset_of_the_prover() -> None:
    """Node-type parity says nothing about the operators *inside* a handler.

    ``BinOp``, ``Compare`` and ``UnaryOp`` are registered on both sides, so
    ``test_expression_subsets_are_identical`` passes no matter what the
    per-operator dispatch tables hold. The rule is one-directional: the
    interpreter may refuse an operator the prover models (fail-closed), but
    modelling one the prover does not would be a runnable-yet-unprovable
    operator -- the same shape as the ``sorted`` and ``AugAssign`` findings.
    """
    from clauz3._vendor.deal_solver._eval_expr import (
        BIN_OPERATIONS,
        BOOL_OPERATIONS,
        COMAPARISON,
    )
    from clauz3.interp.interpreter import _BINOPS, _BOOLOPS, _COMPARISONS, _UNARYOPS

    assert set(_BINOPS) <= set(BIN_OPERATIONS)
    assert set(_COMPARISONS) <= set(COMAPARISON)
    assert set(_BOOLOPS) <= set(BOOL_OPERATIONS)
    # ``eval_unary_op`` is a chain of ``if``s rather than a table; ``not`` is
    # handled ahead of ``_UNARYOPS`` in ``_expr_unaryop``.
    assert set(_UNARYOPS) | {"not"} <= {"+", "-", "~", "not"}

    # And the deliberate omissions, enumerated so a future addition to either
    # table is a decision rather than a drift.
    assert set(BIN_OPERATIONS) - set(_BINOPS) == {"@"}
    assert set(COMAPARISON) - set(_COMPARISONS) == set()


def test_identity_comparisons_are_in_neither_table() -> None:
    """``is`` / ``is not`` were runnable here and unprovable there.

    ``COMAPARISON`` has no entry for them, so ``eval_compare`` raises
    ``UnsupportedError``; an earlier version of ``_COMPARISONS`` mapped them
    to Python's ``is``, which would have run a program no proof covered.
    """
    from clauz3._vendor.deal_solver._eval_expr import COMAPARISON
    from clauz3.interp.interpreter import _COMPARISONS

    for op in ("is", "is not"):
        assert op not in COMAPARISON
        assert op not in _COMPARISONS
