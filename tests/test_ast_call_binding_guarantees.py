"""Native effect facts must reflect Python's argument binding exactly."""

import importlib
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import UnsupportedNode, execute
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source

_NO_BAD = 'effect("record").where(lambda e: e.value == "bad").empty()'
_MIXED_SIGNATURE = "prefix: str, /, value: str, *, tag: str, dry_run: bool = True"
_MIXED_CONTRACT = (
    'effect("record").all(lambda e: e.prefix == "prefix" '
    'and e.value == "safe" and e.tag == "tag" and e.dry_run == True)'
)


def _program(
    tmp_path: Path,
    *,
    signature: str,
    call: str,
    contract: str = _NO_BAD,
    event_value: str = "value",
) -> tuple[str, Path]:
    package = tmp_path / f"call_binding_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS = []\n"
        "@clauz3.trusted\n"
        f"def record({signature}) -> None:\n"
        f"    EVENTS.append({event_value})\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def allowed() -> ContractSpec:\n"
        f"    return {contract}\n"
    )
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import allowed\n"
        "@clauz3.guarantee(allowed())\n"
        "def main() -> None:\n"
        f"    {call}\n"
    )
    return source, package


def _check_runtime(
    source: str,
    package: Path,
    *,
    args: tuple[object, ...],
    kwargs: dict[str, object],
    event: object,
) -> None:
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[package.parent]),
        import_roots=[package.parent],
    )
    assert outcome.effects == ((f"{package.name}.effects.record", args, kwargs),)
    effects = importlib.import_module(f"{package.name}.effects")
    assert [event] == effects.EVENTS


@pytest.mark.parametrize(
    "signature",
    ["prefix: str, /, value: str", "prefix: str, value: str"],
    ids=["positional-only-prefix", "ordinary-control"],
)
@pytest.mark.parametrize(
    ("value", "expected"), [("bad", Conclusion.FAIL), ("safe", Conclusion.OK)]
)
def test_trusted_positional_arguments_keep_their_declared_names(
    tmp_path: Path, signature: str, value: str, expected: Conclusion
) -> None:
    source, package = _program(
        tmp_path,
        signature=signature,
        call=f"record('safe', {value!r})",
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [expected]
    assert "guarantee" in str(results[0].proof)
    _check_runtime(source, package, args=("safe", value), kwargs={}, event=value)


def test_wrong_positional_only_fact_stops_before_approval_or_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, package = _program(
        tmp_path,
        signature="prefix: str, /, value: str",
        call="record('safe', 'bad')",
    )
    approve = Mock(side_effect=AssertionError("approval must not be reached"))
    execute_source = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", execute_source)

    with pytest.raises(RunProofError) as exc_info:
        run_source(
            source,
            source_name="plan.py",
            trusted_roots=[package],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )

    assert [item.proof.conclusion for item in exc_info.value.proofs] == [
        Conclusion.FAIL
    ]
    approve.assert_not_called()
    execute_source.assert_not_called()
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []


@pytest.mark.parametrize(
    ("call", "args", "kwargs", "event", "expected"),
    [
        (
            "record('prefix', 'safe', tag='tag', dry_run=True)",
            ("prefix", "safe"),
            {"tag": "tag", "dry_run": True},
            ("prefix", "safe", "tag", True),
            Conclusion.OK,
        ),
        (
            "record('prefix', value='safe', tag='tag', dry_run=True)",
            ("prefix",),
            {"value": "safe", "tag": "tag", "dry_run": True},
            ("prefix", "safe", "tag", True),
            Conclusion.OK,
        ),
        (
            "record('prefix', 'bad', tag='tag', dry_run=True)",
            ("prefix", "bad"),
            {"tag": "tag", "dry_run": True},
            ("prefix", "bad", "tag", True),
            Conclusion.FAIL,
        ),
        (
            "record('prefix', 'safe', tag='wrong', dry_run=True)",
            ("prefix", "safe"),
            {"tag": "wrong", "dry_run": True},
            ("prefix", "safe", "wrong", True),
            Conclusion.FAIL,
        ),
    ],
    ids=["positional-value", "keyword-value", "bad-value", "bad-keyword-only"],
)
def test_mixed_argument_kinds_populate_the_correct_fact_fields(
    tmp_path: Path,
    call: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
    event: object,
    expected: Conclusion,
) -> None:
    source, package = _program(
        tmp_path,
        signature=_MIXED_SIGNATURE,
        call=call,
        contract=_MIXED_CONTRACT,
        event_value="(prefix, value, tag, dry_run)",
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [expected]
    _check_runtime(source, package, args=args, kwargs=kwargs, event=event)


@pytest.mark.parametrize(
    "call",
    [
        "record('prefix', tag='tag')",
        "record('prefix', 'safe', 'extra', tag='tag')",
        "record('prefix', 'safe', value='bad', tag='tag')",
        "record('prefix', 'safe', tag='tag', unknown=True)",
        "record(prefix='prefix', value='safe', tag='tag')",
        "record('prefix', 'safe')",
    ],
    ids=[
        "missing",
        "extra-positional",
        "duplicate",
        "unknown-keyword",
        "positional-only-as-keyword",
        "missing-keyword-only",
    ],
)
def test_malformed_trusted_calls_are_refused_before_recording_a_fact(
    tmp_path: Path, call: str
) -> None:
    source, package = _program(
        tmp_path, signature="prefix: str, /, value: str, *, tag: str", call=call
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "unsupported function argument binding" in str(results[0].proof)
    # Python also rejects these calls before entering the trusted body.
    with pytest.raises(TypeError):
        execute(
            source,
            source_name="plan.py",
            policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
            import_roots=[tmp_path],
        )
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []


@pytest.mark.parametrize(
    ("call", "contract", "expected", "kwargs", "dry_run"),
    [
        (
            "record('safe', dry_run=True)",
            'effect("record").all(lambda e: e.dry_run == True)',
            Conclusion.OK,
            {"dry_run": True},
            True,
        ),
        (
            "record('safe', dry_run=False)",
            'effect("record").all(lambda e: e.dry_run == True)',
            Conclusion.FAIL,
            {"dry_run": False},
            False,
        ),
        (
            "record('safe')",
            'effect("record").all(lambda e: e.dry_run == True)',
            Conclusion.SKIP,
            {},
            True,
        ),
        ("record('safe')", _NO_BAD, Conclusion.OK, {}, True),
    ],
    ids=["explicit-true", "explicit-false", "omitted-field", "omitted-unqueried"],
)
def test_trusted_defaults_do_not_become_explicit_effect_fields(
    tmp_path: Path,
    call: str,
    contract: str,
    expected: Conclusion,
    kwargs: dict[str, object],
    dry_run: bool,
) -> None:
    source, package = _program(
        tmp_path,
        signature="value: str, *, dry_run: bool = True",
        call=call,
        contract=contract,
        event_value="(value, dry_run)",
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [expected]
    if expected is Conclusion.SKIP:
        assert "dry_run" in str(results[0].proof)
    _check_runtime(
        source, package, args=("safe",), kwargs=kwargs, event=("safe", dry_run)
    )


_UNSUPPORTED_HELPER_KINDS = [
    pytest.param("value: int, /", "helper(1)", "positional-only", id="positional-only"),
    pytest.param("*, value: int", "helper(value=1)", "keyword-only", id="keyword-only"),
]


def _ordinary_helper_program(
    tmp_path: Path, signature: str, call: str
) -> tuple[str, Path]:
    source, package = _program(
        tmp_path,
        signature="value: str",
        call=f"record('safe')\n    assert {call} == 1",
    )
    source = source.replace(
        "@clauz3.guarantee",
        f"def helper({signature}) -> int:\n    return value\n\n@clauz3.guarantee",
        1,
    )
    return source, package


@pytest.mark.parametrize(("signature", "call", "kind"), _UNSUPPORTED_HELPER_KINDS)
def test_native_guarantee_refuses_an_unrunnable_ordinary_helper(
    tmp_path: Path, signature: str, call: str, kind: str
) -> None:
    source, package = _ordinary_helper_program(tmp_path, signature, call)

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "unsupported function argument binding" in str(results[0].proof)
    assert kind in str(results[0].proof)


@pytest.mark.parametrize(("signature", "call", "kind"), _UNSUPPORTED_HELPER_KINDS)
def test_unrunnable_helper_stops_before_approval_and_any_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    signature: str,
    call: str,
    kind: str,
) -> None:
    source, package = _ordinary_helper_program(tmp_path, signature, call)
    approve = Mock(side_effect=AssertionError("approval must not be reached"))
    execute_source = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", execute_source)

    with pytest.raises(RunProofError) as exc_info:
        run_source(
            source,
            source_name="plan.py",
            trusted_roots=[package],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )

    assert [result.proof.conclusion for result in exc_info.value.proofs] == [
        Conclusion.SKIP
    ]
    assert kind in str(exc_info.value.proofs[0].proof)
    approve.assert_not_called()
    execute_source.assert_not_called()
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []


@pytest.mark.parametrize(("signature", "call", "kind"), _UNSUPPORTED_HELPER_KINDS)
def test_interpreter_refuses_helper_only_after_an_earlier_effect(
    tmp_path: Path, signature: str, call: str, kind: str
) -> None:
    # This harmless execution witness shows why proof must refuse the helper:
    # the interpreter's call-time restriction otherwise aborts after an effect.
    source, package = _ordinary_helper_program(tmp_path, signature, call)

    with pytest.raises(UnsupportedNode, match=f"{kind} parameters"):
        execute(
            source,
            source_name="plan.py",
            policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
            import_roots=[tmp_path],
        )

    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["safe"]
