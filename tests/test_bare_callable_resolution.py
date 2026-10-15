"""A bare import must not select a model while hiding another runtime binding."""

import importlib
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source

_SELECTIONS = ["conditional-import", "unknown-candidate"]
_CALL_NAMES = ["match", "selected"]


def _layer(tmp_path: Path, selection: str) -> Path:
    package = tmp_path / f"bare_callable_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n\n"
        "EVENTS = []\n\n"
        "@clauz3.trusted\n"
        "def match(pattern: str, value: str) -> None:\n"
        "    EVENTS.append((pattern, value))\n"
    )
    (package / "exports.py").write_text(
        "from re import match as pure_match\n"
        "from .effects import match as effect_match\n"
    )
    selected = (
        "from .effects import match\n"
        if selection == "conditional-import"
        else 'match = globals()["effect_match"]\n'
    )
    (package / "selection.py").write_text(
        "from .effects import match as effect_match\n"
        "from re import match\n"
        "FLAG = True\n"
        "if FLAG:\n"
        f"    {selected}"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n"
        "    return effect().empty()\n\n"
        "@contract\n"
        "def one_call() -> ContractSpec:\n"
        "    return effect().count() <= 1\n"
    )
    return package


def _program(
    package: Path,
    call_name: str,
    *,
    module: str = "selection",
    contract: str = "silent",
) -> str:
    alias = "" if call_name == "match" else f" as {call_name}"
    return (
        "import clauz3\n"
        f"from {package.name}.{module} import match{alias}\n"
        f"from {package.name}.contracts import {contract}\n\n"
        f"@clauz3.guarantee({contract}())\n"
        "def main() -> None:\n"
        f'    {call_name}("bob", "bob")\n'
    )


@pytest.mark.parametrize("selection", _SELECTIONS)
@pytest.mark.parametrize("call_name", _CALL_NAMES)
def test_ambiguous_bare_import_is_refused(
    tmp_path: Path, selection: str, call_name: str
) -> None:
    package = _layer(tmp_path, selection)
    results = prove_text(
        _program(package, call_name),
        trusted_roots=[package],
        import_paths=[tmp_path],
        source_name="plan.py",
    )

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "callable target" in str(results[0].proof)
    assert call_name in str(results[0].proof)


@pytest.mark.parametrize("selection", _SELECTIONS)
@pytest.mark.parametrize("call_name", _CALL_NAMES)
def test_ambiguous_bare_import_stops_before_approval_or_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
    call_name: str,
) -> None:
    package = _layer(tmp_path, selection)
    approve = Mock(side_effect=AssertionError("ambiguous call reached approval"))
    execute = Mock(side_effect=AssertionError("ambiguous call reached execution"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", execute)

    with pytest.raises(RunProofError) as exc_info:
        run_source(
            _program(package, call_name),
            source_name="plan.py",
            trusted_roots=[package],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )

    assert [result.proof.conclusion for result in exc_info.value.proofs] == [
        Conclusion.SKIP
    ]
    assert "callable target" in str(exc_info.value.proofs[0].proof)
    assert call_name in str(exc_info.value.proofs[0].proof)
    approve.assert_not_called()
    execute.assert_not_called()


@pytest.mark.parametrize("selection", _SELECTIONS)
def test_conditional_reexport_really_calls_the_trusted_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    """Both source forms bind an effect, although inference starts with re.match."""
    package = _layer(tmp_path, selection)
    monkeypatch.syspath_prepend(str(tmp_path))
    chosen = importlib.import_module(f"{package.name}.selection")
    effects = importlib.import_module(f"{package.name}.effects")

    assert chosen.match is effects.match
    assert effects.EVENTS == []
    chosen.match("bob", "bob")
    assert effects.EVENTS == [("bob", "bob")]


@pytest.mark.parametrize("call_name", _CALL_NAMES)
@pytest.mark.parametrize(
    ("contract", "conclusion"),
    [("silent", Conclusion.FAIL), ("one_call", Conclusion.OK)],
)
def test_unambiguous_effect_import_preserves_its_fact(
    tmp_path: Path, call_name: str, contract: str, conclusion: Conclusion
) -> None:
    package = _layer(tmp_path, "conditional-import")
    results = prove_text(
        _program(package, call_name, module="effects", contract=contract),
        trusted_roots=[package],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [conclusion]


@pytest.mark.parametrize("call_name", _CALL_NAMES)
def test_unambiguous_registered_function_import_still_proves(call_name: str) -> None:
    alias = "" if call_name == "match" else f" as {call_name}"
    results = prove_text(
        f"from re import match{alias}\n\n"
        "def main() -> None:\n"
        f'    assert {call_name}("bob", "bob")\n'
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


@pytest.mark.parametrize("binding_scope", ["entry", "trusted-reexport"])
def test_duplicate_import_alias_cannot_hide_its_last_effect_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, binding_scope: str
) -> None:
    package = _layer(tmp_path, "conditional-import")
    source = _program(package, "match")
    duplicate = "pure_match as match, effect_match as match"
    if binding_scope == "entry":
        source = source.replace(
            f"from {package.name}.selection import match",
            f"from {package.name}.exports import {duplicate}",
        )
    else:
        (package / "selection.py").write_text(f"from .exports import {duplicate}\n")

    # Astroid can report only the first member of one duplicate ImportFrom;
    # ordinary Python binds the last. The runtime control records no I/O.
    monkeypatch.syspath_prepend(str(tmp_path))
    namespace: dict[str, Any] = {}
    exec(source, namespace)
    namespace["main"]()
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == [("bob", "bob")]

    results = prove_text(
        source,
        trusted_roots=[package],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "callable target" in str(results[0].proof)
    assert "match" in str(results[0].proof)


@pytest.mark.parametrize("binding", ["constant", "unknown"])
def test_shadowed_builtin_cannot_fall_back_to_its_registered_model(
    binding: str,
) -> None:
    runtime_failure: type[Exception]
    if binding == "constant":
        declarations = "len = 42\n"
        runtime_failure = TypeError
    else:
        declarations = (
            "def wrong_length(value: str) -> int:\n"
            "    return 99\n\n"
            'len = globals()["wrong_length"]\n'
        )
        runtime_failure = AssertionError
    source = declarations + '\ndef main() -> None:\n    assert len("bob") == 3\n'
    namespace: dict[str, Any] = {}
    exec(source, namespace)
    with pytest.raises(runtime_failure):
        namespace["main"]()

    results = prove_text(source)
    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "callable target" in str(results[0].proof)
    assert "len" in str(results[0].proof)


def test_distinct_definitions_with_the_same_qualified_name_are_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _layer(tmp_path, "conditional-import")
    (package / "selection.py").write_text(
        "FLAG = False\n"
        "if FLAG:\n"
        "    def allowed() -> bool:\n"
        "        return True\n"
        "else:\n"
        "    def allowed() -> bool:\n"
        "        return False\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    selected = importlib.import_module(f"{package.name}.selection")
    assert selected.allowed() is False

    results = prove_text(
        f"from {package.name}.selection import allowed\n\n"
        "def main() -> None:\n"
        "    assert allowed()\n",
        trusted_roots=[package],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "callable target" in str(results[0].proof)
    assert "allowed" in str(results[0].proof)


@pytest.mark.parametrize(
    "selection",
    [
        "from .effects import match\n",
        "from .effects import match as effect_match\nmatch = effect_match\n",
    ],
    ids=["simple-reexport", "name-assignment"],
)
def test_unambiguous_reexport_still_proves(tmp_path: Path, selection: str) -> None:
    package = _layer(tmp_path, "conditional-import")
    (package / "selection.py").write_text(selection)
    results = prove_text(
        _program(package, "selected", contract="one_call"),
        trusted_roots=[package],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


def test_aliased_builtin_constructor_still_proves() -> None:
    results = prove_text(
        "from builtins import int as integer\n\n"
        "def main() -> None:\n"
        "    assert integer(3) == 3\n",
        extra_allowed_imports=["builtins"],
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.OK]
