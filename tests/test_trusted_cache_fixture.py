"""Test isolation must keep imported objects, source and contracts together."""

import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from types import ModuleType

import astroid
import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3._vendor.deal_solver._funcs import FUNCTIONS
from clauz3.prover import prove_text
from clauz3.spec import REGISTERED_CONTRACTS


@pytest.mark.parametrize(
    "cache_api", ["available", "module-files", "private-module", "finder-api"]
)
def test_same_named_roots_have_their_own_preconditions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cache_api: str,
    trusted_layer_cache_scope: Callable[[], AbstractContextManager[None]],
) -> None:
    package_name = "cache_fixture_layer"
    contract_name = f"{package_name}.contracts.no_guarantees"
    public_reset = astroid.MANAGER.clear_cache
    resets = []
    for value, conclusion in [(1, Conclusion.OK), (2, Conclusion.FAIL)]:
        base = tmp_path / str(value)
        root = base / package_name
        root.mkdir(parents=True)
        (root / "__init__.py").write_text("")
        (root / "effects.py").write_text(
            "import clauz3\n"
            f"@clauz3.requires(lambda value: value == {value})\n"
            "@clauz3.trusted\n"
            "def send(value: int) -> None:\n"
            "    pass\n"
        )
        (root / "contracts.py").write_text(
            "from clauz3.spec import ContractSpec, contract\n"
            "from clauz3.spec import no_guarantees as core_no_guarantees\n"
            "@contract\n"
            "def no_guarantees() -> ContractSpec:\n"
            "    return core_no_guarantees()\n"
        )
        with monkeypatch.context() as changes, trusted_layer_cache_scope():
            # Production selection restores ambient ASTs. Seed one explicitly
            # so this fixture still exercises their eviction and reset fallback.
            module_name = f"{package_name}.effects"
            ambient_ast = astroid.MANAGER.ast_from_file(
                str(root / "effects.py"), modname=module_name
            )
            results = prove_text(
                f"from {package_name}.effects import send\n"
                "def main() -> None:\n"
                "    send(1)\n",
                trusted_roots=[root],
                import_paths=[base],
            )
            assert [result.proof.conclusion for result in results] == [conclusion]
            assert contract_name in FUNCTIONS
            assert contract_name in REGISTERED_CONTRACTS
            assert f"{package_name}.effects" in sys.modules
            assert astroid.MANAGER.astroid_cache[module_name] is ambient_ast
            if cache_api != "available":

                def reset() -> None:
                    # Simulate a newer astroid whose public reset understands
                    # its changed private layout, then exercise the real reset.
                    changes.undo()
                    resets.append(True)
                    public_reset()

                changes.setattr(astroid.MANAGER, "clear_cache", reset)
                if cache_api == "module-files":
                    changes.setattr(astroid.MANAGER, "_mod_file_cache", None)
                elif cache_api == "private-module":
                    changes.setitem(
                        sys.modules, "astroid.interpreter._import.spec", None
                    )
                else:
                    changes.setitem(
                        sys.modules,
                        "astroid.interpreter._import.spec",
                        ModuleType("changed_private_api"),
                    )
        assert contract_name not in FUNCTIONS
        assert contract_name not in REGISTERED_CONTRACTS
        assert f"{package_name}.effects" not in sys.modules
        assert f"{package_name}.effects" not in astroid.MANAGER.astroid_cache
    assert len(resets) == (0 if cache_api == "available" else 2)


def test_astroid_only_imports_are_evicted(
    tmp_path: Path,
    trusted_layer_cache_scope: Callable[[], AbstractContextManager[None]],
) -> None:
    module_name = "cache_fixture_ast_only"
    for value in [1, 2]:
        base = tmp_path / str(value)
        base.mkdir()
        source = base / f"{module_name}.py"
        source.write_text(f"VALUE = {value}\n")
        with trusted_layer_cache_scope():
            module = astroid.MANAGER.ast_from_file(str(source), modname=module_name)
            inferred = next(module.locals["VALUE"][0].infer())
            assert isinstance(inferred, astroid.Const)
            assert inferred.value == value
            assert module_name not in sys.modules
            assert module_name in astroid.MANAGER.astroid_cache
        assert module_name not in astroid.MANAGER.astroid_cache


@pytest.mark.parametrize("suffix", ["/clauz3/example.py", "_generated/example.py"])
def test_installed_path_check_respects_directory_boundary(
    suffix: str,
    trusted_layer_cache_scope: Callable[[], AbstractContextManager[None]],
) -> None:
    root = Path(__file__).resolve().parents[1] / "src"
    module = ModuleType("cache_fixture_path")
    module.__file__ = f"{root}{suffix}"
    with trusted_layer_cache_scope():
        sys.modules[module.__name__] = module
    try:
        assert (module.__name__ not in sys.modules) == suffix.startswith("_")
    finally:
        sys.modules.pop(module.__name__, None)


def test_builtin_models_and_astroid_cache_are_retained(
    trusted_layer_cache_scope: Callable[[], AbstractContextManager[None]],
) -> None:
    with trusted_layer_cache_scope():
        builtins = astroid.MANAGER.ast_from_module_name("builtins")
        builtin_len = FUNCTIONS["builtins.len"]
    assert astroid.MANAGER.astroid_cache["builtins"] is builtins
    assert FUNCTIONS["builtins.len"] is builtin_len
