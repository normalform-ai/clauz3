import importlib
import importlib.util
import pathlib
import sys
from collections.abc import Callable, Iterator, MutableMapping
from contextlib import AbstractContextManager, contextmanager
from types import ModuleType

import astroid
import pytest

from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._funcs import FUNCTIONS
from clauz3.libraries.discovery import Library, catalog
from clauz3.spec import REGISTERED_CONTRACTS


@pytest.fixture
def ctx_factory() -> Callable[[], Context]:
    def make() -> Context:
        return Context.make_empty(get_contracts=lambda _: iter(()))

    return make


@pytest.fixture
def filesystem_effects(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The bundled filesystem effects, loaded by path to run their bodies."""
    root = pathlib.Path(__file__).resolve().parents[1] / "src/clauz3/stdlib/filesystem"
    # effects.py imports its sibling ``tools.filesystem.trusted.events``.
    monkeypatch.syspath_prepend(str(root))
    spec = importlib.util.spec_from_file_location(
        "filesystem_effects", root / "tools/filesystem/trusted/effects.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def dependent_library(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Library:
    """Catalogue a local ``search`` library that depends on stdlib:filesystem.

    No bundled library declares a dependency, so this adds one to the
    catalogue every installer and discovery path reads. ``search`` performs
    the filesystem ``Read`` event, so its own module needs the dependency.
    """
    root = tmp_path_factory.mktemp("search-library")
    trusted = root / "tools/search/trusted"
    trusted.mkdir(parents=True)
    (trusted / "effects.py").write_text(
        "import clauz3\n"
        "from clauz3.events import performs\n"
        "from tools.filesystem.trusted.events import Read\n"
        "\n"
        "\n"
        "@clauz3.trusted\n"
        "@performs(lambda path: Read(path))\n"
        "def search(pattern: str, path: str) -> list[str]:\n"
        '    """Return the lines in ``path`` containing ``pattern``."""\n'
        "    return []\n"
    )
    (trusted / "contracts.py").write_text(
        "from tools.search.trusted.effects import search\n"
        "\n"
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "\n"
        "Search = effect(search)\n"
        "\n"
        "\n"
        "@contract\n"
        "def searches_at_most(count: int) -> ContractSpec:\n"
        '    """Guarantee at most ``count`` searches."""\n'
        "    return Search.count() <= count\n"
    )
    (trusted.parent / "clauz3.yaml").write_text(
        "schema_version: 1\nname: search\ntitle: Search\n"
        "summary: Search files for a substring.\n"
        "description: Search files using the filesystem library.\n"
        "dependencies: [stdlib:filesystem]\n"
        "requirements:\n  tools:\n    - name: search\n"
        "      description: search executable\n"
    )
    library = Library(
        source=str(root),
        description="Search files for a substring.",
        domains=("search",),
        dependencies=("stdlib:filesystem",),
        requirements=("search executable",),
        visibility="public",
    )
    entries = [*catalog(), library]
    for module in ("discovery", "install", "setup", "workspace"):
        monkeypatch.setattr(f"clauz3.libraries.{module}.catalog", lambda: entries)
    return library


@pytest.fixture(autouse=True)
def _evict_trusted_layers() -> Iterator[None]:
    with _trusted_layer_cache_scope():
        yield


@pytest.fixture
def trusted_layer_cache_scope() -> Callable[[], AbstractContextManager[None]]:
    return _trusted_layer_cache_scope


@contextmanager
def _trusted_layer_cache_scope() -> Iterator[None]:
    """Forget the layer modules a test imported once it is done.

    Trusted layers share dotted names across roots (``tools.gh.trusted`` ships
    under both a stdlib root and an example root; many tests write a layer
    into ``tmp_path`` under the same package name), and ``importlib`` returns
    whatever ``sys.modules`` already holds under a name. Without eviction the
    second test to use a name proves against the first test's layer: a
    path-traversal refutation in ``test_path_safety`` was passing on a SKIP
    for that reason (issue #111), and the trusted-layer loader now refuses a
    module cached from a different file outright. Evicting after each test
    keeps every test's imports its own: every ``tools.*`` module, and every
    module the test itself added whose source is outside the installed
    package and the environment (``tmp_path`` layers, ``tests/fixtures``,
    ``examples``).

    Remove the same names from astroid's source cache and the contract
    registries, including modules only astroid loaded. Keeping either behind
    can mix one root's Python objects with another root's source or contracts.
    Installed-library caches are retained when astroid exposes the expected
    import-cache interfaces; otherwise use its public full reset. Production
    isolation between proofs remains a separate concern (issue #111).
    """
    before = set(sys.modules)
    before_astroid = set(astroid.MANAGER.astroid_cache)
    try:
        yield
    finally:
        names = {
            name
            for name, module in list(sys.modules.items())
            if _is_layer(name) or (name not in before and _is_test_authored(module))
        }
        names.update(
            name
            for name, module in list(astroid.MANAGER.astroid_cache.items())
            if _is_layer(name)
            or (name not in before_astroid and _is_test_authored_file(module.file))
        )
        for name in names:
            sys.modules.pop(name, None)
            astroid.MANAGER.astroid_cache.pop(name, None)
        if names:
            _clear_astroid_import_caches(names)
        for name in list(FUNCTIONS):
            if name.rpartition(".")[0] in names:
                del FUNCTIONS[name]
        REGISTERED_CONTRACTS.difference_update(
            name
            for name in list(REGISTERED_CONTRACTS)
            if name.rpartition(".")[0] in names
        )


def _clear_astroid_import_caches(names: set[str]) -> None:
    """Forget import locations, falling back if astroid changes private APIs."""
    try:
        import_spec = importlib.import_module("astroid.interpreter._import.spec")
        module_files = astroid.MANAGER._mod_file_cache
        clearers = [import_spec._find_spec.cache_clear]
        clearers.extend(
            finder.find_module.cache_clear for finder in import_spec._SPEC_FINDERS
        )
        if not isinstance(module_files, MutableMapping) or any(
            not isinstance(key, tuple) or len(key) != 2 or not isinstance(key[0], str)
            for key in module_files
        ):
            raise TypeError("unrecognized astroid module-file cache")
        # Clearing the AST alone can still rebuild it from the previous root.
        for key in list(module_files):
            if key[0] in names:
                del module_files[key]
        # These smaller lookup caches use path=None for sys.path searches,
        # so they also survive the next test installing a different root.
        for clear in clearers:
            clear()
    except (ImportError, AttributeError, TypeError):
        # The public reset remains responsible for a future cache layout.
        # Never skip cleanup merely because the selective optimization broke.
        astroid.MANAGER.clear_cache()


def _is_layer(name: str) -> bool:
    return name == "tools" or name.startswith("tools.")


_KEEP_UNDER = tuple(
    pathlib.Path(p).resolve()
    for p in (
        sys.prefix,
        sys.base_prefix,
        pathlib.Path(__file__).resolve().parents[1] / "src",
    )
)


def _is_test_authored(module: ModuleType) -> bool:
    return _is_test_authored_file(getattr(module, "__file__", None))


def _is_test_authored_file(file: str | None) -> bool:
    if not file:
        return False
    resolved = pathlib.Path(file).resolve()
    return not any(resolved.is_relative_to(root) for root in _KEEP_UNDER)
