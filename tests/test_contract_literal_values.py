"""Contract builders receive the Python values written in guarantees."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from uuid import uuid4

import astroid
import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3._vendor.deal_solver._proxies import types
from clauz3.contract_literals import materialize_contract_argument
from clauz3.prover import prove_text
from clauz3.spec import ContractSpec, _solve_with, no_guarantees


@pytest.fixture
def literal_layer(tmp_path: Path) -> tuple[Path, str]:
    package = f"literal_value_types_{uuid4().hex}"
    root = tmp_path / package
    root.mkdir()
    (root / "__init__.py").write_text("")
    (root / "effects.py").write_text(
        "import clauz3\n@clauz3.trusted\ndef record(value: str) -> None:\n    pass\n"
    )
    return root, package


_LITERALS = [
    pytest.param("False", id="false"),
    pytest.param("True", id="true"),
    pytest.param("0", id="integer-zero"),
    pytest.param("-123", id="negative-integer"),
    pytest.param("123", id="integer"),
    pytest.param("0.0", id="float-zero"),
    pytest.param("-0.0", id="negative-zero"),
    pytest.param("1.25", id="float"),
    pytest.param("1e309", id="positive-infinity"),
    pytest.param("''", id="empty-string"),
    pytest.param("'A'", id="string"),
    pytest.param(r"'\u2603'", id="unicode"),
    pytest.param(r"r'\u{41}'", id="literal-z3-escape"),
    pytest.param(r"'\x00\n\\'", id="string-controls"),
    pytest.param(r"b'\x00\xff'", id="bytes"),
    pytest.param("None", id="none"),
    pytest.param("[]", id="empty-list"),
    pytest.param("()", id="empty-tuple"),
    pytest.param("{}", id="empty-dict"),
    pytest.param("[False, 0, -0.0, b'', None]", id="mixed-list"),
    pytest.param("(False, 0, -0.0, b'', None)", id="mixed-tuple"),
    pytest.param("{1, 2}", id="set"),
    pytest.param("{'x': [False, {'y': (0, -0.0)}]}", id="nested-dict"),
]


@pytest.mark.parametrize("literal", _LITERALS)
@pytest.mark.parametrize("permits_record", [False, True], ids=["forbidden", "allowed"])
def test_builder_observes_exact_literal_types_and_values(
    literal_layer: tuple[Path, str], literal: str, permits_record: bool
) -> None:
    root, package = literal_layer
    # This comparison runs in the trusted builder, where Python truthiness,
    # element types, and signed zero are part of the declared policy.
    (root / "contracts.py").write_text(
        dedent(
            f"""\
            import math
            from clauz3.spec import contract, effect, no_guarantees

            def exact(left, right):
                if type(left) is not type(right):
                    return False
                if isinstance(left, float):
                    return left == right and (
                        math.copysign(1, left) == math.copysign(1, right)
                    )
                if isinstance(left, (list, tuple)):
                    return len(left) == len(right) and all(
                        exact(a, b) for a, b in zip(left, right)
                    )
                if isinstance(left, dict):
                    return exact(list(left.items()), list(right.items()))
                return left == right

            @contract
            def check(value):
                matches = exact(value, {literal})
                if matches == {permits_record!r}:
                    return no_guarantees()
                return effect("record").empty()
            """
        )
    )
    source = dedent(
        f"""\
        import clauz3
        from {package}.contracts import check
        from {package}.effects import record

        @clauz3.guarantee(check({literal}))
        def main() -> None:
            record("bad")
        """
    )
    results = prove_text(source, trusted_roots=[root], import_paths=[root.parent])
    assert len(results) == 1
    expected = Conclusion.OK if permits_record else Conclusion.FAIL
    assert results[0].proof.conclusion is expected, results[0].proof


def _refuse_name(node: astroid.Name) -> object:
    raise UnsupportedError("not a Row schema", node.name)


@pytest.mark.parametrize(
    "source", ["{[]}", "{[]: 1}", "[*[1]]", "{**{}}", "1 + 2", "call()", "1j", "..."]
)
def test_computed_or_invalid_literal_arguments_are_refused(source: str) -> None:
    node = astroid.extract_node(source)
    with pytest.raises(UnsupportedError, match="unsupported contract literal argument"):
        materialize_contract_argument(node, resolve_row=_refuse_name)


def test_each_contract_call_owns_fresh_nested_literal_containers() -> None:
    node = astroid.extract_node("{'items': [1, {'values': [2]}]}")
    first = materialize_contract_argument(node, resolve_row=_refuse_name).value
    second = materialize_contract_argument(node, resolve_row=_refuse_name).value
    assert isinstance(first, dict)
    first["items"][1]["values"].append(3)
    assert first == {"items": [1, {"values": [2, 3]}]}
    assert second == {"items": [1, {"values": [2]}]}


@pytest.mark.parametrize("kind", ["bool", "int", "float", "str", "list"])
def test_adapter_refuses_solver_proxies_before_entering_python_builder(
    kind: str,
) -> None:
    calls: list[object] = []

    def builder(value: object) -> ContractSpec:
        calls.append(value)
        return no_guarantees()

    ctx = Context.make_empty(get_contracts=lambda _: iter(()))
    arguments = {
        "bool": types.bool.val(False, ctx=ctx),
        "int": types.int.val(0, ctx=ctx),
        "float": types.float.val(0.0, ctx=ctx),
        "str": types.str.val("", ctx=ctx),
        "list": types.list.val([types.int.val(0, ctx=ctx)], ctx=ctx),
    }
    with pytest.raises(
        UnsupportedError, match="must be source literals or Row classes"
    ):
        _solve_with(builder)(arguments[kind], ctx=ctx)
    assert not calls
