"""Issue #129: a guarantee must be recognised by *what it names*, not how it is spelled.

``@clauz3.guarantee`` was matched against the literal source text
``{"clauz3.guarantee", "guarantee"}`` in three independent places — the prover
(``AgentTheorem.get_guarantees``), the approval request
(``runner._extract_guarantees``) and the interpreter
(``interp.interpreter.ALLOWED_DECORATORS``). Any other spelling of the same
decorator (``import clauz3 as c``; ``from clauz3 import guarantee as g``) was
dropped by the first two and refused by the third.

The two failure directions are different and both are tested here:

* **Fail open (the vulnerability).** ``clauz3 prove`` reported ``proved!`` for a
  violating program because the guarantee it was supposed to check had been
  silently discarded, and ``build_approval_request`` showed the operator
  ``guarantees: []`` for a file that plainly reads ``@c.guarantee(...)``.
* **Fail closed but wrong.** The interpreter refused the same file with
  "decorator @c.guarantee is not part of the proved subset", so prove and run
  disagreed about the very same source.

Every spelling below names exactly one object, ``clauz3.guarantee``, so all
three consumers must agree about all of them.
"""

import ast
import importlib
from pathlib import Path

import astroid
import pytest

from clauz3 import decorators
from clauz3._vendor import deal_solver
from clauz3.imports_policy import build_policy
from clauz3.prover import ProverConfigError, prove_text, validate_untrusted_source
from clauz3.runner import RunConfigError, _extract_guarantees, _preflight_structure

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
TRUSTED_ROOT = EMAIL_ROOT / "tools/email/trusted"


@pytest.fixture
def load_email_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Approval grammar checks require the layer's contracts to be registered."""
    monkeypatch.syspath_prepend(str(EMAIL_ROOT))
    importlib.import_module("tools.email.trusted.contracts")


#: ``(import statement, decorator spelling)`` — every pair names
#: ``clauz3.guarantee``.
SPELLINGS = [
    pytest.param("import clauz3", "clauz3.guarantee", id="import-clauz3"),
    pytest.param("import clauz3 as c", "c.guarantee", id="import-as"),
    pytest.param("from clauz3 import guarantee", "guarantee", id="from-import"),
    pytest.param("from clauz3 import guarantee as g", "g", id="from-import-as"),
]


def _program(imports: str, decorator: str, *, recipient: str) -> str:
    return (
        "from tools.email.trusted import contracts as emails\n"
        "from tools.email.trusted.effects import send_email\n"
        f"{imports}\n"
        "\n"
        f"@{decorator}(emails.only(['bob@example.com']))\n"
        "def main() -> None:\n"
        f"    send_email({recipient!r}, 'hi')\n"
    )


def _prove(source: str) -> list[deal_solver.Conclusion]:
    results = prove_text(
        source,
        trusted_roots=[TRUSTED_ROOT],
        import_paths=[EMAIL_ROOT],
        source_name="plan.py",
    )
    assert results, "no theorem was produced for main"
    return [result.proof.conclusion for result in results]


# ── the prover must honour the guarantee, however it is spelled ───────────────


@pytest.mark.parametrize(("imports", "decorator"), SPELLINGS)
def test_violating_program_fails_for_every_spelling(
    imports: str, decorator: str
) -> None:
    source = _program(imports, decorator, recipient="eve@example.com")
    assert _prove(source) == [deal_solver.Conclusion.FAIL]


@pytest.mark.parametrize(("imports", "decorator"), SPELLINGS)
def test_satisfying_program_proves_for_every_spelling(
    imports: str, decorator: str
) -> None:
    source = _program(imports, decorator, recipient="bob@example.com")
    assert _prove(source) == [deal_solver.Conclusion.OK]


# ── the approval request must show the guarantee, however it is spelled ───────


@pytest.mark.parametrize(("imports", "decorator"), SPELLINGS)
def test_approval_request_shows_the_guarantee(
    imports: str, decorator: str, load_email_contracts: None
) -> None:
    source = _program(imports, decorator, recipient="bob@example.com")
    assert _extract_guarantees(
        source=source, target="main", source_name="<string>"
    ) == ["emails.only(['bob@example.com'])"]


# ── the interpreter must run what the prover proved ───────────────────────────


@pytest.mark.parametrize(("imports", "decorator"), SPELLINGS)
def test_run_preflight_accepts_every_spelling(imports: str, decorator: str) -> None:
    source = _program(imports, decorator, recipient="bob@example.com")
    _preflight_structure(
        source=source,
        source_name="plan.py",
        target="main",
        policy=build_policy(
            trusted_roots=[TRUSTED_ROOT],
            import_roots=[EMAIL_ROOT],
            extra_allowed=(),
        ),
        import_roots=[EMAIL_ROOT],
    )


@pytest.mark.parametrize(("imports", "decorator"), SPELLINGS)
def test_prove_and_run_agree_on_the_guarantee_set(
    imports: str, decorator: str, load_email_contracts: None
) -> None:
    """The three consumers resolve the same decorator to the same answer."""

    source = _program(imports, decorator, recipient="bob@example.com")
    module = astroid.parse(source)
    func = next(
        node
        for node in module.body
        if isinstance(node, astroid.FunctionDef) and node.name == "main"
    )
    from clauz3.prover import AgentTheorem

    proved = [node.as_string() for node in AgentTheorem.get_guarantees(func)]
    approved = _extract_guarantees(source=source, target="main", source_name="<string>")
    assert proved == approved
    assert len(proved) == 1


# ── fail closed: a decorator that is not a guarantee is refused, not dropped ──


def _check(source: str) -> None:
    validate_untrusted_source(source=source, path=Path("plan.py"))


@pytest.mark.parametrize(("imports", "decorator"), SPELLINGS)
def test_validate_accepts_every_guarantee_spelling(
    imports: str, decorator: str
) -> None:
    _check(f"{imports}\n@{decorator}(None)\ndef main() -> None:\n    pass\n")


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        pytest.param(
            "@guarantee(None)\ndef main() -> None:\n    pass\n",
            "bare name with no import binding it",
            id="unbound-guarantee",
        ),
        pytest.param(
            "import clauz3\n@clauz3.guarantie(None)\ndef main() -> None:\n    pass\n",
            "typo in the attribute",
            id="typo",
        ),
        pytest.param(
            "import json as clauz3\n"
            "@clauz3.guarantee(None)\n"
            "def main() -> None:\n"
            "    pass\n",
            "the head name is rebound to another module",
            id="shadowed-package",
        ),
        pytest.param(
            "def mydec(f: object) -> object:\n"
            "    return f\n"
            "@mydec\n"
            "def main() -> None:\n"
            "    pass\n",
            "locally defined decorator the interpreter would never apply",
            id="local-decorator",
        ),
        pytest.param(
            "import functools\n@functools.cache\ndef main() -> None:\n    pass\n",
            "an importable but unmodelled decorator",
            id="functools-cache",
        ),
    ],
)
def test_unrecognised_decorator_is_refused(source: str, reason: str) -> None:
    with pytest.raises(ProverConfigError, match="not a clauz3 guarantee"):
        _check(source)
    assert reason  # documented in the parameter id


def test_shadowed_package_is_refused_rather_than_silently_ignored() -> None:
    """The fail-open half of the bug, pinned from the other side.

    ``import json as clauz3`` makes ``@clauz3.guarantee(...)`` name something
    that is not the clauz3 decorator. Matching the source text accepted it;
    resolving the target rejects it. Neither answer may be "ignore it".
    """
    source = (
        "import json as clauz3\n"
        "@clauz3.guarantee(None)\n"
        "def main() -> None:\n"
        "    pass\n"
    )
    with pytest.raises(ProverConfigError):
        _check(source)
    assert (
        _extract_guarantees(source=source, target="main", source_name="<string>") == []
    )


def test_decorated_class_is_refused() -> None:
    """The interpreter checks class decorators too, so the validator must."""
    with pytest.raises(ProverConfigError, match="not a clauz3 guarantee"):
        _check("import dataclasses\n@dataclasses.dataclass\nclass Point:\n    x: int\n")


def test_run_preflight_refuses_an_unrecognised_decorator() -> None:
    source = (
        "import functools\n@functools.cache\ndef main() -> None:\n    return None\n"
    )
    with pytest.raises(RunConfigError):
        _preflight_structure(
            source=source,
            source_name="plan.py",
            target="main",
            policy=build_policy(
                trusted_roots=[TRUSTED_ROOT],
                import_roots=[EMAIL_ROOT],
                extra_allowed=(),
            ),
            import_roots=[EMAIL_ROOT],
        )


# ── the resolver itself ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "dotted", "expected"),
    [
        ("import clauz3", "clauz3.guarantee", "clauz3.guarantee"),
        ("import clauz3 as c", "c.guarantee", "clauz3.guarantee"),
        ("from clauz3 import guarantee as g", "g", "clauz3.guarantee"),
        ("import deal as d", "d.has", "deal.has"),
        ("import json as clauz3", "clauz3.guarantee", "json.guarantee"),
        # No binding: an unresolved head is returned unchanged, so a fully
        # qualified spelling still matches. ``_inject_required_guarantees`` and
        # ``approval_policy._conjoin`` emit exactly that.
        ("", "clauz3.guarantee", "clauz3.guarantee"),
        ("", "g", "g"),
    ],
)
def test_canonical_target(source: str, dotted: str, expected: str) -> None:
    aliases = decorators.import_aliases(ast.parse(source))
    assert decorators.canonical_target(dotted, aliases) == expected


def test_import_aliases_agree_between_ast_and_astroid() -> None:
    """The prover reads astroid, the approval request reads ast.

    They must derive the same alias map or the two can disagree again.
    """
    source = (
        "import clauz3 as c\n"
        "import deal\n"
        "from clauz3 import guarantee as g\n"
        "from tools.email.trusted import contracts as emails\n"
    )
    assert decorators.import_aliases(ast.parse(source)) == decorators.import_aliases(
        astroid.parse(source)
    )
