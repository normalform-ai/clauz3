"""Unmodelled decorators must not erase effects from a successful proof.

The wildcard relation is deliberate: naming the unresolved effect would let
policy validation hide the silent omission that issue #177 reported.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import astroid
import pytest

from clauz3._vendor.deal_solver import Conclusion, UnsupportedError
from clauz3.prover import AgentTheorem, prove_text
from clauz3.runner import RunProofError, run_source


def _program(
    tmp_path: Path, imports: str, decorators: str, *, call_suffix: str = ""
) -> tuple[str, Path]:
    # A unique dotted name keeps Python and astroid caches from selecting a
    # different parametrized case's trusted module.
    package_name = f"trusted_alias_{uuid4().hex}"
    package = tmp_path / package_name
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "_compat.py").write_text(
        "from clauz3 import trusted\n\ndef identity(func):\n    return func\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n"
        "    return effect().empty()\n"
    )
    receipt = (
        "class Receipt:\n"
        "    ok = True\n\n"
        "    def is_ok(self) -> bool:\n"
        "        return True\n\n"
        if call_suffix
        else ""
    )
    function = (
        "def send_email(address: str) -> Receipt:\n    return Receipt()\n"
        if call_suffix
        else "def send_email(address: str) -> None:\n    pass\n"
    )
    (package / "effects.py").write_text(
        f"{imports.format(package=package_name)}\n\n{receipt}{decorators}\n{function}"
    )
    entry_body = (
        f"    if send_email('bob@example.com'){call_suffix}:\n        pass\n"
        if call_suffix
        else "    send_email('bob@example.com')\n"
    )
    source = (
        "import clauz3\n"
        f"from {package_name}.contracts import silent\n"
        f"from {package_name}.effects import send_email\n\n"
        "@clauz3.guarantee(silent())\n"
        "def main() -> None:\n"
        f"{entry_body}"
    )
    return source, package


@pytest.mark.parametrize(
    ("imports", "decorators"),
    [
        pytest.param("import clauz3", "@clauz3.trusted", id="native"),
        pytest.param("import clauz3", "@clauz3.trusted()", id="native-call"),
        pytest.param("import clauz3 as c", "@c.trusted", id="module-alias"),
        pytest.param("from clauz3 import trusted as t", "@t", id="import-alias"),
        pytest.param(
            "from clauz3.effects import trusted", "@trusted", id="effects-import"
        ),
        pytest.param("import deal", '@deal.has("io")', id="legacy"),
        pytest.param("import deal as d", '@d.has("io")', id="legacy-alias"),
        pytest.param("import deal", '@deal.chain(deal.has("io"))', id="legacy-chain"),
        pytest.param(
            "import clauz3 as c\nimport deal as d",
            "@d.chain(d.chain(c.trusted))",
            id="nested-native-chain",
        ),
    ],
)
def test_supported_decorators_keep_effects(
    tmp_path: Path, imports: str, decorators: str
) -> None:
    source, trusted_root = _program(tmp_path, imports, decorators)
    results = prove_text(
        source,
        trusted_roots=[trusted_root],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.FAIL]


@pytest.mark.parametrize(
    ("imports", "decorators"),
    [
        pytest.param(
            "from ._compat import trusted", "@trusted", id="relative-reexport"
        ),
        pytest.param(
            "from ._compat import trusted", "@trusted()", id="relative-reexport-call"
        ),
        pytest.param(
            "from {package}._compat import trusted",
            "@trusted",
            id="absolute-reexport",
        ),
        pytest.param(
            "from {package}._compat import trusted",
            "@trusted()",
            id="absolute-reexport-call",
        ),
        pytest.param(
            "import clauz3\nTRUSTED = clauz3.trusted",
            "@TRUSTED",
            id="assignment-alias",
        ),
        pytest.param(
            "import clauz3\nTRUSTED = clauz3.trusted",
            "@TRUSTED()",
            id="assignment-alias-call",
        ),
        pytest.param(
            "from clauz3 import guarantee as boundary\n"
            "import clauz3\n"
            "boundary = clauz3.trusted",
            "@boundary()",
            id="assignment-shadows-supported-import",
        ),
        pytest.param(
            "import deal as d\nfrom ._compat import trusted as t",
            "@d.chain(d.chain(t))",
            id="nested-chain-reexport",
        ),
        pytest.param(
            "import clauz3\nfrom ._compat import identity",
            "@identity\n@clauz3.trusted",
            id="unknown-outside-boundary",
        ),
        pytest.param(
            "import clauz3\nfrom ._compat import identity",
            "@clauz3.trusted\n@identity",
            id="unknown-inside-boundary",
        ),
        pytest.param("import deal", "@deal.chain", id="bare-chain"),
        pytest.param("import deal", "@deal.has", id="bare-has"),
        pytest.param("from functools import cache", "@cache", id="stdlib-cache"),
        pytest.param("from typing import overload", "@overload", id="stdlib-overload"),
    ],
)
def test_unresolved_decorators_refuse_the_proof(
    tmp_path: Path, imports: str, decorators: str
) -> None:
    source, trusted_root = _program(tmp_path, imports, decorators)
    results = prove_text(
        source,
        trusted_roots=[trusted_root],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "unsupported decorator" in str(results[0].proof).lower()


@pytest.mark.parametrize("call_suffix", [".ok", ".is_ok()"])
def test_receiver_inference_preserves_decorator_refusal(
    tmp_path: Path, call_suffix: str
) -> None:
    source, trusted_root = _program(
        tmp_path, "from ._compat import trusted", "@trusted", call_suffix=call_suffix
    )
    results = prove_text(
        source,
        trusted_roots=[trusted_root],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "unsupported decorator" in str(results[0].proof).lower()


@pytest.mark.parametrize(
    ("condition", "call_suffix"),
    [
        pytest.param("@clauz3.requires(allowed)", ".ok", id="native-attribute"),
        pytest.param("@deal.pre(allowed)", ".ok", id="legacy-attribute"),
        pytest.param("@clauz3.requires(allowed)", ".is_ok()", id="native-method"),
        pytest.param(
            "@clauz3.trusted\n@clauz3.requires(allowed)",
            ".ok",
            id="trusted-with-unreadable-condition",
        ),
    ],
)
def test_receiver_inference_preserves_predicate_refusal(
    tmp_path: Path, condition: str, call_suffix: str
) -> None:
    source, trusted_root = _program(
        tmp_path,
        "import clauz3\nimport deal\n\n"
        "def allowed(address: str) -> bool:\n    return False\n",
        condition,
        call_suffix=call_suffix,
    )
    results = prove_text(
        source,
        trusted_roots=[trusted_root],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "predicate the prover cannot read" in str(results[0].proof).lower()


@pytest.mark.parametrize("call_suffix", ["", ".ok", ".is_ok()"])
def test_refused_decorator_never_reaches_approval_or_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, call_suffix: str
) -> None:
    source, trusted_root = _program(
        tmp_path, "from ._compat import trusted", "@trusted", call_suffix=call_suffix
    )
    approve = Mock(side_effect=AssertionError("refused proof reached approval"))
    execute = Mock(side_effect=AssertionError("refused proof reached execution"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", execute)

    with pytest.raises(RunProofError) as exc_info:
        run_source(
            source,
            source_name="plan.py",
            trusted_roots=[trusted_root],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )

    assert [proof.proof.conclusion for proof in exc_info.value.proofs] == [
        Conclusion.SKIP
    ]
    assert "unsupported decorator" in str(exc_info.value.proofs[0].proof).lower()
    approve.assert_not_called()
    execute.assert_not_called()


def test_later_import_cannot_hide_an_earlier_effect_declaration(tmp_path: Path) -> None:
    source, trusted_root = _program(
        tmp_path, "from deal import has as boundary", '@boundary("io")'
    )
    effects = trusted_root / "effects.py"
    effects.write_text(
        effects.read_text() + "\nfrom clauz3 import guarantee as boundary\n"
    )

    results = prove_text(
        source,
        trusted_roots=[trusted_root],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "unsupported decorator" in str(results[0].proof).lower()


def test_empty_chain_preserves_an_undecorated_function(tmp_path: Path) -> None:
    source, trusted_root = _program(tmp_path, "import deal", "@deal.chain()")
    results = prove_text(
        source,
        trusted_roots=[trusted_root],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


def test_chain_keywords_are_refused_by_declaration_reader() -> None:
    # Invalid keyword arguments prevent importing the trusted module, so check
    # the reader directly to ensure it cannot silently ignore that syntax.
    module = astroid.parse(
        "import deal\n@deal.chain(contracts=())\ndef send_email(): pass\n"
    )
    func = module.body[1]
    assert isinstance(func, astroid.FunctionDef)
    with pytest.raises(UnsupportedError, match="unsupported decorator"):
        list(AgentTheorem.get_contracts(func))


@pytest.mark.parametrize("decorator", ["clauz3.guarantee", "clauz3.fluent.effect"])
@pytest.mark.parametrize("arguments", ["", "()", "(True, False)", "(value=True)"])
def test_malformed_identity_decorators_are_refused_by_declaration_reader(
    decorator: str, arguments: str
) -> None:
    # Most of these forms cannot be imported, so exercise the declaration
    # reader without invoking either decorator's runtime implementation.
    module = astroid.parse(
        f"import clauz3\n@{decorator}{arguments}\ndef send_email(): pass\n"
    )
    func = module.body[1]
    assert isinstance(func, astroid.FunctionDef)
    with pytest.raises(UnsupportedError, match="unsupported decorator"):
        list(AgentTheorem.get_contracts(func))
