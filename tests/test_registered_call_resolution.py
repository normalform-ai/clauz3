"""Registered models must follow the callee's binding, not its spelling."""

from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source

_COLLISIONS = [
    pytest.param("math", "sin", "float", "0.0", id="math-sin"),
    pytest.param("math", "trunc", "float", "1.5", id="math-trunc"),
    pytest.param("posixpath", "join", "str", '"#general"', id="path-join"),
    pytest.param("re", "compile", "str", '"bob"', id="re-compile"),
]


def _program(
    tmp_path: Path, alias: str, function: str, annotation: str, argument: str
) -> tuple[str, Path]:
    package_name = f"registered_collision_{uuid4().hex}"
    package = tmp_path / package_name
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n\n"
        "@clauz3.trusted\n"
        f"def {function}(value: {annotation}) -> None:\n"
        "    pass\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n"
        "    return effect().empty()\n"
    )
    source = (
        "import clauz3\n"
        f"import {package_name}.effects as {alias}\n"
        f"from {package_name}.contracts import silent\n\n"
        "@clauz3.guarantee(silent())\n"
        "def main() -> None:\n"
        f"    {alias}.{function}({argument})\n"
    )
    return source, package


@pytest.mark.parametrize(("alias", "function", "annotation", "argument"), _COLLISIONS)
def test_trusted_module_alias_cannot_select_a_stdlib_model(
    tmp_path: Path, alias: str, function: str, annotation: str, argument: str
) -> None:
    source, trusted_root = _program(tmp_path, alias, function, annotation, argument)
    results = prove_text(
        source,
        trusted_roots=[trusted_root],
        import_paths=[tmp_path],
        source_name="plan.py",
    )

    # Module-qualified user functions have no registered model. Refusal must
    # survive a spelling collision with a supported stdlib operation; using
    # that model would erase this effect and prove the wildcard claim vacuously.
    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "no unambiguous definition" in str(results[0].proof)


@pytest.mark.parametrize(("alias", "function", "annotation", "argument"), _COLLISIONS)
def test_model_name_collision_stops_before_approval_or_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alias: str,
    function: str,
    annotation: str,
    argument: str,
) -> None:
    source, trusted_root = _program(tmp_path, alias, function, annotation, argument)
    approve = Mock(side_effect=AssertionError("unresolved call reached approval"))
    execute = Mock(side_effect=AssertionError("unresolved call reached execution"))
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

    assert [result.proof.conclusion for result in exc_info.value.proofs] == [
        Conclusion.SKIP
    ]
    approve.assert_not_called()
    execute.assert_not_called()


@pytest.mark.parametrize(
    ("imports", "expression"),
    [
        pytest.param("import math", "math.sin(0) == 0", id="math-sin"),
        pytest.param("import math", "math.trunc(1.5) == 1", id="math-trunc"),
        pytest.param(
            "import math as numbers", "numbers.sin(0) == 0", id="module-alias"
        ),
        pytest.param(
            "import posixpath", 'posixpath.join("a", "b") == "a/b"', id="posix-path"
        ),
        pytest.param(
            "import posixpath as paths",
            'paths.join("a", "b") == "a/b"',
            id="path-alias",
        ),
        # Solver registration/alias coverage only: these re functions remain
        # outside the interpreter's runtime surface (the #122 parity gap).
        pytest.param(
            "import re as regex", 'regex.fullmatch("bob", "bob")', id="regex-alias"
        ),
    ],
)
def test_registered_calls_resolve_through_their_actual_imports(
    imports: str, expression: str
) -> None:
    results = prove_text(
        f"{imports}\n\ndef main() -> None:\n    assert {expression}\n",
        extra_allowed_imports=["posixpath"],
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


def test_compiled_pattern_attributes_have_no_interpreter_surface() -> None:
    results = prove_text(
        "import re\n\ndef main() -> None:\n"
        '    assert re.compile("bob").fullmatch("bob")\n'
    )

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "attribute is outside the interpreter surface" in str(results[0].proof)
