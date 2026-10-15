"""Python builder arguments remain encodable inside relation lambdas."""

from pathlib import Path
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_text


@pytest.fixture
def capture_layer(tmp_path: Path) -> Path:
    package = tmp_path / f"literal_capture_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n"
        "@clauz3.trusted\n"
        "def record(value: float) -> None:\n"
        "    raise AssertionError('proof must not execute the trusted effect')\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def member(allowed) -> ContractSpec:\n"
        "    return effect('record').all(lambda event: event.value in allowed)\n"
        "@contract\n"
        "def equal(expected: float) -> ContractSpec:\n"
        "    return effect('record').all(lambda event: event.value == expected)\n"
    )
    return package


@pytest.mark.parametrize(
    ("clause", "value", "expected"),
    [
        ("equal(1.5)", "1.5", Conclusion.OK),
        ("equal(1.5)", "2.5", Conclusion.FAIL),
        ("member((1, 2))", "1", Conclusion.OK),
        ("member((1, 2))", "3", Conclusion.FAIL),
        ("member({1, 2})", "1", Conclusion.OK),
        ("member({1, 2})", "3", Conclusion.FAIL),
        ("member({1: 'one', 2: 'two'})", "1", Conclusion.OK),
        ("member({1: 'one', 2: 'two'})", "3", Conclusion.FAIL),
    ],
    ids=[
        "float-match",
        "float-mismatch",
        "tuple-member",
        "tuple-nonmember",
        "set-member",
        "set-nonmember",
        "dict-key",
        "dict-nonkey",
    ],
)
def test_literal_capture_preserves_relation_constraint(
    capture_layer: Path, clause: str, value: str, expected: Conclusion
) -> None:
    source = (
        "import clauz3\n"
        f"from {capture_layer.name}.effects import record\n"
        f"from {capture_layer.name}.contracts import member, equal\n"
        f"@clauz3.guarantee({clause})\n"
        "def main() -> None:\n"
        f"    record({value})\n"
    )

    results = prove_text(
        source,
        trusted_roots=[capture_layer],
        import_paths=[capture_layer.parent],
    )

    assert [result.proof.conclusion for result in results] == [expected]
