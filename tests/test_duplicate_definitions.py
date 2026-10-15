"""The selected theorem must be the same module binding execution will call."""

import importlib
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.approvals.authority import ApprovedLayer
from clauz3.approvals.policy import approval_policy_from_dict, evaluate_policy
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import (
    ProverConfigError,
    prove_path,
    prove_text,
    validate_untrusted_source,
)
from clauz3.runner import run_source

_BOUND_ERROR = r"bound.*module level"


def _layer(tmp_path: Path) -> tuple[str, Path]:
    package = tmp_path / f"entry_binding_{uuid4().hex}"
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
        "    return effect().empty()\n"
    )
    header = (
        "import clauz3\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import silent\n\n"
    )
    return header, package


def _duplicate_target(
    header: str,
    *,
    target: str = "main",
    first_effectful: bool = False,
    second_decorated: bool = True,
) -> str:
    first_body = 'record("first")' if first_effectful else "pass"
    second_body = "pass" if first_effectful else 'record("second")'
    second_decorator = "@clauz3.guarantee(silent())\n" if second_decorated else ""
    return (
        header
        + "@clauz3.guarantee(silent())\n"
        + f"def {target}() -> None:\n    {first_body}\n\n"
        + second_decorator
        + f"def {target}() -> None:\n    {second_body}\n"
    )


@pytest.mark.parametrize(
    ("first_effectful", "second_decorated"),
    [(False, True), (True, True), (False, False)],
    ids=["effect-last", "effect-first", "undecorated-second"],
)
def test_duplicate_target_cannot_select_a_different_body(
    tmp_path: Path, first_effectful: bool, second_decorated: bool
) -> None:
    header, package = _layer(tmp_path)
    source = _duplicate_target(
        header, first_effectful=first_effectful, second_decorated=second_decorated
    )
    with pytest.raises(ProverConfigError, match=_BOUND_ERROR) as exc_info:
        prove_text(
            source,
            trusted_roots=[package],
            import_paths=[tmp_path],
            source_name="duplicate_entry.py",
        )
    message = str(exc_info.value)
    assert "main" in message
    assert "duplicate_entry.py" in message
    expected_lines = "lines 6, 10" if second_decorated else "lines 6, 9"
    assert expected_lines in message


def test_interpreter_executes_the_last_definition(tmp_path: Path) -> None:
    """The regression's last body really performs a harmless recorded effect."""
    header, package = _layer(tmp_path)
    source = _duplicate_target(header)
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(
            trusted_roots=[package], import_roots=[tmp_path], extra_allowed=()
        ),
        import_roots=[tmp_path],
    )
    assert outcome.effects == ((f"{package.name}.effects.record", ("second",), {}),)
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["second"]


@pytest.mark.parametrize("target", ["main", "task"])
def test_duplicate_selected_target_stops_before_approval_and_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    header, package = _layer(tmp_path)
    source = _duplicate_target(header, target=target, second_decorated=False)
    approve = Mock(side_effect=AssertionError("duplicate target reached approval"))
    execute = Mock(side_effect=AssertionError("duplicate target reached execution"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", execute)

    with pytest.raises(ProverConfigError, match=_BOUND_ERROR) as exc_info:
        run_source(
            source,
            source_name="plan.py",
            trusted_roots=[package],
            import_roots=[tmp_path],
            target=target,
            approval_service_url="http://approval.invalid",
        )
    assert target in str(exc_info.value)
    approve.assert_not_called()
    execute.assert_not_called()


def test_prove_path_checks_a_custom_target(tmp_path: Path) -> None:
    header, package = _layer(tmp_path)
    entry = tmp_path / "plan.py"
    entry.write_text(_duplicate_target(header, target="task"))
    with pytest.raises(ProverConfigError, match=_BOUND_ERROR) as exc_info:
        prove_path(
            entry, trusted_roots=[package], import_roots=[tmp_path], target="task"
        )
    assert "task" in str(exc_info.value)


@pytest.mark.parametrize(
    "replacement",
    [
        "main = 1\n",
        "main: int = 1\n",
        "main, other = (1, 2)\n",
        "import math as main\n",
        "from math import sin as main\n",
        "class main:\n    pass\n",
        "async def main() -> None:\n    pass\n",
    ],
    ids=[
        "assignment",
        "annotated",
        "destructured",
        "import",
        "from-import",
        "class",
        "async",
    ],
)
def test_nonfunction_rebinding_cannot_replace_the_selected_target(
    replacement: str,
) -> None:
    source = "def main() -> None:\n    pass\n\n" + replacement
    with pytest.raises(ProverConfigError, match=_BOUND_ERROR):
        prove_text(source)


def test_source_validation_rejects_duplicate_default_target() -> None:
    with pytest.raises(ProverConfigError, match=_BOUND_ERROR):
        validate_untrusted_source(
            source="def main() -> None:\n    pass\n\ndef main() -> None:\n    pass\n",
            path=Path("plan.py"),
        )


@pytest.mark.parametrize(
    "target", ["helper", None], ids=["custom-target", "all-targets"]
)
def test_selected_duplicate_helper_is_also_refused(target: str | None) -> None:
    source = (
        "def helper() -> None:\n    pass\n\n"
        "def helper() -> None:\n    pass\n\n"
        "def main() -> None:\n    pass\n"
    )
    with pytest.raises(ProverConfigError, match=_BOUND_ERROR) as exc_info:
        prove_text(source, target=target)
    assert "helper" in str(exc_info.value)


@pytest.mark.parametrize(
    "last_effectful", [False, True], ids=["pure-last", "effect-last"]
)
def test_duplicate_helpers_keep_their_existing_last_binding_semantics(
    tmp_path: Path, last_effectful: bool
) -> None:
    header, package = _layer(tmp_path)
    first_body = "pass" if last_effectful else 'record("first")'
    last_body = 'record("last")' if last_effectful else "pass"
    source = (
        header
        + f"def helper() -> None:\n    {first_body}\n\n"
        + f"def helper() -> None:\n    {last_body}\n\n"
        + "@clauz3.guarantee(silent())\n"
        + "def main() -> None:\n    helper()\n"
    )
    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    expected = Conclusion.FAIL if last_effectful else Conclusion.OK
    assert [result.proof.conclusion for result in results] == [expected]


def test_import_shadowing_a_helper_still_records_its_effect(tmp_path: Path) -> None:
    header, package = _layer(tmp_path)
    source = (
        header
        + "def helper(value: str) -> None:\n    pass\n\n"
        + f"from {package.name}.effects import record as helper\n\n"
        + "@clauz3.guarantee(silent())\n"
        + 'def main() -> None:\n    helper("last")\n'
    )
    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert [result.proof.conclusion for result in results] == [Conclusion.FAIL]


def test_custom_target_does_not_reject_an_unselected_duplicate_main(
    tmp_path: Path,
) -> None:
    entry = tmp_path / "plan.py"
    entry.write_text(
        "def main() -> None:\n    pass\n\n"
        "def main() -> None:\n    pass\n\n"
        "def task() -> None:\n    pass\n"
    )
    assert [result.proof.conclusion for result in prove_path(entry, target="task")] == [
        Conclusion.OK
    ]


def test_all_targets_with_unique_bindings_still_prove() -> None:
    results = prove_text(
        "def main() -> None:\n    pass\n\ndef task() -> None:\n    pass\n",
        target=None,
    )
    assert [result.proof.conclusion for result in results] == [
        Conclusion.OK,
        Conclusion.OK,
    ]


@pytest.mark.parametrize(
    "source",
    [
        "def main() -> None:\n    pass\n",
        "def helper() -> None:\n    def main() -> None:\n        pass\n\n"
        "def main() -> None:\n    pass\n",
        "class Commands:\n    def main(self) -> None:\n        pass\n\n"
        "def main() -> None:\n    pass\n",
        "def main() -> None:\n    pass\n\nmain: object\n",
        "def helper() -> None:\n    pass\n\ndef helper() -> None:\n    pass\n\n"
        "def main() -> None:\n    pass\n",
    ],
    ids=[
        "unique",
        "nested-name",
        "method-name",
        "bare-annotation",
        "unused-duplicate-helper",
    ],
)
def test_unique_selected_binding_still_proves(source: str) -> None:
    validate_untrusted_source(source=source, path=Path("plan.py"))
    assert [result.proof.conclusion for result in prove_text(source)] == [Conclusion.OK]


def test_policy_cannot_autoapprove_the_first_of_two_targets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    header, package = _layer(tmp_path)
    policy = approval_policy_from_dict(
        {
            "version": 1,
            "imports": {"pol": f"{package.name}.contracts"},
            "rules": [
                {
                    "name": "silent-only",
                    "decision": "auto_approved",
                    "reason": "the program makes no trusted calls",
                    "when_proven": ["pol.silent()"],
                }
            ],
        }
    )
    decision = evaluate_policy(
        policy,
        {
            "program": _duplicate_target(header),
            "target": "main",
            "trusted_roots": [str(package)],
            "import_roots": [str(tmp_path)],
        },
        authorized_layers=(
            ApprovedLayer(trusted_roots=(package,), import_roots=(tmp_path,)),
        ),
    )
    assert decision is None
    diagnostic = capsys.readouterr().err
    assert "bound" in diagnostic
    assert "module level" in diagnostic


def _required_silent_program(tmp_path: Path, *, asynchronous: bool) -> tuple[str, Path]:
    header, package = _layer(tmp_path)
    (package / "policy.py").write_text(
        "from clauz3.policy import domain_policy\n"
        'POLICY = domain_policy(when_used="record", required=["silent"])\n'
    )
    prefix = "async " if asynchronous else ""
    source = header + f'{prefix}def main() -> None:\n    record("required violation")\n'
    return source, package


@pytest.mark.parametrize("target", ["main", None], ids=["selected-main", "all-targets"])
def test_async_target_cannot_bypass_required_contract_injection(
    tmp_path: Path, target: str | None
) -> None:
    """AsyncFunctionDef was selected as a theorem but omitted from policy injection."""
    source, package = _required_silent_program(tmp_path, asynchronous=True)
    with pytest.raises(
        ProverConfigError, match=r"target 'main'.*synchronous function defined directly"
    ):
        prove_text(
            source,
            target=target,
            trusted_roots=[package],
            import_paths=[tmp_path],
            source_name="async_entry.py",
        )


def test_synchronous_target_enforces_the_same_required_contract(tmp_path: Path) -> None:
    source, package = _required_silent_program(tmp_path, asynchronous=False)
    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert [result.proof.conclusion for result in results] == [Conclusion.FAIL]
    assert "guarantee" in str(results[0].proof)


def test_unselected_async_helper_body_does_not_rebind_the_target() -> None:
    source = (
        "async def helper() -> None:\n    main = 1\n\ndef main() -> None:\n    pass\n"
    )
    results = prove_text(source, target="main")
    assert [result.proof.conclusion for result in results] == [Conclusion.OK]
