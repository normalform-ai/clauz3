"""Registered models validate the Python call surface they actually support."""

import astroid
import pytest
import z3

from clauz3._vendor.deal_solver import Conclusion
from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._eval_expr import eval_expr
from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3._vendor.deal_solver._funcs import FUNCTIONS
from clauz3._vendor.deal_solver._proxies import FuncSort, ProxySort, types
from clauz3.prover import prove_text


@pytest.mark.parametrize(
    ("imports", "call"),
    [
        ("", "len()"),
        ("", "len([1], [2])"),
        ("", "len(items=[1])"),
        ("", "len([1], unknown=1)"),
        ("", "sum()"),
        ("", "sum([1], start=1)"),
        ("", "sum([1], 1)"),
        ("", "min()"),
        ("", "min([1], default=0)"),
        ("", "max([1], default=0)"),
        ("", "max(1, 2, 3)"),
        ("", "ord()"),
        ("", "ord(val='a')"),
        ("", "abs(x=1)"),
        ("", "int('10', base=2)"),
        ("", "float(a=1)"),
        ("", "str('value', encoding='utf-8')"),
        ("", "bool(obj=1)"),
        ("", "list(iterable=[1])"),
        ("", "set(iterable={1})"),
        ("", "dict(x=1)"),
        ("", "print('value', sep=':')"),
        ("import math", "math.sin(x=0)"),
        ("import math", "math.sin(0, unknown=1)"),
        ("import math", "math.isinf()"),
        ("import math", "math.isnan(0, 1)"),
        ("import math", "math.trunc(x=1)"),
        ("import math", "math.isclose(1, 1, 0.1)"),
        ("import math", "math.isclose(1, 1, unknown=1)"),
        ("import re", "re.compile('a', flags=2)"),
        ("import re", "re.match('a')"),
        ("import re", "re.fullmatch('a', 'a', flags=2)"),
        ("import posixpath", "posixpath.join(a='x')"),
        ("import ntpath", "ntpath.join('x', unknown='y')"),
        ("import os.path", "os.path.join()"),
    ],
)
def test_malformed_or_unmodeled_registered_call_shapes_are_refused(
    imports: str, call: str
) -> None:
    results = prove_text(
        f"{imports}\ndef main() -> None:\n    {call}\n    assert True\n",
        extra_allowed_imports=["os", "ntpath", "posixpath", "random"],
    )

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    diagnostic = "unsupported registered call argument binding"
    if call.startswith("int("):
        diagnostic = "int() requires one positional argument and no keywords"
    elif call.startswith("os.path.join("):
        diagnostic = "no unambiguous definition"
    assert diagnostic in str(results[0].proof)


@pytest.mark.parametrize(
    ("imports", "expression"),
    [
        ("", "len([1]) == 1"),
        ("", "sum([1, 2]) == 3"),
        ("", "min(1, 2) == 1"),
        ("", "max([1, 2]) == 2"),
        ("", "ord('a') == 97"),
        ("", "abs(-1) == 1"),
        ("", "int('10') == 10"),
        ("", "float(1) == 1.0"),
        ("", "str('value') == 'value'"),
        ("", "bool([1])"),
        ("", "list() == []"),
        ("", "set({1}) == {1}"),
        ("", "dict({'x': 1}) == {'x': 1}"),
        ("import math", "math.sin(0) == 0"),
        ("import math", "not math.isinf(0)"),
        ("import math", "not math.isnan(0)"),
        ("import math", "math.trunc(1.5) == 1"),
        ("import math", "math.isclose(1.0, 1.0, rel_tol=0.1, abs_tol=0.1)"),
        ("import math", "not math.isclose(1.0, 2.0, rel_tol=0.0, abs_tol=0.0)"),
        ("import re", "re.match(pattern='a', string='ab')"),
        ("import re", "re.fullmatch(string='a', pattern='a')"),
        ("import posixpath", "posixpath.join('a', 'b') == 'a/b'"),
        ("from math import isclose as near", "near(1.0, 1.0, abs_tol=0.1)"),
    ],
)
def test_supported_registered_arguments_keep_their_meaning(
    imports: str, expression: str
) -> None:
    # Pin the argument values accepted by each registered call. Source
    # attributes still have to match the interpreter's closed surface.
    results = prove_text(
        f"{imports}\ndef main() -> None:\n    assert {expression}\n",
        extra_allowed_imports=["posixpath", "random"],
    )

    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


def test_compile_accepts_its_pattern_keyword_without_using_pattern_methods() -> None:
    # Compilation has a provider; Pattern.match/fullmatch attributes do not.
    results = prove_text(
        "import re\ndef main() -> None:\n"
        "    pattern = re.compile(pattern='a')\n    assert True\n"
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


def test_noop_print_accepts_its_supported_positional_objects() -> None:
    results = prove_text("def main() -> None:\n    print('one', 2)\n    assert True\n")
    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


def _random_arguments(values: dict[str, str], ctx: Context) -> dict[str, ProxySort]:
    return {
        name: eval_expr(astroid.extract_node(source), ctx=ctx)
        for name, source in values.items()
    }


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("randint", {"a": "1"}),
        ("randrange", {"start": "1", "stop": "4", "step": "2"}),
        ("choice", {"seq": "[1]", "unknown": "2"}),
    ],
)
def test_random_model_signatures_refuse_unmodeled_arguments(
    name: str, arguments: dict[str, str]
) -> None:
    # Public random callable resolution remains unsupported. Exercise these
    # registered models directly without claiming that the import surface works.
    ctx = Context.make_empty(get_contracts=lambda _: iter(()))
    model = FuncSort(FUNCTIONS[f"random.Random.{name}"])
    kwargs = _random_arguments(arguments, ctx)

    with pytest.raises(UnsupportedError, match="unsupported registered call argument"):
        model.m_call(ctx=ctx, **kwargs)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("randint", {"a": "1", "b": "1"}),
        ("randrange", {"start": "1", "stop": "2"}),
        ("choice", {"seq": "[1]"}),
    ],
)
def test_random_model_supported_keywords_keep_their_bounds(
    name: str, arguments: dict[str, str]
) -> None:
    ctx = Context.make_empty(get_contracts=lambda _: iter(()))
    model = FuncSort(FUNCTIONS[f"random.Random.{name}"])
    kwargs = _random_arguments(arguments, ctx)

    result = model.m_call(ctx=ctx, **kwargs)

    solver = z3.Solver()
    solver.add(*(condition.expr for condition in ctx.given))
    solver.add(result.m_ne(types.int.val(1, ctx=ctx), ctx=ctx).expr)
    assert solver.check() == z3.unsat
