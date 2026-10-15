"""Pure stdlib calls must not activate unrelated effect policies."""

import sys
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.policy import compute_coverage, load_domain_policies, transitive_calls
from clauz3.policy_calls import DYNAMIC_CALL
from clauz3.prover import ProofResult, ProverConfigError, prove_text
from clauz3.runner import build_approval_request


@pytest.fixture
def recording_layer(tmp_path: Path) -> Path:
    package = tmp_path / f"stdlib_policy_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n\n"
        "EVENTS = []\n\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n\n"
        "@contract\n"
        "def must_record() -> ContractSpec:\n"
        '    return effect("record").count() > 0\n'
    )
    (package / "helpers.py").write_text(
        'from .effects import record\n\ndef blast() -> None:\n    record("one")\n'
    )
    (package / "policy.py").write_text(
        "from clauz3.policy import domain_policy\n"
        'POLICY = domain_policy(when_used="record", '
        'required=["must_record"], label="recording")\n'
    )
    return package


def _coverage(source: str, package: Path) -> list[dict[str, object]]:
    return compute_coverage(
        source=source,
        guarantees=[],
        policies=load_domain_policies(
            trusted_roots=[package], import_roots=[package.parent]
        ),
        trusted_roots=[package],
        import_roots=[package.parent],
    )


def _prove(source: str, package: Path) -> list[ProofResult]:
    return prove_text(
        source,
        source_name="plan.py",
        trusted_roots=[package],
        import_paths=[package.parent],
    )


PURE_CALLS = (
    ("import math", "math.sin(0)", "sin"),
    ("from math import sin as sine", "sine(0)", "sin"),
)


@pytest.mark.parametrize(
    ("imports", "expression", "name"),
    (
        *PURE_CALLS,
        ("import re", 're.match("a", "a")', "match"),
        ("from re import fullmatch as match", 'match("a", "a")', "fullmatch"),
    ),
)
def test_stdlib_calls_do_not_activate_unrelated_policy_coverage(
    recording_layer: Path, imports: str, expression: str, name: str
) -> None:
    source = f"{imports}\n\ndef main() -> None:\n    {expression}\n"

    calls = transitive_calls(
        source,
        "main",
        import_roots=[recording_layer.parent],
        trusted_roots=[recording_layer],
    )

    assert calls == {name}
    assert _coverage(source, recording_layer) == []


@pytest.mark.parametrize(("imports", "expression", "_name"), PURE_CALLS)
def test_modeled_pure_math_remains_provable_with_nonvacuous_required_policy(
    recording_layer: Path, imports: str, expression: str, _name: str
) -> None:
    source = f"{imports}\n\ndef main() -> None:\n    assert {expression} == 0.0\n"

    proofs = _prove(source, recording_layer)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]


@pytest.mark.parametrize(("imports", "expression", "_name"), PURE_CALLS)
def test_approval_request_excludes_unrelated_domain_for_pure_math(
    recording_layer: Path, imports: str, expression: str, _name: str
) -> None:
    source = f"{imports}\n\ndef main() -> None:\n    assert {expression} == 0.0\n"

    request = build_approval_request(
        source=source,
        source_name="plan.py",
        target="main",
        proofs=_prove(source, recording_layer),
        trusted_roots=[recording_layer],
        import_roots=[recording_layer.parent],
    )

    assert request["coverage"] == []


def test_refused_math_constant_does_not_activate_a_recording_requirement(
    recording_layer: Path,
) -> None:
    source = "import math\n\ndef main() -> None:\n    assert math.pi > 3\n"

    proofs = _prove(source, recording_layer)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert "module data bindings are not modeled" in str(proofs[0].proof)
    assert _coverage(source, recording_layer) == []


def test_imported_math_constant_does_not_activate_policy_coverage(
    recording_layer: Path,
) -> None:
    source = (
        "from math import pi as limit\n\ndef main() -> None:\n    assert limit > 3\n"
    )

    assert _coverage(source, recording_layer) == []


@pytest.mark.parametrize(
    ("imports", "expression"),
    [("from math import sqrt", "sqrt(4)"), ("import json", 'json.dumps({"x": 1})')],
)
def test_unmodeled_stdlib_callable_keeps_conservative_policy_fallback(
    recording_layer: Path, imports: str, expression: str
) -> None:
    # Import permission alone does not certify arbitrary library callables.
    source = f"{imports}\n\ndef main() -> None:\n    {expression}\n"

    calls = transitive_calls(
        source,
        "main",
        import_roots=[recording_layer.parent],
        trusted_roots=[recording_layer],
    )

    assert DYNAMIC_CALL in calls
    assert [entry["domain"] for entry in _coverage(source, recording_layer)] == [
        "recording"
    ]


def test_explicit_recording_contract_still_fails_for_pure_math(
    recording_layer: Path,
) -> None:
    # Requiring a record is intentionally nonvacuous. Only policy applicability
    # makes the undecorated math cases pass; this explicit guarantee must fail.
    source = (
        "import clauz3\nimport math\n"
        f"from {recording_layer.name} import contracts as checks\n\n"
        "@clauz3.guarantee(checks.must_record())\n"
        "def main() -> None:\n"
        "    assert math.sin(0) == 0.0\n"
    )

    proofs = _prove(source, recording_layer)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]
    assert "guarantee" in str(proofs[0].proof)


@pytest.mark.parametrize(("module", "name"), [("math", "sin"), ("json", "dumps")])
def test_source_root_shadow_of_stdlib_keeps_imported_helpers_effect_policy(
    recording_layer: Path, module: str, name: str
) -> None:
    # Load the manifest before introducing a source-only shadow. Activating a
    # layer that replaces an initialized engine provider can fail earlier;
    # whether math is such a provider varies across Python builds.
    policies = load_domain_policies(
        trusted_roots=[recording_layer], import_roots=[recording_layer.parent]
    )
    (recording_layer.parent / f"{module}.py").write_text(
        f"from {recording_layer.name}.helpers import blast\n\n"
        f"def {name}(value) -> None:\n"
        "    blast()\n"
    )
    source = f"import {module}\n\ndef main() -> None:\n    {module}.{name}(0)\n"

    calls = transitive_calls(
        source,
        "main",
        import_roots=[recording_layer.parent],
        trusted_roots=[recording_layer],
    )

    assert {name, "blast", "record"} <= calls
    assert DYNAMIC_CALL not in calls
    assert compute_coverage(
        source=source,
        guarantees=[],
        policies=policies,
        trusted_roots=[recording_layer],
        import_roots=[recording_layer.parent],
    ) == [
        {
            "domain": "recording",
            "status": "required_gap",
            "missing_required": ["must_record"],
            "missing_recommended": [],
        }
    ]
    if module == "json":
        # Proving still refuses replacement of the initialized JSON package.
        with pytest.raises(
            ProverConfigError, match="cannot replace an initialized engine module: json"
        ):
            _prove(source, recording_layer)


def test_unsupported_source_shadow_is_not_treated_as_genuine_math(
    recording_layer: Path,
) -> None:
    policies = load_domain_policies(
        trusted_roots=[recording_layer], import_roots=[recording_layer.parent]
    )
    (recording_layer.parent / "math.py").write_text(
        f"from {recording_layer.name}.helpers import blast\n"
        "blast.marker = True\n\n"
        "def sin(value) -> None:\n"
        "    blast()\n"
    )
    source = "import math\n\ndef main() -> None:\n    math.sin(0)\n"

    calls = transitive_calls(
        source,
        "main",
        import_roots=[recording_layer.parent],
        trusted_roots=[recording_layer],
    )

    assert DYNAMIC_CALL in calls
    coverage = compute_coverage(
        source=source,
        guarantees=[],
        policies=policies,
        trusted_roots=[recording_layer],
        import_roots=[recording_layer.parent],
    )
    assert [entry["domain"] for entry in coverage] == ["recording"]


def test_loaded_module_with_custom_origin_is_not_treated_as_genuine_math(
    recording_layer: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = recording_layer.parent / "custom_math.py"
    path.write_text("def sin(value):\n    return value\n")
    replacement = ModuleType("math")
    replacement.__file__ = str(path)
    replacement.__spec__ = ModuleSpec("math", loader=None, origin=str(path))
    source = "import math\n\ndef main() -> None:\n    math.sin(0)\n"

    # Only source discovery observes this replacement; restore the real math
    # module before loading trusted fixtures or calling the prover.
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "math", replacement)
        calls = transitive_calls(
            source,
            "main",
            import_roots=[recording_layer.parent],
            trusted_roots=[recording_layer],
        )

    assert DYNAMIC_CALL in calls


def test_explicitly_allowed_unknown_import_still_activates_policy(
    recording_layer: Path,
) -> None:
    module = f"unknown_helper_{uuid4().hex}"
    source = f"from {module} import invoke\n\ndef main() -> None:\n    invoke()\n"
    import_policy = build_policy(
        trusted_roots=[recording_layer],
        import_roots=[recording_layer.parent],
        extra_allowed=[module],
    )
    assert import_policy.is_allowed(module)

    calls = transitive_calls(
        source,
        "main",
        import_roots=[recording_layer.parent],
        trusted_roots=[recording_layer],
    )

    assert DYNAMIC_CALL in calls
    assert _coverage(source, recording_layer) == [
        {
            "domain": "recording",
            "status": "required_gap",
            "missing_required": ["must_record"],
            "missing_recommended": [],
        }
    ]
