"""Public custom models and contract helpers share explicit call binding."""

import inspect
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import pytest

import clauz3
from clauz3._vendor.deal_solver import Conclusion
from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._funcs import FUNCTIONS
from clauz3._vendor.deal_solver._proxies import ProxySort, types
from clauz3.prover import AgentTheorem, ProofResult, prove_text

_BINDING_ERROR = "unsupported registered call argument binding"


def _public_shape(value: int, /, *, increment: int = 3) -> int:
    return value + increment


def _register_model(
    monkeypatch: pytest.MonkeyPatch,
    impl: Callable[..., ProxySort],
    signature: Callable[..., object] | inspect.Signature | None,
) -> str:
    name = f"custom_model_{uuid4().hex}"
    # A module parsed from text has an empty qualified module name. Register
    # the model for its actual function binding, and restore the registry at
    # teardown even though the public decorator performs the final assignment.
    qualified_name = f".{name}"
    monkeypatch.setitem(FUNCTIONS, qualified_name, None)
    registered_impl = clauz3.solver(qualified_name, signature=signature)(impl)
    assert registered_impl is impl
    return name


def _prove_call(name: str, arguments: str, expected_value: int = 9) -> ProofResult:
    # Exercise binding at the model boundary. Public prove_text deliberately
    # discards ambient registrations when it selects a trusted layer; the
    # layer-switch regression below covers that public path separately.
    source = (
        f"def {name}(*args, **kwargs):\n"
        "    raise AssertionError('the custom model was not used')\n\n"
        "def main() -> None:\n"
        f"    assert {name}({arguments}) == {expected_value}\n"
    )
    theorem = next(t for t in AgentTheorem.from_text(source) if t.name == "main")
    return ProofResult(name=theorem.name, proof=theorem.prove())


@pytest.mark.parametrize("metadata", ["callable", "signature"])
@pytest.mark.parametrize(
    ("arguments", "expected_value", "expected", "omitted"),
    [
        ("4, increment=5", 9, Conclusion.OK, False),
        ("4", 7, Conclusion.OK, True),
        ("4, increment=5", 4, Conclusion.FAIL, False),
    ],
    ids=["keyword", "omitted-default", "false-assertion"],
)
def test_custom_solver_metadata_preserves_argument_meaning(
    monkeypatch: pytest.MonkeyPatch,
    metadata: str,
    arguments: str,
    expected_value: int,
    expected: Conclusion,
    omitted: bool,
) -> None:
    seen: list[tuple[ProxySort, ProxySort | None]] = []

    def model(
        value: ProxySort, *, increment: ProxySort | None = None, ctx: Context
    ) -> ProxySort:
        seen.append((value, increment))
        extra = types.int.val(3, ctx=ctx) if increment is None else increment
        return value.m_add(extra, ctx=ctx)

    signature = (
        _public_shape if metadata == "callable" else inspect.signature(_public_shape)
    )
    name = _register_model(monkeypatch, model, signature)

    result = _prove_call(name, arguments, expected_value)

    assert result.proof.conclusion is expected
    assert len(seen) == 1
    value, increment = seen[0]
    assert isinstance(value, ProxySort)
    if omitted:
        # Signature defaults validate omission; they must not inject raw Python
        # integers into the symbolic model's argument list.
        assert increment is None
    else:
        assert isinstance(increment, ProxySort)


@pytest.mark.parametrize(
    "arguments",
    ["", "4, 5", "value=4", "4, unexpected=5"],
    ids=["missing", "extra-positional", "posonly-keyword", "unknown-keyword"],
)
def test_custom_solver_refuses_malformed_calls_before_model_execution(
    monkeypatch: pytest.MonkeyPatch, arguments: str
) -> None:
    called: list[bool] = []

    def model(*args: ProxySort, ctx: Context, **kwargs: ProxySort) -> ProxySort:
        called.append(True)
        return types.int.val(9, ctx=ctx)

    name = _register_model(monkeypatch, model, _public_shape)

    result = _prove_call(name, arguments)

    assert result.proof.conclusion is Conclusion.SKIP
    assert _BINDING_ERROR in str(result.proof.error)
    assert called == []


def test_custom_solver_without_signature_metadata_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[bool] = []

    def model(value: ProxySort, *, ctx: Context) -> ProxySort:
        called.append(True)
        return value

    name = _register_model(monkeypatch, model, None)

    result = _prove_call(name, "9")

    assert result.proof.conclusion is Conclusion.SKIP
    assert f"{_BINDING_ERROR}: missing signature metadata" in str(result.proof.error)
    assert called == []


def test_custom_signature_incompatible_with_implementation_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[bool] = []

    def model(*, ctx: Context) -> ProxySort:
        called.append(True)
        return types.int.val(9, ctx=ctx)

    name = _register_model(monkeypatch, model, _public_shape)

    result = _prove_call(name, "4, increment=5")

    assert result.proof.conclusion is Conclusion.SKIP
    assert _BINDING_ERROR in str(result.proof.error)
    assert called == []


@pytest.fixture
def contract_layer(tmp_path: Path) -> Path:
    package = tmp_path / f"solver_signatures_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    raise AssertionError('proof must not execute the trusted effect')\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def only(value: str = 'default', *, suffix: str = '') -> ContractSpec:\n"
        "    expected = value + suffix\n"
        "    return effect('record').all(lambda event: event.value == expected)\n"
        "@contract\n"
        "def positional(value: str = 'default', /, **kwargs: str) -> ContractSpec:\n"
        "    expected = kwargs.get('value', value)\n"
        "    return effect('record').all(lambda event: event.value == expected)\n"
    )
    return package


def _prove_contract(package: Path, clause: str, value: str) -> ProofResult:
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import only, positional\n"
        f"@clauz3.guarantee({clause})\n"
        "def main() -> None:\n"
        f"    record({value!r})\n"
    )
    results = prove_text(source, trusted_roots=[package], import_paths=[package.parent])
    assert len(results) == 1
    return results[0]


@pytest.mark.parametrize(
    ("clause", "value", "expected"),
    [
        ("only(value='named')", "named", Conclusion.OK),
        ("only(value='named')", "wrong", Conclusion.FAIL),
        ("only()", "default", Conclusion.OK),
        ("only(suffix='!')", "default!", Conclusion.OK),
        ("only(suffix='!')", "default", Conclusion.FAIL),
        ("positional(value='keyword')", "keyword", Conclusion.OK),
        ("positional(value='keyword')", "default", Conclusion.FAIL),
        ("positional('position', value='keyword')", "keyword", Conclusion.OK),
        ("positional()", "default", Conclusion.OK),
    ],
)
def test_contract_keywords_defaults_and_posonly_keyword_capture(
    contract_layer: Path, clause: str, value: str, expected: Conclusion
) -> None:
    result = _prove_contract(contract_layer, clause, value)

    assert result.proof.conclusion is expected


@pytest.mark.parametrize("clause", ["only('value', False)", "only(unknown='value')"])
def test_contract_malformed_call_shape_is_a_clean_refusal(
    contract_layer: Path, clause: str
) -> None:
    result = _prove_contract(contract_layer, clause, "value")

    assert result.proof.conclusion is Conclusion.SKIP
    assert _BINDING_ERROR in str(result.proof.error)


@pytest.mark.parametrize("metadata", ["callable", "signature"])
def test_selected_layers_restore_custom_model_signature(
    tmp_path: Path, metadata: str
) -> None:
    name = f"registered_layer_{uuid4().hex}"
    roots = []
    for label, increment in [("a", 1), ("b", 2)]:
        root = tmp_path / label / name
        root.mkdir(parents=True)
        (root / "__init__.py").write_text("")
        signature = "api" if metadata == "callable" else "inspect.signature(api)"
        (root / "effects.py").write_text(
            "import inspect\nimport clauz3\n"
            "from clauz3._vendor.deal_solver._proxies import types\n"
            "def api(value, /, *, extra=0):\n"
            "    raise AssertionError('use the registered model')\n"
            f"@clauz3.solver(__name__ + '.api', signature={signature})\n"
            "def model(value, *, extra=None, ctx):\n"
            "    if extra is None:\n        extra = types.int.val(0, ctx=ctx)\n"
            f"    offset = types.int.val({increment}, ctx=ctx)\n"
            "    return value.m_add(extra, ctx=ctx).m_add(offset, ctx=ctx)\n"
        )
        roots.append(root)
    for root, increment in [(roots[0], 1), (roots[1], 2), (roots[0], 1)]:
        for call, expected, diagnostic in [
            ("api(4, extra=3)", Conclusion.OK, ""),
            ("api(value=4, extra=3)", Conclusion.SKIP, _BINDING_ERROR),
        ]:
            source = (
                f"from {name}.effects import api\n"
                "def main() -> None:\n"
                f"    assert {call} == {7 + increment}\n"
            )
            results = prove_text(
                source, trusted_roots=[root], import_paths=[root.parent]
            )
            assert [result.proof.conclusion for result in results] == [expected]
            if diagnostic:
                assert diagnostic in str(results[0].proof)
