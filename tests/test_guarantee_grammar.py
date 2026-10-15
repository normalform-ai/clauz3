"""What may appear inside ``@clauz3.guarantee(...)``.

The approval request shows the guarantee's source text, so the text must name
an obligation the trusted layer defined over arguments the reader can see. The
family of expressions that used to prove vacuously -- an uncalled helper, a
string, an integer, a lambda, a look-alike helper the agent defined itself --
is refused here, at prove time, before anything reaches a human.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from textwrap import dedent

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.guarantees import GuaranteeError, guarantee_texts
from clauz3.prover import AgentTheorem, ProofResult, ProverConfigError, prove_text

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
EMAIL_TRUSTED = EMAIL_ROOT / "tools/email/trusted"
LOOP_FIXTURES = Path(__file__).resolve().parent / "fixtures/loop_email"
LOOP_TRUSTED = LOOP_FIXTURES / "qtrusted"

PREAMBLE = """\
import clauz3
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email
"""


def _program(guarantee: str, *, extra: str = "") -> str:
    """An entry file whose ``main`` emails a forbidden address."""
    return f"""\
{PREAMBLE}{extra}

@clauz3.guarantee({guarantee})
def main() -> None:
    send_email("attacker@example.com", "hi")
"""


def _prove(source: str) -> ProofResult:
    results = prove_text(
        source,
        import_paths=[EMAIL_ROOT],
        trusted_roots=[EMAIL_TRUSTED],
    )
    assert len(results) == 1
    return results[0]


#: Every expression here reported ``proved!`` over a program that emails a
#: forbidden address, because ``ProxySort.m_bool`` defaults to true.
VACUOUS_GUARANTEES = [
    pytest.param("emails.only", "", "is not a contract call", id="uncalled-helper"),
    pytest.param(
        "tools.email.trusted.contracts.only",
        "import tools.email.trusted.contracts",
        "is not a contract call",
        id="uncalled-qualified-helper",
    ),
    pytest.param(
        '"only bob is emailed"',
        "",
        "is not a contract call",
        id="string-literal",
    ),
    pytest.param("42", "", "is not a contract call", id="integer-literal"),
    pytest.param("True", "", "is not a contract call", id="true-literal"),
    pytest.param("lambda: True", "", "is not a contract call", id="lambda"),
    pytest.param("1 == 1", "", "is not a contract call", id="comparison"),
    pytest.param(
        'only(["bob@example.com"])',
        "\n\ndef only(addresses: list[str]) -> bool:\n    return True\n",
        "does not call a registered @contract",
        id="agent-defined-look-alike",
    ),
    pytest.param(
        "len([1, 2]) >= 0",
        "",
        "is not a contract call",
        id="builtin-comparison",
    ),
    pytest.param("len([1, 2])", "", "does not call a registered @contract", id="len"),
    pytest.param(
        "emails.at_most(limit)",
        "\nlimit = 2\n",
        "non-literal argument",
        id="argument-names-a-global",
    ),
    pytest.param(
        "emails.only(*[[]])",
        "",
        "non-literal argument",
        id="starred-argument",
    ),
    pytest.param(
        'emails.only(**{"addresses": []})',
        "",
        "unpacks its arguments",
        id="double-starred-argument",
    ),
]


@pytest.mark.parametrize(("guarantee", "extra", "message"), VACUOUS_GUARANTEES)
def test_prove_refuses_guarantee_outside_the_grammar(
    guarantee: str,
    extra: str,
    message: str,
) -> None:
    with pytest.raises(ProverConfigError, match=message):
        _prove(_program(guarantee, extra=extra))


@pytest.mark.parametrize(
    "guarantee",
    [
        pytest.param('emails.only(["bob@example.com"])', id="list-literal"),
        pytest.param("emails.none()", id="no-arguments"),
        pytest.param(
            'emails.recipient_at_most("bob@example.com", 2)',
            id="several-literals",
        ),
        pytest.param("emails.at_most(count=2)", id="literal-keyword"),
        pytest.param("emails.content_length_at_most(-1)", id="negative-literal"),
    ],
)
def test_prove_accepts_a_registered_contract_over_literals(guarantee: str) -> None:
    """These are the guarantees the grammar exists to let through."""
    result = _prove(_program(guarantee))
    assert result.proof.conclusion in {Conclusion.OK, Conclusion.FAIL}


@pytest.mark.parametrize(
    ("contract_import", "call"),
    [
        pytest.param(
            "from tools.email.trusted.contracts import none",
            "none()",
            id="bare-import",
        ),
        pytest.param(
            "from tools.email.trusted.contracts import none as nothing",
            "nothing()",
            id="aliased-import",
        ),
        pytest.param(
            "import tools.email.trusted.contracts",
            "tools.email.trusted.contracts.none()",
            id="fully-qualified",
        ),
    ],
)
def test_callee_is_resolved_through_import_aliases(
    contract_import: str,
    call: str,
) -> None:
    """However a contract is imported, the same registry entry is found."""
    source = f"""\
import clauz3
{contract_import}
from tools.email.trusted.effects import send_email

@clauz3.guarantee({call})
def main() -> None:
    send_email("attacker@example.com", "hi")
"""
    assert _prove(source).proof.conclusion == Conclusion.FAIL


def test_row_class_argument_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Row schema is a fixed reference, not a value the agent computed.

    ``contracts.addresses_from(UserRow, "email")`` is the one non-literal
    argument shape the grammar allows, so it needs a case of its own. The
    proof of this same file runs in ``tests/test_quantified_relations.py``.
    """

    monkeypatch.syspath_prepend(str(LOOP_FIXTURES))
    importlib.import_module("qtrusted.contracts")
    importlib.import_module("qtrusted.rows")
    source = (LOOP_FIXTURES / "cases/addresses_from_users_email_pass.py").read_text()

    assert guarantee_texts(source=source, target="main") == [
        "contracts.addresses_from(UserRow, 'email')",
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        '_predicate=emails.only(["bob@example.com"])',
        'predicate=emails.only(["bob@example.com"])',
        "",
        'emails.only(["bob@example.com"]), emails.only(["bob@example.com"])',
    ],
)
def test_guarantee_decorator_must_carry_exactly_one_positional_argument(
    arguments: str,
) -> None:
    """The keyword form used to be dropped in silence by prover and request."""
    source = f"""\
{PREAMBLE}

@clauz3.guarantee({arguments})
def main() -> None:
    send_email("attacker@example.com", "hi")
"""
    with pytest.raises(ProverConfigError, match="exactly one positional argument"):
        _prove(source)


def test_uncalled_guarantee_decorator_is_refused() -> None:
    source = f"""\
{PREAMBLE}

@clauz3.guarantee
def main() -> None:
    send_email("attacker@example.com", "hi")
"""
    with pytest.raises(ProverConfigError, match="must be called with a guarantee"):
        _prove(source)


def test_a_program_without_guarantees_is_still_provable() -> None:
    """The grammar constrains guarantees; it does not require one."""
    source = f"""\
{PREAMBLE}

def main() -> None:
    send_email("bob@example.com", "hi")
"""
    assert _prove(source).ok


def test_guarantee_on_a_non_target_function_is_checked_too() -> None:
    """The whole entry file is the claim surface, not only ``main``."""
    source = f"""\
{PREAMBLE}

@clauz3.guarantee("looks convincing")
def notify() -> None:
    send_email("attacker@example.com", "hi")

@clauz3.guarantee(emails.none())
def main() -> None:
    pass
"""
    with pytest.raises(ProverConfigError, match="is not a contract call"):
        _prove(source)


def test_approval_request_text_comes_from_the_same_checker() -> None:
    """``guarantee_texts`` reads exactly what the prover proved."""
    source = _program('emails.only(["bob@example.com"])')
    prove_text(source, import_paths=[EMAIL_ROOT], trusted_roots=[EMAIL_TRUSTED])
    assert guarantee_texts(source=source, target="main") == [
        "emails.only(['bob@example.com'])",
    ]


def test_approval_request_refuses_what_the_prover_refuses() -> None:
    with pytest.raises(GuaranteeError, match="is not a contract call"):
        guarantee_texts(source=_program("emails.only"), target="main")


def test_non_boolean_guarantee_is_refused_at_the_theorem_layer() -> None:
    """Defence in depth: the solver itself will not coerce a non-proposition.

    ``AgentTheorem`` is below ``prove_text``'s grammar gate, so this is the
    layer that catches an expression no syntactic rule anticipated. It used to
    reach ``ProxySort.m_bool``, which returns true for anything.
    """
    theorem = next(
        theorem
        for theorem in AgentTheorem.from_text(
            dedent("""
            import clauz3

            @clauz3.guarantee("anything at all")
            def main() -> None:
                pass
            """)
        )
        if theorem.name == "main"
    )
    proof = theorem.prove()

    assert proof.conclusion == Conclusion.SKIP
    assert proof.description == "unsupported guarantee expression"
    assert "guarantee is not a boolean expression" in str(proof.error)
