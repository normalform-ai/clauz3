"""Comprehension mutation guards survive nested evaluation without leaking."""

from textwrap import dedent

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_text


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            """
            def main():
                values = [0]
                result = [values.append.__call__(1) for _ in [0]]
                assert len(values) == 1
            """,
            id="wrapped-mutating-method",
        ),
        pytest.param(
            """
            def main():
                values = [0]
                result = [
                    [item for item in [values.append(1)]]
                    for _ in [0]
                ]
                assert len(values) == 1
            """,
            id="nested-iterable-inherits-outer-guard",
        ),
        pytest.param(
            """
            def append_one(items):
                if True:
                    items.append(1)
                return 0

            def main():
                values = [0]
                result = [append_one(values) for _ in [0]]
                assert len(values) == 1
            """,
            id="helper-child-context",
        ),
        pytest.param(
            """
            def invoke(callback):
                callback(1)
                return 0

            def main():
                values = [0]
                append = values.append
                result = [invoke(append) for _ in [0]]
                assert len(values) == 1
            """,
            id="bound-method-passed-to-helper",
        ),
    ],
)
def test_nested_mutation_dispatch_is_refused_inside_comprehensions(
    source: str,
) -> None:
    proofs = prove_text(dedent(source), target="main")

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    if "__call__" in source or "append = values.append" in source:
        diagnostic = "mutating method requires a direct call on a named receiver"
    elif "def append_one" in source:
        diagnostic = "mutating a function parameter or its alias is not supported"
    else:
        diagnostic = "mutating a captured collection is not supported"
    assert diagnostic in str(proofs[0].proof)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            """
            def main():
                result = [value + 1 for value in [0, 1]]
                assert result == [1, 2]
            """,
            id="arithmetic-element",
        ),
        pytest.param(
            """
            def main():
                result = [value + 1 for value in [0, 1, 2] if value > 0]
                assert result == [2, 3]
            """,
            id="pure-filter",
        ),
        pytest.param(
            """
            def main():
                values = [0]
                result = [len(values) for _ in [0]]
                assert result == [1]
                assert values == [0]
            """,
            id="read-only-capture",
        ),
        pytest.param(
            """
            def main():
                values = []
                result = [len(values + [1]) for _ in [0]]
                assert result == [1]
                assert len(values) == 0
            """,
            id="empty-list-concatenation-is-pure",
        ),
        pytest.param(
            """
            def main():
                result = [len([value + 1 for value in [0]]) for _ in [0]]
                assert result == [1]
            """,
            id="nested-pure-comprehension",
        ),
    ],
)
def test_pure_comprehensions_preserve_exact_values(source: str) -> None:
    proofs = prove_text(dedent(source), target="main")

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "    result = [item for item in [values.pop('key')]]\n",
            id="outer-iterable-is-evaluated-in-caller",
        ),
        pytest.param(
            "    result = [len(values) for _ in [0]]\n    values.append(1)\n",
            id="guard-ends-after-comprehension",
        ),
        pytest.param(
            "    result = [len([item for item in [0]]) for _ in [0]]\n"
            "    values.append(1)\n",
            id="nested-guards-end-after-comprehensions",
        ),
    ],
)
def test_mutation_outside_discarded_contexts_retains_its_value(body: str) -> None:
    expected = "{}" if "values.pop('key')" in body else "[0, 1]"
    source = (
        "def main():\n    values = "
        + ("{'key': 1}" if "values.pop(" in body else "[0]")
        + "\n"
        + body
        + f"    assert values == {expected}\n"
    )

    proofs = prove_text(source, target="main")

    conclusion = Conclusion.SKIP if "values.pop(" in body else Conclusion.OK
    assert [proof.proof.conclusion for proof in proofs] == [conclusion]
    if conclusion is Conclusion.SKIP:
        assert "mutating a captured collection" in str(proofs[0].proof)
