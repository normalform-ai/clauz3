"""Source callable attributes must match the interpreter's closed surface."""

import keyword
from collections.abc import Callable
from textwrap import indent

import astroid
import pytest

from clauz3._vendor.deal_solver import Conclusion, UnsupportedError
from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._eval_expr import eval_expr
from clauz3._vendor.deal_solver._proxies import ProxySort, types
from clauz3._vendor.deal_solver._proxies._method import Method
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.interp.methods import MODELLED_METHODS
from clauz3.prover import prove_text

WRAPPER_ERROR = "explicit __call__ attributes are not supported"
MUTATION_ERROR = "mutating method requires a direct call on a named receiver"
HELPERS = (
    "def helper(value: int) -> int:\n"
    "    return value\n"
    "def invoke(fn) -> int:\n"
    "    return fn.__call__(7)\n"
)


def _source(body: str) -> str:
    return "import math\n" + HELPERS + "def main() -> None:\n" + indent(body, "    ")


@pytest.mark.parametrize(
    "body",
    [
        "assert helper.__call__(7) == 7",
        "assert helper.__call__(value=7) == 7",
        "assert helper.__call__.__call__(7) == 7",
        "saved = helper.__call__\nassert saved(7) == 7",
        "read = lambda value: value\nassert read.__call__(7) == 7",
        "read = lambda value: value\nsaved = read.__call__\nassert saved(7) == 7",
        "assert invoke(helper) == 7",
        "assert len.__call__('x') == 1",
        "assert math.sin.__call__(0.0) == 0.0",
        "assert 'abc'.startswith.__call__('a')",
        "starts = 'abc'.startswith\nsaved = starts.__call__\nassert saved('a')",
        "saved = (1).__call__\nassert True",
        "saved = [].__call__\nassert True",
        "saved = {'x': 1}.__call__\nassert True",
    ],
    ids=[
        "function",
        "function-keyword",
        "repeated-wrapper",
        "saved-function-wrapper",
        "lambda",
        "saved-lambda-wrapper",
        "forwarded-callable",
        "builtin",
        "library",
        "bound-method",
        "saved-bound-method-wrapper",
        "noncallable-scalar",
        "noncallable-list",
        "noncallable-dict",
    ],
)
def test_callable_wrapper_attribute_is_refused(body: str) -> None:
    proofs = prove_text(_source(body), target="main")

    assert [result.proof.conclusion for result in proofs] == [Conclusion.SKIP]
    assert WRAPPER_ERROR in str(proofs[0].proof.error)


@pytest.mark.parametrize(
    "attribute",
    [
        "__len__",
        "__bool__",
        "__str__",
        "__int__",
        "__float__",
        "__abs__",
        "__getattr__",
        "missing",
    ],
)
def test_lambda_attributes_use_a_source_type_name(attribute: str) -> None:
    proofs = prove_text(
        _source(f"read = lambda value: value\nsaved = read.{attribute}\nassert True"),
        target="main",
    )

    assert [result.proof.conclusion for result in proofs] == [Conclusion.SKIP]
    diagnostic = str(proofs[0].proof.error)
    assert "attribute is outside the interpreter surface" in diagnostic
    assert "lambda" in diagnostic
    assert attribute in diagnostic


@pytest.mark.parametrize(
    "body",
    [
        "saved = (lambda value: value).__len__\nassert True",
        "def inspect(fn):\n    return fn.__len__\n"
        "read = lambda value: value\nsaved = inspect(read)\nassert True",
    ],
    ids=["literal-lambda", "forwarded-lambda"],
)
def test_lambda_attribute_refusal_also_handles_literal_and_forwarded_values(
    body: str,
) -> None:
    proofs = prove_text(_source(body), target="main")

    assert [result.proof.conclusion for result in proofs] == [Conclusion.SKIP]
    assert "attribute is outside the interpreter surface" in str(proofs[0].proof.error)


def test_proxy_missing_lambda_attribute_records_a_source_type_name(
    ctx_factory: Callable[[], Context],
) -> None:
    ctx = ctx_factory()
    node = astroid.extract_node("lambda value: value")
    value = eval_expr(node=node, ctx=ctx)

    result = value.m_getattr("missing", ctx=ctx)

    assert result is value
    assert len(ctx.exceptions.layer) == 1
    error = ctx.exceptions.layer[0]
    assert error.name == "AttributeError"
    assert error.message == "'lambda' object has no attribute 'missing'"


def test_row_class_call_attribute_is_refused() -> None:
    source = (
        "import clauz3\n"
        "class Item(clauz3.Row):\n"
        "    value: int\n"
        "def main() -> None:\n"
        "    saved = Item.__call__\n"
        "    assert True\n"
    )

    proofs = prove_text(source, target="main")

    assert [result.proof.conclusion for result in proofs] == [Conclusion.SKIP]
    assert WRAPPER_ERROR in str(proofs[0].proof.error)


@pytest.mark.parametrize(
    ("setup", "expression", "expected"),
    [
        ("", "helper(value=7)", "7"),
        ("saved = helper", "saved(7)", "7"),
        ("read = lambda value: value", "read(7)", "7"),
        ("", "len('abc')", "3"),
        ("saved = len", "saved('abc')", "3"),
        ("", "math.sin(0.0)", "0.0"),
        ("saved = math.sin", "saved(0.0)", "0.0"),
        ("", "'abc'.startswith('a')", "True"),
        ("starts = 'abc'.startswith", "starts('a')", "True"),
    ],
    ids=[
        "function",
        "function-alias",
        "lambda",
        "builtin",
        "builtin-alias",
        "library",
        "library-alias",
        "method",
        "method-alias",
    ],
)
@pytest.mark.parametrize("correct", [True, False], ids=["correct", "wrong"])
def test_direct_calls_preserve_proof_and_runtime_behavior(
    setup: str, expression: str, expected: str, correct: bool
) -> None:
    comparison = "==" if correct else "!="
    body = (setup + "\n" if setup else "") + (
        f"assert {expression} {comparison} {expected}"
    )
    source = _source(body)

    proofs = prove_text(source, target="main")

    assert [result.proof.conclusion for result in proofs] == [
        Conclusion.OK if correct else Conclusion.FAIL
    ]
    if correct:
        outcome = execute(source, source_name="plan.py", policy=build_policy())
        assert outcome.effects == ()


@pytest.mark.parametrize(
    ("receiver", "literal"),
    [
        (str, "'seven'"),
        (list, "[1]"),
        (tuple, "(1,)"),
        (set, "{1}"),
        (dict, "{'x': 1}"),
        (int, "1"),
        (float, "1.5"),
        (bool, "True"),
    ],
    ids=["str", "list", "tuple", "set", "dict", "int", "float", "bool"],
)
def test_every_registered_attribute_matches_the_interpreter_surface(
    ctx_factory: Callable[[], Context], receiver: type[object], literal: str
) -> None:
    proxy = getattr(types, receiver.__name__)
    proxy.methods._finalize()
    allowed = MODELLED_METHODS.get(receiver, frozenset())
    accepted: set[str] = set()
    refused: set[str] = set()
    for name in sorted(proxy.methods._registry):
        # Operator registry labels such as '+' and 'not in' cannot be written
        # as Python attributes. Unsupported stubs and dunders must be checked.
        if not name.isidentifier() or keyword.iskeyword(name):
            continue
        ctx = ctx_factory()
        ctx.scope.set(
            "receiver",
            eval_expr(node=astroid.extract_node(f"value = __({literal})"), ctx=ctx),
        )
        node = astroid.extract_node(f"receiver.{name}")
        assert isinstance(node, astroid.Attribute)
        if name in allowed:
            call = astroid.extract_node(f"receiver.{name}()")
            assert isinstance(call, astroid.Call)
            # Attribute resolution in a direct call admits every supported
            # method. Impure methods cannot also be extracted into aliases.
            value = eval_expr(node=call.func, ctx=ctx)
            assert isinstance(value, ProxySort), (receiver.__name__, name)
            if isinstance(value, Method) and not value.pure:
                with pytest.raises(UnsupportedError, match=MUTATION_ERROR):
                    eval_expr(node=node, ctx=ctx)
            elif isinstance(value, Method) and receiver in {list, dict, set}:
                with pytest.raises(
                    UnsupportedError,
                    match="methods on mutable receivers require an immediate call",
                ):
                    eval_expr(node=node, ctx=ctx)
            else:
                assert isinstance(eval_expr(node=node, ctx=ctx), ProxySort)
            accepted.add(name)
        else:
            with pytest.raises(UnsupportedError):
                eval_expr(node=node, ctx=ctx)
            refused.add(name)

    assert accepted == allowed
    assert "__call__" in refused
    assert refused, receiver.__name__


@pytest.mark.parametrize(
    "body",
    [
        "values = [0]\nmutate = values.extend\nmutate([1, 2])\nassert len(values) == 1",
        "values = [0]\nmutate = values.clear\nmutate()\nassert len(values) == 1",
        "values = {0}\nmutate = values.add\nmutate(1)\nassert len(values) == 1",
        "values = {'a': 0}\nmutate = values.clear\nmutate()\nassert len(values) == 1",
        "values = []\nsaved = values.append\nassert True",
        "def invoke(fn):\n    fn(1)\nvalues = []\ninvoke(values.append)",
        "def accessor(values: list[int]):\n    return values.clear\n"
        "values = [0]\nsaved = accessor(values)",
        "[0].clear()",
        "values = [0]\nvalues.copy().clear()",
    ],
    ids=[
        "saved-extend",
        "saved-list-clear",
        "saved-set-add",
        "saved-dict-clear",
        "unused-untyped-append",
        "callback-argument",
        "returned-method",
        "literal-receiver",
        "call-receiver",
    ],
)
def test_mutating_methods_require_a_direct_call_on_a_named_receiver(body: str) -> None:
    proofs = prove_text(_source(body), target="main")

    assert [result.proof.conclusion for result in proofs] == [Conclusion.SKIP]
    assert MUTATION_ERROR in str(proofs[0].proof.error)


@pytest.mark.parametrize(
    ("setup", "mutation", "assertion"),
    [
        ("values = []", "values.append(1)", "values == [1]"),
        ("values = [0]", "values.extend([1, 2])", "values == [0, 1, 2]"),
        ("values = [0]", "values.clear()", "len(values) == 0"),
        ("values = {0}", "values.add(1)", "values == {0, 1}"),
        ("values = {0}", "values.update({1, 2})", "values == {0, 1, 2}"),
        ("values = {'a': 0}", "values.clear()", "'a' not in values"),
        (
            "values = {'a': 7}",
            "removed = values.pop('a')",
            "removed == 7 and 'a' not in values",
        ),
    ],
    ids=["append", "extend", "list-clear", "add", "update", "dict-clear", "pop"],
)
@pytest.mark.parametrize("correct", [True, False], ids=["correct", "wrong"])
def test_direct_named_mutations_preserve_receiver_state_and_results(
    setup: str, mutation: str, assertion: str, correct: bool
) -> None:
    condition = assertion if correct else f"not ({assertion})"
    source = _source(f"{setup}\n{mutation}\nassert {condition}")

    proofs = prove_text(source, target="main")

    assert [result.proof.conclusion for result in proofs] == [
        Conclusion.OK if correct else Conclusion.FAIL
    ]
    if correct:
        assert (
            execute(source, source_name="plan.py", policy=build_policy()).effects == ()
        )


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("(7).real", "7"),
        ("(7).imag", "0"),
        ("(7).numerator", "7"),
        ("(7).denominator", "1"),
        ("(1.5).real", "1.5"),
        ("(1.5).imag", "0.0"),
    ],
    ids=[
        "int-real",
        "int-imag",
        "numerator",
        "denominator",
        "float-real",
        "float-imag",
    ],
)
def test_supported_properties_preserve_values_without_a_call(
    expression: str, expected: str
) -> None:
    source = _source(f"saved = {expression}\nassert saved == {expected}")

    proofs = prove_text(source, target="main")

    assert [result.proof.conclusion for result in proofs] == [Conclusion.OK]
    outcome = execute(source, source_name="plan.py", policy=build_policy())
    assert outcome.effects == ()


@pytest.mark.parametrize("receiver", ["[1]", "(1,)"])
def test_attribute_authority_uses_only_the_most_specific_receiver(
    monkeypatch: pytest.MonkeyPatch, receiver: str
) -> None:
    monkeypatch.setitem(MODELLED_METHODS, list, MODELLED_METHODS[list] - {"count"})
    source = _source(f"values = {receiver}\nassert values.count(1) == 1")
    proofs = prove_text(source, target="main")
    expected = Conclusion.SKIP if receiver == "[1]" else Conclusion.OK
    assert [item.proof.conclusion for item in proofs] == [expected]
    if expected == Conclusion.SKIP:
        assert "attribute is outside the interpreter surface" in str(proofs[0].proof)
