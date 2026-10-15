"""Nested mutation guards remain effective without an earlier ownership refusal."""

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_text


@pytest.mark.parametrize(
    ("expression", "context"),
    [
        ("(lambda: build())()", "lambda"),
        ("[build() for _ in [0]]", "comprehension"),
    ],
)
def test_nested_helper_cannot_discard_a_fresh_local_mutation(
    expression: str, context: str
) -> None:
    # The receiver is fresh, local, and unaliased. Only the enclosing
    # evaluator's mutation guard should refuse this supported direct call.
    source = (
        "def build() -> int:\n"
        "    values = [0]\n"
        "    values.append(1)\n"
        "    return len(values)\n"
        "def main() -> None:\n"
        f"    result = {expression}\n"
        "    assert True\n"
    )
    proofs = prove_text(source, target="main")
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert f"mutating methods inside a {context}" in str(proofs[0].proof)
