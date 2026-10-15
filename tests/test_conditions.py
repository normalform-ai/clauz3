"""Preconditions and postconditions, and the spelling that used to drop them.

``@clauz3.requires`` is an obligation at the call site and ``@clauz3.ensures``
is what narrows a trusted return, since the body that would have computed it is
never evaluated. Both were ``@deal.pre`` / ``@deal.post``, read by deal's
extractor, which matches the literal text ``deal.pre`` --- so a trusted layer
that wrote ``import deal as d`` had them dropped from the proof entirely, and a
call the precondition forbids reported ``proved!``.

The programs here are built so that exactly one thing decides the conclusion:

* ``charge(11)`` violates ``requires(amount <= 10)`` and nothing else, so the
  proof fails on an *assertion* when the precondition is read and succeeds when
  it is not.
* ``charge(count())`` is only provable when ``ensures(result <= 10)`` bounds
  the materialized return, which is otherwise an unconstrained symbol.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.conditions import ensures, requires
from clauz3.prover import ProverConfigError, prove_text, validate_untrusted_source

CONTRACTS = '''
"""A null guarantee, so every conclusion here comes from the conditions."""

from clauz3.spec import ContractSpec, contract
from clauz3.spec import no_guarantees as core_no_guarantees


@contract
def anything() -> ContractSpec:
    """Impose nothing at all."""
    return core_no_guarantees()
'''

EFFECTS = """
import clauz3
import clauz3.conditions
import deal
import deal as d


{charge_conditions}
@clauz3.trusted
def charge(amount: int) -> None:
    pass


{count_conditions}
@clauz3.trusted
def count() -> int:
    ...


{count_up_to_conditions}
@clauz3.trusted
def count_up_to(cap: int) -> int:
    ...
"""

PROGRAM = """
import clauz3
from {pkg} import contracts
from {pkg}.effects import charge, count, count_up_to


@clauz3.guarantee(contracts.anything())
def main() -> None:
    charge({argument})
"""


def _prove(
    tmp_path: Path,
    *,
    charge_conditions: str = "",
    count_conditions: str = "",
    count_up_to_conditions: str = "",
    argument: str = "11",
) -> Conclusion:
    pkg = tmp_path.name.replace("-", "_")
    package = tmp_path / pkg
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "contracts.py").write_text(CONTRACTS)
    (package / "effects.py").write_text(
        EFFECTS.format(
            charge_conditions=charge_conditions,
            count_conditions=count_conditions,
            count_up_to_conditions=count_up_to_conditions,
        )
    )
    results = prove_text(
        PROGRAM.format(pkg=pkg, argument=argument),
        trusted_roots=[package],
        import_paths=[tmp_path],
        source_name="<program>",
    )
    assert len(results) == 1
    return results[0].proof.conclusion


# ── requires is an obligation at the call site ───────────────────────────────


def test_requires_refutes_a_violating_call(tmp_path: Path) -> None:
    conclusion = _prove(
        tmp_path,
        charge_conditions="@clauz3.requires(lambda amount: amount <= 10)",
    )
    assert conclusion == Conclusion.FAIL


def test_requires_is_discharged_by_a_satisfying_call(tmp_path: Path) -> None:
    conclusion = _prove(
        tmp_path,
        charge_conditions="@clauz3.requires(lambda amount: amount <= 10)",
        argument="3",
    )
    assert conclusion == Conclusion.OK


def test_no_precondition_admits_the_same_call(tmp_path: Path) -> None:
    """The control: without a precondition ``charge(11)`` is simply allowed."""

    assert _prove(tmp_path) == Conclusion.OK


@pytest.mark.parametrize(
    "condition",
    [
        "@clauz3.requires(lambda amount: amount <= 10)",
        "@clauz3.conditions.requires(lambda amount: amount <= 10)",
        "@deal.pre(lambda amount: amount <= 10)",
        pytest.param("@d.pre(lambda amount: amount <= 10)", id="aliased-deal-pre"),
    ],
)
def test_every_spelling_of_a_precondition_binds(tmp_path: Path, condition: str) -> None:
    """The hole: deal's extractor matches the text ``deal.pre`` and nothing else.

    ``import deal as d`` in a *trusted layer* used to drop the obligation, so
    ``charge(11)`` proved against ``requires(amount <= 10)``.
    """

    assert _prove(tmp_path, charge_conditions=condition) == Conclusion.FAIL


# ── deal.chain bundles contracts, and hides them under an alias ──────────────


@pytest.mark.parametrize(
    "condition",
    [
        "@deal.chain(deal.pre(lambda amount: amount <= 10))",
        pytest.param(
            "@d.chain(d.pre(lambda amount: amount <= 10))",
            id="aliased-chain",
        ),
        pytest.param(
            "@deal.chain(d.pre(lambda amount: amount <= 10))",
            id="aliased-inner",
        ),
        pytest.param(
            "@d.chain(deal.pre(lambda amount: amount <= 10))",
            id="aliased-outer",
        ),
    ],
)
def test_a_chained_precondition_binds(tmp_path: Path, condition: str) -> None:
    """``deal.chain`` states several contracts at once, and deal unfolds it.

    By literal text, at every level: one aliased link hid the whole bundle, so
    ``charge(11)`` proved against a precondition forbidding it. Each spelling
    is listed because the unfolding has to know whether deal's own extractor
    already reached a given level, or the same contract would count twice.
    """

    assert _prove(tmp_path, charge_conditions=condition) == Conclusion.FAIL


def test_a_precondition_chained_beside_a_has_still_binds(tmp_path: Path) -> None:
    """A bundle carrying both: the precondition must survive the company it keeps.

    This says nothing about the boundary --- ``charge`` carries its own
    ``@clauz3.trusted`` here, and a violated precondition decides the
    conclusion either way. That the chained ``has`` *is* a boundary is pinned
    where it can fail, in ``tests/test_trusted_decorator.py``.
    """

    conclusion = _prove(
        tmp_path,
        charge_conditions='@d.chain(d.has("io"), d.pre(lambda amount: amount <= 10))',
    )
    assert conclusion == Conclusion.FAIL


# ── the predicate may be passed by keyword ───────────────────────────────────


@pytest.mark.parametrize(
    "condition",
    [
        "@clauz3.requires(predicate=lambda amount: amount <= 10)",
        pytest.param(
            "@deal.pre(validator=lambda amount: amount <= 10)",
            id="deals-own-parameter-name",
        ),
        pytest.param(
            '@clauz3.requires(message="too much", '
            "predicate=lambda amount: amount <= 10)",
            id="after-another-keyword",
        ),
    ],
)
def test_a_precondition_passed_by_keyword_binds(tmp_path: Path, condition: str) -> None:
    """``requires(predicate=...)`` is an ordinary call, and states the same thing.

    The predicate was read as the decorator's first positional argument, so
    this spelling reached the evaluator as a contract with no arguments and
    raised ``IndexError`` out of the prover.
    """

    assert _prove(tmp_path, charge_conditions=condition) == Conclusion.FAIL


def test_a_postcondition_passed_by_keyword_keeps_its_arity(tmp_path: Path) -> None:
    """Arity is what chooses between deal's ``post`` and ``ensure``.

    ``post`` binds its lambda's one parameter to the return value positionally;
    ``ensure`` binds the name ``result`` and leaves the arguments in scope.
    Reading a ``predicate=`` lambda as having no parameters would state this
    two-parameter postcondition as a ``post``, whose binding it does not fit.
    """

    conclusion = _prove(
        tmp_path,
        charge_conditions=LIMIT,
        count_up_to_conditions=(
            "@clauz3.ensures(predicate=lambda cap, result: result <= cap)"
        ),
        argument="count_up_to(10)",
    )
    assert conclusion == Conclusion.OK


# ── an alias is a module-level name ──────────────────────────────────────────


def test_a_local_import_does_not_shadow_a_condition_alias(tmp_path: Path) -> None:
    """The precondition half of the same hole in how aliases were collected.

    ``import calendar as d`` inside a helper nobody calls made ``@d.pre``
    resolve to ``calendar.pre``, which is no contract at all --- so the
    obligation vanished and ``charge(11)`` proved against a precondition
    forbidding it.
    """

    conclusion = _prove(
        tmp_path,
        charge_conditions=(
            "def unused_helper() -> None:\n"
            "    import calendar as d\n"
            "\n"
            "\n"
            "@d.pre(lambda amount: amount <= 10)"
        ),
    )
    assert conclusion == Conclusion.FAIL


# ── ensures is what narrows a materialized return ────────────────────────────


def test_ensures_bounds_a_trusted_return(tmp_path: Path) -> None:
    conclusion = _prove(
        tmp_path,
        charge_conditions="@clauz3.requires(lambda amount: amount <= 10)",
        count_conditions="@clauz3.ensures(lambda result: result <= 10)",
        argument="count()",
    )
    assert conclusion == Conclusion.OK


def test_without_ensures_the_return_is_unconstrained(tmp_path: Path) -> None:
    """The control: a trusted return is a fresh symbol, so nothing bounds it."""

    conclusion = _prove(
        tmp_path,
        charge_conditions="@clauz3.requires(lambda amount: amount <= 10)",
        argument="count()",
    )
    assert conclusion == Conclusion.FAIL


def test_aliased_deal_post_bounds_a_trusted_return(tmp_path: Path) -> None:
    conclusion = _prove(
        tmp_path,
        charge_conditions="@clauz3.requires(lambda amount: amount <= 10)",
        count_conditions="@d.post(lambda result: result <= 10)",
        argument="count()",
    )
    assert conclusion == Conclusion.OK


# ── ensures may also read the arguments ──────────────────────────────────────

LIMIT = "@clauz3.requires(lambda amount: amount <= 10)"


def test_ensures_relates_the_result_to_an_argument(tmp_path: Path) -> None:
    """``count_up_to(10)`` is within ``charge``'s limit only because of the cap.

    Nothing about the return value itself says so --- it is a fresh symbol of
    the annotated sort --- so this is provable only when the postcondition may
    read ``cap``.
    """

    conclusion = _prove(
        tmp_path,
        charge_conditions=LIMIT,
        count_up_to_conditions="@clauz3.ensures(lambda cap, result: result <= cap)",
        argument="count_up_to(10)",
    )
    assert conclusion == Conclusion.OK


def test_the_argument_is_what_bounds_it(tmp_path: Path) -> None:
    """The same postcondition refutes a call whose cap exceeds the limit."""

    conclusion = _prove(
        tmp_path,
        charge_conditions=LIMIT,
        count_up_to_conditions="@clauz3.ensures(lambda cap, result: result <= cap)",
        argument="count_up_to(20)",
    )
    assert conclusion == Conclusion.FAIL


def test_a_result_only_postcondition_cannot_see_the_argument(tmp_path: Path) -> None:
    """The control: one parameter is the result, and nothing bounds it by ``cap``."""

    conclusion = _prove(
        tmp_path,
        charge_conditions=LIMIT,
        count_up_to_conditions="@clauz3.ensures(lambda result: result >= 0)",
        argument="count_up_to(10)",
    )
    assert conclusion == Conclusion.FAIL


def test_aliased_deal_ensure_relates_result_to_argument(tmp_path: Path) -> None:
    conclusion = _prove(
        tmp_path,
        charge_conditions=LIMIT,
        count_up_to_conditions="@d.ensure(lambda cap, result: result <= cap)",
        argument="count_up_to(10)",
    )
    assert conclusion == Conclusion.OK


def test_ensures_over_arguments_needs_a_result_parameter() -> None:
    """Both the prover and deal bind the return value to the name ``result``."""

    with pytest.raises(TypeError, match="named 'result'"):
        ensures(lambda cap, res: res <= cap)


def test_ensures_over_arguments_raises_at_runtime() -> None:
    import deal

    @ensures(lambda cap, result: result <= cap)
    def count_up_to(cap: int) -> int:
        return cap + 1

    with pytest.raises(deal.PostContractError):
        count_up_to(5)


# ── a predicate the evaluator cannot read is refused, not dropped ────────────


@pytest.mark.parametrize(
    "condition",
    [
        "@clauz3.requires(small)",
        pytest.param("@clauz3.requires(predicate=small)", id="by-keyword"),
        pytest.param("@deal.pre(small)", id="deals-own"),
        pytest.param("@d.pre(small)", id="aliased-deal"),
        pytest.param("@d.chain(d.pre(small))", id="inside-a-chain"),
    ],
)
def test_a_named_predicate_is_refused(tmp_path: Path, condition: str) -> None:
    """The evaluator reads the lambda's *body*, so a named function is nothing.

    It returned ``None`` for anything that is not a lambda node, and the
    contract simply ceased to exist: ``charge(11)`` proved clean under a
    precondition forbidding it, while deal went on raising
    ``PreContractError`` at run time --- the proof and the program disagreeing
    about the same layer. A predicate this prover cannot read is now a
    refusal, for the same reason a missing one is.
    """

    conclusion = _prove(
        tmp_path,
        charge_conditions=(
            "def small(amount: int) -> bool:\n    return amount <= 10\n\n\n" + condition
        ),
    )
    assert conclusion == Conclusion.SKIP


def test_the_refusal_says_to_inline_the_lambda(tmp_path: Path) -> None:
    pkg = tmp_path.name.replace("-", "_")
    package = tmp_path / pkg
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "contracts.py").write_text(CONTRACTS)
    (package / "effects.py").write_text(
        EFFECTS.format(
            charge_conditions=(
                "def small(amount: int) -> bool:\n"
                "    return amount <= 10\n"
                "\n"
                "\n"
                "@clauz3.requires(small)"
            ),
            count_conditions="",
            count_up_to_conditions="",
        )
    )
    results = prove_text(
        PROGRAM.format(pkg=pkg, argument="11"),
        trusted_roots=[package],
        import_paths=[tmp_path],
        source_name="<program>",
    )
    error = str(results[0].proof.error)
    assert "@clauz3.requires" in error
    assert "lambda" in error


def test_a_lambda_predicate_is_still_read(tmp_path: Path) -> None:
    """The control: the same program, with the predicate written in place."""

    conclusion = _prove(
        tmp_path,
        charge_conditions="@clauz3.requires(lambda amount: amount <= 10)",
    )
    assert conclusion == Conclusion.FAIL


# ── agent-authored source may not state them ─────────────────────────────────


@pytest.mark.parametrize(
    "source",
    [
        "import clauz3\n@clauz3.requires(lambda: True)\ndef f() -> None: ...\n",
        "import clauz3\n@clauz3.ensures(lambda result: True)\ndef f() -> None: ...\n",
        "from clauz3 import requires as r\n@r(lambda: True)\ndef f() -> None: ...\n",
    ],
)
def test_conditions_are_refused_in_agent_source(source: str) -> None:
    with pytest.raises(ProverConfigError, match="only allowed in trusted roots"):
        validate_untrusted_source(source=source, path=Path("plan.py"))


# ── runtime enforcement is still deal's ──────────────────────────────────────


def test_requires_raises_at_runtime() -> None:
    import deal

    @requires(lambda amount: amount <= 10, message="amount must be <= 10")
    def charge(amount: int) -> int:
        return amount

    assert charge(3) == 3
    with pytest.raises(deal.PreContractError):
        charge(11)


def test_ensures_raises_at_runtime() -> None:
    import deal

    @ensures(lambda result: result <= 10)
    def count(value: int) -> int:
        return value

    assert count(3) == 3
    with pytest.raises(deal.PostContractError):
        count(11)
