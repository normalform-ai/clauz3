"""Consent must describe the selected entry definition, not a same-named nested one."""

import importlib
from pathlib import Path
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.guarantees import GuaranteeError, guarantee_texts
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import ProverConfigError, prove_text
from clauz3.runner import build_approval_request


def _program(
    tmp_path: Path,
    *,
    scope: str = "function",
    target: str = "main",
    nested_guarantee: str = "checks.silent()",
    entry_guarantee: bool = False,
) -> tuple[str, Path]:
    package = tmp_path / f"entry_guarantee_{uuid4().hex}"
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
        "def silent() -> ContractSpec:\n"
        "    return effect().empty()\n\n"
        "@contract\n"
        "def once() -> ContractSpec:\n"
        "    return effect().count() <= 1\n"
    )
    (package / "policy.py").write_text(
        "from clauz3.policy import domain_policy\n"
        'POLICY = domain_policy(when_used="record", recommended=["silent"], '
        'label="recording")\n'
    )
    container = "def unused() -> None:" if scope == "function" else "class Docs:"
    parameters = "" if scope == "function" else "self"
    entry_decorator = "@clauz3.guarantee(checks.once())\n" if entry_guarantee else ""
    source = (
        "import clauz3\n"
        f"from {package.name} import contracts as checks\n"
        f"from {package.name}.effects import record\n\n"
        f"{container}\n"
        f"    @clauz3.guarantee({nested_guarantee})\n"
        f"    def {target}({parameters}) -> None:\n"
        "        pass\n\n"
        f"{entry_decorator}"
        f"def {target}() -> None:\n"
        '    record("entry executed")\n'
    )
    return source, package


@pytest.mark.parametrize(
    ("scope", "target"),
    [("function", "main"), ("class", "main"), ("function", "task")],
    ids=["nested-function", "class-method", "custom-target"],
)
def test_guarantee_texts_selects_only_the_direct_entry_definition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str, target: str
) -> None:
    source, package = _program(tmp_path, scope=scope, target=target)
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.import_module(f"{package.name}.contracts")

    assert guarantee_texts(source=source, target=target) == []


def test_approval_request_does_not_claim_a_nested_guarantee_covers_the_entry(
    tmp_path: Path,
) -> None:
    source, package = _program(tmp_path)
    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]

    request = build_approval_request(
        source=source,
        source_name="plan.py",
        target="main",
        proofs=proofs,
        trusted_roots=[package],
        import_roots=[tmp_path],
    )
    assert request["guarantees"] == []
    assert request["coverage"] == [
        {
            "domain": "recording",
            "status": "silent_gap",
            "missing_required": [],
            "missing_recommended": ["silent"],
        }
    ]


def test_the_actual_entry_executes_the_effect_despite_the_nested_silent_claim(
    tmp_path: Path,
) -> None:
    source, package = _program(tmp_path)
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(
            trusted_roots=[package], import_roots=[tmp_path], extra_allowed=()
        ),
        import_roots=[tmp_path],
    )
    assert outcome.effects == (
        (f"{package.name}.effects.record", ("entry executed",), {}),
    )
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["entry executed"]


def test_entry_guarantee_is_preserved_without_adding_the_nested_claim(
    tmp_path: Path,
) -> None:
    source, package = _program(tmp_path, entry_guarantee=True)
    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
    assert guarantee_texts(source=source, target="main") == ["checks.once()"]


@pytest.mark.parametrize("scope", ["function", "class"])
def test_selecting_entry_guarantees_still_validates_malformed_nested_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    source, package = _program(tmp_path, scope=scope, nested_guarantee="lambda: True")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.import_module(f"{package.name}.contracts")

    with pytest.raises(GuaranteeError, match="not a contract call"):
        guarantee_texts(source=source, target="main")
    with pytest.raises(ProverConfigError, match="not a contract call"):
        prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
