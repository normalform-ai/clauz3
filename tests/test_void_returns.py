"""A call's None result must never become False or zero in a proof."""

from pathlib import Path
from textwrap import indent

import pytest
import z3

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import ProofResult, prove_text

FIXTURE = Path(__file__).resolve().parent / "fixtures/trusted_returns"
TRUSTED = FIXTURE / "rtrusted"


def _prove(body: str, helper: str = "") -> ProofResult:
    source = """
import math
import clauz3
from rtrusted import contracts
from rtrusted.effects import send_email

def note():
    pass

def identity(value):
    return value
"""
    source += helper
    source += """
@clauz3.guarantee(contracts.only_bob())
def main() -> None:
"""
    results = prove_text(
        source + indent(body, "    "),
        trusted_roots=[TRUSTED],
        import_paths=[FIXTURE],
        capture_observations=True,
    )
    assert len(results) == 1
    return results[0]


@pytest.mark.parametrize("call", ['send_email("bob@example.com", "hi")', "note()"])
@pytest.mark.parametrize(
    "use",
    [
        "value != False",
        "False != value",
        "value != 0",
        "0 != value",
        "identity(value) != False",
        "[value] != [False]",
        "(value,) != (False,)",
        "{value} != {False}",
        '{"key": value} != {"key": False}',
        "{value: 1} != {False: 1}",
        "(value and True) != False",
        "(False or value) != False",
        "(True and value) != False",
        "(value if True else False) != False",
        "False.__eq__(value)",
        "value.__eq__(False)",
        "value < 0",
        "0 < value",
        "value + 1",
        "1 + value",
        "min(False, value)",
        "value in {False}",
        "math.isnan(value)",
        "math.isinf(value)",
        "str(value)",
        "bool(obj=value)",
    ],
)
def test_void_value_uses_fail_closed(call: str, use: str) -> None:
    # Comparison/container examples reach the forbidden send in Python;
    # arithmetic/order examples raise. None must not be modelled as zero in
    # either case. Include reversed operands and operand-returning and/or.
    result = _prove(
        f'value = {call}\nif {use}:\n    send_email("victim@example.com", "hi")\n'
    )
    # Exact None scalar inequality now refutes the forbidden effect directly.
    scalar_inequality = use in {
        "value != False",
        "False != value",
        "value != 0",
        "0 != value",
        "identity(value) != False",
        "(value if True else False) != False",
    }
    expected = Conclusion.FAIL if scalar_inequality else Conclusion.SKIP
    assert result.proof.conclusion == expected, result
    assert result.observation is None


@pytest.mark.parametrize(
    "body",
    [
        "value = note()\nalias = value\nif alias != False:",
        "value = note()\nitems = []\nitems.append(value)\nif items != [False]:",
        "value = note()\nif True:\n    value = False\nif value != False:",
        'value = note()\nsend_email(value, "hi")\nif True:',
    ],
)
def test_void_alias_mutation_and_branch_merges_fail_closed(body: str) -> None:
    result = _prove(body + '\n    send_email("victim@example.com", "hi")\n')
    expected = Conclusion.FAIL if "alias = value" in body else Conclusion.SKIP
    assert result.proof.conclusion == expected, result


@pytest.mark.parametrize("call", ['send_email("bob@example.com", "hi")', "note()"])
@pytest.mark.parametrize(
    "condition", ["value", "bool(value)", "not value", "not bool(value)"]
)
def test_void_truthiness_remains_exact(call: str, condition: str) -> None:
    truthy = condition.startswith("not ")
    then = "bob@example.com" if truthy else "victim@example.com"
    otherwise = "victim@example.com" if truthy else "bob@example.com"
    result = _prove(
        f"value = {call}\n"
        f"if {condition}:\n"
        f'    send_email("{then}", "hi")\n'
        "else:\n"
        f'    send_email("{otherwise}", "hi")\n'
    )
    assert result.ok, result
    assert result.observation is not None
    assert all(fact.result is None for fact in result.observation.effects)


def test_void_truthiness_after_helper_return_remains_exact() -> None:
    result = _prove(
        'if not identity(note()):\n    send_email("bob@example.com", "hi")\n'
    )
    assert result.ok, result


@pytest.mark.parametrize("swap", [False, True])
def test_void_scalar_return_merge_fails_closed(swap: bool) -> None:
    then, otherwise = ("note()", "False") if swap else ("False", "note()")
    helper = f"""
def mixed(flag):
    if flag:
        return {then}
    else:
        return {otherwise}
"""
    result = _prove(
        'if mixed(False) != False:\n    send_email("victim@example.com", "hi")\n',
        helper,
    )
    assert result.proof.conclusion == Conclusion.SKIP, result


def test_implicit_none_path_is_not_replaced_by_explicit_return() -> None:
    result = _prove(
        'if maybe(False) != False:\n    send_email("victim@example.com", "hi")\n',
        """
def maybe(flag):
    if flag:
        return False
""",
    )
    assert result.proof.conclusion == Conclusion.SKIP, result


def test_total_scalar_helper_stays_supported() -> None:
    result = _prove(
        'if choose(False):\n    send_email("victim@example.com", "hi")\n',
        """
def choose(flag):
    if flag:
        return True
    else:
        return False
""",
    )
    assert result.ok, result


@pytest.mark.parametrize(
    "body",
    [
        '''if n > 1:
    return "a"
if n > 0:
    return "b"
return "c"''',
        '''if n > 1:
    return "a"
elif n > 0:
    return "b"
else:
    return "c"''',
        '''if n > 0:
    if n > 1:
        return "a"
    else:
        return "b"
else:
    return "c"''',
    ],
    ids=["sequential_guards", "elif_chain", "nested_if"],
)
def test_total_branching_helper_stays_supported(body: str) -> None:
    source = "import clauz3\ndef classify(n: int) -> str:\n" + indent(body, "    ")
    source += """
@clauz3.ensures(lambda result: result in ("a", "b", "c"))
def main(n: int) -> str:
    return classify(n)
"""
    results = prove_text(source)
    assert len(results) == 1
    assert results[0].proof.conclusion is Conclusion.OK, results[0]


def test_total_helper_with_modelled_exception_stays_supported() -> None:
    results = prove_text("""
import clauz3
import deal
def lookup(d: dict[int, int], n: int) -> int:
    if n < 0:
        return 0
    return d[n]

@deal.raises(KeyError)
@clauz3.ensures(lambda result: result in (0, 1))
def main(n: int) -> int:
    return lookup({0: 1}, n)
""")
    assert len(results) == 1
    assert results[0].proof.conclusion is Conclusion.OK, results[0]


def test_unknown_helper_totality_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(z3.Solver, "check", lambda self: z3.unknown)
    results = prove_text("""
import clauz3
def classify(n: int) -> str:
    if n > 1:
        return "a"
    elif n > 0:
        return "b"
    else:
        return "c"

@clauz3.ensures(lambda result: result in ("a", "b", "c"))
def main(n: int) -> str:
    return classify(n)
""")
    assert len(results) == 1
    assert results[0].proof.conclusion is Conclusion.SKIP, results[0]
    assert "helper may implicitly return None" in str(results[0].proof.error)


def test_contract_assumptions_do_not_hide_helper_fallthrough() -> None:
    results = prove_text("""
import clauz3
@clauz3.ensures(lambda result: result > 0)
def impossible() -> int:
    return 0

def maybe(flag: bool):
    impossible()
    if flag:
        return False

@clauz3.ensures(lambda result: result == False)
def main(flag: bool):
    return maybe(flag)
""")
    result = next(result for result in results if result.name == "main")
    assert result.proof.conclusion is Conclusion.SKIP, result
    assert "helper may implicitly return None" in str(result.proof.error)
