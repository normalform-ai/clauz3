"""Behavioural tests for the subset interpreter prototype.

Companion to ``tests/test_interp_escapes.py`` (which covers containment) and
``tests/test_interp_parity.py`` (which pins the subset to the prover's).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from clauz3.imports_policy import ImportPolicy, build_policy
from clauz3.interp import (
    ExecutionLimitExceeded,
    InterpError,
    NameResolutionError,
    Outcome,
    ProgramError,
    TrustedBoundaryError,
    UnsupportedNode,
    execute,
    target_node,
)
from clauz3.prover import _trusted_layer_context

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
EMAIL_TRUSTED = EMAIL_ROOT / "tools/email/trusted"


def _policy(**kwargs: Any) -> ImportPolicy:
    return build_policy(
        trusted_roots=[EMAIL_TRUSTED],
        import_roots=[EMAIL_ROOT],
        **kwargs,
    )


def run(
    source: str,
    *,
    target: str = "main",
    dispatch: Any = None,
    step_budget: int | None = None,
    **kwargs: Any,
) -> Outcome:
    extra = {} if step_budget is None else {"step_budget": step_budget}
    return execute(
        source,
        source_name="plan.py",
        policy=_policy(**kwargs),
        target=target,
        import_roots=[EMAIL_ROOT],
        trusted_dispatch=dispatch,
        **extra,
    )


# --------------------------------------------------------------------------
# Core evaluation
# --------------------------------------------------------------------------


def test_arithmetic_and_return() -> None:
    outcome = run("def main():\n    x = 2 + 3 * 4\n    return x\n")
    assert outcome.value == 14


def test_helper_functions_and_locals() -> None:
    outcome = run(
        "def double(n):\n    return n * 2\n\ndef main():\n    return double(21)\n"
    )
    assert outcome.value == 42


def test_module_level_defaults_must_be_constants() -> None:
    """A default on a module-level def evaluates outside the proved body.

    An earlier version of this prototype evaluated module-level defaults with
    the full expression evaluator and this test asserted that was correct,
    reasoning that "the interpreter evaluates it, so it is just more interpreted
    code". That confuses containment with soundness: it does not matter *who*
    evaluates an effect outside the proved body, only that it is outside.
    ``runner._check_defaults`` rejects the same thing for the same reason.
    """
    with pytest.raises(UnsupportedNode, match="must be a constant"):
        run("def size(n=3 * 7):\n    return n\n\ndef main():\n    return size()\n")


def test_nested_defaults_must_also_be_constants() -> None:
    """A nested ``def``'s defaults are as unproved as a module-level one's.

    An earlier version of this test asserted the opposite, on the reasoning
    that a nested ``def`` is reached only on a proved path. The path is
    proved; the default is not. ``deal_solver``'s ``eval_func_def`` binds a
    ``FuncSort`` and never walks ``node.args``, so::

        @clauz3.guarantee(emails.only(["bob@example.com"]))
        def main() -> None:
            def helper(x=send_email("eve@example.com", "exfil")):
                return x

    proved, and then sent the mail. The gate is scope-independent because the
    prover's blind spot is.
    """
    with pytest.raises(UnsupportedNode, match="must be a constant"):
        run(
            "def main():\n    def size(n=3 * 7):\n        return n\n    return size()\n"
        )


def test_nested_defaults_may_be_constants() -> None:
    outcome = run(
        "def main():\n    def size(n=21):\n        return n\n    return size()\n"
    )
    assert outcome.value == 21


def test_keyword_only_defaults_are_gated_too() -> None:
    """``arguments.kw_defaults`` is a separate list.

    Gating ``arguments.defaults`` alone left it uncovered;
    ``runner._check_defaults`` had walked both.
    """
    with pytest.raises(UnsupportedNode, match="keyword-only defaults"):
        run(
            "def main():\n"
            "    def size(*, n=3 * 7):\n"
            "        return n\n"
            "    return size(n=1)\n"
        )


def test_branches_loops_and_comprehensions() -> None:
    outcome = run(
        "def main():\n"
        "    total = 0\n"
        "    for n in [1, 2, 3, 4]:\n"
        "        if n % 2 == 0:\n"
        "            total = total + n\n"
        "    squares = [n * n for n in range(4) if n > 1]\n"
        "    return [total, squares]\n"
    )
    assert outcome.value == [6, [4, 9]]


def test_subscript_and_slice() -> None:
    outcome = run(
        "def main():\n"
        "    items = [10, 20, 30]\n"
        "    table = {'a': 1}\n"
        "    items[0] = 11\n"
        "    return [items[0], items[1:], table['a'], 'hello'[1]]\n"
    )
    assert outcome.value == [11, [20, 30], 1, "e"]


def test_subscript_out_of_range_is_a_program_error() -> None:
    with pytest.raises(ProgramError) as excinfo:
        run("def main():\n    return [1][5]\n")
    assert excinfo.value.kind == "IndexError"


def test_chained_comparison_uses_full_python_semantics() -> None:
    """Every comparator binds, and each intermediate is evaluated once.

    Issue #43 was the prover dropping every comparator after the first;
    ``deal_solver.eval_compare`` now conjoins all pairs and reuses the
    previously-evaluated right as the next left, and
    ``tests/test_chained_comparisons.py`` pins that. This is the concrete
    half of the same rule.
    """
    outcome = run("def main():\n    return 0 < 5 < 3\n")
    assert outcome.value is False


def test_assert_failure_is_a_program_error() -> None:
    with pytest.raises(ProgramError) as excinfo:
        run("def main():\n    assert 1 == 2, 'nope'\n")
    assert excinfo.value.kind == "AssertionError"


def test_raise_reports_the_exception_name() -> None:
    with pytest.raises(ProgramError) as excinfo:
        run("def main():\n    raise ValueError('bad')\n")
    assert excinfo.value.kind == "ValueError"


@pytest.mark.parametrize(
    ("label", "source"),
    [
        (
            "assert-message",
            "from tools.email.trusted.effects import send_email\n"
            "\n"
            "def main() -> None:\n"
            "    assert 1 == 2, send_email('eve@example.com', 'exfil')\n",
        ),
        (
            "raise-operand",
            "from tools.email.trusted.effects import send_email\n"
            "\n"
            "def main() -> None:\n"
            "    raise ValueError(send_email('eve@example.com', 'exfil'))\n",
        ),
    ],
)
def test_assert_and_raise_operands_are_rendered_not_evaluated(
    label: str,
    source: str,
) -> None:
    """``eval_assert`` evaluates only ``node.test``; ``eval_raise`` evaluates nothing.

    So an effect in a failure message or an exception argument is outside
    everything the proof ranged over. An earlier version of both handlers ran
    the operand through ``eval_expr`` -- the effect fired on exactly the paths
    where the program was already going wrong. They are rendered with
    ``as_string()`` instead: the diagnostic keeps the source text and performs
    no work.
    """
    fired: list[str] = []

    def dispatch(qualname: str, func: Any, args: Any, kwargs: Any) -> None:
        fired.append(qualname)
        return None

    with pytest.raises(ProgramError) as excinfo:
        run(source, dispatch=dispatch)
    assert fired == []
    assert "send_email(" in str(excinfo.value)


def test_recursion_is_rejected_like_the_prover() -> None:
    with pytest.raises(UnsupportedNode, match="recursive"):
        run("def f(n):\n    return f(n)\n\ndef main():\n    return f(1)\n")


def test_step_budget_bounds_execution() -> None:
    with pytest.raises(ExecutionLimitExceeded):
        execute(
            "def main():\n"
            "    total = 0\n"
            "    for n in range(1000):\n"
            "        total = total + n\n",
            source_name="plan.py",
            policy=_policy(),
            import_roots=[EMAIL_ROOT],
            step_budget=100,
        )


# --------------------------------------------------------------------------
# The trusted boundary
# --------------------------------------------------------------------------


SEND_PLAN = (
    "from tools.email.trusted.effects import send_email\n"
    "\n"
    "import clauz3\n"
    "\n"
    "\n"
    "def main() -> None:\n"
    "    send_email('bob@example.com', 'hello')\n"
)


def test_trusted_call_reaches_cpython_and_is_recorded() -> None:
    seen: list[tuple[str, tuple[Any, ...]]] = []

    def dispatch(qualname: str, func: Any, args: Any, kwargs: Any) -> None:
        seen.append((qualname, tuple(args)))
        return None

    outcome = run(SEND_PLAN, dispatch=dispatch)
    assert seen == [
        ("tools.email.trusted.effects.send_email", ("bob@example.com", "hello"))
    ]
    assert outcome.effects == (
        ("tools.email.trusted.effects.send_email", ("bob@example.com", "hello"), {}),
    )


def test_trusted_return_outside_the_value_domain_is_refused() -> None:
    """A trusted layer cannot donate a live Python object to the program."""

    def dispatch(qualname: str, func: Any, args: Any, kwargs: Any) -> object:
        return object()

    with pytest.raises(TrustedBoundaryError, match="outside the subset value domain"):
        run(SEND_PLAN, dispatch=dispatch)


def test_library_modules_expose_only_what_the_prover_models() -> None:
    """``math.sin`` is modelled; ``math.sqrt`` is not, so it cannot run."""
    assert run("import math\n\ndef main():\n    return math.sin(0)\n").value == 0.0
    with pytest.raises(TrustedBoundaryError, match="not exposed"):
        run("import math\n\ndef main():\n    return math.sqrt(4)\n")


def test_clauz3_surface_is_two_names() -> None:
    with pytest.raises(TrustedBoundaryError, match="not exposed"):
        run("import clauz3\n\ndef main():\n    return clauz3.spec\n")


def test_disallowed_import_is_refused() -> None:
    with pytest.raises(Exception, match="allowlist"):
        run("import os\n\ndef main():\n    return 1\n")


# --------------------------------------------------------------------------
# Module level and target selection
# --------------------------------------------------------------------------


def test_module_level_effect_cannot_run() -> None:
    with pytest.raises(UnsupportedNode, match="outside the proved function"):
        run(
            "from tools.email.trusted.effects import send_email\n"
            "\n"
            "send_email('eve@example.com', 'exfil')\n"
            "\n"
            "def main() -> None:\n"
            "    pass\n"
        )


def test_non_guarantee_decorator_is_refused_and_never_evaluated() -> None:
    with pytest.raises(UnsupportedNode, match="decorator"):
        run(
            "from tools.email.trusted.effects import send_email\n"
            "\n"
            "@send_email\n"
            "def main() -> None:\n"
            "    pass\n"
        )


def _target_node(source: str, target: str = "main") -> Any:
    with _trusted_layer_context([EMAIL_TRUSTED], [EMAIL_ROOT]):
        return target_node(
            source,
            target=target,
            source_name="plan.py",
            policy=_policy(),
            import_roots=[EMAIL_ROOT],
        )


def test_duplicate_target_resolves_to_the_last_binding() -> None:
    """Issue #97: the node the interpreter will run is knowable up front."""
    source = "def main() -> None:\n    return 1\n\ndef main() -> None:\n    return 2\n"
    assert run(source).value == 2
    assert _target_node(source).lineno == 4


def test_rebinding_the_target_by_assignment_is_refused() -> None:
    """``main = sneaky`` at module level cannot even be expressed.

    The constant-expression gate on module-level assignment closes this by
    construction: ``sneaky`` is a Name, not a literal.
    """
    source = (
        "def sneaky() -> None:\n"
        "    return 99\n"
        "\n"
        "def main() -> None:\n"
        "    return 1\n"
        "\n"
        "main = sneaky\n"
    )
    with pytest.raises(UnsupportedNode, match="must be a constant"):
        run(source)


@pytest.mark.parametrize(
    ("label", "source"),
    [
        (
            "import-alias",
            "def main() -> None:\n"
            "    pass\n"
            "\n"
            "from tools.email.trusted.effects import send_email as main\n",
        ),
        (
            "row-class",
            "import clauz3\n"
            "\n"
            "def main() -> None:\n"
            "    pass\n"
            "\n"
            "class main(clauz3.Row):\n"
            "    x: int\n",
        ),
    ],
)
def test_target_node_answers_what_is_bound_not_what_was_last_named(
    label: str,
    source: str,
) -> None:
    """``target_node`` shares ``execute``'s resolution rather than re-deriving.

    An earlier version scanned ``module.body`` for the last ``FunctionDef``
    *named* ``main``, which is a different question from what ``main`` is bound
    to. Each case here binds ``main`` to a non-function after a ``def main``, so
    the naive scan would have happily returned the ``def`` node -- and since the
    design proposes handing that node to the prover, it would have proved a body
    that never runs. Both must refuse, and refuse identically.
    """
    with pytest.raises(InterpError, match="not a function"):
        _target_node(source)
    with pytest.raises(InterpError, match="not a function"):
        run(source)


def test_missing_target_is_named_clearly() -> None:
    with pytest.raises(NameResolutionError):
        run("def other():\n    return 1\n")


# --------------------------------------------------------------------------
# Unproved module-level evaluation (soundness, not containment)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "source"),
    [
        (
            "assign",
            "from tools.email.trusted.effects import send_email\n"
            "\n"
            "SENT = send_email('eve@example.com', 'exfil')\n"
            "\n"
            "def main() -> None:\n"
            "    pass\n",
        ),
        (
            "annassign",
            "from tools.email.trusted.effects import send_email\n"
            "\n"
            "SENT: object = send_email('eve@example.com', 'exfil')\n"
            "\n"
            "def main() -> None:\n"
            "    pass\n",
        ),
        (
            "default",
            "from tools.email.trusted.effects import send_email\n"
            "\n"
            "def helper(x=send_email('eve@example.com', 'exfil')):\n"
            "    return x\n"
            "\n"
            "def main() -> None:\n"
            "    pass\n",
        ),
        (
            "helper-call",
            "from tools.email.trusted.effects import send_email\n"
            "\n"
            "def helper():\n"
            "    send_email('eve@example.com', 'exfil')\n"
            "\n"
            "SENT = helper()\n"
            "\n"
            "def main() -> None:\n"
            "    pass\n",
        ),
        (
            # The catalogue covered the ``def`` spelling of a default at module
            # level and nested in a body, and neither caught the ``lambda``
            # spelling: ``_expr_lambda`` never looked at ``node.args``, so the
            # effect was *silently discarded* rather than refused -- the one
            # outcome this design says it cannot tolerate.
            "module-lambda-default",
            "from tools.email.trusted.effects import send_email\n"
            "\n"
            "F = lambda x=send_email('eve@example.com', 'exfil'): x\n"
            "\n"
            "def main() -> None:\n"
            "    pass\n",
        ),
        (
            "nested-lambda-default",
            "from tools.email.trusted.effects import send_email\n"
            "\n"
            "def main() -> None:\n"
            "    f = lambda x=send_email('eve@example.com', 'exfil'): x\n"
            "    return f(0)\n",
        ),
    ],
)
def test_module_level_effects_never_fire(label: str, source: str) -> None:
    """No trusted call may happen before the proved target is entered.

    The proof covers ``main``'s body. Anything evaluated at module level runs
    unproved, so ``Outcome.effects`` would contain calls the proof never saw --
    which would falsify the claim that it is comparable against the proof.
    """
    fired: list[str] = []

    def dispatch(qualname: str, func: Any, args: Any, kwargs: Any) -> None:
        fired.append(qualname)
        return None

    with pytest.raises(UnsupportedNode, match="must be a constant"):
        run(source, dispatch=dispatch)
    assert fired == []


@pytest.mark.parametrize(
    ("label", "source"),
    [
        (
            "subscript-target",
            "from tools.email.trusted.effects import send_email\n"
            "\n"
            "CFG = {}\n"
            "CFG[send_email('eve@example.com', 'exfil')] = 1\n"
            "\n"
            "def main() -> None:\n"
            "    pass\n",
        ),
        (
            "subscript-target-annassign",
            "from tools.email.trusted.effects import send_email\n"
            "\n"
            "CFG = {}\n"
            "CFG[send_email('eve@example.com', 'exfil')]: int = 1\n"
            "\n"
            "def main() -> None:\n"
            "    pass\n",
        ),
        (
            "subscript-inside-tuple-target",
            "from tools.email.trusted.effects import send_email\n"
            "\n"
            "CFG = {}\n"
            "A, CFG[send_email('eve@example.com', 'exfil')] = 1, 2\n"
            "\n"
            "def main() -> None:\n"
            "    pass\n",
        ),
    ],
)
def test_module_level_assignment_targets_never_fire_effects(
    label: str,
    source: str,
) -> None:
    """Gating the assignment *value* is not enough -- the target evaluates too.

    ``CFG[send_email(...)] = 1`` has a constant value and a subscript target,
    and ``_assign_to`` evaluates both ``target.value`` and ``target.slice``. The
    first version of this gate checked only the value, so the trusted call fired
    at module level while ``test_module_level_effects_never_fire`` stayed green.
    """
    fired: list[str] = []

    def dispatch(qualname: str, func: Any, args: Any, kwargs: Any) -> None:
        fired.append(qualname)
        return None

    with pytest.raises(UnsupportedNode, match="must be a name"):
        run(source, dispatch=dispatch)
    assert fired == []


def test_target_node_does_not_fire_module_level_effects() -> None:
    """``target_node`` runs module level too, so it inherits the same gate."""
    source = (
        "from tools.email.trusted.effects import send_email\n"
        "\n"
        "CFG = {}\n"
        "CFG[send_email('eve@example.com', 'exfil')] = 1\n"
        "\n"
        "def main() -> None:\n"
        "    pass\n"
    )
    with pytest.raises(UnsupportedNode, match="must be a name"):
        _target_node(source)


def test_lambda_recursion_is_rejected_like_function_recursion() -> None:
    """Recursion is tracked by node identity, not by name.

    Every lambda is called ``<lambda>``, so a name-keyed trace let
    ``f = lambda n: f(n - 1)`` recurse freely -- a program that runs cleanly here
    and cannot be proved at all. The unbounded form additionally escaped as a
    bare ``RecursionError``, outside the documented error contract.
    """
    with pytest.raises(UnsupportedNode, match="recursive"):
        run(
            "def main():\n"
            "    f = lambda n: 1 if n == 0 else f(n - 1)\n"
            "    return f(3)\n"
        )
    with pytest.raises(UnsupportedNode, match="recursive"):
        run("def main():\n    f = lambda: f()\n    return f()\n")


def test_distinct_lambdas_are_not_falsely_rejected() -> None:
    """Identity-keyed, so two different lambdas may call each other."""
    outcome = run(
        "def main():\n"
        "    double = lambda n: n * 2\n"
        "    apply = lambda n: double(n) + 1\n"
        "    return apply(4)\n"
    )
    assert outcome.value == 9


def test_dotted_unaliased_import_is_refused_by_name() -> None:
    """``import a.b.c`` needs package traversal the subset does not have.

    It used to re-resolve the head package and fail with ``import of 'tools' is
    not in the allowlist`` -- blaming a module the author never wrote, for a
    program ``imports_policy.validate_imports`` accepts.
    """
    with pytest.raises(UnsupportedNode, match="not in the proved subset"):
        run("import tools.email.trusted.effects\n\ndef main() -> None:\n    pass\n")
    outcome = run(
        "import tools.email.trusted.effects as effects\n"
        "\n"
        "def main() -> None:\n"
        "    effects.send_email('bob@example.com', 'hi')\n",
        dispatch=lambda q, f, a, k: None,
    )
    assert [effect[0] for effect in outcome.effects] == [
        "tools.email.trusted.effects.send_email",
    ]


@pytest.mark.parametrize(
    ("label", "source", "expected"),
    [
        (
            "row-unknown-field",
            "import clauz3\n\nclass R(clauz3.Row):\n    x: int\n\n"
            "def main():\n    return R(x=1, y=2)\n",
            ProgramError,
        ),
        (
            "row-missing-field",
            "import clauz3\n\nclass R(clauz3.Row):\n    x: int\n\n"
            "def main():\n    return R()\n",
            ProgramError,
        ),
        ("bad-slice", "def main():\n    return 'abc'['a':]\n", ProgramError),
        ("huge-range", "def main():\n    return range(10 ** 30)\n", UnsupportedNode),
        ("unary-minus-str", "def main():\n    return -'a'\n", ProgramError),
        ("invert-float", "def main():\n    return ~1.5\n", ProgramError),
        ("unary-minus-list", "def main():\n    return -[1]\n", ProgramError),
        ("overflow", "def main():\n    return 10 ** 400 * 1.0\n", ProgramError),
        ("int-of-inf", "def main():\n    return int(1e999)\n", ProgramError),
        ("complex", "def main():\n    return (-8) ** 0.5\n", UnsupportedNode),
        ("unhashable-set", "def main():\n    return {[1]}\n", ProgramError),
        ("unhashable-dict-key", "def main():\n    return {[1]: 2}\n", ProgramError),
        # Modelled *library* calls reach CPython through the ``TrustedCallable``
        # branch, which invoked ``value.func`` bare -- so these two escaped
        # ``execute`` as native ``OverflowError``/``ValueError``, past both
        # ``except`` clauses in ``runner._execute_source``, to the CLI
        # catch-all. The parametrisation covered builtins, operators and
        # containers and stopped one branch short of the boundary.
        (
            "library-overflow",
            "import math\ndef main():\n    return math.trunc(1e999)\n",
            ProgramError,
        ),
        (
            "library-bad-argument",
            "import math\ndef main():\n    return math.isclose(1, 2, rel_tol=-1)\n",
            ProgramError,
        ),
    ],
)
def test_error_contract_is_total(
    label: str,
    source: str,
    expected: type[Exception],
) -> None:
    """``execute`` promises InterpError/ProgramError, so nothing native escapes.

    ``range(10 ** 30)`` is the subtle one: ``len()`` on a range wider than a C
    ssize_t raises OverflowError *before* the MAX_RANGE check could reject it,
    so the bound is now computed from start/stop/step arithmetically.
    """
    with pytest.raises(expected):
        run(source)


def test_row_fields_are_marshalled_outbound() -> None:
    """``Row.__init__`` checks field *names*, not field values.

    Without recursing, ``R(x=lambda n: n)`` hands a trusted layer a
    ``LambdaFunction`` holding a live interpreter ``Scope`` -- the boundary is
    advertised as total in both directions, so it has to be.
    """
    with pytest.raises(TrustedBoundaryError, match="cannot pass LambdaFunction"):
        run(
            "import clauz3\n"
            "from tools.email.trusted.effects import send_email\n"
            "\n"
            "class R(clauz3.Row):\n"
            "    x: int\n"
            "\n"
            "def main():\n"
            "    send_email(R(x=lambda n: n), 'hi')\n",
            dispatch=lambda q, f, a, k: None,
        )


def test_only_trusted_calls_are_recorded_as_effects() -> None:
    """``math.sin`` reaches CPython through the same branch but is not an effect.

    ``Outcome.effects`` is offered as directly comparable against what the proof
    reasoned about; a modelled library call is not part of that comparison.
    """
    outcome = run("import math\n\ndef main():\n    return math.sin(0)\n")
    assert outcome.value == 0.0
    assert outcome.effects == ()


# --------------------------------------------------------------------------
# Local binding
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "source"),
    [
        (
            "read-before-assign",
            "def main():\n"
            "    x = 1\n"
            "    def inner():\n"
            "        y = x\n"
            "        x = 2\n"
            "        return y\n"
            "    return inner()\n",
        ),
        (
            "augmented-shadow",
            "def main():\n"
            "    x = 1\n"
            "    def inner():\n"
            "        x = x + 1\n"
            "        return x\n"
            "    return inner()\n",
        ),
    ],
)
def test_read_before_assignment_raises_unbound_local(
    label: str,
    source: str,
) -> None:
    """Python decides local-vs-enclosing statically, per function body.

    Without that, the interpreter resolved a read preceding a same-body
    assignment to the *enclosing* binding: the first case returned 1 where
    CPython raises, and the second silently shadowed instead of failing. Those
    are wrong answers rather than refusals -- the one failure mode a component
    claiming "proof and execution are two readings of one tree" cannot have.
    """
    with pytest.raises(ProgramError) as excinfo:
        run(source)
    assert excinfo.value.kind == "UnboundLocalError"


@pytest.mark.parametrize(
    ("label", "source"),
    [
        (
            "nested-def",
            "LIMIT = 10\n"
            "\n"
            "def main():\n"
            "    def inner():\n"
            "        return LIMIT\n"
            "    first = inner()\n"
            "    LIMIT = 5\n"
            "    return first\n",
        ),
        (
            "comprehension",
            "LIMIT = 10\n"
            "\n"
            "def main():\n"
            "    values = [LIMIT for i in [1]]\n"
            "    LIMIT = 5\n"
            "    return values\n",
        ),
    ],
)
def test_enclosing_unbound_locals_are_not_read_from_globals(
    label: str,
    source: str,
) -> None:
    """The rule has to cover nested bodies, not just the assigning one.

    A nested def or comprehension resolves through the closure chain, which
    walks past the enclosing frame's still-empty scope layer and lands on the
    module global -- returning 10 where CPython raises. The first version of
    this fix checked only the current body, so it was one syntactic position
    short of the rule it claimed to pin: the same "value but not target" shape
    recorded in the design note.
    """
    with pytest.raises(ProgramError) as excinfo:
        run(source)
    assert excinfo.value.kind == "UnboundLocalError"


def test_closures_and_comprehensions_still_read_enclosing_names() -> None:
    """The fix must not break reading a name the body never assigns."""
    assert (
        run(
            "def main():\n"
            "    x = 1\n"
            "    def inner():\n"
            "        return x + 1\n"
            "    return inner()\n"
        ).value
        == 2
    )
    assert run("def main():\n    x = 1\n    return [x for i in [1, 2]]\n").value == [
        1,
        1,
    ]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("x = 7\n    x += 3", 10),
        ("x = 7\n    x -= 2", 5),
        ("x = 7\n    x *= 2", 14),
        ("x = 7\n    x /= 2", 3.5),
        ("x = 7\n    x //= 2", 3),
        ("x = 7\n    x %= 4", 3),
        ("x = 7\n    x **= 2", 49),
        ("x = 7\n    x &= 3", 3),
        ("x = 7\n    x |= 8", 15),
        ("x = 7\n    x ^= 1", 6),
        ("x = 7\n    x <<= 1", 14),
        ("x = 7\n    x >>= 1", 3),
        ("x = 2.5\n    x *= 2", 5.0),
        ("x = 'hi'\n    x += '!'", "hi!"),
        ("x = True\n    x += 1", 2),
    ],
)
def test_augmented_assignment_rebinds_immutable_values(
    body: str, expected: Any
) -> None:
    """``x op= y`` is ``x = x op y`` for the four sorts the prover desugars (#109).

    All twelve augmented operators are covered, matching the prover-side table
    in ``tests/test_aug_assign.py``; ``/=`` is the one that changes the sort.
    """
    value = run(f"def main():\n    {body}\n    return x\n").value
    assert value == expected
    assert type(value) is type(expected)


@pytest.mark.parametrize(
    "body",
    [
        "xs = [1]\n    xs += [2]",
        "s = {1}\n    s |= {2}",
        "d = {1: 2}\n    d |= {3: 4}",
        "t = (1,)\n    t += (2,)",
        "xs = [1]\n    xs[0] += 1",
    ],
)
def test_augmented_assignment_outside_the_immutable_sorts_is_refused(body: str) -> None:
    """Containers and subscripts are refused by ``eval_aug_assign``; so here."""
    with pytest.raises(UnsupportedNode, match="augmented assignment"):
        run(f"def main():\n    {body}\n    return 1\n")


def test_augmented_assignment_to_an_unbound_name_is_unbound_local() -> None:
    """``LIMIT += 1`` makes ``LIMIT`` local, so CPython never reads the global."""
    with pytest.raises(ProgramError, match="LIMIT") as excinfo:
        run("LIMIT = 10\n\ndef main():\n    LIMIT += 1\n    return LIMIT\n")
    assert excinfo.value.kind == "UnboundLocalError"


def test_refused_augmented_assignment_evaluates_no_operand() -> None:
    """The target is checked before the value is evaluated, as in the prover.

    Otherwise ``xs += [send_email(...)]`` would fire the effect and then be
    refused -- an effect the proof never saw, on a program that never ran.
    """
    fired: list[str] = []

    def dispatch(qualname: str, func: Any, args: Any, kwargs: Any) -> None:
        fired.append(qualname)

    source = (
        "from tools.email.trusted.effects import send_email\n"
        "\n"
        "def main() -> None:\n"
        "    xs = []\n"
        "    xs += [send_email('bob@example.com', 'hello')]\n"
    )
    with pytest.raises(UnsupportedNode, match="augmented assignment"):
        run(source, dispatch=dispatch)
    assert fired == []


def test_annotated_assignment_inside_a_body_is_refused() -> None:
    """Module-level declarations only; the prover cannot model the in-body form."""
    with pytest.raises(UnsupportedNode, match="annotated assignment"):
        run("def main():\n    x: int = 1\n    return x\n")


# --------------------------------------------------------------------------
# Boundary edge cases
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("str-startswith", "return 'sandbox/x'.startswith('sandbox')"),
        ("str-find", "return 'abc'.find('b') == 1"),
        ("list-append", "xs = [1]\n    xs.append(2)\n    return xs == [1, 2]"),
        ("float-is-integer", "return (2.0).is_integer()"),
        ("int-numerator", "return (5).numerator == 5"),
    ],
)
def test_prover_modelled_methods_are_interpretable(label: str, body: str) -> None:
    """Refusing these made the interpreter narrower than the proved subset."""
    assert run(f"def main():\n    {body}\n").value is True


@pytest.mark.parametrize("body", ["'a'.upper()", "{'a': 1}.items()", "[1].sort()"])
def test_methods_the_prover_cannot_model_stay_refused(body: str) -> None:
    """The prover routes these to its ``unsupported`` stub, so they stay out."""
    with pytest.raises(UnsupportedNode, match="attribute access"):
        run(f"def main():\n    return {body}\n")


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("tuple-unpack", "def main():\n    a, b = 1, 2\n    return a + b\n"),
        (
            "for-tuple-target",
            "def main():\n"
            "    t = 0\n"
            "    for a, b in [(1, 2)]:\n"
            "        t = t + a + b\n"
            "    return t\n",
        ),
        (
            "return-in-for",
            "def main():\n    for n in [1, 2]:\n        return n\n    return 0\n",
        ),
        (
            "class-in-body",
            "import clauz3\n"
            "\n"
            "def main():\n"
            "    class R(clauz3.Row):\n"
            "        x: int\n"
            "    return R(x=1).x\n",
        ),
    ],
)
def test_constructs_the_prover_refuses_inside_its_handlers(
    label: str,
    source: str,
) -> None:
    """Node-type parity cannot see restrictions *inside* a handler.

    ``Assign`` and ``For`` are registered on both sides, so
    ``test_statement_subsets_agree`` passes -- but ``eval_assign`` accepts only
    names and subscripts, and ``eval_for`` requires an ``AssignName`` target and
    walks the body rejecting ``Return``. ``ClassDef`` has no prover handler at
    all. All four ran here and skipped there: the same "the prover happened to
    choke is not the same as the executor cannot run it" gap that removed
    ``sorted`` (and, until #109, ``AugAssign``), one level deeper.
    """
    with pytest.raises(UnsupportedNode):
        run(source)


def test_module_level_tuple_destructuring_is_still_allowed() -> None:
    """Module level is never evaluated by the prover, so it keeps the form."""
    outcome = run("A, B = 1, 2\n\ndef main():\n    return [A, B]\n")
    assert outcome.value == [1, 2]


def test_future_annotations_import_is_accepted() -> None:
    """``runner._sanitize_for_exec`` injects exactly this line into every run."""
    assert (
        run("from __future__ import annotations\n\ndef main():\n    return 1\n").value
        == 1
    )


def test_scalar_subclasses_are_normalised_at_the_boundary() -> None:
    """A ``str`` subclass is a live object with overridable behaviour.

    ``methods.lookup`` resolves against ``type(value)`` and ``a + b`` dispatches
    on it, so admitting one would falsify "there are no user-defined classes in
    the domain, so an operator cannot dispatch into attacker-written code".
    A ``StrEnum`` member is the realistic shape. The value the program sees must
    be exactly ``str``.
    """
    import enum

    class Colour(enum.StrEnum):
        RED = "red"

    def dispatch(qualname: str, func: Any, args: Any, kwargs: Any) -> Any:
        return Colour.RED

    assert type(Colour.RED) is not str, "the layer really returns a subclass"
    outcome = run(
        SEND_PLAN.replace(
            "    send_email('bob@example.com', 'hello')",
            "    got = send_email('bob@example.com', 'hello')\n"
            "    return [got.startswith('r'), got == 'red']",
        ),
        dispatch=dispatch,
    )
    assert outcome.value == [True, True]


@pytest.mark.parametrize("jump", ["break", "continue"])
def test_loop_jumps_are_refused_statically(jump: str) -> None:
    """``eval_for`` refuses these before evaluating anything, so this does too.

    Refusing only when the statement is reached would let an unexecuted branch
    decide whether a program runs at all -- a dynamic answer to a question the
    prover settles statically.
    """
    with pytest.raises(UnsupportedNode, match=jump):
        run(
            "def main():\n"
            "    for n in [1]:\n"
            "        if False:\n"
            f"            {jump}\n"
            "    return 1\n"
        )


def test_frozenset_survives_the_boundary_as_a_frozenset() -> None:
    """Collapsing it to ``set`` silently changed a value's declared type."""
    outcome = run(
        SEND_PLAN.replace(
            "    send_email('bob@example.com', 'hello')",
            "    got = send_email('bob@example.com', 'hello')\n    return got == got",
        ),
        dispatch=lambda q, f, a, k: frozenset({1, 2}),
    )
    assert outcome.value is True


def test_print_keyword_arguments_are_refused_not_dropped() -> None:
    """Silently ignoring ``sep=''`` would diverge from ``clauz3 run``."""
    with pytest.raises(UnsupportedNode, match="keyword arguments"):
        run("def main():\n    print('a', 'b', sep='')\n")


def test_row_mediated_cycles_are_refused() -> None:
    """``_marshal_row`` threads the seen-set; every Row hop used to reset it."""
    with pytest.raises(TrustedBoundaryError, match="cyclic"):
        run(
            "import clauz3\n"
            "from tools.email.trusted.effects import send_email\n"
            "\n"
            "class R(clauz3.Row):\n"
            "    x: int\n"
            "\n"
            "def main():\n"
            "    lst = [0]\n"
            "    r = R(x=lst)\n"
            "    lst[0] = r\n"
            "    send_email(r, 'hi')\n",
            dispatch=lambda q, f, a, k: None,
        )


def test_containers_inside_rows_cross_the_boundary_copied() -> None:
    """The copy invariant has to hold through a Row, not just beside one."""
    outcome = run(
        "import clauz3\n"
        "from tools.email.trusted.effects import send_email\n"
        "\n"
        "class R(clauz3.Row):\n"
        "    x: int\n"
        "\n"
        "def main():\n"
        "    items = [1, 2]\n"
        "    send_email(R(x=items), 'hi')\n"
        "    return items\n",
        dispatch=lambda q, f, a, k: a[0].x.append(99),
    )
    assert outcome.value == [1, 2]


def test_cyclic_trusted_return_is_refused_not_a_recursion_error() -> None:
    def dispatch(qualname: str, func: Any, args: Any, kwargs: Any) -> Any:
        cycle: list[Any] = []
        cycle.append(cycle)
        return cycle

    with pytest.raises(TrustedBoundaryError, match="cyclic"):
        run(SEND_PLAN, dispatch=dispatch)


def test_module_level_constants_are_still_allowed() -> None:
    outcome = run(
        "LIMIT = 10\n"
        "NAMES = ['a', 'b']\n"
        "OFFSET = -3\n"
        "\n"
        "def main():\n"
        "    return [LIMIT, NAMES, OFFSET]\n"
    )
    assert outcome.value == [10, ["a", "b"], -3]


def test_lambda_literal_defaults_bind_like_a_def() -> None:
    """The other half of the missing-``defaults`` bug: a literal default failed.

    ``call()`` bound lambdas with a hard-coded empty ``defaults``, so
    ``f = lambda x=1: x; f()`` raised "missing argument 'x'" for a default
    ``_eval_defaults`` would have admitted.
    """
    outcome = run(
        "def main():\n    f = lambda x=1, y=2: x + y\n    return [f(), f(10)]\n"
    )
    assert outcome.value == [3, 12]


def test_re_refusal_names_the_real_reason() -> None:
    """``re`` is modelled by the prover; the empty surface is a marshalling limit.

    The diagnostic used to say "the prover models none of its functions", which
    is false for ``re`` -- ``compile``/``match``/``fullmatch`` are all in
    ``FUNCTIONS`` -- and pointed the reader at a fix that cannot work. The
    honest statement is that the return type is outside the value domain.
    """
    with pytest.raises(TrustedBoundaryError) as excinfo:
        run("import re\ndef main():\n    return bool(re.match('a', 'a'))\n")
    message = str(excinfo.value)
    assert "outside the interpreter's domain" in message
    assert "models none of its functions" not in message


def test_frozenset_subclass_crosses_the_boundary_as_a_frozenset() -> None:
    """``type(value) is frozenset`` let a subclass through as a mutable ``set``.

    Which is exactly the silent cross-boundary type change the branch exists to
    prevent -- and worse than the original, because the program gained a
    mutating surface the trusted layer did not offer.
    """

    class Frozen(frozenset[int]):  # noqa: FURB189 - subclassing is the point
        pass

    outcome = run(
        "from tools.email.trusted.effects import send_email\n"
        "def main():\n    return send_email('bob@example.com', 'x')\n",
        dispatch=lambda q, f, a, k: Frozen({1, 2}),
    )
    assert type(outcome.value) is frozenset
    assert outcome.value == frozenset({1, 2})


def test_step_budget_exhaustion_names_the_effects_already_performed() -> None:
    """Exhaustion aborts a proved, approved program mid-run, not before it.

    An arbitrary prefix of its trusted effects has already fired by then, so
    "exceeded step budget of N" alone left the operator unable to tell a
    partial run from a refused one.
    """
    source = (
        "from tools.email.trusted.effects import send_email\n"
        "\n"
        "def main() -> None:\n"
        "    send_email('bob@example.com', 'one')\n"
        "    for n in range(1000):\n"
        "        x = n + 1\n"
    )
    with pytest.raises(ExecutionLimitExceeded) as excinfo:
        run(source, dispatch=lambda q, f, a, k: None, step_budget=60)
    message = str(excinfo.value)
    assert "exceeded step budget of 60" in message
    assert "tools.email.trusted.effects.send_email" in message


def test_step_budget_exhaustion_says_so_when_nothing_fired() -> None:
    with pytest.raises(ExecutionLimitExceeded, match="no effects had been performed"):
        run(
            "def main():\n    for n in range(1000):\n        x = n + 1\n",
            step_budget=20,
        )


def _trusted_layer(tmp_path: Path, body: str) -> tuple[Path, Path]:
    """A minimal trusted layer under its own import root."""
    root = tmp_path / "proj"
    trusted = root / "tools/demo/trusted"
    trusted.mkdir(parents=True)
    # Ancestors outside the trusted root are inert namespace packages.
    (trusted / "__init__.py").write_text("")
    (trusted / "effects.py").write_text(body)
    return root, trusted


def _run_against(root: Path, trusted: Path, source: str, **kwargs: Any) -> Outcome:
    import sys

    saved = {
        k: v for k, v in sys.modules.items() if k == "tools" or k.startswith("tools.")
    }
    for name in saved:
        del sys.modules[name]
    try:
        return execute(
            source,
            source_name="plan.py",
            policy=build_policy(trusted_roots=[trusted], import_roots=[root]),
            target="main",
            import_roots=[root],
            **kwargs,
        )
    finally:
        for name in list(sys.modules):
            if name == "tools" or name.startswith("tools."):
                del sys.modules[name]
        sys.modules.update(saved)


def test_trusted_layer_container_constants_are_exposed(tmp_path: Path) -> None:
    """A builtin container has no ``__module__``, so the origin check dropped it.

    ``_is_trusted_definition`` admitted scalars by source provenance and other
    objects by ``__module__``. ``getattr([1], "__module__", None)`` is ``None``,
    so every ``ALLOWED = [...]`` a trusted layer defines fell into the
    ``origin is None`` branch and was refused -- while the prover reads such a
    constant happily (``eval_attr`` infers the ``List`` node and evaluates it).
    So ``if addr in effects.ALLOWED:`` **proved** and was then refused at run,
    after the approval round trip and after the receipt nonce was consumed.
    ``marshal_in`` is the admission test because it is exactly the predicate
    that decides whether a value may cross the boundary, and it recurses.
    """
    root, trusted = _trusted_layer(
        tmp_path,
        "import deal\n"
        "\n"
        'ALLOWED = ["bob@example.com"]\n'
        "LIMITS = {'max': 3}\n"
        "\n"
        "\n"
        '@deal.has("network")\n'
        "def ship(msg: str) -> None:\n"
        "    return None\n",
    )
    outcome = _run_against(
        root,
        trusted,
        "from tools.demo.trusted import effects\n"
        "from tools.demo.trusted.effects import ship\n"
        "\n"
        "def main() -> None:\n"
        "    if 'bob@example.com' in effects.ALLOWED:\n"
        "        ship(str(effects.LIMITS['max']))\n",
        trusted_dispatch=lambda q, f, a, k: None,
    )
    assert [name for name, _a, _k in outcome.effects] == [
        "tools.demo.trusted.effects.ship"
    ]


def test_a_container_holding_a_live_object_is_still_refused(tmp_path: Path) -> None:
    """The widening admits containers, not an escape hatch through them."""
    root, trusted = _trusted_layer(
        tmp_path,
        "import pathlib\n"
        "\n"
        "LEAK = [pathlib.Path]\n"
        "\n"
        "\n"
        "def ship(msg: str) -> None:\n"
        "    return None\n",
    )
    with pytest.raises(TrustedBoundaryError):
        _run_against(
            root,
            trusted,
            "from tools.demo.trusted import effects\n"
            "\n"
            "def main():\n    return effects.LEAK\n",
        )


def test_unreadable_layer_source_exposes_no_constants(tmp_path: Path) -> None:
    """``_imported_names`` fails *closed* when provenance cannot be established.

    It is the check standing between a layer's ``from myapp.settings import
    API_TOKEN`` and the interpreted surface, and both of its failure paths
    returned an empty set -- which ``_is_trusted_definition`` read as "nothing
    was imported" and therefore "every constant here is local". A zipimport or
    frozen loader, a namespace package, or a source-less ``.pyc`` deployment
    all reach it. Every other admission rule in this package enumerates what is
    in; this one defaulted to admitting on error.
    """
    root, trusted = _trusted_layer(
        tmp_path,
        "SECRET = 'token'\n\n\ndef ship(msg: str) -> None:\n    return None\n",
    )
    source = (
        "from tools.demo.trusted import effects\n"
        "\n"
        "def main():\n"
        "    return effects.SECRET\n"
    )
    assert _run_against(root, trusted, source).value == "token"

    (trusted / "effects.py").unlink()
    (trusted / "effects.py").write_text("SECRET = 'token'\n")
    import clauz3.interp.interpreter as interpreter

    original = interpreter._imported_names
    interpreter._imported_names = lambda module: None
    try:
        with pytest.raises(TrustedBoundaryError):
            _run_against(root, trusted, source)
    finally:
        interpreter._imported_names = original
