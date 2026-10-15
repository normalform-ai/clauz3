"""Verified shared imports keep their histories without widening agent imports."""

import importlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest

from clauz3 import layer_state
from clauz3._vendor.deal_solver import Conclusion
from clauz3.layer_state import selected_layer
from clauz3.prover import ProverConfigError, prove_text

ROOT = Path(__file__).resolve().parents[1]


def _layer(
    directory: Path, name: str, imported: str, *, budget: int = 1
) -> tuple[Path, str]:
    root = directory / name
    root.mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "effects.py").write_text(
        "import clauz3\n"
        "from clauz3.spec import contract, effect\n"
        f"import {imported}\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
        "@contract\n"
        "def budget():\n"
        f"    return effect('record').count() <= {budget}\n"
    )
    source = (
        "import clauz3\n"
        f"from {name}.effects import record, budget\n"
        "@clauz3.guarantee(budget())\n"
        "def main() -> None:\n"
        "    record('one')\n"
    )
    return root, source


def _prove(root: Path, source: str) -> list[Conclusion]:
    return [
        result.proof.conclusion
        for result in prove_text(
            source, trusted_roots=[root], import_paths=[root.parent]
        )
    ]


def _cold(script: str, directory: Path, *arguments: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", script, *arguments],
        cwd=directory,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_agent_asyncio_import_remains_disallowed() -> None:
    with pytest.raises(ProverConfigError, match="asyncio"):
        prove_text("import asyncio\ndef main() -> None:\n    assert True\n")


@pytest.mark.parametrize("dependency", ["asyncio", "decimal"])
def test_cold_cli_trusted_stdlib_import_proves_and_executes(
    tmp_path: Path, dependency: str
) -> None:
    root, source = _layer(tmp_path, f"cold_tools_{uuid4().hex}", dependency)
    entry = tmp_path / "entry.py"
    entry.write_text(source)
    _cold(
        """
import sys
from clauz3.cli import main
from pathlib import Path
from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_text
from clauz3.imports_policy import build_policy
from clauz3.interp import execute

root, entry = map(Path, sys.argv[1:])
assert main(['prove', '--trusted-root', str(root), '--import-root',
             str(root.parent), str(entry)]) == 0
source = entry.read_text()
stronger = prove_text(source + "    record('two')\\n",
                      trusted_roots=[root], import_paths=[root.parent])
assert [result.proof.conclusion for result in stronger] == [Conclusion.FAIL]
effects = sys.modules[root.name + '.effects']
assert effects.EVENTS == []
outcome = execute(source, source_name=str(entry),
    policy=build_policy(trusted_roots=[root], import_roots=[root.parent]),
    import_roots=[root.parent])
assert effects.EVENTS == ['one']
assert [event[1] for event in outcome.effects] == [('one',)]
""",
        tmp_path,
        str(root),
        str(entry),
    )


def test_cold_sqlite_shared_identity_survives_a_b_a(tmp_path: Path) -> None:
    name = f"sqlite_tools_{uuid4().hex}"
    a, source = _layer(tmp_path / "A", name, "sqlite3")
    b, _ = _layer(tmp_path / "B", name, "sqlite3", budget=0)
    entry = tmp_path / "entry.py"
    entry.write_text(source)
    _cold(
        """
import sys
from pathlib import Path
from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_text

assert 'sqlite3' not in sys.modules
a, b, entry = map(Path, sys.argv[1:])
source = entry.read_text()
shared = None
first_effects = None
for root, expected in ((a, Conclusion.OK), (b, Conclusion.FAIL),
                       (a, Conclusion.OK)):
    results = prove_text(source, trusted_roots=[root], import_paths=[root.parent])
    assert [result.proof.conclusion for result in results] == [expected]
    if shared is None:
        shared = sys.modules['sqlite3']
        first_effects = sys.modules[a.name + '.effects']
    assert sys.modules['sqlite3'] is shared
    assert sys.modules[a.name + '.effects'] is first_effects
    assert first_effects.EVENTS == []
""",
        tmp_path,
        str(a),
        str(b),
        str(entry),
    )


@dataclass(frozen=True)
class _Shared:
    a: Path
    b: Path
    source: str
    carrier: str
    provider: str
    kind: str


def _shared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> _Shared:
    installed = tmp_path / "installed"
    installed.mkdir()
    suffix = uuid4().hex
    provider, carrier = f"provider_{suffix}", f"carrier_{suffix}"
    (installed / f"{provider}.py").write_text(
        "__all__ = ['decision']\n"
        "def decision() -> bool:\n    return True\n"
        "def replacement() -> bool:\n    return False\n"
    )
    binding = f"from {provider} import *\n"
    if kind == "alias":
        binding = (
            f"from {provider} import decision, replacement as alternate\n"
            "USE_ALTERNATE = True\n"
            "if USE_ALTERNATE:\n    decision = alternate\n"
        )
    (installed / f"{carrier}.py").write_text(binding)
    monkeypatch.syspath_prepend(str(installed))
    monkeypatch.setattr(
        layer_state,
        "_AUTHORITY_ROOTS",
        (*vars(layer_state)["_AUTHORITY_ROOTS"], installed),
    )
    name = f"shared_tools_{suffix}"
    a, source = _layer(tmp_path / "A", name, carrier)
    b, _ = _layer(tmp_path / "B", name, carrier)
    return _Shared(a, b, source, carrier, provider, kind)


@pytest.mark.parametrize("kind", ["star", "alias"])
def test_installed_binding_history_is_shared_between_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    case = _shared(tmp_path, monkeypatch, kind)
    assert _prove(case.a, case.source) == [Conclusion.OK]
    carrier = sys.modules[case.carrier]
    provider = sys.modules[case.provider]
    selected = provider.decision if kind == "star" else provider.replacement
    assert carrier.decision is selected
    for root in (case.b, case.a):
        assert _prove(root, case.source) == [Conclusion.OK]
        assert sys.modules[case.carrier] is carrier
        assert carrier.decision is selected


@pytest.mark.parametrize(
    ("kind", "change"),
    [
        ("star", "exports"),
        ("star", "binding"),
        ("star", "owner"),
        ("alias", "binding"),
        ("alias", "candidate"),
    ],
)
def test_shared_history_rechecks_objects_and_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, change: str
) -> None:
    case = _shared(tmp_path, monkeypatch, kind)
    assert _prove(case.a, case.source) == [Conclusion.OK]
    carrier = sys.modules[case.carrier]
    provider = sys.modules[case.provider]
    if change == "exports":
        monkeypatch.setattr(provider, "__all__", [])
    elif change == "binding":
        replacement = provider.replacement if kind == "star" else provider.decision
        monkeypatch.setattr(carrier, "decision", replacement)
    elif change == "candidate":
        monkeypatch.setattr(provider, "decision", provider.replacement)
    else:
        replacement_module = ModuleType(case.carrier)
        vars(replacement_module).update(vars(carrier))
        monkeypatch.setitem(sys.modules, case.carrier, replacement_module)
    with pytest.raises(ProverConfigError, match="cached layer.*binding"):
        _prove(case.b, case.source)


@pytest.mark.parametrize(
    ("kind", "changed"),
    [("star", "importer"), ("alias", "importer"), ("star", "exporter")],
)
def test_shared_history_rechecks_source_even_when_import_disappears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, changed: str
) -> None:
    case = _shared(tmp_path, monkeypatch, kind)
    assert _prove(case.a, case.source) == [Conclusion.OK]
    name = case.carrier if changed == "importer" else case.provider
    source = Path(sys.modules[name].__file__ or "")
    assert source.is_file()
    source.write_text(
        "# The cached importer still retains its original bindings.\n"
        if changed == "importer"
        else "__all__ = ['decision']\ndef decision() -> bool:\n    return False\n"
    )
    with pytest.raises(ProverConfigError, match="changed"):
        _prove(case.b, case.source)


@pytest.mark.parametrize("kind", ["star", "alias"])
def test_failed_activation_does_not_publish_shared_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    case = _shared(tmp_path, monkeypatch, kind)
    with (
        pytest.raises(RuntimeError, match="abort checked imports"),
        selected_layer(trusted_roots=[case.a], import_paths=[case.a.parent]) as image,
    ):
        importlib.import_module(f"{case.a.name}.effects")
        image.refresh_registrations()
        carrier = sys.modules[case.carrier]
        provider = sys.modules[case.provider]
        raise RuntimeError("abort checked imports")
    # Keep the exact checked-but-aborted identities available to another image.
    # It must not infer successful initialization from a history published early.
    monkeypatch.setitem(sys.modules, case.carrier, carrier)
    monkeypatch.setitem(sys.modules, case.provider, provider)
    with pytest.raises(ProverConfigError, match="cached layer.*binding"):
        _prove(case.b, case.source)
