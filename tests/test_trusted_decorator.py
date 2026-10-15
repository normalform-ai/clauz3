"""The trusted-effect boundary: ``@clauz3.trusted``, and the hole it closes.

Which functions are trusted effects used to be decided by matching the literal
source text ``deal.has`` (deal's own extractor). The discriminator throughout
this module is a ``none()`` guarantee over a program that *does* call the
effect: if the call was recorded as a fact the proof must be refuted, and if
the boundary went unrecognised the body (a ``pass``) is inlined instead, no
fact exists, and the guarantee holds vacuously --- reporting ``proved!`` for a
program that emails whoever it likes.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.cli import main
from clauz3.decorators import import_aliases, is_trusted_effect
from clauz3.effects import trusted
from clauz3.lint import LintError, lint_file
from clauz3.policy import load_trusted_effects
from clauz3.prover import ProverConfigError, prove_text, validate_untrusted_source

CONTRACTS = '''
"""Relations name the trusted function; nothing else addresses an effect."""

from clauz3.spec import ContractSpec, contract, effect


@contract
def none() -> ContractSpec:
    """No email is sent."""
    return effect("send_email").empty()


@contract
def silent() -> ContractSpec:
    """The program has no effects at all."""
    return effect().empty()
'''

PROGRAM = """
import clauz3
from {pkg} import contracts
from {pkg}.effects import send_email


@clauz3.guarantee(contracts.{guarantee}())
def main() -> None:
    send_email("bob@example.com", "hi")
"""


def _prove_with_effects(
    tmp_path: Path,
    effects: str,
    *,
    guarantee: str = "none",
    extra: dict[str, str] | None = None,
) -> Conclusion:
    """Prove a guarantee against a one-effect layer spelled however ``effects`` says.

    Returns ``FAIL`` when the decorator made ``send_email`` a boundary and
    ``OK`` when it did not (the vacuous proof).
    """

    pkg = tmp_path.name.replace("-", "_")
    package = tmp_path / pkg
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "contracts.py").write_text(CONTRACTS)
    (package / "effects.py").write_text(effects)
    for name, text in (extra or {}).items():
        (tmp_path / name).write_text(text)
    results = prove_text(
        PROGRAM.format(pkg=pkg, guarantee=guarantee),
        trusted_roots=[package],
        import_paths=[tmp_path],
        source_name="<program>",
    )
    assert len(results) == 1
    return results[0].proof.conclusion


def _effects(imports: str, decorator: str) -> str:
    """A one-effect trusted module: the body is a mock the prover never runs."""

    return (
        f"{imports}\n\n\n{decorator}\n"
        "def send_email(addr: str, msg: str) -> None:\n"
        "    pass\n"
    )


# ── the boundary is recognised, however it is spelled ────────────────────────


def test_trusted_is_a_boundary(tmp_path: Path) -> None:
    conclusion = _prove_with_effects(
        tmp_path, _effects("import clauz3", "@clauz3.trusted")
    )
    assert conclusion == Conclusion.FAIL


def test_trusted_called_empty_is_a_boundary(tmp_path: Path) -> None:
    """``@clauz3.trusted()`` is the same declaration; the parens are habit."""

    conclusion = _prove_with_effects(
        tmp_path, _effects("import clauz3", "@clauz3.trusted()")
    )
    assert conclusion == Conclusion.FAIL


def test_trusted_under_module_alias_is_a_boundary(tmp_path: Path) -> None:
    conclusion = _prove_with_effects(
        tmp_path, _effects("import clauz3 as c", "@c.trusted")
    )
    assert conclusion == Conclusion.FAIL


def test_trusted_from_import_is_a_boundary(tmp_path: Path) -> None:
    conclusion = _prove_with_effects(
        tmp_path, _effects("from clauz3 import trusted as t", "@t")
    )
    assert conclusion == Conclusion.FAIL


def test_legacy_deal_has_is_still_a_boundary(tmp_path: Path) -> None:
    conclusion = _prove_with_effects(
        tmp_path, _effects("import deal", '@deal.has("io")')
    )
    assert conclusion == Conclusion.FAIL


def test_aliased_deal_has_is_a_boundary(tmp_path: Path) -> None:
    """The hole: deal's extractor matches the text ``deal.has`` and nothing else.

    A trusted layer written as ``import deal as d`` got a function deal
    enforces at runtime and ``clauz3 lint`` checks, which the prover silently
    inlined --- so this program proved, and sent an email to whoever it liked.
    """

    conclusion = _prove_with_effects(
        tmp_path, _effects("import deal as d", '@d.has("io")')
    )
    assert conclusion == Conclusion.FAIL


@pytest.mark.parametrize(
    ("imports", "decorator"),
    [
        pytest.param(
            "import clauz3\nimport deal",
            "@deal.chain(clauz3.trusted)",
            id="bare-chain",
        ),
        pytest.param(
            "import clauz3\nimport deal",
            "@deal.chain(clauz3.trusted, deal.pre(lambda addr, msg: True))",
            id="bundled-with-a-precondition",
        ),
        pytest.param(
            "import clauz3 as c\nimport deal as d",
            "@d.chain(c.trusted)",
            id="aliased-chain",
        ),
    ],
)
def test_trusted_inside_a_chain_is_a_boundary(
    tmp_path: Path, imports: str, decorator: str
) -> None:
    """``deal.chain`` bundles decorators, and the boundary may be one of them.

    The declaration is the same wherever it is written, but the prover used to
    test for it only at the top level while the policy scanner unfolded the
    chain. The two then disagreed in the fail-open direction: the scanner
    listed ``send_email`` as an effect --- so a relation naming it resolved ---
    while the prover inlined the body, recorded no fact, and proved the
    guarantee vacuously. Nothing here carries a separate outer
    ``@clauz3.trusted``, which is what makes the chain the only declaration.
    """

    conclusion = _prove_with_effects(tmp_path, _effects(imports, decorator))
    assert conclusion == Conclusion.FAIL


@pytest.mark.parametrize(
    ("imports", "decorator"),
    [
        pytest.param("import deal", '@deal.chain(deal.has("io"))', id="chain"),
        pytest.param("import deal as d", '@d.chain(d.has("io"))', id="aliased-chain"),
        pytest.param(
            "import deal as d",
            '@d.chain(d.has("io"), d.pre(lambda addr, msg: True))',
            id="bundled-with-a-precondition",
        ),
    ],
)
def test_a_chained_has_is_a_boundary(
    tmp_path: Path, imports: str, decorator: str
) -> None:
    """The legacy boundary inside a bundle, with nothing else to decide it.

    ``policy.load_trusted_effects`` unfolds a chain, so ``send_email`` is
    listed as an effect and a relation naming it resolves; a prover that did
    not unfold it would inline the body, record no fact, and let the guarantee
    hold over nothing. The precondition in the third case is satisfied by
    every call, so only the boundary can decide the conclusion.
    """

    conclusion = _prove_with_effects(tmp_path, _effects(imports, decorator))
    assert conclusion == Conclusion.FAIL


# ── and refused when the name resolves elsewhere ─────────────────────────────


def test_shadowed_clauz3_is_not_assumed_to_be_inert(tmp_path: Path) -> None:
    """``import elsewhere as clauz3`` spells a decorator that is not ours.

    It must not be mistaken for our boundary, nor silently ignored: a custom
    decorator could re-export ``clauz3.trusted`` or change the function's
    behavior. Its undecorated body alone is not enough to prove the call.
    """

    # ``silent`` is ``effect().empty()`` -- every trusted call, named or not --
    # so it would prove if this decorator were ignored. A relation *naming*
    # send_email would be refused before the proof, since the layer has no such
    # effect; this asks the prover the question directly.
    conclusion = _prove_with_effects(
        tmp_path,
        _effects("import elsewhere as clauz3", "@clauz3.trusted"),
        guarantee="silent",
        extra={"elsewhere.py": "def trusted(func):\n    return func\n"},
    )
    assert conclusion == Conclusion.SKIP


def test_bare_deal_has_is_not_a_boundary(tmp_path: Path) -> None:
    """``@deal.has()`` asserts the function is free of side effects.

    Turning that into a boundary would skip the body of a function whose whole
    claim is that its body is safe to run.
    """

    conclusion = _prove_with_effects(
        tmp_path, _effects("import deal", "@deal.has()"), guarantee="silent"
    )
    assert conclusion == Conclusion.OK


# ── an alias is a module-level name ──────────────────────────────────────────

LOCAL_IMPORT = """def unused_helper() -> None:
    import calendar as c


@c.trusted"""


def test_a_local_import_does_not_shadow_the_alias(tmp_path: Path) -> None:
    """An import inside a function binds a local, not a module attribute.

    Aliases were collected with a plain walk, so the ``import calendar as c``
    in a helper nobody calls decided what ``@c.trusted`` meant. The boundary
    went unrecognised, the body was inlined, no fact was recorded, and
    ``effect().empty()`` held vacuously for a program that sends email.
    """

    conclusion = _prove_with_effects(
        tmp_path,
        _effects("import clauz3 as c", LOCAL_IMPORT),
        guarantee="silent",
    )
    assert conclusion == Conclusion.FAIL


def test_an_import_under_control_flow_still_binds(tmp_path: Path) -> None:
    """``if``/``try``/``with`` do not open a scope, so their imports count."""

    guarded = "try:\n    import clauz3 as c\nexcept ImportError:\n    raise"
    conclusion = _prove_with_effects(
        tmp_path,
        _effects(guarded, "@c.trusted"),
        guarantee="silent",
    )
    assert conclusion == Conclusion.FAIL


# ── the decorator takes no arguments ─────────────────────────────────────────


def test_trusted_rejects_markers() -> None:
    """Markers are deal's vocabulary; clauz3 addresses the function by name."""

    with pytest.raises(TypeError, match="takes no arguments"):
        trusted("read")  # type: ignore[call-overload]


def test_trusted_is_inert_at_runtime() -> None:
    def send(addr: str) -> str:
        return addr

    assert trusted(send) is send
    assert trusted()(send) is send


# ── a relation over every effect ─────────────────────────────────────────────


def test_bare_effect_relation_covers_every_trusted_call(tmp_path: Path) -> None:
    """``effect()`` is what ``effect("trusted")`` used to be, without the string."""

    conclusion = _prove_with_effects(
        tmp_path,
        _effects("import clauz3", "@clauz3.trusted"),
        guarantee="silent",
    )
    assert conclusion == Conclusion.FAIL


# ── agent-authored source may not declare effects ────────────────────────────


@pytest.mark.parametrize(
    "source",
    [
        "import clauz3\n@clauz3.trusted\ndef f() -> None: ...\n",
        "import clauz3\n@clauz3.trusted()\ndef f() -> None: ...\n",
        "import clauz3 as c\n@c.trusted\ndef f() -> None: ...\n",
        "from clauz3 import trusted as t\n@t\ndef f() -> None: ...\n",
        "from clauz3.effects import trusted\n@trusted\ndef f() -> None: ...\n",
    ],
)
def test_trusted_is_refused_in_agent_source(source: str) -> None:
    with pytest.raises(ProverConfigError, match="only allowed in trusted roots"):
        validate_untrusted_source(source=source, path=Path("plan.py"))


# ── effect names reach the policy layer ──────────────────────────────────────


def test_effect_signatures_are_loaded_for_when_used(tmp_path: Path) -> None:
    """Names for ``when_used``; parameters for the fields a contract may read."""

    trusted_root = tmp_path / "trusted"
    trusted_root.mkdir()
    (trusted_root / "effects.py").write_text(
        textwrap.dedent(
            """
            import clauz3
            import deal


            @clauz3.trusted
            def read_file(path: str) -> str:
                return ""


            @deal.has("io")
            def legacy_write(path: str) -> None:
                pass


            @deal.has()
            def pure_helper(path: str) -> str:
                return path


            def helper(path: str) -> str:
                return path
            """
        )
    )
    assert load_trusted_effects([trusted_root]) == {
        "read_file": ("path",),
        "legacy_write": ("path",),
    }


def test_is_trusted_effect_reads_both_spellings() -> None:
    tree = ast.parse(
        "import clauz3\n"
        "import deal\n"
        "@clauz3.trusted\n"
        "def a() -> None: ...\n"
        '@deal.has("io")\n'
        "def b() -> None: ...\n"
        "@deal.has()\n"
        "def c() -> None: ...\n"
        "def d() -> None: ...\n"
    )
    aliases = import_aliases(tree)
    found = {
        node.name: is_trusted_effect(node, aliases)
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    assert found == {"a": True, "b": True, "c": False, "d": False}


# ── clauz3 lint runs deal's marker check ─────────────────────────────────────


SUBPROCESS_EFFECT = '''
import subprocess

import clauz3
import deal


@deal.has({markers})
@clauz3.trusted
def run(path: str) -> str:
    """Shell out, which deal's linter reads as a syscall."""
    return subprocess.run([path], capture_output=True, text=True).stdout
'''


def test_lint_reports_a_marker_the_body_needs(tmp_path: Path) -> None:
    path = tmp_path / "effects.py"
    path.write_text(SUBPROCESS_EFFECT.format(markers='"io"'))
    violations = lint_file(path)
    assert [(v.code, v.value) for v in violations] == [("DEL050", "syscall")]
    assert "subprocess.run" in violations[0].content, "row must point into the body"


def test_lint_is_quiet_when_markers_are_honest(tmp_path: Path) -> None:
    path = tmp_path / "effects.py"
    path.write_text(SUBPROCESS_EFFECT.format(markers='"syscall", "io"'))
    assert lint_file(path) == []


def test_lint_ignores_an_effect_that_opts_out_of_deal(tmp_path: Path) -> None:
    """``@clauz3.trusted`` alone declares no markers, so there is nothing to check.

    deal's linter only checks a function carrying a deal contract. A layer that
    wants its bodies checked adds ``@deal.has(...)``; that is the opt-in, and
    the reason this is a warning in the docs rather than an error here.
    """

    path = tmp_path / "effects.py"
    path.write_text(
        SUBPROCESS_EFFECT.format(markers='"io"')
        .replace('@deal.has("io")\n', "")
        .replace("import deal\n", "")
    )
    assert lint_file(path) == []


BAD_POSTCONDITION = """
import clauz3


{condition}
def count() -> int:
    return -1
"""


@pytest.mark.parametrize(
    "condition",
    [
        "@clauz3.ensures(lambda result: result > 0)",
        pytest.param(
            "@clauz3.conditions.ensures(lambda result: result > 0)",
            id="through-the-module",
        ),
        pytest.param("@deal.post(lambda result: result > 0)", id="deals-own"),
        pytest.param("@d.post(lambda result: result > 0)", id="aliased-deal"),
        pytest.param(
            "@d.chain(d.post(lambda result: result > 0))",
            id="inside-an-aliased-chain",
        ),
    ],
)
def test_lint_reports_a_postcondition_the_body_violates(
    tmp_path: Path, condition: str
) -> None:
    """``DEL012``: the body returns -1 under a contract saying it is positive.

    deal's linter matches the text ``deal.post``, so every spelling above but
    one was invisible to it --- a layer that moved to ``@clauz3.ensures`` kept
    the check in the prover and silently lost it here.
    """

    path = tmp_path / "effects.py"
    path.write_text(
        BAD_POSTCONDITION.format(condition=condition).replace(
            "import clauz3",
            "import clauz3\nimport clauz3.conditions\nimport deal\nimport deal as d",
        )
    )
    violations = lint_file(path)
    assert [(v.code, v.row) for v in violations] == [("DEL012", 10)]
    assert "return -1" in violations[0].content, "the row is the author's own"


def test_lint_reports_a_raises_contract_the_body_violates(tmp_path: Path) -> None:
    """``DEL021``, reached through an aliased spelling deal could not read."""

    path = tmp_path / "effects.py"
    path.write_text(
        "import deal as d\n"
        "\n"
        "\n"
        "@d.raises()\n"
        "def f() -> int:\n"
        '    raise ValueError("x")\n'
    )
    assert [(v.code, v.row) for v in lint_file(path)] == [("DEL021", 6)]


FORBIDDEN_CALL = """
import clauz3
import deal


{condition}
def positive(n: int) -> int:
    return n


@deal.has()
def caller() -> int:
    return positive(-1)
"""


@pytest.mark.parametrize(
    "condition",
    [
        "@clauz3.requires(lambda n: n > 0)",
        pytest.param(
            "@clauz3.requires(predicate=lambda n: n > 0)",
            id="predicate-by-keyword",
        ),
        pytest.param(
            '@clauz3.requires(message="too small", predicate=lambda n: n > 0)',
            id="after-another-keyword",
        ),
        pytest.param("@deal.pre(validator=lambda n: n > 0)", id="deals-own-keyword"),
    ],
)
def test_lint_reports_a_call_the_precondition_forbids(
    tmp_path: Path, condition: str
) -> None:
    """``DEL011``, which is the check that needs deal to resolve the *callee*.

    The contract is on ``positive`` and the violation is in ``caller``, so
    nothing local to either function shows it --- deal reads this one through
    astroid, which it only uses when it is handed a real filename. A rewritten
    tree cannot carry it, which is why the second pass lints a copy of the file
    rather than an AST.

    The keyword spellings are here because the rewrite renames the predicate to
    deal's own parameter name: left as ``predicate=``, the contract reached
    deal's linter with nothing it could read and the check was silently lost.
    """

    path = tmp_path / "effects.py"
    path.write_text(FORBIDDEN_CALL.format(condition=condition))
    violations = lint_file(path)
    assert [(v.code, v.value, v.row) for v in violations] == [("DEL011", "-1", 13)]
    assert "positive(-1)" in violations[0].content


def test_lint_reports_a_keyword_postcondition_the_body_violates(
    tmp_path: Path,
) -> None:
    """The same rename, on the other contract: ``DEL012`` for a body of ``-1``."""

    path = tmp_path / "effects.py"
    path.write_text(
        "import clauz3\n"
        "\n"
        "\n"
        "@clauz3.ensures(predicate=lambda result: result > 0)\n"
        "@clauz3.trusted\n"
        "def positive() -> int:\n"
        "    return -1\n"
    )
    assert [(v.code, v.row) for v in lint_file(path)] == [("DEL012", 7)]


def test_lint_reads_an_aliased_marker(tmp_path: Path) -> None:
    """The alias hole again: ``@d.has("io")`` is a marker deal never checked.

    The marker check is the one that reads the *body* --- it is deal inferring
    that ``subprocess.run`` is a syscall --- so this is the spelling that says
    whether the second pass keeps that inference.
    """

    path = tmp_path / "effects.py"
    source = (
        SUBPROCESS_EFFECT.format(markers='"io"')
        .replace("import deal", "import deal as d")
        .replace("@deal.has", "@d.has")
    )
    assert "@d.has" in source and "@deal.has" not in source, "the alias is the point"
    path.write_text(source)
    assert [(v.code, v.value) for v in lint_file(path)] == [("DEL050", "syscall")]


def test_lint_refuses_a_file_it_cannot_parse(tmp_path: Path) -> None:
    path = tmp_path / "effects.py"
    path.write_text("def f(:\n")
    with pytest.raises(LintError):
        lint_file(path)


def test_lint_command_exit_codes(tmp_path: Path) -> None:
    clean = tmp_path / "clean.py"
    clean.write_text(SUBPROCESS_EFFECT.format(markers='"syscall", "io"'))
    dirty = tmp_path / "dirty.py"
    dirty.write_text(SUBPROCESS_EFFECT.format(markers='"io"'))

    assert main(["lint", str(clean)]) == 0
    assert main(["lint", str(dirty)]) == 1
    assert main(["lint", str(tmp_path)]) == 1, "a directory is walked"
