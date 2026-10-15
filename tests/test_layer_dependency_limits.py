"""Operator budgets bound traversal without bypassing dependency provenance."""

import importlib
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from clauz3 import layer_state
from clauz3._vendor.deal_solver import Conclusion
from clauz3._vendor.deal_solver._funcs import FUNCTIONS
from clauz3.layer_state import LayerStateError, selected_layer
from clauz3.prover import ProverConfigError, prove_text

MODULE_LIMIT = "CLAUZ3_MAX_DEPENDENCY_MODULES"
NODE_LIMIT = "CLAUZ3_MAX_DEPENDENCY_AST_NODES"


@pytest.fixture(autouse=True)
def _default_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MODULE_LIMIT, raising=False)
    monkeypatch.delenv(NODE_LIMIT, raising=False)


def _layer(
    directory: Path, *, name: str | None = None, imports: str = ""
) -> tuple[Path, str]:
    name = name or f"budget_layer_{uuid4().hex}"
    root = directory / name
    root.mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "effects.py").write_text(
        "import clauz3\n"
        "from clauz3.spec import contract, effect\n"
        f"{imports}"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
        "@contract\n"
        "def once():\n"
        "    return effect('record').count() <= 1\n"
    )
    source = (
        "import clauz3\n"
        f"from {name}.effects import record, once\n"
        "@clauz3.guarantee(once())\n"
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


def _installed_path(directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Model an installed distribution outside the configured local roots.
    monkeypatch.setattr(
        layer_state,
        "_AUTHORITY_ROOTS",
        (*vars(layer_state)["_AUTHORITY_ROOTS"], directory),
    )
    monkeypatch.syspath_prepend(str(directory))


@pytest.mark.parametrize("installed_sdk", [True, False], ids=["installed", "local"])
def test_large_sdk_accepts_operator_module_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installed_sdk: bool
) -> None:
    directory = tmp_path / ("installed" if installed_sdk else "project")
    name = f"large_sdk_{uuid4().hex}"
    package = directory / name
    package.mkdir(parents=True)
    leaves = [f"service_{index:03}" for index in range(300)]
    (package / "__init__.py").write_text(
        "".join(f"from . import {leaf}\n" for leaf in leaves)
    )
    for index, leaf in enumerate(leaves):
        (package / f"{leaf}.py").write_text(f"SERVICE_ID = {index}\n")
    if installed_sdk:
        _installed_path(directory, monkeypatch)
    root, source = _layer(tmp_path / "project", imports=f"import {name}\n")

    with pytest.raises(
        ProverConfigError,
        match="cached layer dependency graph is too large",
    ):
        _prove(root, source)

    monkeypatch.setenv(MODULE_LIMIT, "1024")
    assert _prove(root, source) == [Conclusion.OK]
    assert all(f"{name}.{leaf}" in sys.modules for leaf in leaves)
    assert _prove(root, source + "    record('two')\n") == [Conclusion.FAIL]


@pytest.mark.parametrize("setting", [MODULE_LIMIT, NODE_LIMIT])
@pytest.mark.parametrize("value", ["0", "-1", "not-a-number", ""])
def test_invalid_operator_budget_refuses_before_layer_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, setting: str, value: str
) -> None:
    root, source = _layer(tmp_path)
    marker = tmp_path / "initialized.txt"
    (root / "__init__.py").write_text(
        f"with open({str(marker)!r}, 'w') as stream:\n    stream.write('initialized')\n"
    )
    monkeypatch.setenv(setting, value)
    with pytest.raises(
        ProverConfigError, match=f"{setting} must be a positive integer"
    ):
        _prove(root, source)
    assert not marker.exists()
    assert root.name not in sys.modules


def test_source_budget_override_is_reread_for_existing_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source = _layer(tmp_path)
    monkeypatch.setattr(layer_state, "MAX_DEPENDENCY_AST_NODES", 1)
    with pytest.raises(
        ProverConfigError,
        match="cached layer dependency source graph is too large",
    ):
        _prove(root, source)

    monkeypatch.setenv(NODE_LIMIT, "500000")
    assert _prove(root, source) == [Conclusion.OK]
    module = sys.modules[f"{root.name}.effects"]
    monkeypatch.setenv(NODE_LIMIT, "1")
    with pytest.raises(
        ProverConfigError,
        match=f"cached layer dependency source graph is too large.*{NODE_LIMIT}",
    ):
        _prove(root, source)
    assert sys.modules[f"{root.name}.effects"] is module
    monkeypatch.setenv(NODE_LIMIT, "500000")
    assert _prove(root, source) == [Conclusion.OK]
    assert sys.modules[f"{root.name}.effects"] is module


def test_raised_budgets_do_not_adopt_stale_installed_plugin_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MODULE_LIMIT, "1024")
    monkeypatch.setenv(NODE_LIMIT, "1000000")
    installed = tmp_path / "installed"
    installed.mkdir()
    plugin = f"budget_plugin_{uuid4().hex}"
    dependency = f"budget_dependency_{uuid4().hex}"
    model_name = f"audit.{plugin}"
    (installed / f"{plugin}.py").write_text(
        "import clauz3\n"
        f"from {dependency} import ALLOWED\n"
        f"@clauz3.solver({model_name!r})\n"
        "def registered(*args, **kwargs):\n"
        "    return ALLOWED\n"
    )
    _installed_path(installed, monkeypatch)
    name = f"budget_shared_{uuid4().hex}"
    roots = []
    for label, allowed in (("A", True), ("B", False)):
        root, _ = _layer(tmp_path / label, name=name, imports=f"import {plugin}\n")
        (root.parent / f"{dependency}.py").write_text(f"ALLOWED = {allowed!r}\n")
        roots.append(root)
    a, b = roots
    with selected_layer(trusted_roots=[a], import_paths=[a.parent]) as image:
        importlib.import_module(f"{name}.effects")
        image.refresh_registrations()
        model = FUNCTIONS[model_name]
    with (
        pytest.raises(LayerStateError, match="cached layer dependency"),
        selected_layer(trusted_roots=[b], import_paths=[b.parent]) as other,
    ):
        importlib.import_module(f"{name}.effects")
        other.refresh_registrations()
    with image.activate():
        assert FUNCTIONS[model_name] is model
