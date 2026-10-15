"""Selected Python and Astroid authorities must agree across real requests."""

import importlib
import sys
from functools import wraps
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import astroid
import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.guarantees import contract_descriptions, contract_documentation
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import (
    ProverConfigError,
    _selected_layer_context,
    prove_path,
    prove_text,
)
from clauz3.runner import RunConfigError, RunProofError, run_source


def _layer(parent: Path, name: str, *, allowed: bool) -> Path:
    layer = parent / name
    layer.mkdir(parents=True)
    (layer / "__init__.py").write_text("")
    (layer / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        f"@clauz3.ensures(lambda result: result == {allowed!r})\n"
        "def allowed() -> bool:\n"
        f"    return {allowed!r}\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
    )
    (layer / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n"
        "    return effect('record').empty()\n"
        "@contract\n"
        "def once() -> ContractSpec:\n"
        "    return effect('record').count() <= 1\n"
    )
    return layer


def _source(name: str) -> str:
    return (
        "import clauz3\n"
        f"from {name}.effects import allowed, record\n"
        f"from {name}.contracts import silent\n"
        "@clauz3.guarantee(silent())\n"
        "def main() -> None:\n"
        "    if not allowed():\n"
        "        record('bad')\n"
    )


def _prove(source: str, layer: Path) -> list[Conclusion]:
    return [
        item.proof.conclusion
        for item in prove_text(
            source, trusted_roots=[layer], import_paths=[layer.parent]
        )
    ]


def _records(source: str, layer: Path) -> list[str]:
    outcome = execute(
        source,
        source_name="entry.py",
        policy=build_policy(trusted_roots=[layer], import_roots=[layer.parent]),
        import_roots=[layer.parent],
    )
    return [args[0] for name, args, _ in outcome.effects if name.endswith(".record")]


def test_documentation_uses_selected_owner_despite_same_dotted_name(
    tmp_path: Path,
) -> None:
    name = f"layer_docs_{uuid4().hex}"
    layers = [_layer(tmp_path / label, name, allowed=True) for label in ("a", "b")]
    for label, layer in zip(("Layer A", "Layer B"), layers, strict=True):
        contracts = layer / "contracts.py"
        contracts.write_text(
            contracts.read_text().replace(
                "@contract\ndef silent() -> ContractSpec:\n",
                f'@contract(description="{label} permits no record calls.")\n'
                f'def silent() -> ContractSpec:\n    """{label}"""\n',
            )
        )
    # A bare imported helper still resolves to its selected qualified identity.
    source = _source(name)
    for index in (0, 1, 0):
        layer = layers[index]
        with _selected_layer_context([layer], [layer.parent]):
            assert _prove(source, layer) == [Conclusion.OK]
            docs = contract_documentation(source=source, target="main")
            assert docs == {"silent()": "Layer A" if index == 0 else "Layer B"}
            descriptions = contract_descriptions(source=source, target="main")
            expected = "Layer A" if index == 0 else "Layer B"
            assert (
                descriptions["silent()"]["text"]
                == f"{expected} permits no record calls."
            )


def test_astroid_only_wrong_root_cannot_override_selected_python_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = f"layer_ast_only_{uuid4().hex}"
    layer_a = _layer(tmp_path / "a", name, allowed=True)
    layer_b = _layer(tmp_path / "b", name, allowed=False)
    source = _source(name)
    assert _prove("def main() -> None:\n    pass\n", layer_b) == [Conclusion.OK]
    effects = importlib.import_module(name + ".effects")
    assert effects.__file__ is not None
    assert Path(effects.__file__).resolve() == (layer_b / "effects.py").resolve()
    assert effects.EVENTS == []
    # Only source inference is stale: Python already holds the correct B file.
    astroid.MANAGER.astroid_cache[name + ".effects"] = astroid.parse(
        (layer_a / "effects.py").read_text(),
        module_name=name + ".effects",
        path=str(layer_a / "effects.py"),
    )
    assert _prove(source, layer_b) == [Conclusion.FAIL]
    assert sys.modules[name + ".effects"] is effects
    approval, execution = Mock(), Mock()
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approval)
    monkeypatch.setattr("clauz3.runner._execute_source", execution)
    with pytest.raises(RunProofError):
        run_source(
            source,
            source_name="entry.py",
            trusted_roots=[layer_b],
            import_roots=[layer_b.parent],
            approval_service_url="http://approval.invalid",
        )
    approval.assert_not_called()
    execution.assert_not_called()
    assert effects.EVENTS == []
    assert _records(source, layer_b) == ["bad"]
    assert effects.EVENTS == ["bad"]


@pytest.mark.parametrize("consumer", ["text", "path"])
@pytest.mark.parametrize("first_allowed", [True, False])
def test_same_dotted_layer_switches_a_b_a_with_correct_proof_and_runtime(
    tmp_path: Path, consumer: str, first_allowed: bool
) -> None:
    name = f"layer_switch_{uuid4().hex}"
    layers = [
        _layer(tmp_path / "a", name, allowed=first_allowed),
        _layer(tmp_path / "b", name, allowed=not first_allowed),
    ]
    source = _source(name)
    retained = None
    parent = None
    for index in (0, 1, 0, 1, 0):
        layer = layers[index]
        allowed = first_allowed if index == 0 else not first_allowed
        if consumer == "text":
            conclusions = _prove(source, layer)
        else:
            entry = layer.parent / "entry.py"
            entry.write_text(source)
            conclusions = [
                item.proof.conclusion
                for item in prove_path(
                    entry, trusted_roots=[layer], import_roots=[layer.parent]
                )
            ]
        assert conclusions == [Conclusion.OK if allowed else Conclusion.FAIL]
        if retained is None:
            retained = sys.modules[name + ".effects"]
            parent = sys.modules[name]
        # B's operation restores occupied A bindings and parent links.
        assert sys.modules[name + ".effects"] is retained
        assert sys.modules[name] is parent
        assert vars(parent)["effects"] is retained
        assert _records(source, layer) == ([] if allowed else ["bad"])


def test_prove_inspect_patch_execute_preserves_verified_provider_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = f"layer_identity_{uuid4().hex}"
    layer = _layer(tmp_path, name, allowed=True)
    source = (
        "import clauz3\n"
        f"from {name}.effects import record\n"
        f"from {name}.contracts import once\n"
        "@clauz3.guarantee(once())\n"
        "def main() -> None:\n"
        "    record('one')\n"
    )
    assert _prove(source, layer) == [Conclusion.OK]
    effects = importlib.import_module(name + ".effects")
    original = effects.record
    observed: list[str] = []

    @wraps(original)
    def patched(value: str) -> None:
        observed.append(value)
        original(value)

    monkeypatch.setattr(effects, "record", patched)
    assert _records(source, layer) == ["one"]
    assert observed == ["one"]
    assert effects.EVENTS == ["one"]
    assert sys.modules[name + ".effects"] is effects
    assert effects.record is patched


@pytest.mark.parametrize("binding", ["module", "copied-scalar", "reexport"])
def test_shared_source_keeps_each_images_original_dependency_bindings(
    tmp_path: Path, binding: str
) -> None:
    name = f"shared_layer_{uuid4().hex}"
    dependency = f"authority_{uuid4().hex}"
    layer = _layer(tmp_path / "common", name, allowed=True)
    if binding == "module":
        imported = f"import {dependency} as authority\n"
        declaration = (
            "@clauz3.trusted\n"
            "@clauz3.ensures(lambda result: result == authority.ALLOWED)\n"
            "def allowed() -> bool:\n"
            "    return authority.ALLOWED\n"
        )
    elif binding == "copied-scalar":
        imported = f"from {dependency} import ALLOWED\n"
        # Bare imported scalar conditions are not modeled on this base. Use
        # the valid upper bound to exercise successful proof and real runtime
        # selection without pretending that unsupported condition is provable.
        declaration = "@clauz3.trusted\ndef allowed() -> bool:\n    return ALLOWED\n"
    else:
        imported = f"from {dependency} import allowed\n"
        declaration = ""
    (layer / "effects.py").write_text(
        "import clauz3\n"
        + imported
        + "EVENTS: list[str] = []\n"
        + declaration
        + "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
    )
    paths = [tmp_path / "a", tmp_path / "b"]
    for path, value in zip(paths, (True, False), strict=True):
        path.mkdir()
        if binding == "reexport":
            package = path / dependency
            package.mkdir()
            (package / "__init__.py").write_text(
                "import clauz3\n"
                "@clauz3.trusted\n"
                f"@clauz3.ensures(lambda result: result == {value!r})\n"
                "def allowed() -> bool:\n"
                f"    return {value!r}\n"
            )
        else:
            (path / f"{dependency}.py").write_text(f"ALLOWED = {value!r}\n")
    source = _source(name).replace("if not allowed():", "if allowed():")
    if binding == "copied-scalar":
        source = source.replace("silent", "once")
    original = None
    for index in (0, 1, 0):
        imports = [paths[index], layer.parent]
        roots = [layer]
        if binding == "reexport":
            roots.append(paths[index] / dependency)
        proofs = prove_text(source, trusted_roots=roots, import_paths=imports)
        expected = (
            Conclusion.SKIP
            if binding == "module"
            else Conclusion.OK
            if index == 1 or binding == "copied-scalar"
            else Conclusion.FAIL
        )
        assert [item.proof.conclusion for item in proofs] == [expected]
        if binding == "module":
            # Module data cannot establish a symbolic postcondition, even when
            # authority selection is correct. Keep the runtime A/B/A witness.
            assert "module data bindings are not modeled" in str(proofs[0].proof)
        if original is None:
            original = sys.modules[name + ".effects"]
        assert sys.modules[name + ".effects"] is original
        outcome = execute(
            source,
            source_name="entry.py",
            policy=build_policy(trusted_roots=roots, import_roots=imports),
            import_roots=imports,
        )
        records = [
            args[0] for effect, args, _ in outcome.effects if effect.endswith(".record")
        ]
        assert records == (["bad"] if index == 0 else [])
        assert sys.modules[name + ".effects"] is original
    assert vars(original)["EVENTS"] == ["bad", "bad"]


@pytest.mark.parametrize("copied_scalar", [False, True])
@pytest.mark.parametrize("matching", [False, True])
@pytest.mark.parametrize("preloaded_owner", [False, True])
def test_preloaded_transitive_dependency_must_match_before_first_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    copied_scalar: bool,
    matching: bool,
    preloaded_owner: bool,
) -> None:
    name = f"preloaded_layer_{uuid4().hex}"
    carrier = f"carrier_{uuid4().hex}"
    dependency = f"dependency_{uuid4().hex}"
    layer = _layer(tmp_path / "common", name, allowed=True)
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    (ambient / f"{carrier}.py").write_text(
        f"from {dependency} import ALLOWED\n"
        if copied_scalar
        else f"import {dependency} as authority\n"
    )
    reference = "carrier.ALLOWED" if copied_scalar else "carrier.authority.ALLOWED"
    condition = (
        ""
        if copied_scalar
        else f"@clauz3.ensures(lambda result: result == {reference})\n"
    )
    (layer / "effects.py").write_text(
        "import clauz3\n"
        f"import {carrier} as carrier\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n" + condition + "def allowed() -> bool:\n"
        f"    return {reference}\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
    )
    a, b = tmp_path / "a", tmp_path / "b"
    for path, value in ((a, True), (b, False)):
        path.mkdir()
        (path / f"{dependency}.py").write_text(f"ALLOWED = {value!r}\n")
    monkeypatch.syspath_prepend(str(ambient))
    original_paths = list(sys.path)
    try:
        sys.path[:0] = [str(a), str(layer.parent)]
        retained_carrier = importlib.import_module(carrier)
        module = importlib.import_module(name + ".effects") if preloaded_owner else None
    finally:
        sys.path[:] = original_paths
    if module is not None:
        assert module.EVENTS == []
    source = _source(name).replace("if not allowed():", "if allowed():")
    if copied_scalar:
        source = source.replace("silent", "once")
    paths = [a if matching else b, layer.parent]
    if matching:
        proofs = prove_text(source, trusted_roots=[layer], import_paths=paths)
        expected = Conclusion.OK if copied_scalar else Conclusion.SKIP
        assert [item.proof.conclusion for item in proofs] == [expected]
        if not copied_scalar:
            assert "module data bindings are not modeled" in str(proofs[0].proof)
        selected = importlib.import_module(name + ".effects")
        if module is not None:
            assert selected is module
        module = selected
        outcome = execute(
            source,
            source_name="entry.py",
            policy=build_policy(trusted_roots=[layer], import_roots=paths),
            import_roots=paths,
        )
        assert [
            args[0] for effect, args, _ in outcome.effects if effect.endswith(".record")
        ] == ["bad"]
        assert module.EVENTS == ["bad"]
    else:
        with pytest.raises(ProverConfigError, match="cached layer dependency"):
            prove_text(source, trusted_roots=[layer], import_paths=paths)
        approval, execution = Mock(), Mock()
        monkeypatch.setattr("clauz3.runner.submit_approval_request", approval)
        monkeypatch.setattr("clauz3.runner._execute_source", execution)
        with pytest.raises(RunConfigError, match="cached layer dependency"):
            run_source(
                source,
                source_name="entry.py",
                trusted_roots=[layer],
                import_roots=paths,
                approval_service_url="http://approval.invalid",
            )
        approval.assert_not_called()
        execution.assert_not_called()
        if module is not None:
            assert module.EVENTS == []
    if module is not None:
        assert sys.modules[name + ".effects"] is module
    assert sys.modules[carrier] is retained_carrier
