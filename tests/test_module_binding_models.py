"""Import spelling cannot establish the lifetime of a module data binding."""

from pathlib import Path
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_text


def _package(tmp_path: Path, declaration: str = "FLAG = True\n") -> Path:
    package = tmp_path / f"module_bindings_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "data.py").write_text(declaration)
    (package / "facade.py").write_text("from . import data\n")
    (package / "helper.py").write_text(
        "from . import data\ndef flag() -> bool:\n    return data.FLAG\n"
    )
    return package


@pytest.mark.parametrize(
    ("imports", "body"),
    [
        ("import {package}.data", "assert {package}.data.FLAG"),
        ("from {package} import data as values", "assert values.FLAG"),
        ("import {package}.facade as facade", "assert facade.data.FLAG"),
        ("from {package}.helper import flag", "assert flag()"),
        ("import {package}.data as values", "saved = values.FLAG\n    assert saved"),
    ],
    ids=["qualified", "from-module-alias", "facade", "helper", "local-snapshot"],
)
def test_module_data_routes_refuse_without_assuming_stability(
    tmp_path: Path, imports: str, body: str
) -> None:
    package = _package(tmp_path)
    source = (
        imports.format(package=package.name)
        + "\ndef main() -> None:\n    "
        + body.format(package=package.name)
        + "\n"
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "module data bindings are not modeled" in str(results[0].proof)


def test_final_annotation_does_not_authorize_module_data_folding(
    tmp_path: Path,
) -> None:
    package = _package(tmp_path, "from typing import Final\nFLAG: Final[bool] = True\n")
    source = (
        f"import {package.name}.data as values\n"
        "def main() -> None:\n"
        "    assert values.FLAG\n"
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "module data bindings are not modeled" in str(results[0].proof)


@pytest.mark.parametrize("alias", [False, True], ids=["bare", "alias"])
def test_imported_data_snapshot_keeps_existing_refusal(
    tmp_path: Path, alias: bool
) -> None:
    package = _package(tmp_path)
    name = "saved" if alias else "FLAG"
    suffix = " as saved" if alias else ""
    source = (
        f"from {package.name}.data import FLAG{suffix}\n"
        "def main() -> None:\n"
        f"    assert {name}\n"
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "cannot resolve callable target" in str(results[0].proof)


@pytest.mark.parametrize("correct", [False, True], ids=["false", "true"])
def test_local_scalar_values_keep_exact_results(correct: bool) -> None:
    source = f"def main() -> None:\n    flag = True\n    assert flag == {correct}\n"

    results = prove_text(source)

    expected = Conclusion.OK if correct else Conclusion.FAIL
    assert [result.proof.conclusion for result in results] == [expected]
