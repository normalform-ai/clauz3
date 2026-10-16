"""The prove-time module gate must not execute an agent's import candidates."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import cast
from unittest.mock import Mock
from uuid import uuid4

import pytest

import clauz3
from clauz3 import interp
from clauz3._vendor.deal_solver import Conclusion
from clauz3.approvals.authority import ApprovedLayer
from clauz3.approvals.decisions import Decision
from clauz3.approvals.policy import ApprovalPolicy, Rule, evaluate_policy
from clauz3.imports_policy import build_policy
from clauz3.interp import library_origins
from clauz3.prover import ProverConfigError, prove_path

_IMPORT_PROBE = """
import json
import sys
from pathlib import Path
from types import ModuleType

from clauz3.approvals.authority import ApprovedLayer
from clauz3.approvals.policy import ApprovalPolicy, Rule, evaluate_policy
from clauz3.imports_policy import build_policy
from clauz3.interp import execute, target_node
from clauz3.prover import prove_path

consumer, module, directory, raw_extras, poison = sys.argv[1:]
root = Path(directory)
entry = root / 'plan.py'
source = entry.read_text()
extras = json.loads(raw_extras)
sys.modules.pop(module, None)
if poison == 'yes':
    cached = ModuleType(module)
    def access(name):
        (root / 'cached-access.txt').write_text(name)
        raise AttributeError(name)
    cached.__getattr__ = access
    sys.modules[module] = cached

try:
    if consumer == 'prove':
        results = prove_path(entry, import_roots=[root], extra_allowed_imports=extras)
        result = {'conclusions': [item.proof.conclusion.name for item in results]}
    elif consumer == 'approval':
        policy = ApprovalPolicy(
            imports=(),
            rules=(Rule(name='empty', decision='auto_approved', clauses=()),),
        )
        decision = evaluate_policy(policy, {
            'program': source,
            'target': 'main',
            'trusted_roots': [],
            'import_roots': [str(root)],
        }, authorized_layers=(ApprovedLayer(trusted_roots=(), import_roots=(root,)),))
        result = {'decision': None if decision is None else decision.decision}
    elif consumer == 'runtime':
        policy = build_policy(import_roots=[root], extra_allowed=extras)
        outcome = execute(source, source_name=str(entry), policy=policy,
                          import_roots=[root])
        result = {'effects': list(outcome.effects)}
    else:
        policy = build_policy(import_roots=[root], extra_allowed=extras)
        node = target_node(source, target='main', source_name=str(entry),
                           policy=policy, import_roots=[root])
        result = {'target': node.name}
except Exception as exc:
    result = {'error': type(exc).__name__, 'message': str(exc)}
print(json.dumps(result))
"""


def _probe(
    tmp_path: Path,
    *,
    consumer: str,
    module: str,
    extras: tuple[str, ...] = (),
    poison: bool = False,
) -> dict[str, object]:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            _IMPORT_PROBE,
            consumer,
            module,
            str(tmp_path),
            json.dumps(extras),
            "yes" if poison else "no",
        ],
        # Make the candidate visible through the configured import root, after
        # the framework itself has loaded. This isolates gate execution from
        # unrelated Python startup imports through the working directory.
        cwd=Path(clauz3.__file__).resolve().parents[2],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(clauz3.__file__).resolve().parent.parent),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    return cast(dict[str, object], json.loads(completed.stdout))


def _marker_source(marker: Path) -> str:
    return (
        f"with open({str(marker)!r}, 'w', encoding='utf-8') as stream:\n"
        "    stream.write('executed')\n"
    )


@pytest.mark.parametrize("consumer", ["prove", "approval", "preflight"])
def test_gate_refuses_a_stdlib_shadow_without_execution(
    tmp_path: Path, consumer: str
) -> None:
    marker = tmp_path / "agent-executed.txt"
    (tmp_path / "statistics.py").write_text(_marker_source(marker))
    (tmp_path / "plan.py").write_text(
        "import statistics\ndef main() -> None:\n    assert True\n"
    )

    result = _probe(tmp_path, consumer=consumer, module="statistics")

    if consumer == "approval":
        assert result == {"decision": None}
    else:
        expected = (
            "ProverConfigError" if consumer == "prove" else "TrustedBoundaryError"
        )
        assert result["error"] == expected
        assert "nontrusted import" in str(result["message"])
    assert not marker.exists()


@pytest.mark.parametrize("dotted", [False, True], ids=["module", "package-child"])
def test_gate_refuses_an_opted_in_source_origin_without_execution(
    tmp_path: Path, dotted: bool
) -> None:
    name = f"opted_in_{uuid4().hex}"
    marker = tmp_path / "agent-executed.txt"
    parent_marker = tmp_path / "parent-executed.txt"
    if dotted:
        package = tmp_path / name
        package.mkdir()
        (package / "__init__.py").write_text(_marker_source(parent_marker))
        (package / "child.py").write_text(_marker_source(marker))
        module = f"{name}.child"
    else:
        (tmp_path / f"{name}.py").write_text(_marker_source(marker))
        module = name
    (tmp_path / "plan.py").write_text(
        f"import {module} as library\ndef main() -> None:\n    assert True\n"
    )

    result = _probe(tmp_path, consumer="prove", module=module, extras=(module,))

    assert result["error"] == "ProverConfigError"
    assert "nontrusted import" in str(result["message"])
    assert not marker.exists()
    assert not parent_marker.exists()


@pytest.mark.parametrize("stdlib_shadow", [True, False], ids=["stdlib", "opt-in"])
def test_runtime_refuses_the_same_nontrusted_origins_as_the_gate(
    tmp_path: Path, stdlib_shadow: bool
) -> None:
    """Both direct execution and proof refuse before initializing agent code."""
    name = "statistics" if stdlib_shadow else f"opted_in_{uuid4().hex}"
    extras = () if stdlib_shadow else (name,)
    marker = tmp_path / "agent-executed.txt"
    (tmp_path / f"{name}.py").write_text(_marker_source(marker))
    (tmp_path / "plan.py").write_text(
        f"import {name}\ndef main() -> None:\n    assert True\n"
    )

    gate = _probe(tmp_path, consumer="prove", module=name, extras=extras)
    assert gate["error"] == "ProverConfigError"
    assert "nontrusted import" in str(gate["message"])
    assert not marker.exists()

    runtime = _probe(tmp_path, consumer="runtime", module=name, extras=extras)
    assert runtime["error"] == "TrustedBoundaryError"
    assert "nontrusted import" in str(runtime["message"])
    assert not marker.exists()


def test_cached_module_dynamic_attributes_are_not_read_by_the_gate(
    tmp_path: Path,
) -> None:
    (tmp_path / "plan.py").write_text(
        "from math import sin\ndef main() -> None:\n    assert True\n"
    )

    result = _probe(tmp_path, consumer="prove", module="math", poison=True)

    assert result["error"] == "ProverConfigError"
    assert "nontrusted import" in str(result["message"])
    assert not (tmp_path / "cached-access.txt").exists()


@pytest.mark.parametrize("outside", [True, False], ids=["outside", "authorized"])
def test_unused_from_import_still_checks_a_child_module_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outside: bool
) -> None:
    installed = tmp_path / "installed"
    name = f"origin_child_{uuid4().hex}"
    package = installed / name
    package.mkdir(parents=True)
    marker = tmp_path / "initialized.txt"
    (package / "__init__.py").write_text(_marker_source(marker))
    child = package / "child.py"
    if outside:
        candidate = tmp_path / "agent.py"
        candidate.write_text(_marker_source(marker))
        child.symlink_to(candidate)
    else:
        child.write_text(_marker_source(marker))
    monkeypatch.setattr(library_origins, "_AUTHORITY_ROOTS", (installed.resolve(),))
    source = f"def unused() -> None:\n    from {name} import child\n"
    policy = build_policy(import_roots=[installed], extra_allowed=[name])

    def validate() -> None:
        interp.validate_import_origins(
            source, source_name="plan.py", policy=policy, import_roots=[installed]
        )

    if outside:
        with pytest.raises(interp.TrustedBoundaryError, match="nontrusted import"):
            validate()
    else:
        validate()
    assert not marker.exists()
    assert name not in sys.modules
    assert f"{name}.child" not in sys.modules


def _layer(tmp_path: Path) -> tuple[Path, Path]:
    package = tmp_path / f"module_gate_origin_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    marker = tmp_path / "trusted-initialized.txt"
    (package / "effects.py").write_text(
        "import clauz3\n"
        f"with open({str(marker)!r}, 'w', encoding='utf-8') as stream:\n"
        "    stream.write('trusted')\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    pass\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def only_bob() -> ContractSpec:\n"
        "    return effect('record').all(lambda e: e.value == 'bob')\n"
    )
    return package, marker


def test_unenumerated_trusted_cache_subclass_cannot_execute_attribute_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, _marker = _layer(tmp_path)
    cached_name = f"{package.name}.pwn"
    marker = tmp_path / "cached-hook.txt"

    class Poison(ModuleType):
        def __getattribute__(self, name: str) -> object:
            marker.write_text(name)
            raise AssertionError("a cached module subclass must not be inspected")

    monkeypatch.setitem(sys.modules, cached_name, Poison(cached_name))
    entry = tmp_path / "plan.py"
    entry.write_text(
        f"from {cached_name} import helper\ndef main() -> None:\n    assert True\n"
    )

    with pytest.raises(ProverConfigError, match="trusted module"):
        prove_path(entry, trusted_roots=[package], import_roots=[tmp_path])

    assert not marker.exists()


def _policy(package: Path, *, empty: bool = False) -> ApprovalPolicy:
    return ApprovalPolicy(
        imports=(("pol", f"{package.name}.contracts"),),
        rules=(
            Rule(
                name="allow",
                decision=Decision.AUTO_APPROVED,
                clauses=() if empty else ("pol.only_bob()",),
            ),
        ),
    )


def _request(source: str, package: Path) -> dict[str, object]:
    return {
        "program": source,
        "target": "main",
        "trusted_roots": [str(package)],
        "import_roots": [str(package.parent)],
    }


@pytest.mark.parametrize(
    "source",
    [
        "import math\nimport os\ndef main() -> None:\n    assert True\n",
        "import math\ndef unused() -> None:\n    import os\n"
        "def main() -> None:\n    assert True\n",
    ],
    ids=["module-import", "unused-function-import"],
)
@pytest.mark.parametrize("consumer", ["prove", "approval", "empty-approval"])
def test_static_import_validation_precedes_module_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    source: str,
    consumer: str,
) -> None:
    package, marker = _layer(tmp_path)
    check_module = Mock(wraps=interp.check_module_level)
    monkeypatch.setattr(interp, "check_module_level", check_module)
    reason = "import of 'os' is not in the allowlist"

    if consumer == "prove":
        entry = tmp_path / "plan.py"
        entry.write_text(source)
        with pytest.raises(ProverConfigError, match=reason):
            prove_path(entry, trusted_roots=[package], import_roots=[tmp_path])
    else:
        decision = evaluate_policy(
            _policy(package, empty=consumer == "empty-approval"),
            _request(source, package),
            authorized_layers=(
                ApprovedLayer(trusted_roots=(package,), import_roots=(tmp_path,)),
            ),
        )
        assert decision is None
        assert reason in capsys.readouterr().err

    check_module.assert_not_called()
    assert not marker.exists()


@pytest.mark.parametrize(
    "source",
    [
        "import math\nimport typing\nLIMIT: typing.Final[int] = 1\n"
        "def main() -> None:\n    assert math.sin(0) == 0.0\n",
        "from math import sin as sine\n"
        "def main() -> None:\n    assert sine(0) == 0.0\n",
    ],
    ids=["module-and-annotation", "from-import-alias"],
)
def test_supported_library_surface_still_proves(tmp_path: Path, source: str) -> None:
    entry = tmp_path / "plan.py"
    entry.write_text(source)

    results = prove_path(entry)

    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


def test_explicit_trusted_layer_initialization_and_contracts_remain_valid(
    tmp_path: Path,
) -> None:
    package, marker = _layer(tmp_path)
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import only_bob\n"
        "@clauz3.guarantee(only_bob())\n"
        "def main() -> None:\n"
        "    record('bob')\n"
    )
    entry = tmp_path / "plan.py"
    entry.write_text(source)

    results = prove_path(entry, trusted_roots=[package], import_roots=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.OK]
    assert marker.read_text() == "trusted"
    decision = evaluate_policy(
        _policy(package),
        _request(source, package),
        authorized_layers=(
            ApprovedLayer(trusted_roots=(package,), import_roots=(tmp_path,)),
        ),
    )
    assert decision is not None
    assert decision.decision == "auto_approved"


_NAMESPACE_PROBE = """
import contextlib
import io
import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

from clauz3.approvals.authority import ApprovedLayer
from clauz3.approvals.policy import ApprovalPolicy, Rule, evaluate_policy
from clauz3.imports_policy import build_policy
from clauz3.interp import execute, target_node
from clauz3.prover import prove_path, prove_text
from clauz3.runner import run_source

consumer, directory, domain = sys.argv[1:]
root = Path(directory)
cases = root / 'cases'
trusted = root / 'tools' / domain / 'trusted'
prefix = f'tools.{domain}.trusted'
entry = cases / 'plan.py'
source = entry.read_text()
roots = [root, cases]
approve = Mock(side_effect=RuntimeError('mock approval reached'))
executor = Mock(side_effect=RuntimeError('mock execution reached'))
stderr = io.StringIO()
try:
    with contextlib.redirect_stderr(stderr):
        if consumer == 'prove':
            proofs = prove_path(entry, trusted_roots=[trusted], import_roots=roots)
            result = {'conclusions': [p.proof.conclusion.name for p in proofs]}
        elif consumer in ('approval', 'empty-approval'):
            clauses = () if consumer == 'empty-approval' else ('pol.only_bob()',)
            policy = ApprovalPolicy(
                imports=(('pol', f'{prefix}.contracts'),),
                rules=(Rule(name='allow', decision='auto_approved',
                            clauses=clauses),),
            )
            decision = evaluate_policy(policy, {
                'program': source, 'target': 'main',
                'trusted_roots': [str(trusted)],
                'import_roots': [str(path) for path in roots],
            }, authorized_layers=(ApprovedLayer(trusted_roots=(trusted,),
                                                import_roots=tuple(roots)),))
            result = {'decision': None if decision is None else decision.decision}
        elif consumer == 'target-node':
            # Establish only the actual configured trusted layer, matching
            # callers that already proved/loaded it. No candidate pwn import.
            prove_text('def main() -> None:\\n    assert True\\n',
                       trusted_roots=[trusted], import_paths=roots)
            node = target_node(source, target='main', source_name=str(entry),
                policy=build_policy(trusted_roots=[trusted], import_roots=roots),
                import_roots=roots)
            result = {'target': node.name}
        elif consumer == 'runtime':
            outcome = execute(source, source_name=str(entry),
                policy=build_policy(trusted_roots=[trusted], import_roots=roots),
                import_roots=roots)
            result = {'effects': list(outcome.effects)}
        else:
            with patch('clauz3.runner.submit_approval_request', approve), \\
                 patch('clauz3.runner._execute_source', executor):
                run_source(source, source_name=str(entry), trusted_roots=[trusted],
                    import_roots=roots, approval_service_url='http://approval.invalid')
            result = {'run_returned': True}
except Exception as exc:
    result = {'error': type(exc).__name__, 'message': str(exc)}
result.update(stderr=stderr.getvalue(), approval_calls=approve.call_count,
              execution_calls=executor.call_count,
              entry_executed=(root / 'entry-executed.txt').exists(),
              agent_executed=(root / 'agent-executed.txt').exists(),
              trusted_initialized=(root / 'trusted-initialized.txt').exists())
print(json.dumps(result))
"""


def _namespace_probe(
    tmp_path: Path, *, consumer: str, outside_module: bool, body_import: bool = False
) -> dict[str, object]:
    domain = f"domain_{uuid4().hex}"
    trusted = tmp_path / "tools" / domain / "trusted"
    trusted.mkdir(parents=True)
    # Every parent and the trusted root itself is a PEP 420 namespace package,
    # matching the shipped examples. Nothing creates an __init__.py.
    (trusted / "effects.py").write_text(
        "import clauz3\n"
        + _marker_source(tmp_path / "trusted-initialized.txt")
        + "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        f"    with open({str(tmp_path / 'entry-executed.txt')!r}, 'w') as stream:\n"
        "        stream.write(value)\n"
    )
    (trusted / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def only_bob() -> ContractSpec:\n"
        "    return effect('record').all(lambda e: e.value == 'bob')\n"
    )
    cases = tmp_path / "cases"
    cases.mkdir()
    prefix = f"tools.{domain}.trusted"
    extra_import = ""
    if outside_module:
        candidate = cases / "tools" / domain / "trusted"
        candidate.mkdir(parents=True)
        (candidate / "pwn.py").write_text(
            _marker_source(tmp_path / "agent-executed.txt")
            + "def helper() -> None:\n    pass\n"
        )
        extra_import = f"from {prefix}.pwn import helper\n"
    (cases / "plan.py").write_text(
        ("" if body_import else extra_import)
        + "import clauz3\n"
        + f"from {prefix}.effects import record\n"
        + f"from {prefix}.contracts import only_bob\n"
        + "@clauz3.guarantee(only_bob())\n"
        + "def main() -> None:\n    record('bob')\n"
        + ("    " + extra_import if body_import else "")
    )
    completed = subprocess.run(
        [sys.executable, "-c", _NAMESPACE_PROBE, consumer, str(tmp_path), domain],
        cwd=Path(clauz3.__file__).resolve().parents[2],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(clauz3.__file__).resolve().parent.parent),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    return cast(dict[str, object], json.loads(completed.stdout))


@pytest.mark.parametrize(
    "consumer", ["prove", "approval", "empty-approval", "target-node", "run"]
)
def test_namespace_trusted_prefix_cannot_execute_a_module_outside_the_root(
    tmp_path: Path, consumer: str
) -> None:
    result = _namespace_probe(tmp_path, consumer=consumer, outside_module=True)

    assert result["agent_executed"] is False, result
    assert result["approval_calls"] == 0, result
    assert result["execution_calls"] == 0, result
    if consumer in ("approval", "empty-approval"):
        assert result.get("decision") is None, result
        message = str(result["stderr"])
    else:
        expected_error = {
            "prove": "ProverConfigError",
            "target-node": "TrustedBoundaryError",
            "run": "RunConfigError",
        }[consumer]
        assert result.get("error") == expected_error, result
        message = str(result["message"])
    assert "trusted root" in message, result
    assert ".pwn" in message, result


@pytest.mark.parametrize("consumer", ["prove", "run", "runtime"])
def test_trusted_prefix_body_import_is_checked_before_the_first_entry_effect(
    tmp_path: Path, consumer: str
) -> None:
    result = _namespace_probe(
        tmp_path, consumer=consumer, outside_module=True, body_import=True
    )

    assert result["trusted_initialized"] is True, result
    assert result["agent_executed"] is False, result
    assert result["entry_executed"] is False, result
    assert result["approval_calls"] == 0, result
    assert result["execution_calls"] == 0, result
    assert (
        result["error"]
        == {
            "prove": "ProverConfigError",
            "run": "RunConfigError",
            "runtime": "TrustedBoundaryError",
        }[consumer]
    )
    assert "trusted root" in str(result["message"])
    assert ".pwn" in str(result["message"])


@pytest.mark.parametrize(
    "consumer", ["prove", "approval", "empty-approval", "target-node", "run"]
)
def test_actual_trusted_namespace_modules_remain_usable(
    tmp_path: Path, consumer: str
) -> None:
    result = _namespace_probe(tmp_path, consumer=consumer, outside_module=False)

    assert result["agent_executed"] is False, result
    assert result["trusted_initialized"] is True, result
    assert result["execution_calls"] == 0, result
    if consumer == "prove":
        assert result.get("conclusions") == ["OK"], result
    elif consumer in ("approval", "empty-approval"):
        assert result.get("decision") == "auto_approved", result
    elif consumer == "target-node":
        assert result.get("target") == "main", result
    else:
        # The valid layer may reach approval; never send a real request or
        # execute the entry just to test module loading.
        assert result.get("message") == "mock approval reached", result
        assert result["approval_calls"] == 1, result
