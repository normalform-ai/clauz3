"""Augmented assignment (``x op= y``) in the prover (#109).

For ``bool``, ``int``, ``float`` and ``str`` Python defines ``x op= y`` as
``x = x op y``: none of them has an in-place operator, so the statement rebinds
the name and no alias can observe a change. The handler desugars exactly those
four sorts. ``list``, ``set`` and ``dict`` mutate in place under the same
syntax, so a blanket rewrite would be unsound under aliasing. ``tuple`` is
refused too, although Python's is immutable, because the solver's ``ListSort``
subclasses ``VarTupleSort``: admitting the tuple proxy would admit lists. All
of them stay outside the subset with an explicit reason rather than the bare
"unsupported ast node".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from clauz3._vendor.deal_solver import Conclusion, Proof, _context
from clauz3._vendor.deal_solver._context import FactInfo
from clauz3.prover import ProofResult, prove_text

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
BANK_ROOT = ROOT / "examples/bank"

EMAIL_PRELUDE = """
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

import clauz3
"""

BANK_PRELUDE = """
from tools.bank.trusted import contracts as bank
from tools.bank.trusted.effects import withdraw

import clauz3
"""


def _result(source: str, root: Path = EMAIL_ROOT) -> ProofResult:
    (result,) = prove_text(
        source,
        import_paths=[root],
        trusted_roots=sorted((root / "tools").glob("*/trusted")),
    )
    return result


def _prove(source: str, root: Path = EMAIL_ROOT) -> Proof:
    return _result(source, root).proof


def _pure(body: str, params: str = "") -> str:
    """A ``main`` whose only obligations are its own ``assert`` statements."""
    return (
        EMAIL_PRELUDE
        + "\n@clauz3.guarantee(emails.no_guarantees())\n"
        + f"def main({params}) -> None:\n    {body}\n"
    )


@pytest.mark.parametrize(
    ("suffix", "conclusion", "description"),
    [
        ("!", Conclusion.OK, "assertion, guarantee"),
        (" a much longer body than the contract allows", Conclusion.FAIL, "guarantee"),
    ],
)
def test_str_concat_is_proved_or_refuted(
    suffix: str,
    conclusion: Conclusion,
    description: str,
) -> None:
    """The program that used to SKIP: with the handler it is decided."""
    proof = _prove(
        EMAIL_PRELUDE
        + f"""
@clauz3.guarantee(emails.content_length_at_most(5))
def main() -> None:
    msg = "hi"
    msg += {suffix!r}
    send_email("bob@example.com", msg)
"""
    )
    assert proof.conclusion is conclusion
    assert proof.description == description


@pytest.mark.parametrize(
    ("limit", "conclusion"),
    [(100, Conclusion.OK), (90, Conclusion.FAIL)],
)
def test_int_accumulator_in_an_unrolled_loop_feeds_the_guarantee(
    limit: int,
    conclusion: Conclusion,
) -> None:
    """``amount`` grows 10, 20, 30, 40 across the unrolled loop: total 100."""
    proof = _prove(
        BANK_PRELUDE
        + f"""
@clauz3.guarantee(bank.max_spend({limit}))
def main() -> None:
    amount = 0
    for _ in range(4):
        amount += 10
        withdraw("checking", amount)
""",
        BANK_ROOT,
    )
    assert proof.conclusion is conclusion


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("x += 3", "10"),
        ("x -= 2", "5"),
        ("x *= 2", "14"),
        # The one operator whose result sort differs from the target's.
        ("x /= 2", "3.5"),
        ("x //= 2", "3"),
        ("x %= 4", "3"),
        ("x **= 2", "49"),
        ("x &= 3", "3"),
        ("x |= 8", "15"),
        ("x ^= 1", "6"),
        ("x <<= 1", "14"),
        ("x >>= 1", "3"),
    ],
)
def test_every_binary_operator_desugars(statement: str, expected: str) -> None:
    """Each operator proves its value, and the negated assert is refuted."""
    holds = _prove(_pure(f"x = 7\n    {statement}\n    assert x == {expected}"))
    assert holds.conclusion is Conclusion.OK, holds
    refuted = _prove(_pure(f"x = 7\n    {statement}\n    assert x != {expected}"))
    assert refuted.conclusion is Conclusion.FAIL, refuted


def test_float_and_bool_targets_are_admitted() -> None:
    proof = _prove(
        _pure(
            "x = 2.5\n    x *= 2\n    b = True\n    b += 1\n"
            "    assert x == 5.0 and b == 2"
        )
    )
    assert proof.conclusion is Conclusion.OK, proof


def test_parameter_target_is_admitted() -> None:
    """A parameter is bound on entry; ``n += 1`` need not be positive."""
    proof = _prove(_pure("n += 1\n    assert n > 0", params="n: int"))
    assert proof.conclusion is Conclusion.FAIL, proof


@pytest.mark.parametrize(
    ("body", "sort"),
    [
        ("xs = [1]\n    xs += [2]", "list"),
        ("xs = []\n    xs += [2]", "list"),
        ("s = {1}\n    s |= {2}", "set"),
        ("d = {1: 2}\n    d |= {3: 4}", "dict"),
        ("t = (1,)\n    t += (2,)", "tuple"),
    ],
)
def test_container_targets_are_refused_with_the_sort_named(
    body: str, sort: str
) -> None:
    """Fail closed where the rewrite is unsound or the sort model cannot tell.

    ``tuple`` is immutable in Python, but ``ListSort`` subclasses
    ``VarTupleSort`` in the proxy hierarchy, so it is refused with the rest
    rather than risk admitting lists through the tuple proxy.
    """
    proof = _prove(_pure(body))
    assert proof.conclusion is Conclusion.SKIP
    assert "augmented assignment" in str(proof.error)
    assert sort in str(proof.error)


def test_refused_target_records_no_fact_for_its_operand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The target is checked before ``node.value`` is evaluated.

    Evaluating ``[send_email(...)]`` first would record a ``send_email`` fact
    for a statement that is then refused; the interpreter's counterpart is
    ``test_refused_augmented_assignment_evaluates_no_operand``. The positive
    control shows the spy does see the call when it is evaluated.
    """
    recorded: list[str] = []

    def spy(*args: Any, **kwargs: Any) -> FactInfo:
        fact = FactInfo(*args, **kwargs)
        recorded.append(fact.name)
        return fact

    # ``_proxies._func`` imports ``FactInfo`` from ``_context`` per call.
    monkeypatch.setattr(_context, "FactInfo", spy)
    template = (
        EMAIL_PRELUDE
        + """
@clauz3.guarantee(emails.no_guarantees())
def main() -> None:
    xs = []
    {statement}
"""
    )
    refused = _prove(
        template.format(statement='xs += [send_email("bob@example.com", "hi")]')
    )
    assert refused.conclusion is Conclusion.SKIP
    assert "augmented assignment" in str(refused.error)
    assert recorded == []

    _prove(template.format(statement='xs = [send_email("bob@example.com", "hi")]'))
    assert recorded == ["send_email"]


def test_subscript_target_is_refused() -> None:
    proof = _prove(_pure("xs = [1]\n    xs[0] += 1"))
    assert proof.conclusion is Conclusion.SKIP
    assert "augmented assignment" in str(proof.error)
    assert "Subscript" in str(proof.error)


def test_unbound_target_is_refused() -> None:
    """``LIMIT += 1`` inside ``main`` is an ``UnboundLocalError`` in CPython."""
    proof = _prove(
        EMAIL_PRELUDE
        + """
LIMIT = 10


@clauz3.guarantee(emails.no_guarantees())
def main() -> None:
    LIMIT += 1
"""
    )
    assert proof.conclusion is Conclusion.SKIP
    assert "cannot resolve name" in str(proof.error)
    assert "LIMIT" in str(proof.error)
