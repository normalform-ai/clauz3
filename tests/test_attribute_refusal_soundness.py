"""Attribute inference must not replace a refused call with a concrete value."""

from pathlib import Path
from textwrap import indent
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source


def _program(
    tmp_path: Path,
    layer: str,
    body: str,
    *,
    imports: str = "",
    callables: str = "send_email",
) -> tuple[str, Path]:
    package_name = f"attribute_refusal_{uuid4().hex}"
    package = tmp_path / package_name
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n\n"
        "@clauz3.trusted\n"
        "def send_email(address: str) -> None:\n"
        "    pass\n\n"
        f"{layer}\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n\n"
        "@contract\n"
        "def no_email() -> ContractSpec:\n"
        '    return effect("send_email").empty()\n'
    )
    source = (
        "import clauz3\n"
        f"from {package_name} import effects\n"
        f"from {package_name}.effects import {callables}\n"
        f"from {package_name}.contracts import no_email\n"
        f"{imports}\n"
        "@clauz3.guarantee(no_email())\n"
        "def main() -> None:\n"
        f"{indent(body, '    ')}\n"
    )
    return source, package


def _helper_layer(*, effect_first: bool) -> str:
    statements = ['send_email("eve@evil.com")', "n: int = 1"]
    if not effect_first:
        statements.reverse()
    body = "\n".join([*statements, "return Receipt()"])
    return (
        "class Receipt:\n"
        "    ok = True\n\n"
        "def helper() -> Receipt:\n"
        f"{indent(body, '    ')}\n"
    )


def _probe_layer(*, annotated: bool) -> str:
    annotation = " -> Receipt" if annotated else ""
    # Astroid sees the class default but cannot see this instance mutation.
    # Inferring the trusted body's result would therefore turn False into True.
    return (
        "class Receipt:\n"
        "    ok = True\n\n"
        "    def __init__(self, ok: bool) -> None:\n"
        '        object.__setattr__(self, "ok", ok)\n\n'
        "@clauz3.trusted\n"
        f"def probe(zone: str){annotation}:\n"
        "    return Receipt(False)\n"
    )


_PROBE_BODY = 'if probe("front").ok:\n    pass\nelse:\n    send_email("eve@evil.com")'


@pytest.mark.parametrize("effect_first", [True, False], ids=["after", "before"])
@pytest.mark.parametrize("attribute", [True, False], ids=["attribute", "bare"])
def test_helper_body_refusal_cannot_discard_effects(
    tmp_path: Path, effect_first: bool, attribute: bool
) -> None:
    body = "if helper().ok:\n    pass" if attribute else "helper()"
    source, trusted_root = _program(
        tmp_path, _helper_layer(effect_first=effect_first), body, callables="helper"
    )
    results = prove_text(
        source,
        trusted_roots=[trusted_root],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "AnnAssign" in str(results[0].proof)


@pytest.mark.parametrize("annotated", [True, False], ids=["unsupported", "missing"])
def test_trusted_return_refusal_cannot_prune_live_effect(
    tmp_path: Path, annotated: bool
) -> None:
    source, trusted_root = _program(
        tmp_path,
        _probe_layer(annotated=annotated),
        _PROBE_BODY,
        callables="probe, send_email",
    )
    results = prove_text(
        source,
        trusted_roots=[trusted_root],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "return annotation for trusted function" in str(results[0].proof)


@pytest.mark.parametrize("scenario", ["helper", "annotated-probe", "unannotated-probe"])
def test_attribute_refusal_stops_before_approval_and_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    if scenario == "helper":
        layer = _helper_layer(effect_first=True)
        body = "if helper().ok:\n    pass"
    else:
        layer = _probe_layer(annotated=scenario == "annotated-probe")
        body = _PROBE_BODY
    source, trusted_root = _program(
        tmp_path,
        layer,
        body,
        callables="helper" if scenario == "helper" else "probe, send_email",
    )
    approve = Mock(side_effect=AssertionError("refused call reached approval"))
    execute = Mock(side_effect=AssertionError("refused call reached execution"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", execute)

    with pytest.raises(RunProofError) as exc_info:
        run_source(
            source,
            source_name="plan.py",
            trusted_roots=[trusted_root],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )

    assert [result.proof.conclusion for result in exc_info.value.proofs] == [
        Conclusion.SKIP
    ]
    approve.assert_not_called()
    execute.assert_not_called()


@pytest.mark.parametrize(
    ("imports", "body", "expected"),
    [
        pytest.param(
            "import math", "assert math.pi > 3", Conclusion.SKIP, id="module-constant"
        ),
        pytest.param(
            "import math as numbers",
            "assert numbers.sin(0) == 0",
            Conclusion.OK,
            id="module-alias",
        ),
        pytest.param(
            "import os as filesystem",
            'assert filesystem.path.join("a", "b") == "a/b"',
            Conclusion.SKIP,
            id="nested-module",
        ),
        pytest.param(
            "from os import path as paths",
            'assert paths.join("a", "b") == "a/b"',
            Conclusion.SKIP,
            id="from-module-alias",
        ),
        pytest.param(
            "",
            "assert effects.ALLOWED[0] == 'bob'",
            Conclusion.SKIP,
            id="trusted-tuple",
        ),
    ],
)
def test_static_module_calls_remain_supported_but_data_reads_refuse(
    tmp_path: Path, imports: str, body: str, expected: Conclusion
) -> None:
    source, trusted_root = _program(
        tmp_path, 'ALLOWED = ("bob",)', body, imports=imports
    )
    results = prove_text(
        source,
        trusted_roots=[trusted_root],
        import_paths=[tmp_path],
        source_name="plan.py",
        extra_allowed_imports=["os"],
    )
    assert [result.proof.conclusion for result in results] == [expected]
    if expected == Conclusion.SKIP:
        diagnostic = (
            "no unambiguous definition"
            if "join(" in body
            else "module data bindings are not modeled"
        )
        assert diagnostic in str(results[0].proof)


@pytest.mark.parametrize(
    ("imports", "body"),
    [
        ("import math", "assert math.taua > 3"),
        ("from math import taua", "assert taua.value > 3"),
    ],
    ids=["missing-attribute", "missing-from-import"],
)
def test_missing_module_attributes_refuse_without_a_traceback(
    tmp_path: Path, imports: str, body: str
) -> None:
    source, trusted_root = _program(tmp_path, "", body, imports=imports)
    results = prove_text(
        source,
        trusted_roots=[trusted_root],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "taua" in str(results[0].proof)


def test_property_returning_module_cannot_hide_effect(tmp_path: Path) -> None:
    source, trusted_root = _program(
        tmp_path,
        "import math\n\n"
        "class Provider:\n"
        "    @property\n"
        "    def module(self):\n"
        '        send_email("eve@evil.com")\n'
        "        return math\n\n"
        "provider = Provider()\n",
        "if effects.provider.module.pi > 0:\n    pass",
    )
    results = prove_text(
        source,
        trusted_roots=[trusted_root],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]


def test_local_value_shadows_imported_module(tmp_path: Path) -> None:
    source, trusted_root = _program(
        tmp_path,
        "",
        'numbers = "bob"\nassert numbers.startswith("b")',
        imports="import math as numbers",
    )
    results = prove_text(
        source,
        trusted_roots=[trusted_root],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


def test_conflicting_inferred_module_constants_are_refused(tmp_path: Path) -> None:
    # Astroid considers both posixpath.sep and ntpath.sep. Picking the first
    # would turn an unresolved platform choice into a concrete proof value.
    source, trusted_root = _program(
        tmp_path,
        "",
        "separator = filesystem.path.sep",
        imports="import os as filesystem",
    )
    results = prove_text(
        source,
        trusted_roots=[trusted_root],
        import_paths=[tmp_path],
        source_name="plan.py",
        extra_allowed_imports=["os"],
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "ambiguous module attribute" in str(results[0].proof)


def test_computed_module_container_cannot_capture_caller_local(tmp_path: Path) -> None:
    source, trusted_root = _program(
        tmp_path,
        'allowed = "bob"\nALLOWED = [allowed]',
        'allowed = "eve"\nassert "eve" in effects.ALLOWED',
    )
    results = prove_text(
        source,
        trusted_roots=[trusted_root],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]


def test_nested_immutable_module_literals_do_not_establish_binding_stability(
    tmp_path: Path,
) -> None:
    source, trusted_root = _program(
        tmp_path,
        "LIMITS = ((-3, +4),)",
        "assert effects.LIMITS[0][0] == -3\nassert effects.LIMITS[0][1] == 4",
    )
    results = prove_text(
        source,
        trusted_roots=[trusted_root],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "module data bindings are not modeled" in str(results[0].proof)


def test_computed_module_value_cannot_ignore_its_decorator(tmp_path: Path) -> None:
    source, trusted_root = _program(
        tmp_path,
        "def replace(func):\n"
        "    return lambda: False\n\n"
        "@replace\n"
        "def yes():\n"
        "    return True\n\n"
        "VALUE = yes()\n",
        "assert effects.VALUE",
    )
    results = prove_text(
        source,
        trusted_roots=[trusted_root],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "non-literal module attribute" in str(results[0].proof)


@pytest.mark.parametrize("direct_import", [True, False], ids=["from-import", "nested"])
def test_computed_module_reference_is_not_a_static_import(
    tmp_path: Path, direct_import: bool
) -> None:
    reference = "ns" if direct_import else "effects.ns"
    source, trusted_root = _program(
        tmp_path,
        "import math\n\ndef factory():\n    return math\n\nns = factory()\n",
        f"assert {reference}.pi > 3",
        callables="ns" if direct_import else "send_email",
    )
    results = prove_text(
        source,
        trusted_roots=[trusted_root],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]


@pytest.mark.parametrize("spelling", ["import", "from-import", "reexport"])
def test_duplicate_module_aliases_cannot_select_first_binding(
    tmp_path: Path, spelling: str
) -> None:
    source, trusted_root = _program(tmp_path, "", "assert constants.VALUE")
    (trusted_root / "good.py").write_text("VALUE = True\n")
    (trusted_root / "bad.py").write_text("VALUE = False\n")
    package_name = trusted_root.name
    if spelling == "import":
        imported = (
            f"import {package_name}.good as constants, "
            f"{package_name}.bad as constants\n"
        )
    elif spelling == "from-import":
        imported = f"from {package_name} import good as constants, bad as constants\n"
    else:
        (trusted_root / "__init__.py").write_text(
            f"import {package_name}.good as shared, {package_name}.bad as shared\n"
        )
        imported = f"from {package_name} import shared as constants\n"
    # Python binds the last alias (False); astroid may infer the first (True).
    # Neither a direct import nor an intervening re-export may justify a proof.
    results = prove_text(
        imported + source,
        trusted_roots=[trusted_root],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
