"""Recognize vetted library models without treating import permission as purity."""

from __future__ import annotations

import sys
import sysconfig
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.machinery import (
    BuiltinImporter,
    ExtensionFileLoader,
    ModuleSpec,
    SourceFileLoader,
    all_suffixes,
)
from pathlib import Path
from types import ModuleType

# These non-callback APIs have explicit prover/interpreter models. Keep this
# narrower than the import allowlist: frameworks, opt-in imports, and arbitrary
# library callbacks can reach effects even when their module may be imported.
_CALLS = {
    "math": frozenset({"sin", "isclose", "isinf", "isnan", "trunc"}),
    "re": frozenset({"match", "fullmatch", "compile"}),
}
# These names only avoid a DYNAMIC_CALL during policy discovery. They do not
# authorize symbolic data reads: the prover independently refuses module data
# bindings, including math constants, because those bindings can be replaced.
_VALUES = {"math": frozenset({"pi", "e", "inf", "nan"})}


@dataclass(frozen=True)
class ModeledStdlib:
    name: str

    def knows(self, member: str, *, as_value: bool) -> bool:
        return member in _CALLS[self.name] or (
            as_value and member in _VALUES.get(self.name, ())
        )


def modeled_stdlib(name: str, import_roots: Sequence[Path]) -> ModeledStdlib | None:
    """Recognize an already loaded standard module with its real origin.

    Configured source candidates win, even if unreadable or unsupported. An
    ambient or cached replacement must never gain the exemption just by being
    named math/re. No imports, finders, or module-level attribute hooks run.
    """
    if name not in _CALLS or any(
        (root / name).exists()
        or any((root / f"{name}{suffix}").exists() for suffix in all_suffixes())
        for root in import_roots
    ):
        return None
    module = sys.modules.get(name)
    if type(module) is not ModuleType:
        return None
    attributes = vars(module)
    spec = attributes.get("__spec__")
    if type(spec) is not ModuleSpec or spec.name != name:
        return None
    loader: object = spec.loader
    if spec.origin == "built-in" and loader is BuiltinImporter:
        return ModeledStdlib(name) if name in sys.builtin_module_names else None
    filename = attributes.get("__file__")
    if (
        not isinstance(filename, str)
        or not isinstance(spec.origin, str)
        or type(loader) not in {SourceFileLoader, ExtensionFileLoader}
    ):
        return None
    try:
        origin = Path(spec.origin).resolve()
        if Path(filename).resolve() != origin:
            return None
        stdlib = [
            Path(sysconfig.get_path(key)).resolve() for key in ("stdlib", "platstdlib")
        ]
        packages = [
            Path(sysconfig.get_path(key)).resolve() for key in ("purelib", "platlib")
        ]
        if (
            not any(origin.is_relative_to(root) for root in stdlib)
            or any(origin.is_relative_to(root) for root in packages)
            or {"site-packages", "dist-packages"} & set(origin.parts)
        ):
            return None
    except (OSError, RuntimeError):
        return None
    return ModeledStdlib(name)
