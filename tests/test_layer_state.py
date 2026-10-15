"""The coordinator parks verified identities, never arbitrary ambient authority."""

import importlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest

from clauz3 import spec
from clauz3._vendor.deal_solver._funcs._registry import ENGINE_FUNCTIONS, FUNCTIONS
from clauz3._vendor.deal_solver._proxies import _row
from clauz3.layer_state import LayerStateError, selected_layer
from clauz3.row import Row


def _layers(tmp_path: Path, *, namespace: bool = False) -> tuple[str, Path, Path]:
    name = f"owned_{uuid4().hex}"
    roots = []
    for label in ("A", "B"):
        search = tmp_path / label
        root = search / name
        if namespace:
            root /= "trusted"
        root.mkdir(parents=True)
        (root / "__init__.py").write_text("")
        (root / "effects.py").write_text(
            "import clauz3\n"
            "from clauz3.spec import contract, effect\n"
            f"LABEL = {label!r}\n"
            "EVENTS: list[str] = []\n"
            "RELATION = effect('record')\n"
            "class Item(clauz3.Row):\n    value: str\n"
            "@clauz3.trusted\n"
            "def record(value: str) -> None:\n    EVENTS.append(value)\n"
            "@contract\n"
            "def allowed():\n    return RELATION.empty()\n"
            "@clauz3.solver('audit.shared_model')\n"
            "def symbolic(*args, **kwargs):\n    return LABEL\n"
        )
        roots.append(root)
    return name + (".trusted" if namespace else ""), roots[0], roots[1]


def _search(root: Path, name: str) -> Path:
    return root.parents[len(name.split(".")) - 1]


@pytest.mark.parametrize("namespace", [False, True], ids=["package", "namespace"])
def test_a_b_a_reuses_objects_and_restores_parent_links(
    tmp_path: Path, namespace: bool
) -> None:
    name, a, b = _layers(tmp_path, namespace=namespace)
    with selected_layer(trusted_roots=[a], import_paths=[_search(a, name)]) as image_a:
        first = importlib.import_module(f"{name}.effects")
        first.EVENTS.append("host patch")
        model_a = FUNCTIONS["audit.shared_model"]
    assert sys.modules[f"{name}.effects"] is first
    assert vars(sys.modules[name])["effects"] is first

    with selected_layer(trusted_roots=[b], import_paths=[_search(b, name)]) as image_b:
        second = importlib.import_module(f"{name}.effects")
        assert second is not first
        assert second.LABEL == "B"
        assert vars(sys.modules[name])["effects"] is second
        assert FUNCTIONS["audit.shared_model"] is not model_a
    assert sys.modules[f"{name}.effects"] is first
    assert vars(sys.modules[name])["effects"] is first

    with image_a.activate():
        assert importlib.import_module(f"{name}.effects") is first
        assert first.EVENTS == ["host patch"]
        assert FUNCTIONS["audit.shared_model"] is model_a
    with image_b.activate():
        assert importlib.import_module(f"{name}.effects") is second
    assert sys.modules[f"{name}.effects"] is first


def test_same_effective_path_nesting_and_incompatible_nested_selection(
    tmp_path: Path,
) -> None:
    name, a, b = _layers(tmp_path)
    search = _search(a, name)
    before = list(sys.path)
    with selected_layer(trusted_roots=[a], import_paths=[search]) as image:
        with selected_layer(trusted_roots=[a], import_paths=[search, search]) as nested:
            assert nested is image
        with (
            pytest.raises(LayerStateError, match="incompatible nested"),
            selected_layer(trusted_roots=[b], import_paths=[_search(b, name)]),
        ):
            pytest.fail("incompatible layer became active")
    assert sys.path == before


def test_selection_itself_does_not_run_initializers(tmp_path: Path) -> None:
    name, a, _ = _layers(tmp_path)
    (a / "__init__.py").write_text("raise AssertionError('initializer ran')\n")
    with selected_layer(trusted_roots=[a], import_paths=[_search(a, name)]):
        assert name not in sys.modules


def test_matching_ambient_module_and_registration_are_adopted(tmp_path: Path) -> None:
    name, a, _ = _layers(tmp_path)
    old_path = list(sys.path)
    try:
        sys.path.insert(0, str(_search(a, name)))
        module = importlib.import_module(f"{name}.effects")
    finally:
        sys.path[:] = old_path
    model = FUNCTIONS[f"{name}.effects.allowed"]
    relations = set(spec._RELATIONS[str(a / "effects.py")])
    with selected_layer(trusted_roots=[a], import_paths=[_search(a, name)]):
        assert importlib.import_module(f"{name}.effects") is module
        assert FUNCTIONS[f"{name}.effects.allowed"] is model
        assert f"{name}.effects.allowed" in spec.REGISTERED_CONTRACTS
        assert spec._RELATIONS[str(a / "effects.py")] == relations


def test_unknown_registry_changes_never_become_selected_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name, a, _ = _layers(tmp_path)
    ambient = object()
    monkeypatch.setitem(FUNCTIONS, "arbitrary.ambient", ambient)
    with selected_layer(trusted_roots=[a], import_paths=[_search(a, name)]) as image:
        assert "arbitrary.ambient" not in FUNCTIONS
        assert FUNCTIONS["builtins.len"] is ENGINE_FUNCTIONS["builtins.len"]
        FUNCTIONS["arbitrary.during"] = object()
    assert FUNCTIONS["arbitrary.ambient"] is ambient
    assert "arbitrary.during" not in FUNCTIONS
    with image.activate():
        assert "arbitrary.during" not in FUNCTIONS
        assert "arbitrary.ambient" not in FUNCTIONS


def test_unknown_module_journal_restores_replacements_deletions_and_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name, a, _ = _layers(tmp_path)
    host = f"host_{uuid4().hex}"
    parent, child = ModuleType(host), ModuleType(f"{host}.child")
    vars(parent)["child"] = child
    monkeypatch.setitem(sys.modules, host, parent)
    monkeypatch.setitem(sys.modules, f"{host}.child", child)
    with selected_layer(trusted_roots=[a], import_paths=[_search(a, name)]):
        sys.modules[f"{host}.child"] = ModuleType("replacement")
        sys.modules[f"{host}.new"] = ModuleType("new")
        vars(parent)["child"] = ModuleType("wrong_link")
        vars(parent)["new"] = sys.modules[f"{host}.new"]
        del sys.modules[host]
    assert sys.modules[host] is parent
    assert sys.modules[f"{host}.child"] is child
    assert vars(parent)["child"] is child
    assert f"{host}.new" not in sys.modules
    assert "new" not in vars(parent)


def test_parent_link_changes_without_module_rebinding_are_restored(
    tmp_path: Path,
) -> None:
    name, a, _ = _layers(tmp_path)
    with selected_layer(trusted_roots=[a], import_paths=[_search(a, name)]) as image:
        module = importlib.import_module(f"{name}.effects")
    parent = sys.modules[name]
    with (
        pytest.raises(LayerStateError, match="cached layer dependency"),
        image.activate(),
    ):
        vars(parent)["effects"] = ModuleType("wrong")
    assert vars(parent)["effects"] is module


@pytest.mark.parametrize("change", ["edit", "add", "remove"])
def test_changed_trusted_inventory_refuses_resume(tmp_path: Path, change: str) -> None:
    name, a, _ = _layers(tmp_path)
    with selected_layer(trusted_roots=[a], import_paths=[_search(a, name)]) as image:
        importlib.import_module(f"{name}.effects")
    if change == "edit":
        (a / "effects.py").write_text("NEW = True\n")
    elif change == "add":
        (a / "new.py").write_text("NEW = True\n")
    else:
        (a / "effects.py").unlink()
    with pytest.raises(LayerStateError, match="inventory changed"), image.activate():
        pytest.fail("changed layer became active")


def test_sibling_and_lazy_dependencies_switch_and_are_fingerprinted(
    tmp_path: Path,
) -> None:
    name, a, b = _layers(tmp_path)
    sibling = f"authority_{uuid4().hex}"
    lazy = f"lazy_{uuid4().hex}"
    for label, root in (("A", a), ("B", b)):
        (root.parent / f"{sibling}.py").write_text(f"LABEL = {label!r}\n")
        (root.parent / f"{lazy}.py").write_text(f"LABEL = {label!r}\n")
        with (root / "effects.py").open("a") as stream:
            stream.write(
                f"import {sibling}\n"
                f"def read():\n    import {lazy}\n"
                f"    return {sibling}.LABEL + {lazy}.LABEL\n"
            )
    with selected_layer(trusted_roots=[a], import_paths=[a.parent]) as image_a:
        first = importlib.import_module(f"{name}.effects")
        assert first.read() == "AA"
    with selected_layer(trusted_roots=[b], import_paths=[b.parent]):
        second = importlib.import_module(f"{name}.effects")
        assert second.read() == "BB"
    with image_a.activate():
        assert first.read() == "AA"
    (a.parent / f"{lazy}.py").write_text("LABEL = 'changed'\n")
    with pytest.raises(LayerStateError, match="source changed"), image_a.activate():
        pytest.fail("changed dependency became active")


def test_relation_and_row_cache_selection_preserves_class_identity(
    tmp_path: Path,
) -> None:
    name, a, b = _layers(tmp_path)
    with selected_layer(trusted_roots=[a], import_paths=[a.parent]) as image_a:
        first = importlib.import_module(f"{name}.effects")
        first_sort = _row.z3_datatype_for_row(first.Item)
    with selected_layer(trusted_roots=[b], import_paths=[b.parent]):
        second = importlib.import_module(f"{name}.effects")
        second_sort = _row.z3_datatype_for_row(second.Item)
        assert first.Item not in _row._DATATYPE_CACHE
        assert str(a / "effects.py") not in spec._RELATIONS
        assert first_sort != second_sort
    with image_a.activate():
        assert _row.z3_datatype_for_row(first.Item) is first_sort
        assert second.Item not in _row._DATATYPE_CACHE
        assert first.RELATION in spec._RELATIONS[str(a / "effects.py")]


def test_cross_thread_callback_has_bounded_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name, a, _ = _layers(tmp_path)
    monkeypatch.setattr("clauz3.layer_state.LOCK_TIMEOUT", 0.05)

    def select() -> None:
        with selected_layer(trusted_roots=[a], import_paths=[a.parent]):
            pytest.fail("callback entered active operation")

    with (
        selected_layer(trusted_roots=[a], import_paths=[_search(a, name)]),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        future = pool.submit(select)
        with pytest.raises(LayerStateError, match="cross-thread callbacks"):
            future.result(timeout=1)


def test_new_registration_survives_nested_selection_and_resume(tmp_path: Path) -> None:
    name, a, _ = _layers(tmp_path)
    qualified = f"{name}.effects.allowed"
    with selected_layer(trusted_roots=[a], import_paths=[a.parent]) as image:
        module = importlib.import_module(f"{name}.effects")
        model = FUNCTIONS[qualified]
        with selected_layer(trusted_roots=[a], import_paths=[a.parent]):
            assert FUNCTIONS[qualified] is model
            assert qualified in spec.REGISTERED_CONTRACTS
    with image.activate():
        assert sys.modules[f"{name}.effects"] is module
        assert FUNCTIONS[qualified] is model
        assert qualified in spec.REGISTERED_CONTRACTS


def test_preimported_synthetic_row_cache_is_adopted(tmp_path: Path) -> None:
    name, a, _ = _layers(tmp_path)
    old_path = list(sys.path)
    try:
        sys.path.insert(0, str(a.parent))
        importlib.import_module(f"{name}.effects")
    finally:
        sys.path[:] = old_path
    schema = type("Synthetic", (Row,), {"__annotations__": {"value": str}})
    key = (f"{name}.effects", str(a / "effects.py"), "Synthetic", (("value", str),))
    _row._SYNTHETIC_ROW_CACHE[key] = schema
    datatype = _row.z3_datatype_for_row(schema)
    with selected_layer(trusted_roots=[a], import_paths=[a.parent]):
        assert _row._SYNTHETIC_ROW_CACHE[key] is schema
        assert _row.z3_datatype_for_row(schema) is datatype


def test_exception_restores_displaced_package_and_registration(tmp_path: Path) -> None:
    name, a, b = _layers(tmp_path)
    with selected_layer(trusted_roots=[a], import_paths=[a.parent]):
        module = importlib.import_module(f"{name}.effects")
        model = FUNCTIONS[f"{name}.effects.allowed"]
    with (
        pytest.raises(RuntimeError, match="trusted failure"),
        selected_layer(trusted_roots=[b], import_paths=[b.parent]),
    ):
        importlib.import_module(f"{name}.effects")
        raise RuntimeError("trusted failure")
    assert sys.modules[f"{name}.effects"] is module
    assert vars(sys.modules[name])["effects"] is module
    assert FUNCTIONS[f"{name}.effects.allowed"] is model


def test_initialized_extension_cannot_be_displaced(tmp_path: Path) -> None:
    parent = importlib.import_module("pydantic_core")
    extension = importlib.import_module("pydantic_core._pydantic_core")
    root = tmp_path / "pydantic_core"
    root.mkdir()
    (root / "__init__.py").write_text("raise AssertionError('shadow initialized')\n")
    (root / "_pydantic_core.py").write_text(
        "raise AssertionError('shadow initialized')\n"
    )
    with (
        pytest.raises(LayerStateError, match="initialized extension"),
        selected_layer(trusted_roots=[root], import_paths=[tmp_path]),
    ):
        pytest.fail("extension was displaced")
    assert sys.modules["pydantic_core"] is parent
    assert sys.modules["pydantic_core._pydantic_core"] is extension
    assert vars(parent)["_pydantic_core"] is extension


def test_environment_dependencies_keep_identity_across_switches(tmp_path: Path) -> None:
    name, a, b = _layers(tmp_path)
    with selected_layer(trusted_roots=[a], import_paths=[a.parent]) as image:
        library = importlib.import_module("fractions")
        extension = importlib.import_module("pydantic_core._pydantic_core")
    with selected_layer(trusted_roots=[b], import_paths=[b.parent]):
        assert importlib.import_module("fractions") is library
        assert importlib.import_module("pydantic_core._pydantic_core") is extension
    with image.activate():
        assert importlib.import_module("fractions") is library
        assert importlib.import_module("pydantic_core._pydantic_core") is extension


@pytest.mark.parametrize("preloaded", [False, True])
@pytest.mark.parametrize("reference", ["module", "function"])
def test_selected_environment_plugin_registration_survives_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preloaded: bool,
    reference: str,
) -> None:
    from clauz3 import layer_state

    name, a, b = _layers(tmp_path)
    installed = tmp_path / "installed"
    installed.mkdir()
    plugin = f"plugin_{uuid4().hex}"
    model_name = f"audit.{plugin}"
    plugin_file = installed / f"{plugin}.py"
    plugin_file.write_text(
        "import clauz3\n"
        f"@clauz3.solver({model_name!r})\n"
        "def registered(*args, **kwargs):\n    return True\n"
    )
    # Exercise the environment-module classification with an actual importable
    # source fixture, without installing test files in the active environment.
    monkeypatch.setattr(
        layer_state,
        "_AUTHORITY_ROOTS",
        (*vars(layer_state)["_AUTHORITY_ROOTS"], installed),
    )
    monkeypatch.syspath_prepend(str(installed))
    if preloaded:
        importlib.import_module(plugin)
    with (a / "effects.py").open("a") as stream:
        stream.write(
            f"import {plugin}\n"
            if reference == "module"
            else f"from {plugin} import registered\n"
        )
    with selected_layer(trusted_roots=[a], import_paths=[a.parent]) as image:
        importlib.import_module(f"{name}.effects")
        image.refresh_registrations()
        model = FUNCTIONS[model_name]
        assert plugin not in image.owned_modules
    with selected_layer(trusted_roots=[b], import_paths=[b.parent]) as other:
        importlib.import_module(f"{name}.effects")
        other.refresh_registrations()
        assert model_name not in FUNCTIONS
    with image.activate():
        assert FUNCTIONS[model_name] is model
    plugin_file.write_text("CHANGED = True\n")
    with pytest.raises(LayerStateError, match="source changed"), image.activate():
        pytest.fail("changed selected plugin became active")


def test_refresh_removes_unowned_initializer_registry_writes(tmp_path: Path) -> None:
    name, a, _ = _layers(tmp_path)
    unknown = f"unowned_{uuid4().hex}"
    with (a / "effects.py").open("a") as stream:
        stream.write(
            "from clauz3._vendor.deal_solver._funcs import FUNCTIONS\n"
            "from clauz3.spec import REGISTERED_CONTRACTS\n"
            f"FUNCTIONS[{unknown!r}] = lambda *args, **kwargs: True\n"
            f"REGISTERED_CONTRACTS.add({unknown!r})\n"
        )
    with selected_layer(trusted_roots=[a], import_paths=[a.parent]) as image:
        importlib.import_module(f"{name}.effects")
        assert unknown in FUNCTIONS
        image.refresh_registrations()
        assert unknown not in FUNCTIONS
        assert unknown not in spec.REGISTERED_CONTRACTS
    with image.activate():
        assert unknown not in FUNCTIONS
        assert unknown not in spec.REGISTERED_CONTRACTS


@pytest.mark.parametrize("binding", ["module", "scalar"])
@pytest.mark.parametrize("matching", [False, True])
def test_ambient_adoption_checks_retained_dependency_selection(
    tmp_path: Path, binding: str, matching: bool
) -> None:
    name, a, _ = _layers(tmp_path)
    dependency = f"ambient_dependency_{uuid4().hex}"
    left, right = tmp_path / "dependencies_a", tmp_path / "dependencies_b"
    for path, value in ((left, True), (right, False)):
        path.mkdir()
        (path / f"{dependency}.py").write_text(f"ALLOWED = {value!r}\n")
    with (a / "effects.py").open("a") as stream:
        stream.write(
            f"import {dependency}\n"
            if binding == "module"
            else f"from {dependency} import ALLOWED\n"
        )
    old_path = list(sys.path)
    try:
        sys.path[:0] = [str(left), str(a.parent)]
        module = importlib.import_module(f"{name}.effects")
    finally:
        sys.path[:] = old_path
    if matching:
        with selected_layer(trusted_roots=[a], import_paths=[left, a.parent]):
            assert importlib.import_module(f"{name}.effects") is module
    else:
        with (
            pytest.raises(LayerStateError, match="cached layer dependency"),
            selected_layer(trusted_roots=[a], import_paths=[right, a.parent]),
        ):
            pytest.fail("mismatching ambient dependency was adopted")
    assert sys.modules[f"{name}.effects"] is module


def test_selected_environment_plugin_cannot_retain_another_layers_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clauz3 import layer_state

    name, a, b = _layers(tmp_path)
    installed = tmp_path / "installed"
    installed.mkdir()
    plugin = f"scoped_plugin_{uuid4().hex}"
    dependency = f"plugin_dependency_{uuid4().hex}"
    model_name = f"audit.{plugin}"
    (installed / f"{plugin}.py").write_text(
        "import clauz3\n"
        f"from {dependency} import ALLOWED\n"
        f"@clauz3.solver({model_name!r})\n"
        "def registered(*args, **kwargs):\n    return ALLOWED\n"
    )
    monkeypatch.setattr(
        layer_state,
        "_AUTHORITY_ROOTS",
        (*vars(layer_state)["_AUTHORITY_ROOTS"], installed),
    )
    monkeypatch.syspath_prepend(str(installed))
    for path, value in ((a, True), (b, False)):
        (path.parent / f"{dependency}.py").write_text(f"ALLOWED = {value!r}\n")
        with (path / "effects.py").open("a") as stream:
            stream.write(f"import {plugin}\n")
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


@pytest.mark.parametrize(
    "limit", [None, "modules", "source"], ids=["cycle", "module-bound", "source-bound"]
)
def test_retained_dependency_cycles_are_bounded_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str | None
) -> None:
    name, a, _ = _layers(tmp_path)
    first, second = f"cycle_a_{uuid4().hex}", f"cycle_b_{uuid4().hex}"
    (a.parent / f"{first}.py").write_text(f"import {second}\n")
    (a.parent / f"{second}.py").write_text(f"import {first}\n")
    with (a / "effects.py").open("a") as stream:
        stream.write(f"import {first}\n")
    old_path = list(sys.path)
    try:
        sys.path.insert(0, str(a.parent))
        module = importlib.import_module(f"{name}.effects")
    finally:
        sys.path[:] = old_path
    if limit is None:
        with selected_layer(trusted_roots=[a], import_paths=[a.parent]):
            assert sys.modules[f"{name}.effects"] is module
    else:
        setting = (
            "MAX_DEPENDENCY_MODULES"
            if limit == "modules"
            else "MAX_DEPENDENCY_AST_NODES"
        )
        monkeypatch.setattr(f"clauz3.layer_state.{setting}", 1)
        with (
            pytest.raises(LayerStateError, match="dependency.*too large"),
            selected_layer(trusted_roots=[a], import_paths=[a.parent]),
        ):
            pytest.fail("dependency work exceeded its bound")
    assert sys.modules[f"{name}.effects"] is module


def test_inactive_class_import_uses_retained_origin_without_metaclass_hooks(
    tmp_path: Path,
) -> None:
    name, root, _ = _layers(tmp_path)
    absent = f"inactive_provider_{uuid4().hex}"
    with (root / "effects.py").open("a") as stream:
        stream.write(
            f"if False:\n    from {absent} import Selected\n"
            "class Meta(type):\n"
            "    def __getattribute__(self, name):\n"
            "        if name == '__module__':\n"
            "            raise AssertionError('metaclass hook ran')\n"
            "        return super().__getattribute__(name)\n"
            "class Selected(metaclass=Meta):\n    pass\n"
        )
    with selected_layer(trusted_roots=[root], import_paths=[root.parent]) as image:
        module = importlib.import_module(f"{name}.effects")
        image.refresh_registrations()
        assert absent not in sys.modules
    with image.activate():
        assert sys.modules[f"{name}.effects"] is module


@pytest.mark.parametrize("imported", ["VALUE", "*"], ids=["scalar", "star"])
def test_missing_retained_local_import_is_not_treated_as_inactive(
    tmp_path: Path, imported: str
) -> None:
    name, root, _ = _layers(tmp_path)
    dependency = f"removed_provider_{uuid4().hex}"
    (root.parent / f"{dependency}.py").write_text("VALUE = True\n")
    with (root / "effects.py").open("a") as stream:
        stream.write(f"from {dependency} import {imported}\n")
    original_paths = list(sys.path)
    try:
        sys.path.insert(0, str(root.parent))
        module = importlib.import_module(f"{name}.effects")
    finally:
        sys.path[:] = original_paths
    removed = sys.modules.pop(dependency)
    try:
        with (
            pytest.raises(LayerStateError, match="cached layer dependency"),
            selected_layer(trusted_roots=[root], import_paths=[root.parent]),
        ):
            pytest.fail("missing retained source import was adopted")
        assert sys.modules[f"{name}.effects"] is module
    finally:
        sys.modules[dependency] = removed


@pytest.mark.parametrize("change", ["identity", "package-path", "source-shadow"])
def test_captured_engine_dependency_still_requires_identity_metadata_and_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    import deal

    name, root, _ = _layers(tmp_path)
    with (root / "effects.py").open("a") as stream:
        stream.write("import deal\n")
    if change == "identity":
        replacement = ModuleType("deal")
        vars(replacement).update(vars(deal))
        monkeypatch.setitem(sys.modules, "deal", replacement)
    elif change == "package-path":
        monkeypatch.setattr(deal, "__path__", [str(tmp_path)])
    else:
        (root.parent / "deal.py").write_text("raise AssertionError('shadow ran')\n")
    with (
        pytest.raises(LayerStateError, match="engine"),
        selected_layer(trusted_roots=[root], import_paths=[root.parent]) as image,
    ):
        importlib.import_module(f"{name}.effects")
        image.refresh_registrations()


def test_external_carrier_preloaded_before_coordinator_is_not_engine_authority(
    tmp_path: Path,
) -> None:
    name, root, _ = _layers(tmp_path)
    carrier_name = f"early_carrier_{uuid4().hex}"
    dependency = f"early_dependency_{uuid4().hex}"
    carrier_root = tmp_path / "external"
    carrier_root.mkdir()
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / f"{dependency}.py").write_text("ALLOWED = True\n")
    (right / f"{dependency}.py").write_text("ALLOWED = False\n")
    (carrier_root / f"{carrier_name}.py").write_text(
        f"from {dependency} import ALLOWED\n"
    )
    with (root / "effects.py").open("a") as stream:
        stream.write(f"import {carrier_name}\n")
    script = f"""
import importlib
import sys
from pathlib import Path
sys.path[:0] = [{str(left)!r}, {str(carrier_root)!r}]
importlib.import_module({carrier_name!r})
sys.path.remove({str(left)!r})
assert 'clauz3.layer_state' not in sys.modules
from clauz3.layer_state import LayerStateError, _ENGINE_MODULES, selected_layer
assert {carrier_name!r} not in _ENGINE_MODULES
assert {dependency!r} not in _ENGINE_MODULES
try:
    with selected_layer(
        trusted_roots=[Path({str(root)!r})],
        import_paths=[Path({str(right)!r}), Path({str(root.parent)!r})],
    ) as image:
        importlib.import_module({f"{name}.effects"!r})
        image.refresh_registrations()
except LayerStateError as exc:
    assert 'cached layer dependency' in str(exc), str(exc)
else:
    raise AssertionError('stale external carrier received engine authority')
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_resolution_cache_does_not_span_trusted_python(tmp_path: Path) -> None:
    from clauz3.interp.import_bindings import _PHASE_FINDERS

    name, root, _ = _layers(tmp_path)
    with (root / "effects.py").open("a") as stream:
        stream.write(
            "from clauz3.interp.import_bindings import _PHASE_FINDERS\n"
            "assert _PHASE_FINDERS.get() is None\n"
            "def check_phase():\n"
            "    assert _PHASE_FINDERS.get() is None\n"
        )
    with selected_layer(trusted_roots=[root], import_paths=[root.parent]) as image:
        assert _PHASE_FINDERS.get() is None
        module = importlib.import_module(f"{name}.effects")
        image.refresh_registrations()
        module.check_phase()
    assert _PHASE_FINDERS.get() is None
    with image.activate():
        module.check_phase()


def test_resumed_image_detects_new_shadow_with_restored_directory_time(
    tmp_path: Path,
) -> None:
    import os

    name, root, _ = _layers(tmp_path)
    earlier, later = tmp_path / "earlier", tmp_path / "later"
    earlier.mkdir()
    later.mkdir()
    dependency = f"new_shadow_{uuid4().hex}"
    (later / f"{dependency}.py").write_text("VALUE = True\n")
    with (root / "effects.py").open("a") as stream:
        stream.write(f"import {dependency}\n")
    with selected_layer(
        trusted_roots=[root], import_paths=[earlier, later, root.parent]
    ) as image:
        module = importlib.import_module(f"{name}.effects")
    previous = earlier.stat()
    (earlier / f"{dependency}.py").write_text("raise AssertionError('shadow ran')\n")
    os.utime(earlier, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    with (
        pytest.raises(LayerStateError, match="layer import selection changed"),
        image.activate(),
    ):
        pytest.fail("resumption missed the newly introduced shadow")
    assert module.EVENTS == []
    assert sys.modules[f"{name}.effects"] is module


@pytest.mark.parametrize("primary_failure", [False, True])
def test_capture_failure_preserves_primary_error_and_restores_displaced_layer(
    tmp_path: Path, primary_failure: bool
) -> None:
    name, left, right = _layers(tmp_path, namespace=True)
    with selected_layer(trusted_roots=[left], import_paths=[_search(left, name)]):
        first = importlib.import_module(f"{name}.effects")
    primary = ValueError("primary operation failed")
    try:
        with selected_layer(
            trusted_roots=[right], import_paths=[_search(right, name)]
        ) as image:
            second = importlib.import_module(f"{name}.effects")
            assert second is not first
            image.refresh_registrations()
            (right.parent / "__init__.py").write_text("")
            if primary_failure:
                raise primary
    except ValueError as caught:
        assert primary_failure
        assert caught is primary
        assert any(
            "layer state capture also failed" in note for note in caught.__notes__
        )
    except LayerStateError:
        assert not primary_failure
    else:
        pytest.fail("capture accepted the changed parent source")
    assert sys.modules[f"{name}.effects"] is first
    assert vars(sys.modules[name])["effects"] is first


def test_capture_failure_is_not_suppressed_by_a_callers_handled_exception(
    tmp_path: Path,
) -> None:
    name, root, _ = _layers(tmp_path)
    with pytest.raises(LayerStateError, match="cached layer dependency"):
        try:
            raise ValueError("unrelated caller exception")
        except ValueError:
            with selected_layer(
                trusted_roots=[root], import_paths=[root.parent]
            ) as image:
                module = importlib.import_module(f"{name}.effects")
                image.refresh_registrations()
                vars(module)["unknown_dependency"] = ModuleType("missing_dependency")
