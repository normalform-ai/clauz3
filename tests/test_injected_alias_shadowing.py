"""Submitted source cannot rebind the imports clauz3 injects into a proof.

A ``required`` contract and an approval-policy clause are both enforced by
rewriting the proved copy of the program: an ``import <module> as <alias>`` is
added at the top and the obligation is stated as a guarantee through that
alias. The alias is only as good as its binding. A later import of another
module under the same name moved the obligation to that module's same-named
contract, so the one the trusted layer or the policy admin chose was never
proved. Names with the internal prefix are reserved, and both rewriters check
that what they injected still names the module they meant.
"""

import ast
from pathlib import Path
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.approvals.authority import ApprovedLayer
from clauz3.approvals.decisions import Decision
from clauz3.approvals.policy import (
    ApprovalPolicy,
    _conjoin,
    approval_policy_from_dict,
    evaluate_policy,
)
from clauz3.prover import (
    ProverConfigError,
    prove_path,
    prove_text,
    validate_untrusted_source,
)
from clauz3.reserved_names import (
    InjectedImports,
    ReservedNameError,
    reserved_names,
)


def _layers(tmp_path: Path, *, required: bool) -> tuple[Path, Path]:
    """Two domains whose contracts share a name and constrain different effects."""
    suffix = uuid4().hex
    strict = tmp_path / f"strict_{suffix}"
    strict.mkdir()
    (strict / "__init__.py").write_text("")
    (strict / "effects.py").write_text(
        "import clauz3\n\n@clauz3.trusted\ndef send(addr: str, msg: str) -> None: ...\n"
    )
    (strict / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n\n"
        "@contract\n"
        "def nothing_sent() -> ContractSpec:\n"
        '    return effect("send").empty()\n'
    )
    if required:
        (strict / "policy.py").write_text(
            "from clauz3.policy import domain_policy\n\n"
            'POLICY = domain_policy(when_used="send", required=["nothing_sent"])\n'
        )
    other = tmp_path / f"other_{suffix}"
    other.mkdir()
    (other / "__init__.py").write_text("")
    (other / "effects.py").write_text(
        "import clauz3\n\n@clauz3.trusted\ndef post(channel: str) -> None: ...\n"
    )
    (other / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n\n"
        "@contract\n"
        "def nothing_sent() -> ContractSpec:\n"
        '    return effect("post").empty()\n'
    )
    return strict, other


def _sends(strict: Path, *, preamble: str = "") -> str:
    return (
        f"{preamble}from {strict.name}.effects import send\n\n"
        'def main() -> None:\n    send("eve@example.com", "hi")\n'
    )


def _prove(source: str, strict: Path, other: Path) -> list[Conclusion]:
    results = prove_text(
        source,
        source_name="plan.py",
        trusted_roots=[strict, other],
        import_paths=[strict.parent],
    )
    return [result.proof.conclusion for result in results]


def test_required_contract_is_enforced_on_an_honest_program(tmp_path: Path) -> None:
    strict, other = _layers(tmp_path, required=True)

    assert _prove(_sends(strict), strict, other) == [Conclusion.FAIL]
    assert _prove("def main() -> None:\n    pass\n", strict, other) == [Conclusion.OK]


@pytest.mark.parametrize("alias", ["_clauz3_required_0", "_clauz3_required_7"])
def test_required_alias_cannot_be_rebound_to_another_module(
    tmp_path: Path, alias: str
) -> None:
    strict, other = _layers(tmp_path, required=True)
    source = _sends(strict, preamble=f"import {other.name}.contracts as {alias}\n")

    with pytest.raises(ProverConfigError, match=f"plan.py: .*{alias}"):
        _prove(source, strict, other)


def test_prove_path_refuses_the_reserved_prefix_before_loading_a_layer(
    tmp_path: Path,
) -> None:
    strict, other = _layers(tmp_path, required=True)
    entry = tmp_path / "plan.py"
    entry.write_text(
        _sends(strict, preamble=f"import {other.name}.contracts as _clauz3_pol_0\n")
    )

    with pytest.raises(ProverConfigError, match="_clauz3_pol_0.*reserved"):
        prove_path(entry, trusted_roots=[strict, other], import_roots=[tmp_path])


@pytest.mark.parametrize(
    "source",
    [
        "import json as _clauz3_x\ndef main() -> None: ...\n",
        "from json import loads as _clauz3_x\ndef main() -> None: ...\n",
        "import _clauz3_x.sub\ndef main() -> None: ...\n",
        "from _clauz3_x import name\ndef main() -> None: ...\n",
        "_clauz3_x = 1\ndef main() -> None: ...\n",
        "def _clauz3_x() -> None: ...\ndef main() -> None: ...\n",
        "class _clauz3_x: ...\ndef main() -> None: ...\n",
        "def main(_clauz3_x: int = 0) -> None: ...\n",
        "def main() -> None:\n    global _clauz3_x\n",
        "def main() -> None:\n    def inner() -> None:\n        nonlocal _clauz3_x\n",
        "def main() -> None:\n    import json as _clauz3_x\n",
        "import json\ndef main() -> None:\n    json._clauz3_x = 1\n",
        "def main() -> None:\n    print(_clauz3_x)\n",
        "def main() -> None:\n    print(value=1, _clauz3_x=2)\n",
        "def main() -> None:\n    try:\n        pass\n"
        "    except Exception as _clauz3_x:\n        pass\n",
    ],
    ids=[
        "import-as",
        "from-import-as",
        "dotted-import",
        "from-module",
        "assignment",
        "def",
        "class",
        "parameter",
        "global",
        "nonlocal",
        "local-import",
        "attribute-store",
        "use",
        "keyword",
        "except-as",
    ],
)
def test_every_spelling_of_a_reserved_name_is_refused(source: str) -> None:
    assert reserved_names(ast.parse(source)) == ["_clauz3_x"]
    with pytest.raises(ProverConfigError, match="plan.py: .*_clauz3_x.*reserved"):
        validate_untrusted_source(source=source, path=Path("plan.py"))


@pytest.mark.parametrize(
    "source",
    [
        'def main() -> None:\n    print("_clauz3_x")\n',
        "def main() -> None:\n    __clauz3_x = 1\n    my_clauz3_x = __clauz3_x\n",
        "import clauz3\ndef main() -> None:\n    clauz3_x = 1\n",
    ],
    ids=["string-literal", "other-prefixes", "plain-clauz3"],
)
def test_names_outside_the_reserved_prefix_are_untouched(source: str) -> None:
    assert reserved_names(ast.parse(source)) == []
    validate_untrusted_source(source=source, path=Path("plan.py"))


def _approval(strict: Path, other: Path) -> tuple[ApprovalPolicy, ApprovedLayer]:
    policy = approval_policy_from_dict(
        {
            "version": 1,
            "imports": {"pol": f"{strict.name}.contracts"},
            "rules": [
                {
                    "name": "auto-pass-silent",
                    "decision": "auto_approved",
                    "when_proven": ["pol.nothing_sent()"],
                }
            ],
        }
    )
    return policy, ApprovedLayer((strict, other), (strict.parent,))


def _request(program: str, layer: ApprovedLayer) -> dict[str, object]:
    return {
        "program": program,
        "target": "main",
        "trusted_roots": [str(path) for path in layer.trusted_roots],
        "import_roots": [str(path) for path in layer.import_roots],
    }


def test_approval_policy_decides_honest_programs_as_before(tmp_path: Path) -> None:
    strict, other = _layers(tmp_path, required=False)
    policy, layer = _approval(strict, other)

    silent = evaluate_policy(
        policy,
        _request("def main() -> None:\n    pass\n", layer),
        authorized_layers=[layer],
    )
    assert silent is not None
    assert silent.decision == Decision.AUTO_APPROVED
    assert (
        evaluate_policy(
            policy, _request(_sends(strict), layer), authorized_layers=[layer]
        )
        is None
    )


@pytest.mark.parametrize("alias", ["_clauz3_pol_0", "_clauz3_required_0"])
def test_policy_alias_cannot_be_rebound_into_an_automatic_approval(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], alias: str
) -> None:
    strict, other = _layers(tmp_path, required=False)
    policy, layer = _approval(strict, other)
    program = _sends(strict, preamble=f"import {other.name}.contracts as {alias}\n")

    decision = evaluate_policy(
        policy, _request(program, layer), authorized_layers=[layer]
    )

    # Not evaluable, so no rule fires and a human decides.
    assert decision is None
    assert f"{alias}" in capsys.readouterr().err


def test_conjoin_itself_refuses_a_program_that_names_its_aliases() -> None:
    program = "import json as _clauz3_pol_0\ndef main() -> None: ...\n"

    with pytest.raises(ReservedNameError, match="_clauz3_pol_0"):
        _conjoin(
            program,
            clauses=("pol.nothing_sent()",),
            imports=(("pol", "strict.contracts"),),
            target="main",
        )


def test_conjoin_binds_each_policy_alias_to_its_module() -> None:
    rendered = _conjoin(
        "def main() -> None: ...\n",
        clauses=("pol.nothing_sent()", "extra.bounded(1)"),
        imports=(("pol", "strict.contracts"), ("extra", "other.contracts")),
        target="main",
    )

    assert rendered.splitlines()[:5] == [
        "import clauz3",
        "import strict.contracts as _clauz3_pol_0",
        "import other.contracts as _clauz3_pol_1",
        "",
        "@clauz3.guarantee(_clauz3_pol_1.bounded(1))",
    ]


def test_injected_imports_are_checked_after_rewriting() -> None:
    """The backstop: a rebinding the first check missed is still refused."""
    tree = ast.parse("def main() -> None: ...\n")
    injected = InjectedImports(tree, kind="required")
    alias = injected.alias("strict.contracts")
    assert alias == injected.alias("strict.contracts") == "_clauz3_required_0"
    assert injected.alias("other.contracts") == "_clauz3_required_1"

    tree.body.append(ast.parse(f"import evil.contracts as {alias}").body[0])

    with pytest.raises(ReservedNameError, match="strict.contracts"):
        injected.render()


def test_injected_imports_render_the_aliases_they_allocated() -> None:
    tree = ast.parse("def main() -> None: ...\n")
    injected = InjectedImports(tree, kind="required")
    injected.alias("strict.contracts")

    assert injected.render().splitlines()[:2] == [
        "import clauz3",
        "import strict.contracts as _clauz3_required_0",
    ]
