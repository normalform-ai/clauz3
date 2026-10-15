"""Closure refusals cover indirect calls and statically resolved module paths."""

from pathlib import Path
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import ProgramError, execute
from clauz3.prover import prove_text


def _layer(tmp_path: Path) -> tuple[str, Path]:
    package = tmp_path / f"closure_edges_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
    )
    (package / "quiet.py").write_text("def record(value: str) -> None:\n    pass\n")
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n"
        '    return effect("record").empty()\n'
    )
    header = (
        "import clauz3\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import silent\n"
        "def quiet(value: str) -> None:\n    pass\n"
        "def invoke(fn):\n    fn()\n"
    )
    return header, package


@pytest.mark.parametrize(
    "body",
    [
        "    action = record\n"
        "    def helper() -> None:\n"
        '        action("one")\n'
        "    run = helper\n"
        "    run()\n"
        "    action = quiet\n",
        "    action = record\n"
        "    def helper() -> None:\n"
        '        action("one")\n'
        "    invoke(helper)\n"
        "    action = quiet\n",
        "    from {package}.effects import record as action\n"
        "    def helper() -> None:\n"
        '        action("one")\n'
        "    helper()\n"
        "    from {package}.quiet import record as action\n",
        "    def action(value: str) -> None:\n"
        "        record(value)\n"
        "    def helper() -> None:\n"
        '        action("one")\n'
        "    helper()\n"
        "    def action(value: str) -> None:\n"
        "        pass\n",
        "    def make():\n"
        "        action = record\n"
        "        def helper() -> None:\n"
        '            action("one")\n'
        "        return helper\n"
        "        action = quiet\n"
        "    make()()\n",
    ],
    ids=[
        "helper-alias",
        "callback",
        "callable-reimport",
        "duplicate-function",
        "returned-closure",
    ],
)
def test_indirect_closure_calls_cannot_use_a_later_enclosing_binding(
    tmp_path: Path, body: str
) -> None:
    header, package = _layer(tmp_path)
    source = (
        header
        + "@clauz3.guarantee(silent())\ndef main() -> None:\n"
        + body.format(package=package.name)
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "closure" in str(results[0].proof)
    assert "action" in str(results[0].proof)


@pytest.mark.parametrize(
    "body",
    [
        "    def helper() -> None:\n"
        '        action("one")\n'
        "    from {package}.effects import record as action\n"
        "    helper()\n",
        "    def helper() -> None:\n"
        '        action("one")\n'
        "    helper()\n"
        "    from {package}.quiet import record as action\n",
        "    if True:\n"
        "        from {package}.effects import record as action\n"
        "    def helper() -> None:\n"
        '        action("one")\n'
        "    helper()\n",
        "    def make(action):\n"
        "        def helper() -> None:\n"
        '            action("one")\n'
        "        return helper\n"
        "    make(record)()\n",
    ],
    ids=["late-before-call", "late-after-call", "conditional-import", "parameter"],
)
def test_unmodeled_capture_initialization_is_explicitly_refused(
    tmp_path: Path, body: str
) -> None:
    header, package = _layer(tmp_path)
    source = (
        header
        + "@clauz3.guarantee(silent())\ndef main() -> None:\n"
        + body.format(package=package.name)
    )
    # Some initializations are safe in this particular source order. A static
    # inference fallback cannot generally establish the closure's cell value.
    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "closure" in str(results[0].proof)
    assert "action" in str(results[0].proof)


def _module_source(tmp_path: Path, *, rebound: bool) -> tuple[str, Path]:
    package = tmp_path / f"closure_modules_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "first.py").write_text("VALUE = 1\n")
    (package / "second.py").write_text("VALUE = 2\n")
    source = (
        "def main() -> None:\n"
        f"    import {package.name}.first as ops\n"
        "    def helper() -> int:\n"
        "        return ops.VALUE\n"
        f"    assert helper() == {2 if rebound else 1}\n"
    )
    if rebound:
        source += f"    import {package.name}.second as ops\n"
    return source, package


def test_module_attribute_cannot_use_a_later_enclosing_import(tmp_path: Path) -> None:
    source, package = _module_source(tmp_path, rebound=True)

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "closure" in str(results[0].proof)
    assert "ops" in str(results[0].proof)


def test_stable_enclosing_module_import_remains_provable() -> None:
    # Keep testing the stable captured import through a registered callable;
    # module data reads cannot establish that their bindings stay unchanged.
    source = (
        "def main() -> None:\n"
        "    import math as ops\n"
        "    def helper() -> float:\n"
        "        return ops.sin(0)\n"
        "    assert helper() == 0\n"
    )

    results = prove_text(source)

    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


def test_runtime_module_capture_still_refers_to_the_earlier_import(
    tmp_path: Path,
) -> None:
    source, package = _module_source(tmp_path, rebound=True)
    # The first module's VALUE is 1. Static inference previously used the later
    # module's 2 and proved the assertion that actual execution refutes.
    with pytest.raises(ProgramError, match="AssertionError"):
        execute(
            source,
            source_name="module_capture.py",
            policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
            import_roots=[tmp_path],
        )
