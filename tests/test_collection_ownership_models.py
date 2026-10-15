"""Writes require a uniquely owned local collection, never a shared snapshot."""

from textwrap import indent

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3._vendor.deal_solver._proxies import (
    DictSort,
    IntSort,
    ListSort,
    ProxySort,
    SetSort,
    UntypedDictSort,
    UntypedListSort,
    UntypedSetSort,
)
from clauz3._vendor.deal_solver._proxies._method import Method
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import prove_text

ERROR = "shared or borrowed collection mutation is not supported"
HELPERS = (
    "def identity(value: list[int]) -> list[int]:\n    return value\n"
    "def size(value: list[int]) -> int:\n    return len(value)\n"
    "def fresh() -> list[int]:\n    return [0]\n"
)


def _source(body: str, parameters: str = "") -> str:
    return HELPERS + f"def main({parameters}) -> None:\n" + indent(body, "    ") + "\n"


@pytest.mark.parametrize(
    "body",
    [
        "values = [0]\nalias = values\nalias.append(1)\nassert len(values) == 1",
        "values = [0]\nalias = values\nvalues.append(1)\nassert len(alias) == 1",
        "values = {'x': 0}\nalias = values\nalias['x'] = 1\nassert values['x'] == 0",
        "values = {0}\nalias = values\nalias.add(1)\nassert len(values) == 1",
        "values = alias = [0]\nvalues.append(1)\nassert len(alias) == 1",
        "values = [0]\nalias = values\nalias = []\nvalues.append(1)\nassert True",
        "values = [0]\nouter = [values]\nvalues.append(1)\nassert len(outer[0]) == 1",
        "values = [0]\nouter = (values,)\nvalues.append(1)\nassert len(outer[0]) == 1",
        "values = [0]\nouter = {'x': values}\nvalues.append(1)\n"
        "assert len(outer['x']) == 1",
        "outer = [[0]]\nalias = outer[0]\nalias.append(1)\nassert len(outer[0]) == 1",
        "values = [0]\nouter = [[2]]\nouter.append(values)\nvalues.append(1)\n"
        "assert len(outer[1]) == 1",
        "values = [0]\nouter = {'x': [2]}\nouter['x'] = values\nvalues.append(1)\n"
        "assert len(outer['x']) == 1",
        "values = [0]\nouter = [values for ignored in [0]]\nvalues.append(1)\n"
        "assert len(outer[0]) == 1",
        "values = [0]\nalias = identity(values)\nalias.append(1)\n"
        "assert len(values) == 1",
        "values = [0]\nassert size(values) == 1\nvalues.append(1)\nassert True",
        "values = fresh()\nvalues.append(1)\nassert True",
        "values = [0]\nread = lambda: len(values)\nvalues.append(1)\nassert True",
        "read = lambda: len(values)\nvalues = [0]\nvalues.append(1)\nassert True",
        "values = [0]\ndef read() -> int:\n    return len(values)\n"
        "values.append(1)\nassert True",
        "values = [0]\nfor alias in [values]:\n    values.append(1)\nassert True",
        "values = [0]\nfor alias in [values]:\n    alias.append(1)\nassert True",
        "values = [0]\ndata = {'x': [1]}\nalias = data.get('absent', values)\n"
        "values.append(1)\nassert len(alias) == 1",
        "outer = [[0]]\ncopy = outer.copy()\nalias = copy[0]\nalias.append(1)\n"
        "assert len(outer[0]) == 1",
        "values = [0]\nouter = {'x': values}\nremoved = outer.pop('x')\n"
        "values.append(1)\nassert len(removed) == 1",
    ],
    ids=[
        "alias-write",
        "original-write",
        "dict-alias",
        "set-alias",
        "multiple-targets",
        "rebound-alias",
        "list-retention",
        "tuple-retention",
        "dict-retention",
        "extraction",
        "append-retention",
        "item-retention",
        "comprehension-retention",
        "returned-alias",
        "read-helper-escape",
        "borrowed-return",
        "lambda-capture",
        "future-capture",
        "function-capture",
        "loop-original",
        "loop-alias",
        "get-default",
        "shallow-copy",
        "pop-retention",
    ],
)
def test_shared_or_borrowed_writes_fail_closed(body: str) -> None:
    proofs = prove_text(_source(body), target="main")
    assert [item.proof.conclusion for item in proofs] == [Conclusion.SKIP]
    assert ERROR in str(proofs[0].proof)


@pytest.mark.parametrize(
    ("setup", "copy", "mutation", "check"),
    [
        (
            "values = [0]",
            "[0]",
            "other.append(1)",
            "len(values) == 1 and len(other) == 2",
        ),
        (
            "values = [0]",
            "values.copy()",
            "other.append(1)",
            "len(values) == 1 and len(other) == 2",
        ),
        (
            "values = [0]",
            "list(values)",
            "other.append(1)",
            "len(values) == 1 and len(other) == 2",
        ),
        (
            "values = [0]",
            "values[:]",
            "other.append(1)",
            "len(values) == 1 and len(other) == 2",
        ),
        (
            "values = [0]",
            "[] + values",
            "other.append(1)",
            "len(values) == 1 and len(other) == 2",
        ),
        (
            "values = [0]",
            "values + []",
            "other.append(1)",
            "len(values) == 1 and len(other) == 2",
        ),
        (
            "values = {'x': 0}",
            "values.copy()",
            "other['x'] = 1",
            "values['x'] == 0 and other['x'] == 1",
        ),
        (
            "values = {'x': 0}",
            "dict(values)",
            "other['x'] = 1",
            "values['x'] == 0 and other['x'] == 1",
        ),
        (
            "values = {0}",
            "values.copy()",
            "other.add(1)",
            "1 not in values and 1 in other",
        ),
        (
            "values = {0}",
            "set(values)",
            "other.add(1)",
            "1 not in values and 1 in other",
        ),
        (
            "values = {0}",
            "values | {0}",
            "other.add(1)",
            "1 not in values and 1 in other",
        ),
        (
            "values = {0}",
            "values.union({0})",
            "other.add(1)",
            "1 not in values and 1 in other",
        ),
    ],
    ids=[
        "equal-literals",
        "list-copy",
        "list-constructor",
        "slice",
        "empty-concat",
        "concat",
        "dict-copy",
        "dict-constructor",
        "set-copy",
        "set-constructor",
        "set-operator",
        "set-method",
    ],
)
@pytest.mark.parametrize("correct", [True, False], ids=["correct", "wrong"])
def test_independent_collections_keep_separate_write_permission(
    setup: str, copy: str, mutation: str, check: str, correct: bool
) -> None:
    assertion = check if correct else f"not ({check})"
    source = _source(f"{setup}\nother = {copy}\n{mutation}\nassert {assertion}")
    proofs = prove_text(source, target="main")
    assert [item.proof.conclusion for item in proofs] == [
        Conclusion.OK if correct else Conclusion.FAIL
    ]
    if correct:
        assert (
            execute(source, source_name="plan.py", policy=build_policy()).effects == ()
        )


@pytest.mark.parametrize(
    "body",
    [
        "values = [0]\nalias = values\nassert len(alias) == len(values)",
        "values = [0]\nouter = (values,)\nassert len(outer[0]) == 1",
        "values = [0]\nassert size(values) == 1\nassert len(values) == 1",
        "values = []\nfor item in range(3):\n    values.append(item)\n"
        "assert len(values) == 3",
        "values = [0]\nalias = values\nvalues = [2]\nvalues.append(3)\n"
        "assert len(values) == 2 and len(alias) == 1",
        "values = []\nvalues.append(1)\nvalues.clear()\nvalues.append(2)\n"
        "assert len(values) == 1 and values[0] == 2",
        "values = {}\nvalues['x'] = 1\nremoved = values.pop('x')\n"
        "assert removed == 1 and 'x' not in values",
        "values = set()\nvalues.add(1)\nvalues.clear()\nvalues.add(2)\n"
        "assert 2 in values",
    ],
    ids=[
        "read-alias",
        "read-retention",
        "read-helper",
        "unrolled-loop",
        "new-root",
        "list-updates",
        "dict-pop",
        "set-updates",
    ],
)
def test_read_only_sharing_and_owned_updates_remain_supported(body: str) -> None:
    source = _source(body)
    proofs = prove_text(source, target="main")
    assert [item.proof.conclusion for item in proofs] == [Conclusion.OK]
    assert execute(source, source_name="plan.py", policy=build_policy()).effects == ()


@pytest.mark.parametrize("kind", ["list", "dict", "set"])
@pytest.mark.parametrize("correct", [True, False], ids=["correct", "wrong"])
def test_same_origin_survives_branch_snapshot_merge(kind: str, correct: bool) -> None:
    setups = {
        "list": (
            "[0]",
            "values.append(1)",
            "len(values) == 2",
            "values.append(2)",
            "(len(values) == 3) == flag",
        ),
        "dict": (
            "{'x': 1}",
            "values['x'] = 2",
            "values['x'] == 2",
            "values['x'] = 3",
            "values['x'] == 3",
        ),
        "set": (
            "{0}",
            "values.add(1)",
            "1 in values",
            "values.add(2)",
            "2 in values and ((1 in values) == flag)",
        ),
    }
    value, mutation, measure, update, final = setups[kind]
    check = f"({measure}) == flag"
    if not correct:
        check = f"not ({check})"
    body = (
        f"values = {value}\nif flag:\n    {mutation}\n"
        f"assert {check}\n{update}\nassert {final}"
    )
    proofs = prove_text(_source(body, "flag: bool"), target="main")
    assert [item.proof.conclusion for item in proofs] == [
        Conclusion.OK if correct else Conclusion.FAIL
    ]
    if correct:
        for flag in (True, False):
            runtime = _source(body.replace("flag", str(flag)))
            assert (
                execute(runtime, source_name="plan.py", policy=build_policy()).effects
                == ()
            )


@pytest.mark.parametrize("empty_first", [True, False], ids=["empty-else", "empty-then"])
@pytest.mark.parametrize("correct", [True, False], ids=["correct", "wrong"])
def test_empty_and_typed_string_list_branches_keep_the_selected_value(
    empty_first: bool, correct: bool
) -> None:
    initial, changed = ("[]", "['a']") if empty_first else ("['a']", "[]")
    selected = "flag" if empty_first else "not flag"
    check = f"(values == ['a']) == ({selected})"
    if not correct:
        check = f"not ({check})"
    body = f"values = {initial}\nif flag:\n    values = {changed}\nassert {check}"
    proofs = prove_text(_source(body, "flag: bool"), target="main")
    assert [item.proof.conclusion for item in proofs] == [
        Conclusion.OK if correct else Conclusion.FAIL
    ]
    if correct:
        for flag in (True, False):
            runtime = _source(body.replace("flag", str(flag)))
            assert (
                execute(runtime, source_name="plan.py", policy=build_policy()).effects
                == ()
            )


@pytest.mark.parametrize(
    "rebind", [False, True], ids=["alias-branch", "different-roots"]
)
def test_uncertain_branch_ownership_refuses_subsequent_write(rebind: bool) -> None:
    branch = "values = [1]" if rebind else "alias = values"
    source = _source(
        f"values = [0]\nalias = [2]\nif flag:\n    {branch}\n"
        "values.append(1)\nassert True",
        "flag: bool",
    )
    proofs = prove_text(source, target="main")
    assert [item.proof.conclusion for item in proofs] == [Conclusion.SKIP]
    assert ERROR in str(proofs[0].proof)


@pytest.mark.parametrize("shared", [False, True], ids=["owned", "already-shared"])
def test_replace_preserves_distinct_origins_and_shared_flags(shared: bool) -> None:
    ctx = Context.make_empty(get_contracts=lambda _: iter(()))
    before = ctx.ownership.fresh(UntypedListSort(), ctx=ctx)
    after = ctx.ownership.fresh(
        ListSort.val([IntSort.val(1, ctx=ctx)], ctx=ctx), ctx=ctx
    )
    ctx.ownership.bind("values", before, ctx=ctx)
    ctx.ownership.bind("other", after, ctx=ctx)
    first, second = ctx.ownership.origin(before), ctx.ownership.origin(after)
    assert first is not None and second is not None and first is not second
    second.shared = shared
    ctx.ownership.replace(before, after)
    assert ctx.ownership.origin(before) is first
    assert ctx.ownership.origin(after) is second
    assert first.shared and second.shared
    assert first.owner == "values" and second.owner == "other"
    for name, value in (("values", before), ("other", after)):
        with pytest.raises(UnsupportedError, match=ERROR):
            ctx.ownership.require_write(value, name, ctx=ctx)


EXTEND_MUTATIONS = {
    "argument": ("other.append(2)", "values == [1] and other == [1, 2]"),
    "receiver": ("values.append(2)", "values == [1, 2] and other == [1]"),
    "both": (
        "values.append(2)\nother.append(3)",
        "values == [1, 2] and other == [1, 3]",
    ),
}


@pytest.mark.parametrize("write", EXTEND_MUTATIONS)
@pytest.mark.parametrize("correct", [True, False], ids=["correct", "wrong"])
def test_empty_list_extend_preserves_independent_write_permission(
    write: str, correct: bool
) -> None:
    mutation, claim = EXTEND_MUTATIONS[write]
    check = claim if correct else f"not ({claim})"
    body = f"values = []\nother = [1]\nvalues.extend(other)\n{mutation}\nassert {check}"
    proofs = prove_text(_source(body), target="main")
    assert [item.proof.conclusion for item in proofs] == [
        Conclusion.OK if correct else Conclusion.FAIL
    ]


@pytest.mark.parametrize("write", EXTEND_MUTATIONS)
def test_empty_list_extend_runtime_copies_instead_of_aliasing(write: str) -> None:
    mutation, claim = EXTEND_MUTATIONS[write]
    body = f"values = []\nother = [1]\nvalues.extend(other)\n{mutation}\nassert {claim}"
    source = _source(body)
    assert execute(source, source_name="plan.py", policy=build_policy()).effects == ()
    namespace: dict[str, object] = {}
    exec(source, namespace)
    function = namespace["main"]
    assert callable(function)
    function()


def test_same_named_method_on_another_receiver_cannot_create_ownership() -> None:
    class AliasingList(ListSort):
        pass

    ctx = Context.make_empty(get_contracts=lambda _: iter(()))
    value: ProxySort = AliasingList.val([IntSort.val(1, ctx=ctx)], ctx=ctx)
    value = ctx.ownership.fresh(value, ctx=ctx)
    ctx.ownership.bind("values", value, ctx=ctx)
    origin = ctx.ownership.origin(value)
    assert origin is not None

    def alias(receiver: ProxySort, *, ctx: Context) -> ProxySort:
        return receiver

    method = Method(name="copy", impl=alias, pure=True, prop=False, obj=value)
    result = method.m_call(ctx=ctx)
    assert result is value
    assert ctx.ownership.origin(result) is origin
    assert origin.shared
    with pytest.raises(UnsupportedError, match=ERROR):
        ctx.ownership.require_write(result, "values", ctx=ctx)


@pytest.mark.parametrize(
    ("models", "names"),
    [
        ((ListSort, UntypedListSort), ("copy", "__add__")),
        ((DictSort, UntypedDictSort), ("copy",)),
        (
            (SetSort, UntypedSetSort),
            (
                "copy",
                "union",
                "intersection",
                "difference",
                "symmetric_difference",
                "__or__",
                "__and__",
                "__xor__",
            ),
        ),
    ],
    ids=["list", "dict", "set"],
)
def test_copy_producing_models_keep_pure_metadata_and_exact_receiver_permission(
    models: tuple[type[ProxySort], ...], names: tuple[str, ...]
) -> None:
    from clauz3._vendor.deal_solver._proxies._method import _returns_fresh_collection

    for model in models:
        for name in names:
            method = model.methods.get(name)
            assert method is not None, (model.__name__, name)
            assert method.pure, (model.__name__, name)
            assert _returns_fresh_collection(model, name)
            assert not _returns_fresh_collection(ProxySort, name)


@pytest.mark.parametrize("correct", [True, False], ids=["correct", "wrong"])
def test_extending_empty_list_with_empty_input_keeps_both_allocations(
    correct: bool,
) -> None:
    claim = "values == [1] and other == [2]"
    source = _source(
        "values = []\nother = []\nvalues.extend(other)\n"
        "values.append(1)\nother.append(2)\nassert "
        + (claim if correct else f"not ({claim})")
    )
    proofs = prove_text(source, target="main")
    assert [item.proof.conclusion for item in proofs] == [
        Conclusion.OK if correct else Conclusion.FAIL
    ]
    if correct:
        assert (
            execute(source, source_name="plan.py", policy=build_policy()).effects == ()
        )


def test_extend_can_copy_shared_input_without_changing_its_write_permission() -> None:
    body = (
        "values = []\nother = [1]\nalias = other\nvalues.extend(other)\n"
        "values.append(2)\nassert values == [1, 2] and alias == [1] and other == [1]"
    )
    proofs = prove_text(_source(body), target="main")
    assert [item.proof.conclusion for item in proofs] == [Conclusion.OK]
    assert (
        execute(_source(body), source_name="plan.py", policy=build_policy()).effects
        == ()
    )
    proofs = prove_text(_source(body + "\nother.append(3)"), target="main")
    assert [item.proof.conclusion for item in proofs] == [Conclusion.SKIP]
    assert ERROR in str(proofs[0].proof)
