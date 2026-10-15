"""Module data bindings are refused; local container models remain usable."""

from pathlib import Path
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_text


def _module(tmp_path: Path, literal: str) -> Path:
    package = tmp_path / f"module_values_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "values.py").write_text(f"VALUE = {literal}\n")
    return package


@pytest.mark.parametrize(
    ("literal", "assertion"),
    [
        ('["bob"]', 'constants.VALUE[0] == "bob"'),
        ('{"bob"}', '"bob" in constants.VALUE'),
        ('{"bob": 1}', 'constants.VALUE["bob"] == 1'),
        ('(["bob"],)', 'constants.VALUE[0][0] == "bob"'),
        ('(({"bob"},),)', '"bob" in constants.VALUE[0][0]'),
        ('(({"bob": 1},),)', 'constants.VALUE[0][0]["bob"] == 1'),
    ],
    ids=["list", "set", "dict", "tuple-list", "nested-tuple-set", "nested-tuple-dict"],
)
def test_mutable_module_values_are_refused_even_without_mutation(
    tmp_path: Path, literal: str, assertion: str
) -> None:
    package = _module(tmp_path, literal)
    source = (
        f"import {package.name}.values as constants\n"
        "def main() -> None:\n"
        f"    assert {assertion}\n"
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "mutable or non-literal module attribute" in str(results[0].proof)


@pytest.mark.parametrize(
    ("literal", "assertion"),
    [
        ('"bob"', 'constants.VALUE == "bob"'),
        ("True", "constants.VALUE"),
        ("7", "constants.VALUE == 7"),
        ("-7", "constants.VALUE == -7"),
        ("+2.5", "constants.VALUE == 2.5"),
        ("-2.5", "constants.VALUE == -2.5"),
        ("(((1, 2),),)", "constants.VALUE[0][0][1] == 2"),
        ("()", "len(constants.VALUE) == 0"),
    ],
    ids=[
        "str",
        "bool",
        "int",
        "negative-int",
        "positive-float",
        "negative-float",
        "deep-tuple",
        "empty-tuple",
    ],
)
@pytest.mark.parametrize("correct", [True, False], ids=["true", "false"])
def test_immutable_module_values_do_not_establish_binding_stability(
    tmp_path: Path, literal: str, assertion: str, correct: bool
) -> None:
    package = _module(tmp_path, literal)
    negate = "" if correct else "not "
    source = (
        f"import {package.name}.values as constants\n"
        "def main() -> None:\n"
        f"    assert {negate}({assertion})\n"
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "module data bindings are not modeled" in str(results[0].proof)


@pytest.mark.parametrize(
    ("imports", "reference"),
    [
        ("import {package}.values", "{package}.values.VALUE"),
        ("from {package} import values as constants", "constants.VALUE"),
    ],
    ids=["qualified-module", "from-module-alias"],
)
def test_module_import_spelling_cannot_bypass_mutable_value_refusal(
    tmp_path: Path, imports: str, reference: str
) -> None:
    package = _module(tmp_path, '["bob"]')
    source = (
        imports.format(package=package.name)
        + "\ndef main() -> None:\n"
        + f"    assert {reference.format(package=package.name)}[0] == 'bob'\n"
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "mutable or non-literal module attribute" in str(results[0].proof)


@pytest.mark.parametrize("route", ["facade", "helper"])
def test_indirect_module_access_cannot_bypass_mutable_value_refusal(
    tmp_path: Path, route: str
) -> None:
    package = _module(tmp_path, '["bob"]')
    if route == "facade":
        (package / "facade.py").write_text("from . import values as data\n")
        source = (
            f"import {package.name}.facade as facade\n"
            "def main() -> None:\n"
            "    assert facade.data.VALUE[0] == 'bob'\n"
        )
    else:
        (package / "helper.py").write_text(
            "from . import values as data\n"
            "def first() -> str:\n"
            "    return data.VALUE[0]\n"
        )
        source = (
            f"from {package.name}.helper import first\n"
            "def main() -> None:\n"
            "    assert first() == 'bob'\n"
        )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "mutable or non-literal module attribute" in str(results[0].proof)


@pytest.mark.parametrize("alias", [False, True], ids=["bare-name", "aliased-name"])
@pytest.mark.parametrize("literal", ['["bob"]', '"bob"'], ids=["mutable", "immutable"])
def test_from_imported_constant_retains_the_existing_bare_name_refusal(
    tmp_path: Path, alias: bool, literal: str
) -> None:
    package = _module(tmp_path, literal)
    name = "value" if alias else "VALUE"
    suffix = " as value" if alias else ""
    source = (
        f"from {package.name}.values import VALUE{suffix}\n"
        "def main() -> None:\n"
        f"    assert len({name}) > 0\n"
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "cannot resolve callable target" in str(results[0].proof)


@pytest.mark.parametrize(
    "body",
    [
        "values = [1]\n    values.append(2)\n    assert values == [1, 2]",
        "values = {1}\n    values.add(2)\n    assert values == {1, 2}",
        "values = {'bob': 1}\n    values['eve'] = 2\n    assert values['eve'] == 2",
        "values = (([1, 2],),)\n    assert values[0][0][1] == 2",
    ],
    ids=["list", "set", "dict", "nested-tuple"],
)
def test_local_container_models_remain_supported(body: str) -> None:
    results = prove_text(f"def main() -> None:\n    {body}\n")

    assert [result.proof.conclusion for result in results] == [Conclusion.OK]
