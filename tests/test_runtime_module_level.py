"""Issue #71: module-level code must not execute unproven during ``clauz3 run``.

The proof and the approved guarantees cover the target body only, so anything
at module top level -- bare statements, effectful assignments and assignment
*targets*, effectful decorators, default arguments, class bodies -- would run
outside the proven obligation and outside the effect set the approval receipt
covers.

These payloads are unchanged from when the runtime was ``exec`` and a validator
in ``runner.py`` had to reject them. The runtime is now ``clauz3.interp``, which
enforces the same rule in ``_exec_module`` / ``_require_inert`` against the tree
the prover walked, so the assertions run against the real execution path rather
than against a separate validator that had to agree with it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clauz3 import interp
from clauz3.imports_policy import build_policy
from clauz3.prover import ProverConfigError
from clauz3.runner import (
    RunConfigError,
    RunProgramError,
    _execute_source,
    _preflight_structure,
    _validate_runtime_source,
)

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
TRUSTED_ROOT = EMAIL_ROOT / "tools/email/trusted"

_HEADER = "import clauz3\nfrom tools.email.trusted.effects import send_email\n"


def _validate(source: str) -> None:
    """Exactly what ``run_source`` does before ``prove_text``.

    This calls the *shipped* pair -- ``_validate_runtime_source`` then
    ``_preflight_structure`` -- rather than reimplementing the second half.
    An earlier version called ``_validate_runtime_source`` plus
    ``interp.target_node`` while claiming in this docstring to be "everything
    the runtime does before the target is called". Once ``_preflight_structure``
    added a node-type gate the claim stopped being true, and every positive
    assertion in this module came unbound from ``clauz3 run``:
    ``test_module_level_starred_unpacking_stays_allowed`` stayed green while
    ``clauz3 run`` refused starred unpacking outright. A helper that
    approximates the path it is testing is worth less than no helper.
    """
    policy = build_policy(trusted_roots=[TRUSTED_ROOT], import_roots=[EMAIL_ROOT])
    _validate_runtime_source(source=source, source_name="plan.py", policy=policy)
    _preflight_structure(
        source=source,
        source_name="plan.py",
        target="main",
        policy=policy,
        import_roots=[EMAIL_ROOT],
    )


def _execute(source: str) -> None:
    """Run the executed-copy path (sanitization + exec + call main), no network."""
    policy = build_policy(trusted_roots=[TRUSTED_ROOT], import_roots=[EMAIL_ROOT])
    _execute_source(
        source,
        source_name="plan.py",
        target="main",
        import_roots=[EMAIL_ROOT],
        request_id="r",
        receipt="x",
        policy=policy,
    )


# ── benign module-level forms must pass ───────────────────────────────────────


def test_imports_def_and_guarantee_pass() -> None:
    _validate(
        _HEADER
        + "@clauz3.guarantee(None)\n"
        + "def main() -> None:\n"
        + "    send_email('a@b.c', 'hi')\n"
    )


def test_constant_module_assignment_passes() -> None:
    _validate(_HEADER + "MAX = 5\n\ndef main() -> None:\n    pass\n")


def test_row_class_declaration_passes() -> None:
    _validate(
        "import clauz3\n"
        "class UserRow(clauz3.Row):\n"
        "    name: str\n"
        "    email: str\n\n"
        "def main() -> None:\n"
        "    pass\n"
    )


def test_helper_function_passes() -> None:
    _validate(
        _HEADER
        + "def helper() -> None:\n"
        + "    send_email('a@b.c', 'hi')\n\n"
        + "def main() -> None:\n"
        + "    helper()\n"
    )


# ── effectful module-level forms must be rejected ─────────────────────────────


def test_bare_module_level_call_rejected() -> None:
    with pytest.raises(RunConfigError):
        _validate(
            _HEADER + "send_email('a@b.c', 'hi')\n\ndef main() -> None:\n    pass\n"
        )


def test_module_level_effect_assignment_rejected() -> None:
    with pytest.raises(RunConfigError):
        _validate(
            _HEADER + "x = send_email('a@b.c', 'hi')\n\ndef main() -> None:\n    pass\n"
        )


def test_module_level_for_loop_rejected() -> None:
    with pytest.raises(RunConfigError):
        _validate(
            _HEADER
            + "for _i in [1]:\n    send_email('a@b.c', 'hi')\n\n"
            + "def main() -> None:\n    pass\n"
        )


def test_module_level_augmented_assignment_rejected() -> None:
    """``AugAssign`` passes the node-type gate now that both evaluators have a
    handler (#109), so module level is closed only by ``_exec_module``'s
    statement allow-list. Pin it, so admitting ``AugAssign`` there is a
    decision rather than a drift.
    """
    with pytest.raises(RunConfigError, match="AugAssign"):
        _validate(_HEADER + "MAX = 5\nMAX += 1\n\ndef main() -> None:\n    pass\n")


def test_effectful_decorator_rejected() -> None:
    # Refused by the decorator gate in ``validate_untrusted_source`` (issue
    # #129: a decorator that does not resolve to ``clauz3.guarantee`` is a
    # refusal, not a no-op) before the module-level gate in the interpreter
    # gets to it. Both halves of ``_validate`` reject it and both exit 2 from
    # ``clauz3 run``; which one speaks first is not the property under test.
    with pytest.raises((RunConfigError, ProverConfigError)):
        _validate(
            _HEADER + "@send_email('a@b.c', 'hi')\n" + "def main() -> None:\n    pass\n"
        )


def test_effectful_default_argument_rejected() -> None:
    with pytest.raises(RunConfigError):
        _validate(
            _HEADER
            + "def main(x: object = send_email('a@b.c', 'hi')) -> None:\n    pass\n"
        )


def test_effectful_class_body_rejected() -> None:
    with pytest.raises(RunConfigError):
        _validate(
            _HEADER
            + "class C:\n    send_email('a@b.c', 'hi')\n\n"
            + "def main() -> None:\n    pass\n"
        )


def test_effectful_class_base_rejected() -> None:
    with pytest.raises(RunConfigError):
        _validate(
            _HEADER
            + "class C(send_email('a@b.c', 'hi')):\n    pass\n\n"
            + "def main() -> None:\n    pass\n"
        )


def test_effectful_class_keyword_rejected() -> None:
    # `class C(metaclass=send_email(...))` evaluates the keyword at class-def time.
    with pytest.raises(RunConfigError):
        _validate(
            _HEADER
            + "class C(metaclass=send_email('a@b.c', 'hi')):\n    pass\n\n"
            + "def main() -> None:\n    pass\n"
        )


def test_plain_name_class_keyword_now_refused() -> None:
    """A deliberate narrowing when the interpreter became the runtime.

    ``exec`` needed ``metaclass=`` values restricted to plain names so a call
    could not run at class-definition time. The interpreter does not execute a
    class body at all -- it reads a ``Row`` schema out of it as syntax -- so the
    subset admits ``class X(Row)`` and nothing else. The prover has no
    ``ClassDef`` handler either, so anything more would run unproved.
    """
    with pytest.raises(RunConfigError):
        _validate(
            _HEADER
            + "import abc\n"
            + "\n"
            + "class Schema(metaclass=abc.ABCMeta):\n"
            + "    pass\n"
            + "\n"
            + "def main() -> None:\n"
            + "    pass\n"
        )


def test_class_keywords_are_refused_with_the_class_itself() -> None:
    """Replaces a test that asserted ``metaclass=type`` *passed* the keyword check.

    That was true of ``runner._is_declarative_member``, which inspected class
    keywords for calls. The interpreter never gets that far: the only class form
    in the subset is a ``Row`` schema declaration, so the keyword check has
    nothing left to guard. Renaming the old test to ``_unused_...`` left it
    silently uncollected; asserting the rule that replaced it is the point.
    """
    with pytest.raises(RunConfigError, match="class X\\(Row\\)"):
        _validate(
            "class C(metaclass=type):\n    pass\n\ndef main() -> None:\n    pass\n"
        )


# ── executed-copy sanitization (decorator args & annotations never run) ───────
# These drive the exec path directly. A guarantee arg or annotation that would
# raise if evaluated (`1 / 0`) must NOT raise once sanitized.


def test_guarantee_argument_not_evaluated_at_exec() -> None:
    # @clauz3.guarantee passes validation (it's a guarantee), but its argument
    # must not run at exec — the decorator is stripped from the executed copy.
    _execute("import clauz3\n@clauz3.guarantee(1 / 0)\ndef main() -> None:\n    pass\n")


def test_effectful_annotation_not_evaluated_at_exec() -> None:
    # Injected `from __future__ import annotations` makes the annotation a string
    # that is never evaluated, so `1 / 0` does not run.
    _execute("def main(x: 1 / 0 = 0) -> None:\n    pass\n")


def test_plain_program_executes() -> None:
    _execute("def main() -> None:\n    pass\n")


def test_row_schema_class_still_executes() -> None:
    # Sanitization (future-annotations) must not break a clauz3.Row schema class.
    _execute(
        "import clauz3\n"
        "class UserRow(clauz3.Row):\n"
        "    name: str\n"
        "    age: int\n\n"
        "def main() -> None:\n    pass\n"
    )


@pytest.mark.parametrize(
    "statement",
    [
        "CFG[send_email('eve@example.com', 'exfil')] = 1",
        "CFG[send_email('eve@example.com', 'exfil')]: int",
    ],
)
def test_module_level_assignment_target_cannot_fire_an_effect(
    statement: str,
) -> None:
    """The gate that ``_check_assign_targets`` adds, pinned directly.

    The value is a constant, so the old value-only check passed; ``exec`` then
    evaluates the subscript index at module level and fires a trusted effect
    before ``main()``, outside the effect set the approval receipt covers.
    ``_validate_runtime_source``'s call walk does not catch it either --
    ``send_email`` is not a forbidden builtin.
    """
    source = (
        "from tools.email.trusted.effects import send_email\n"
        "\n"
        "CFG = {}\n"
        f"{statement}\n"
        "\n"
        "def main() -> None:\n"
        "    return None\n"
    )
    with pytest.raises(RunConfigError, match="assignment target"):
        _validate(source)


def test_class_body_assignment_target_cannot_fire_an_effect() -> None:
    """Issue found in review of the module-level target fix, one scope down.

    ``exec`` runs a class body, so a subscript target inside one evaluates its
    index at class-definition time -- before the proved target is entered, and
    outside the effect set the approval request was built from.
    """
    for body in (
        "    CFG[send_email('eve@example.com', 'exfil')] = 1",
        "    CFG[send_email('eve@example.com', 'exfil')]: int",
    ):
        source = (
            "from tools.email.trusted.effects import send_email\n"
            "\n"
            "CFG = {}\n"
            "\n"
            "class Schema:\n"
            f"{body}\n"
            "\n"
            "def main() -> None:\n"
            "    return None\n"
        )
        with pytest.raises(RunConfigError):
            _validate(source)


def test_module_level_starred_unpacking_stays_allowed() -> None:
    """Inert: binds names, evaluates no subexpression."""
    _validate("A, *B = (1, 2, 3)\n\ndef main() -> None:\n    pass\n")


# ── runtime faults are not configuration faults ───────────────────────────────


def test_a_program_that_raises_is_a_runtime_error_not_a_config_error() -> None:
    """``clauz3 run`` exit 1, not exit 2.

    Under ``exec`` an uncaught exception from a proved, approved program
    propagated and the CLI's catch-all reported ``runtime error`` with exit 1.
    The first version of the interpreter swap wrapped every ``InterpError`` --
    including ``ProgramError`` and ``ExecutionLimitExceeded`` -- as
    ``RunConfigError``, so a program that merely failed was reported as a
    program that should never have been submitted.
    """
    source = _HEADER + "def main() -> None:\n    raise ValueError('boom')\n"
    with pytest.raises(RunProgramError, match="ValueError"):
        _execute(source)


def test_exhausting_the_step_budget_is_a_runtime_error() -> None:
    """The other ``ProgramFault``: the program ran, it just ran too long."""
    policy = build_policy(trusted_roots=[TRUSTED_ROOT], import_roots=[EMAIL_ROOT])
    source = (
        "def main() -> None:\n"
        "    total = 0\n"
        "    for n in range(1000):\n"
        "        total = total + n\n"
    )
    with pytest.raises(interp.ExecutionLimitExceeded):
        interp.execute(
            source,
            source_name="plan.py",
            policy=policy,
            target="main",
            import_roots=[EMAIL_ROOT],
            step_budget=100,
        )


def test_leaving_the_subset_stays_a_config_error() -> None:
    """The other half of the split: a subset violation is still exit 2."""
    source = _HEADER + "def main() -> None:\n    x = (i for i in [1])\n    return x\n"
    with pytest.raises(RunConfigError):
        _execute(source)


def test_deferred_trusted_import_still_resolves_at_call_time(tmp_path: Path) -> None:
    """``import_roots`` stay on ``sys.path`` for the whole run, as under ``exec``.

    ``_execute_source`` used to wrap the entire ``exec`` in
    ``_temporary_sys_path(import_roots)``. After the swap the only remaining
    wrapper was inside ``Program.resolve_module``, around a single
    ``importlib.import_module`` -- so a trusted layer that defers an import to
    call time saw a restored ``sys.path`` and raised ``ModuleNotFoundError``
    where it previously worked. Trusted layers are ordinary Python and are
    entitled to ordinary import behaviour; this widens nothing for the
    interpreted program, which can only import through ``resolve_module``.
    """
    import sys

    root = tmp_path / "proj"
    trusted = root / "tools/demo/trusted"
    trusted.mkdir(parents=True)
    # Ancestors outside the trusted root are inert namespace packages.
    (trusted / "__init__.py").write_text("")
    (root / "mailer.py").write_text("def send(x):\n    return 'sent:' + x\n")
    (trusted / "effects.py").write_text(
        "import deal\n"
        "\n"
        "\n"
        '@deal.has("network")\n'
        "def ship(msg: str) -> str:\n"
        "    from mailer import send  # deferred: resolved when the program calls\n"
        "    return send(msg)\n"
    )

    # ``tools`` is already imported from ``examples/email`` by other tests in
    # this process; a cached package would resolve ``tools.demo`` against the
    # wrong ``__path__``.
    saved = {
        k: v for k, v in sys.modules.items() if k == "tools" or k.startswith("tools.")
    }
    for name in saved:
        del sys.modules[name]

    before = list(sys.path)
    outcome = interp.execute(
        "from tools.demo.trusted.effects import ship\n"
        "\n"
        "def main() -> str:\n"
        "    return ship('hi')\n",
        source_name="plan.py",
        policy=build_policy(trusted_roots=[trusted], import_roots=[root]),
        target="main",
        import_roots=[root],
    )
    for name in list(sys.modules):
        if name == "tools" or name.startswith("tools."):
            del sys.modules[name]
    sys.modules.update(saved)

    assert outcome.value == "sent:hi"
    # And it is put back: leaking an import root would widen every later import
    # in the process, including the approval and receipt paths.
    assert sys.path == before


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("attribute-assign", "    obj.attr = 1"),
        ("delete", "    del x"),
        ("try", "    try:\n        pass\n    except ValueError:\n        pass"),
        # ``Starred``, ``Slice`` and ``Keyword`` are scaffolding only where
        # their reader is: a starred *target*, a slice under a *loaded*
        # subscript, a *named* call argument.
        ("call-starred", "    print(*[1, 2])"),
        ("list-starred", "    xs = [*[1], 1]"),
        ("slice-assign", "    xs = [1, 2, 3]\n    xs[0:2] = [1, 2]"),
        ("call-double-starred", "    d = {'a': 1}\n    print(**d)"),
        ("dict-double-starred", "    d = {'a': 1}\n    e = dict(**d)"),
    ],
)
def test_constructs_outside_the_subset_are_refused_before_approval(
    label: str,
    body: str,
) -> None:
    """``_STRUCTURAL_NODES`` must exempt only nodes a handler actually reads.

    Its first version listed ``AssignAttr``, ``DelName`` and ``ExceptHandler``,
    which are children of constructs the subset rejects outright and which no
    handler reads. ``obj.attr = 1`` therefore passed the pre-flight and was
    refused only by ``_assign_to``'s catch-all -- after the approval round trip
    and after ``_verify_receipt_or_refuse`` consumed the nonce, which is the
    post-approval residue the pre-flight exists to remove.
    """
    with pytest.raises(RunConfigError, match="not part of the proved subset"):
        _validate(f"def main() -> None:\n{body}\n")


def test_calling_an_inner_function_wrongly_is_a_runtime_error() -> None:
    """``f()`` against ``def f(a)`` is a program fault: exit 1, as under ``exec``.

    ``_bind_arguments`` raised ``UnsupportedNode`` for it, which
    ``_execute_source`` maps to ``RunConfigError`` -- exit 2, "this source
    should not have been submitted".
    """
    with pytest.raises(RunProgramError, match=r"TypeError: f\(\): missing"):
        _execute(
            "def main() -> None:\n"
            "    def f(a: int) -> int:\n"
            "        return a\n"
            "    f()\n"
        )


@pytest.mark.parametrize(
    "body",
    [
        "    xs = [1, 2, 3]\n    ys = xs[0:2]",
        "    xs = [1, 2, 3]\n    ys = xs[::2]",
        "    a, *b = (1, 2, 3)",
        "    print('a', sep='')",
    ],
)
def test_scaffolding_in_its_read_position_still_passes_the_pre_flight(
    body: str,
) -> None:
    """The positions the handlers actually read ``Slice``/``Starred``/``Keyword``."""
    _validate(f"def main() -> None:\n{body}\n")


@pytest.mark.parametrize(
    "signature",
    ["x: int", "x: int, y: int = 1"],
)
def test_a_target_that_needs_arguments_is_refused_before_approval(
    signature: str,
) -> None:
    """``execute`` calls the target with no arguments; ``main(x)`` cannot bind.

    The pre-flight resolved the target but never bound it, so a parameterised
    ``main`` proved, was approved, consumed the receipt nonce and only then
    failed in ``_bind_arguments``.
    """
    with pytest.raises(RunConfigError, match=r"main\(\): missing argument 'x'"):
        _validate(f"def main({signature}) -> None:\n    pass\n")


def test_a_target_whose_parameters_all_default_passes_the_pre_flight() -> None:
    """Binding with no arguments succeeds, so ``execute`` can call it."""
    _validate("def main(x: int = 1) -> None:\n    pass\n")
