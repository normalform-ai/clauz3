"""``import_aliases`` maps the name a plain ``import`` binds, not the empty name.

``import X`` binds ``X`` and ``import X.Y`` binds ``X``. Both were stored under
the key ``""``, so the map lost the binding it documents, kept only the last
plain import, and resolved a decorator with no dotted spelling (whose head is
``""``) to whatever that import was.
"""

import ast
from pathlib import Path

import astroid
import pytest

from clauz3 import decorators
from clauz3.prover import ProverConfigError, validate_untrusted_source


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import clauz3", {"clauz3": "clauz3"}),
        ("import deal", {"deal": "deal"}),
        ("import a.b.c", {"a": "a"}),
        ("import a.b.c as abc", {"abc": "a.b.c"}),
        (
            "import clauz3, deal as d, json",
            {"clauz3": "clauz3", "d": "deal", "json": "json"},
        ),
        ("import json as clauz3\nimport clauz3", {"clauz3": "clauz3"}),
        ("import clauz3\nimport json as clauz3", {"clauz3": "json"}),
        ("from clauz3 import guarantee", {"guarantee": "clauz3.guarantee"}),
    ],
    ids=[
        "plain",
        "plain-deal",
        "dotted",
        "dotted-as",
        "several",
        "plain-rebinds-alias",
        "alias-rebinds-plain",
        "from-import",
    ],
)
def test_each_import_is_keyed_by_the_name_it_binds(
    source: str, expected: dict[str, str]
) -> None:
    assert decorators.import_aliases(ast.parse(source)) == expected
    assert decorators.import_aliases(astroid.parse(source)) == expected


def test_a_later_plain_import_restores_the_real_guarantee() -> None:
    tree = ast.parse(
        "import json as clauz3\nimport clauz3\n@clauz3.guarantee(1)\ndef f(): ...\n"
    )
    function = tree.body[2]
    assert isinstance(function, ast.FunctionDef)

    aliases = decorators.import_aliases(tree)

    assert decorators.is_guarantee(function.decorator_list[0], aliases)


def test_an_unspellable_decorator_resolves_to_nothing() -> None:
    tree = ast.parse("import clauz3\n@(lambda f: f)\ndef f(): ...\n")
    function = tree.body[1]
    assert isinstance(function, ast.FunctionDef)
    spelled = decorators.dotted_name(function.decorator_list[0])

    assert spelled == ""
    assert decorators.canonical_target(spelled, decorators.import_aliases(tree)) == ""


def test_refusing_an_unspellable_decorator_does_not_name_an_import() -> None:
    source = "import clauz3\n@(lambda f: f)\ndef main() -> None: ...\n"

    with pytest.raises(ProverConfigError, match="no importable target"):
        validate_untrusted_source(source=source, path=Path("plan.py"))
