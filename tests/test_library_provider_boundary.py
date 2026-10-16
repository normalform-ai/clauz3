"""A symbolic registration alone cannot authorize a new runtime provider."""

import importlib
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3._vendor.deal_solver._funcs import FUNCTIONS
from clauz3.approvals.client import ApprovalResponse
from clauz3.approvals.decisions import Decision
from clauz3.imports_policy import build_policy
from clauz3.interp import TrustedBoundaryError, UnsupportedNode, execute
from clauz3.prover import ProverConfigError, prove_path
from clauz3.runner import ApprovalDeniedError, RunConfigError, run_source


@pytest.fixture(autouse=True)
def _restore_library_models() -> Iterator[None]:
    # These keys name stdlib modules, so trusted-package cache eviction alone
    # cannot undo the registrations made by the fixture's trusted source.
    names = ("statistics.mean", "math.sqrt", "os.path.some_member")
    previous = {name: FUNCTIONS[name] for name in names if name in FUNCTIONS}
    try:
        yield
    finally:
        for name in names:
            FUNCTIONS.pop(name, None)
        FUNCTIONS.update(previous)


def _program(tmp_path: Path, *, captured_provider: bool) -> tuple[Path, Path]:
    package = tmp_path / f"library_provider_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
    )
    if captured_provider:
        library = "math"
        operation = "assert math.sqrt(4) == 2.0"
        model = (
            "import math\n"
            "@clauz3.solver('math.sqrt', signature=math.sqrt)\n"
            "def sqrt_of_four(value, *, ctx):\n"
            "    ctx.expected.add(value.m_eq(types.int.val(4, ctx=ctx), ctx=ctx))\n"
            "    return types.float.val(2.0, ctx=ctx)\n"
        )
    else:
        library = "statistics"
        operation = "statistics.mean([1, 2, 3])"
        model = (
            "@clauz3.solver('statistics.mean')\n"
            "def mean(values, *, ctx):\n"
            "    total = FUNCTIONS['builtins.sum'](values, ctx=ctx)\n"
            "    return total.m_truediv(values.m_len(ctx=ctx), ctx=ctx)\n"
        )
    (package / "contracts.py").write_text(
        "import clauz3\n"
        "from clauz3._vendor.deal_solver._funcs import FUNCTIONS\n"
        "from clauz3._vendor.deal_solver._proxies import types\n"
        "from clauz3.spec import ContractSpec, contract, effect\n"
        + model
        + "@contract\n"
        "def enough_records() -> ContractSpec:\n"
        "    return effect('record').count() >= 2\n"
    )
    entry = tmp_path / "plan.py"
    entry.write_text(
        f"import clauz3\nimport {library}\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import enough_records\n"
        "@clauz3.guarantee(enough_records())\n"
        "def main() -> None:\n"
        f"    record('first')\n    {operation}\n    record('second')\n"
    )
    return entry, package


def test_symbolic_registration_without_provider_is_refused_before_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry, package = _program(tmp_path, captured_provider=False)
    diagnostic = "'statistics' has no fixed interpreter provider for its modelled names"

    with pytest.raises(ProverConfigError, match=diagnostic):
        prove_path(entry, trusted_roots=[package], import_roots=[tmp_path])
    assert "statistics.mean" in FUNCTIONS
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.__file__ is not None
    assert Path(effects.__file__).resolve() == (package / "effects.py").resolve()
    assert effects.EVENTS == []

    approve = Mock(side_effect=AssertionError("approval must not be reached"))
    executor = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", executor)
    with pytest.raises(RunConfigError, match=diagnostic):
        run_source(
            entry.read_text(),
            source_name=str(entry),
            trusted_roots=[package],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )
    approve.assert_not_called()
    executor.assert_not_called()
    assert effects.EVENTS == []


def test_registered_model_can_use_an_existing_captured_provider(
    tmp_path: Path,
) -> None:
    entry, package = _program(tmp_path, captured_provider=True)
    proofs = prove_path(entry, trusted_roots=[package], import_roots=[tmp_path])
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
    assert "math.sqrt" in FUNCTIONS
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []

    outcome = execute(
        entry.read_text(),
        source_name=str(entry),
        policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
        import_roots=[tmp_path],
    )
    assert effects.EVENTS == ["first", "second"]
    assert outcome.effects == (
        (f"{package.name}.effects.record", ("first",), {}),
        (f"{package.name}.effects.record", ("second",), {}),
    )


@pytest.mark.parametrize("library", ["statistics", "math"])
def test_stale_registration_does_not_disable_a_later_unused_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, library: str
) -> None:
    entry, package = _program(tmp_path, captured_provider=False)
    with pytest.raises(ProverConfigError, match="no fixed interpreter provider"):
        prove_path(entry, trusted_roots=[package], import_roots=[tmp_path])
    assert "statistics.mean" in FUNCTIONS
    if library == "math":
        monkeypatch.setitem(
            FUNCTIONS, "math.missing_provider", FUNCTIONS["statistics.mean"]
        )

    entry.write_text(f"import {library}\ndef main() -> None:\n    assert True\n")
    proofs = prove_path(entry, import_roots=[tmp_path])
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
    assert (
        execute(
            entry.read_text(),
            source_name=str(entry),
            policy=build_policy(import_roots=[tmp_path]),
            import_roots=[tmp_path],
        ).effects
        == ()
    )

    approve = Mock(return_value=ApprovalResponse(decision=Decision.REJECTED))
    executor = Mock(side_effect=AssertionError("a denied program cannot execute"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", executor)
    with pytest.raises(ApprovalDeniedError):
        run_source(
            entry.read_text(),
            source_name=str(entry),
            trusted_roots=[],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )
    approve.assert_called_once()
    executor.assert_not_called()


@pytest.mark.parametrize(
    ("imports", "helpers", "operation"),
    [
        ("import statistics as stats", "", "stats.mean([1, 2, 3])"),
        ("import statistics", "", "saved = statistics.mean\n    saved([1, 2, 3])"),
        ("import statistics", "", "saved = statistics\n    saved.mean([1, 2, 3])"),
        (
            "import statistics",
            "def forward(module):\n    return module.mean([1, 2, 3])\n",
            "forward(statistics)",
        ),
        ("from statistics import mean as average", "", "average([1, 2, 3])"),
        (
            "",
            "def helper():\n    import statistics as stats\n"
            "    return stats.mean([1, 2, 3])\n",
            "helper()",
        ),
        (
            "",
            "def helper():\n    from statistics import mean\n"
            "    return mean([1, 2, 3])\n",
            "helper()",
        ),
        (
            "",
            "def unused():\n    import statistics\n"
            "    return statistics.mean([1, 2, 3])\n",
            "assert True",
        ),
    ],
    ids=[
        "import-alias",
        "saved-member",
        "saved-module",
        "forwarded-module",
        "from-alias",
        "nested-import",
        "nested-from",
        "unused-helper-read",
    ],
)
def test_unavailable_member_use_is_refused_before_any_entry_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    imports: str,
    helpers: str,
    operation: str,
) -> None:
    entry, package = _program(tmp_path, captured_provider=False)
    source = entry.read_text().replace("import statistics\n", imports + "\n")
    source = source.replace("statistics.mean([1, 2, 3])", operation)
    source = source.replace("@clauz3.guarantee", helpers + "@clauz3.guarantee")
    entry.write_text(source)
    diagnostic = "no fixed interpreter provider"
    with pytest.raises(ProverConfigError, match=diagnostic):
        prove_path(entry, trusted_roots=[package], import_roots=[tmp_path])
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []

    approve = Mock(side_effect=AssertionError("approval must not be reached"))
    executor = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", executor)
    with pytest.raises(RunConfigError, match=diagnostic):
        run_source(
            entry.read_text(),
            source_name=str(entry),
            trusted_roots=[package],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )
    approve.assert_not_called()
    executor.assert_not_called()
    assert effects.EVENTS == []

    with pytest.raises(TrustedBoundaryError, match=diagnostic):
        execute(
            entry.read_text(),
            source_name=str(entry),
            policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
            import_roots=[tmp_path],
        )
    assert effects.EVENTS == []


def test_missing_math_member_does_not_hide_a_supported_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry, package = _program(tmp_path, captured_provider=True)
    proofs = prove_path(entry, trusted_roots=[package], import_roots=[tmp_path])
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
    monkeypatch.setitem(FUNCTIONS, "math.missing_provider", FUNCTIONS["math.sqrt"])
    source = entry.read_text().replace(
        "assert math.sqrt(4) == 2.0",
        "assert math.sqrt(4) == 2.0\n"
        "    saved = math.sin\n"
        "    assert saved(0) == 0\n"
        "    assert identity(1) == 1",
    )
    entry.write_text(
        source.replace(
            "@clauz3.guarantee",
            "def identity(math: int) -> int:\n    return math\n@clauz3.guarantee",
        )
    )
    proofs = prove_path(entry, trusted_roots=[package], import_roots=[tmp_path])
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
    execute(
        entry.read_text(),
        source_name=str(entry),
        policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
        import_roots=[tmp_path],
    )
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["first", "second"]


@pytest.mark.parametrize("shape", ["module", "dotted", "helper", "alias", "from"])
@pytest.mark.parametrize("missing_provider", [False, True])
def test_dotted_import_cannot_defer_an_import_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shape: str,
    missing_provider: bool,
) -> None:
    entry, package = _program(tmp_path, captured_provider=not missing_provider)
    if missing_provider:
        contracts = package / "contracts.py"
        contracts.write_text(
            contracts.read_text().replace("statistics.mean", "os.path.some_member")
        )
    original_operation = (
        "statistics.mean([1, 2, 3])"
        if missing_provider
        else "assert math.sqrt(4) == 2.0"
    )
    source = (
        entry.read_text()
        .replace("import statistics\n", "")
        .replace("import math\n", "")
    )
    # Load the genuine trusted layer through a harmless valid request so its
    # event recorder is observable even when the next preflight refuses before
    # trusted initialization. No entry body executes during this setup.
    entry.write_text(source.replace(original_operation, "assert True"))
    control = prove_path(entry, trusted_roots=[package], import_roots=[tmp_path])
    assert [proof.proof.conclusion for proof in control] == [Conclusion.OK]
    statement = {
        "module": "assert os.path.join('a', 'b') == 'a/b'",
        "helper": "assert joined() == 'a/b'",
        "dotted": "import os.path\n    assert os.path.join('a', 'b') == 'a/b'",
        "alias": "import os.path as path\n    assert path.join('a', 'b') == 'a/b'",
        "from": "from os import path\n    assert path.join('a', 'b') == 'a/b'",
    }[shape]
    source = source.replace(original_operation, statement)
    if shape == "module":
        source = "import os.path\n" + source
    elif shape == "helper":
        source = source.replace(
            "@clauz3.guarantee",
            "def joined() -> str:\n    import os.path\n"
            "    return os.path.join('a', 'b')\n@clauz3.guarantee",
        )
    entry.write_text(source)
    if shape in {"module", "dotted", "helper"}:
        diagnostic = "import os.path.*not in the proved subset"
        with pytest.raises(ProverConfigError, match=diagnostic):
            prove_path(
                entry,
                trusted_roots=[package],
                import_roots=[tmp_path],
                extra_allowed_imports=["os"],
            )
        effects = importlib.import_module(f"{package.name}.effects")
        approve = Mock(side_effect=AssertionError("approval must not be reached"))
        executor = Mock(side_effect=AssertionError("execution must not be reached"))
        monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
        monkeypatch.setattr("clauz3.runner._execute_source", executor)
        with pytest.raises(RunConfigError, match=diagnostic):
            run_source(
                source,
                source_name=str(entry),
                trusted_roots=[package],
                import_roots=[tmp_path],
                extra_allowed_imports=["os"],
                approval_service_url="http://approval.invalid",
            )
        approve.assert_not_called()
        executor.assert_not_called()
        with pytest.raises(UnsupportedNode, match=diagnostic):
            execute(
                source,
                source_name=str(entry),
                policy=build_policy(
                    trusted_roots=[package],
                    import_roots=[tmp_path],
                    extra_allowed=["os"],
                ),
                import_roots=[tmp_path],
            )
        assert effects.EVENTS == []
    else:
        proofs = prove_path(
            entry,
            trusted_roots=[package],
            import_roots=[tmp_path],
            extra_allowed_imports=["os"],
        )
        # These import shapes have a runtime provider, but os.path.join no
        # longer receives a platform-specific proof model. Keep the native
        # effect-order witness below while pinning the symbolic refusal.
        assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
        diagnostic = (
            "platform-dependent os.path.join"
            if shape == "alias"
            else "no unambiguous definition"
        )
        assert diagnostic in str(proofs[0].proof)
        execute(
            source,
            source_name=str(entry),
            policy=build_policy(
                trusted_roots=[package], import_roots=[tmp_path], extra_allowed=["os"]
            ),
            import_roots=[tmp_path],
        )
        effects = importlib.import_module(f"{package.name}.effects")
        assert effects.EVENTS == ["first", "second"]
