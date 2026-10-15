"""Entry-selected library imports cannot initialize agent-authored Python."""

import json
import os
import subprocess
import sys
from pathlib import Path
from textwrap import indent
from typing import cast
from uuid import uuid4

import pytest

import clauz3

_PROBE = r"""
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import prove_path
from clauz3.runner import run_source

root = Path(sys.argv[1])
options = json.loads(sys.argv[2])
package = root / options['package']
cases = root / 'cases'
entry = cases / 'plan.py'
roots = [cases, root]
trusted_roots = [
    package, *(root / path for path in options.get('additional_trusted', [])),
]
extras = options['extras']
source = entry.read_text()

# Load only the genuine, explicitly trusted recorder. No agent candidate is
# involved in this setup, and each probe has a fresh interpreter process.
sys.path.insert(0, str(root))
effects = importlib.import_module(package.name + '.effects')
sys.path.pop(0)
for name in options.get('fresh', []):
    sys.modules.pop(name, None)

accesses = []
cache_mode = options.get('cache_mode')
if cache_mode == 'wrong-origin':
    cached = ModuleType('statistics')
    cached.__file__ = str(cases / 'cached-statistics.py')
    def access(name):
        accesses.append(name)
        effects.record('bad')
        raise AttributeError(name)
    cached.__getattr__ = access
    sys.modules['statistics'] = cached
elif cache_mode == 'parent-path':
    json.__path__ = [str(cases / 'json')]
    sys.modules.pop('json.tool', None)
elif cache_mode == 'dependency':
    cached = ModuleType('numbers')
    cached.__file__ = str(cases / 'numbers.py')
    def access(name):
        accesses.append(name)
        effects.record('bad')
        raise AttributeError(name)
    cached.__getattr__ = access
    sys.modules['numbers'] = cached
elif cache_mode == 'trusted-parent-path':
    name = options['trusted_parent']
    # Establish the real namespace without searching the agent directory,
    # then redirect only its cached child search path to the agent's tree.
    sys.path.insert(0, str(root))
    parent = importlib.import_module(name)
    sys.path.pop(0)
    parent.__path__ = [str(cases / name)]

approve = Mock(side_effect=AssertionError('approval must not be reached'))
receipt = Mock(side_effect=AssertionError('receipt must not be consumed'))
executor = Mock(side_effect=AssertionError('executor must not be reached'))
result = {}
try:
    if options['consumer'] == 'runtime':
        policy = build_policy(trusted_roots=trusted_roots, import_roots=roots,
                              extra_allowed=extras)
        outcome = execute(source, source_name=str(entry), policy=policy,
                          import_roots=roots)
        result['effects'] = outcome.effects
    elif options['consumer'] == 'prove':
        proofs = prove_path(entry, trusted_roots=trusted_roots, import_roots=roots,
                            extra_allowed_imports=extras)
        result['conclusions'] = [proof.proof.conclusion.name for proof in proofs]
    else:
        with patch('clauz3.runner.submit_approval_request', approve), \
             patch('clauz3.runner._verify_receipt_or_refuse', receipt), \
             patch('clauz3.runner._execute_source', executor):
            run_source(source, source_name=str(entry), trusted_roots=trusted_roots,
                       import_roots=roots, extra_allowed_imports=extras,
                       approval_service_url='http://approval.invalid')
except Exception as exc:
    result['error'] = type(exc).__name__
    result['message'] = str(exc)

result.update(events=effects.EVENTS, accesses=accesses,
              approval_calls=approve.call_count, receipt_calls=receipt.call_count,
              execution_calls=executor.call_count)
print(json.dumps(result))
"""


def _layer(tmp_path: Path) -> Path:
    package = tmp_path / f"runtime_origins_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def no_bad() -> ContractSpec:\n"
        "    return effect('record').where(lambda e: e.value == 'bad').empty()\n"
    )
    (tmp_path / "cases").mkdir()
    return package


def _source(
    package: Path, *, imports: str = "", helpers: str = "", body: str = "pass"
) -> None:
    source = (
        "import clauz3\n"
        + imports
        + f"from {package.name}.effects import record\n"
        + f"from {package.name}.contracts import no_bad\n"
        + helpers
        + "@clauz3.guarantee(no_bad())\n"
        + "def main() -> None:\n"
        + indent(body, "    ")
        + "\n"
    )
    (package.parent / "cases" / "plan.py").write_text(source)


def _initializer(package: Path) -> str:
    return f"from {package.name}.effects import record\nrecord('bad')\n"


def _probe(
    package: Path,
    consumer: str,
    *,
    extras: tuple[str, ...] = (),
    fresh: tuple[str, ...] = (),
    cache_mode: str = "",
    additional_trusted: tuple[Path, ...] = (),
    trusted_parent: str = "",
) -> dict[str, object]:
    source_root = Path(clauz3.__file__).resolve().parent.parent
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            _PROBE,
            str(package.parent),
            json.dumps(
                {
                    "package": package.name,
                    "consumer": consumer,
                    "extras": extras,
                    "fresh": fresh,
                    "cache_mode": cache_mode,
                    "additional_trusted": [
                        str(path.relative_to(package.parent))
                        for path in additional_trusted
                    ],
                    "trusted_parent": trusted_parent,
                }
            ),
        ],
        cwd=source_root.parent,
        env={**os.environ, "PYTHONPATH": str(source_root)},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    return cast(dict[str, object], json.loads(completed.stdout))


def _assert_origin_refusal(result: dict[str, object], consumer: str) -> None:
    expected_error = {
        "runtime": "TrustedBoundaryError",
        "run": "RunConfigError",
        "prove": "ProverConfigError",
    }[consumer]
    assert result.get("error") == expected_error, result
    assert "nontrusted import" in str(result.get("message")), result
    assert result["events"] == []
    assert result["accesses"] == []
    assert result["approval_calls"] == 0
    assert result["receipt_calls"] == 0
    assert result["execution_calls"] == 0


@pytest.mark.parametrize("consumer", ["runtime", "run", "prove"])
@pytest.mark.parametrize("shape", ["stdlib", "opt-in", "package-parent"])
def test_shadowed_library_origins_cannot_call_a_genuine_trusted_effect(
    tmp_path: Path, consumer: str, shape: str
) -> None:
    package = _layer(tmp_path)
    cases = tmp_path / "cases"
    name = "statistics" if shape == "stdlib" else f"opted_{uuid4().hex}"
    if shape == "package-parent":
        parent = cases / name
        parent.mkdir()
        (parent / "__init__.py").write_text(_initializer(package))
        (parent / "child.py").write_text(_initializer(package))
        name += ".child"
    else:
        (cases / f"{name}.py").write_text(_initializer(package))
    extras = () if shape == "stdlib" else (name,)
    _source(package, imports=f"import {name}\n")

    result = _probe(package, consumer, extras=extras, fresh=(name,))

    _assert_origin_refusal(result, consumer)


@pytest.mark.parametrize("consumer", ["runtime", "run"])
@pytest.mark.parametrize("called", [True, False], ids=["called", "unused"])
def test_imports_in_helpers_are_checked_before_any_entry_effect(
    tmp_path: Path, consumer: str, called: bool
) -> None:
    package = _layer(tmp_path)
    (tmp_path / "cases" / "statistics.py").write_text(_initializer(package))
    _source(
        package,
        helpers="def helper() -> None:\n    import statistics\n",
        body="record('first')\nhelper()" if called else "record('safe')",
    )

    result = _probe(package, consumer, fresh=("statistics",))

    _assert_origin_refusal(result, consumer)


@pytest.mark.parametrize("consumer", ["runtime", "run"])
def test_cached_wrong_origin_is_refused_without_dynamic_attribute_access(
    tmp_path: Path, consumer: str
) -> None:
    package = _layer(tmp_path)
    _source(package, imports="import statistics\n")

    result = _probe(package, consumer, cache_mode="wrong-origin")

    _assert_origin_refusal(result, consumer)


@pytest.mark.parametrize("consumer", ["runtime", "run"])
def test_cached_package_path_cannot_select_an_agent_child_initializer(
    tmp_path: Path, consumer: str
) -> None:
    package = _layer(tmp_path)
    child_dir = tmp_path / "cases" / "json"
    child_dir.mkdir()
    (child_dir / "tool.py").write_text(_initializer(package))
    _source(package, imports="import json.tool\n")

    result = _probe(package, consumer, cache_mode="parent-path")

    _assert_origin_refusal(result, consumer)


@pytest.mark.parametrize("cached", [False, True], ids=["source", "cached"])
def test_genuine_statistics_does_not_initialize_an_agent_dependency(
    tmp_path: Path, cached: bool
) -> None:
    package = _layer(tmp_path)
    (tmp_path / "cases" / "numbers.py").write_text(_initializer(package))
    _source(package, imports="import statistics\n")

    result = _probe(
        package,
        "runtime",
        fresh=("statistics", "numbers"),
        cache_mode="dependency" if cached else "",
    )

    assert "error" not in result, result
    assert result["events"] == []
    assert result["accesses"] == []
    assert result["effects"] == []


@pytest.mark.parametrize("consumer", ["runtime", "prove"])
def test_verified_library_bindings_and_trusted_calls_remain_usable(
    tmp_path: Path, consumer: str
) -> None:
    package = _layer(tmp_path)
    _source(
        package,
        imports="import math\nimport posixpath as paths\nimport typing\n",
        helpers=(
            "def identity(values: typing.List[int]) -> typing.List[int]:\n"
            "    return values\n"
        ),
        body=(
            "assert math.sin(0.0) == 0.0\n"
            "assert paths.join('safe', 'file') == 'safe/file'\n"
            "assert identity([1]) == [1]\n"
            "record('safe')"
        ),
    )

    result = _probe(package, consumer, extras=("posixpath",))

    assert "error" not in result, result
    if consumer == "runtime":
        assert result["events"] == ["safe"]
        assert result["effects"] == [[f"{package.name}.effects.record", ["safe"], {}]]
    else:
        assert result["conclusions"] == ["OK"]
        assert result["events"] == []


@pytest.mark.parametrize("bad", [False, True], ids=["quiet", "direct-forbidden-effect"])
def test_native_guarantee_controls_still_distinguish_real_effects(
    tmp_path: Path, bad: bool
) -> None:
    package = _layer(tmp_path)
    _source(package, body="record('bad')" if bad else "pass")

    proof = _probe(package, "prove")
    runtime = _probe(package, "runtime")

    assert proof.get("conclusions") == ["FAIL" if bad else "OK"], proof
    assert proof["events"] == []
    assert "error" not in runtime, runtime
    assert runtime["events"] == (["bad"] if bad else [])
    assert runtime["effects"] == (
        [[f"{package.name}.effects.record", ["bad"], {}]] if bad else []
    )


def _namespace_layer(package: Path) -> tuple[Path, str]:
    name = f"namespace_tools_{uuid4().hex}"
    trusted = package.parent / name / "domain" / "trusted"
    trusted.mkdir(parents=True)
    (trusted / "effects.py").write_text(
        "import clauz3\n@clauz3.trusted\ndef ping() -> None:\n    pass\n"
    )
    return trusted, name


@pytest.mark.parametrize("consumer", ["runtime", "run", "prove"])
@pytest.mark.parametrize("shadow", ["parent-initializer", "leaf", "cached-parent"])
def test_trusted_import_chains_are_checked_before_any_shadow_initializer(
    tmp_path: Path, consumer: str, shadow: str
) -> None:
    package = _layer(tmp_path)
    trusted, name = _namespace_layer(package)
    shadow_parent = tmp_path / "cases" / name
    shadow_parent.mkdir()
    if shadow == "parent-initializer":
        (shadow_parent / "__init__.py").write_text(_initializer(package))
    else:
        shadow_leaf = shadow_parent / "domain" / "trusted"
        shadow_leaf.mkdir(parents=True)
        (shadow_leaf / "effects.py").write_text(_initializer(package))
    _source(package, imports=f"from {name}.domain.trusted.effects import ping\n")

    result = _probe(
        package,
        consumer,
        additional_trusted=(trusted,),
        cache_mode="trusted-parent-path" if shadow == "cached-parent" else "",
        trusted_parent=name,
    )

    # The recorder is a separate genuine trusted layer loaded without the
    # agent path. A bad event here comes only from executing the shadow chain.
    assert result["events"] == [], result
    assert result["accesses"] == []
    expected_error = {
        "runtime": "TrustedBoundaryError",
        "run": "RunConfigError",
        "prove": "ProverConfigError",
    }[consumer]
    assert result.get("error") == expected_error, result
    assert "trusted" in str(result.get("message")), result
    assert result["approval_calls"] == 0
    assert result["receipt_calls"] == 0
    assert result["execution_calls"] == 0


@pytest.mark.parametrize("consumer", ["runtime", "prove"])
def test_genuine_trusted_namespace_chain_remains_usable(
    tmp_path: Path, consumer: str
) -> None:
    package = _layer(tmp_path)
    trusted, name = _namespace_layer(package)
    _source(
        package,
        imports=f"from {name}.domain.trusted.effects import ping\n",
        body="ping()",
    )

    result = _probe(package, consumer, additional_trusted=(trusted,))

    assert "error" not in result, result
    assert result["events"] == []
    if consumer == "runtime":
        assert result["effects"] == [[f"{name}.domain.trusted.effects.ping", [], {}]]
    else:
        assert result["conclusions"] == ["OK"]
