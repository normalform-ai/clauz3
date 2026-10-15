"""Native effects must not be hidden by independent snapshots of shared data."""

import importlib
from pathlib import Path
from textwrap import indent
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source

DIAGNOSTIC = "shared or borrowed collection mutation is not supported"
IDENTITY = "def identity(items: list[int]) -> list[int]:\n    return items\n"

# Each true runtime condition produces two records, violating the native budget.
# Keep the oracle separate from the proof/refusal test so the archived baseline
# still executes every runtime witness after its proof assertions fail.
SHARED_WRITES = {
    "list-alias": (
        "values = [0]\nalias = values\nalias.append(1)\n",
        "len(values) == 2",
    ),
    "original-after-alias": (
        "values = [0]\nalias = values\nvalues.append(1)\n",
        "len(alias) == 2",
    ),
    "dict-alias": (
        "values = {'size': 1}\nalias = values\nalias['size'] = 2\n",
        "values['size'] == 2",
    ),
    "set-alias": (
        "values = {'a'}\nalias = values\nalias.add('b')\n",
        "'b' in values",
    ),
    "list-retention": (
        "values = [0]\nouter = [values]\nvalues.append(1)\n",
        "len(outer[0]) == 2",
    ),
    "dict-retention": (
        "values = [0]\nouter = {'items': values}\nvalues.append(1)\n",
        "len(outer['items']) == 2",
    ),
    "tuple-retention": (
        "values = [0]\nouter = (values,)\nvalues.append(1)\n",
        "len(outer[0]) == 2",
    ),
    "for-iteration-alias": (
        "values = [0]\nfor item in [values]:\n    item.append(1)\n",
        "len(values) == 2",
    ),
    "extracted-inner": (
        "outer = [[0]]\ninner = outer[0]\ninner.append(1)\n",
        "len(outer[0]) == 2",
    ),
    "ordinary-helper-return": (
        "values = [0]\nalias = identity(values)\nalias.append(1)\n",
        "len(values) == 2",
    ),
    "shallow-list-copy": (
        "outer = [[0]]\ncopy = outer.copy()\ninner = copy[0]\ninner.append(1)\n",
        "len(outer[0]) == 2",
    ),
    "shallow-dict-copy": (
        "outer = {'items': [0]}\ncopy = outer.copy()\n"
        "inner = copy['items']\ninner.append(1)\n",
        "len(outer['items']) == 2",
    ),
}


def _program(tmp_path: Path, body: str) -> tuple[str, Path]:
    package = tmp_path / f"collection_owner_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "MUTATED_INPUTS: list[list[int]] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
        "@clauz3.trusted\n"
        "def mutate_input(values: list[int]) -> None:\n"
        "    values.append(99)\n"
        "    MUTATED_INPUTS.append(values)\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def at_most_one() -> ContractSpec:\n"
        "    return effect('record').count() <= 1\n"
    )
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import record, mutate_input\n"
        f"from {package.name}.contracts import at_most_one\n"
        + IDENTITY
        + "@clauz3.guarantee(at_most_one())\n"
        "def main() -> None:\n" + indent(body, "    ")
    )
    return source, package


def _shared_body(case: str) -> str:
    setup, condition = SHARED_WRITES[case]
    return setup + f"if {condition}:\n    record('a')\n    record('b')\n"


def _assert_runtime(source: str, package: Path, expected: list[str]) -> None:
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[package.parent]),
        import_roots=[package.parent],
    )
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.__file__ is not None
    assert Path(effects.__file__).resolve() == (package / "effects.py").resolve()
    assert expected == effects.EVENTS
    records = tuple(item for item in outcome.effects if item[0].endswith(".record"))
    assert records == tuple(
        (f"{package.name}.effects.record", (value,), {}) for value in expected
    )


@pytest.mark.parametrize("case", SHARED_WRITES)
def test_shared_write_refuses_before_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    source, package = _program(tmp_path, _shared_body(case))
    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert [result.proof.conclusion for result in proofs] == [Conclusion.SKIP]
    assert DIAGNOSTIC in str(proofs[0].proof)
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []

    approve = Mock(side_effect=AssertionError("approval must not be reached"))
    executor = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", executor)
    with pytest.raises(RunProofError) as exc_info:
        run_source(
            source,
            source_name="plan.py",
            trusted_roots=[package],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )
    assert [result.proof.conclusion for result in exc_info.value.proofs] == [
        Conclusion.SKIP
    ]
    assert DIAGNOSTIC in str(exc_info.value.proofs[0].proof)
    approve.assert_not_called()
    executor.assert_not_called()
    assert effects.EVENTS == []


@pytest.mark.parametrize("case", SHARED_WRITES)
def test_shared_write_really_produces_two_records(tmp_path: Path, case: str) -> None:
    source, package = _program(tmp_path, _shared_body(case))
    _assert_runtime(source, package, ["a", "b"])


@pytest.mark.parametrize(
    "copy_expression",
    ["[0]", "values.copy()", "list(values)", "values[:]", "values + []", "[] + values"],
    ids=["equal-literal", "copy", "constructor", "slice", "concat", "empty-concat"],
)
def test_independent_list_roots_remain_writable(
    tmp_path: Path, copy_expression: str
) -> None:
    body = (
        "values = [0]\n"
        f"independent = {copy_expression}\n"
        "independent.append(1)\n"
        "assert len(values) == 1\n"
        "assert len(independent) == 2\n"
        "record('a')\n"
    )
    _assert_supported(tmp_path, body)


@pytest.mark.parametrize("kind", ["dict", "set"])
@pytest.mark.parametrize("operation", ["literal", "copy", "constructor"])
def test_independent_dict_and_set_roots_remain_writable(
    tmp_path: Path, kind: str, operation: str
) -> None:
    literal = "{'size': 1}" if kind == "dict" else "{'a'}"
    expression = {
        "literal": literal,
        "copy": "values.copy()",
        "constructor": f"{kind}(values)",
    }[operation]
    body = f"values = {literal}\nindependent = {expression}\n"
    if kind == "dict":
        body += (
            "independent['size'] = 2\n"
            "assert values['size'] == 1\n"
            "assert independent['size'] == 2\n"
        )
    else:
        body += (
            "independent.add('b')\n"
            "assert 'b' not in values\n"
            "assert 'b' in independent\n"
        )
    _assert_supported(tmp_path, body + "record('a')\n")


@pytest.mark.parametrize("alias", ["values", "identity(values)"])
def test_read_only_aliases_remain_supported(tmp_path: Path, alias: str) -> None:
    body = (
        "values = [0]\n"
        f"alias = {alias}\n"
        "assert len(values) == 1\n"
        "assert len(alias) == 1\n"
        "record('a')\n"
    )
    _assert_supported(tmp_path, body)


def test_trusted_input_copy_does_not_share_the_interpreter_collection(
    tmp_path: Path,
) -> None:
    body = (
        "values = [0]\n"
        "mutate_input(values)\n"
        "assert values == [0]\n"
        "values.append(1)\n"
        "assert values == [0, 1]\n"
        "record('a')\n"
    )
    package = _assert_supported(tmp_path, body)
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.MUTATED_INPUTS == [[0, 99]]


def _assert_supported(tmp_path: Path, body: str) -> Path:
    source, package = _program(tmp_path, body)
    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert [result.proof.conclusion for result in proofs] == [Conclusion.OK]
    _assert_runtime(source, package, ["a"])
    return package
