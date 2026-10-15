"""Retained import evidence follows Python's lexical binding scopes."""

import sys
from pathlib import Path
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import ProverConfigError, prove_text


def _layer(tmp_path: Path, imports: str) -> tuple[Path, str]:
    layer = tmp_path / f"scope_layer_{uuid4().hex}"
    layer.mkdir()
    (layer / "__init__.py").write_text("")
    (layer / "effects.py").write_text(
        "import clauz3\n" + imports + "@clauz3.trusted\n"
        "@clauz3.ensures(lambda result: result)\n"
        "def allowed() -> bool:\n"
        "    return True\n"
    )
    source = (
        f"from {layer.name}.effects import allowed\ndef main():\n    assert allowed()\n"
    )
    return layer, source


@pytest.mark.parametrize("scope", ["function", "method", "nested_global", "relative"])
def test_unused_local_import_cannot_borrow_same_named_module_global(
    tmp_path: Path, scope: str
) -> None:
    name = f"dependency_{uuid4().hex}"
    package = tmp_path / name
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "optional.py").write_text("raise AssertionError('unused import ran')\n")
    if scope == "function":
        imports = f"def unused():\n    import {name}.optional\n"
    elif scope == "method":
        imports = (
            f"class Unused:\n    def method(self):\n        import {name}.optional\n"
        )
    elif scope == "relative":
        imports = "def unused():\n    from ...missing import local\n"
    else:
        imports = (
            "def unused():\n"
            f"    import {name}.optional\n"
            "    def inner():\n"
            f"        global {name}\n"
        )
    layer, source = _layer(tmp_path, f"import {name}\n" + imports)
    proofs = prove_text(source, trusted_roots=[layer], import_paths=[tmp_path])
    assert [result.proof.conclusion for result in proofs] == [Conclusion.OK]
    assert name in sys.modules
    assert name + ".optional" not in sys.modules


@pytest.mark.parametrize("scope", ["module", "function_global", "class_global"])
def test_missing_retained_scalar_import_still_requires_verified_identity(
    tmp_path: Path, scope: str
) -> None:
    name = f"dependency_{uuid4().hex}"
    (tmp_path / f"{name}.py").write_text("FLAG = True\n")
    if scope == "module":
        imports = f"if True:\n    from {name} import FLAG\n"
    elif scope == "function_global":
        imports = (
            "def initialize():\n"
            "    global FLAG\n"
            f"    from {name} import FLAG\n"
            "initialize()\n"
        )
    else:
        imports = (
            "class Initialize:\n    global FLAG\n" + f"    from {name} import FLAG\n"
        )
    imports += f"import sys\nsys.modules.pop({name!r})\n"
    layer, source = _layer(tmp_path, imports)
    with pytest.raises(
        ProverConfigError,
        match=f"cached layer dependency has no verified identity: {name}",
    ):
        prove_text(source, trusted_roots=[layer], import_paths=[tmp_path])
