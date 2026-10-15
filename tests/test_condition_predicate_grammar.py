"""Both predicate AST representations must recognize Deal's calling convention."""

import ast

import astroid
import pytest

from clauz3._vendor.deal_solver import UnsupportedError
from clauz3.prover import _require_lambda


@pytest.mark.parametrize("parser", ["ast", "astroid"])
@pytest.mark.parametrize(
    "predicate",
    [
        "lambda _: True",
        "lambda _=None: True",
        "lambda _, /: True",
        "lambda *, _: True",
        "lambda *_: True",
        "lambda **_: True",
    ],
)
def test_sole_underscore_is_refused_for_every_parameter_kind(
    parser: str, predicate: str
) -> None:
    node = (
        ast.parse(predicate, mode="eval").body
        if parser == "ast"
        else astroid.extract_node(predicate)
    )
    with pytest.raises(UnsupportedError, match="short-form"):
        _require_lambda(node, "@clauz3.requires")


@pytest.mark.parametrize("parser", ["ast", "astroid"])
@pytest.mark.parametrize(
    "predicate",
    [
        "lambda: True",
        "lambda x: True",
        "lambda _, x: True",
        "lambda _, result: True",
        "lambda x, *, _: True",
        "lambda x, *_: True",
        "lambda x, **_: True",
    ],
)
def test_explicit_parameter_names_do_not_select_the_short_form(
    parser: str, predicate: str
) -> None:
    node = (
        ast.parse(predicate, mode="eval").body
        if parser == "ast"
        else astroid.extract_node(predicate)
    )
    _require_lambda(node, "@clauz3.requires")
