"""A ``required`` name must resolve to exactly one contract the prover can state.

``required`` is a trusted layer's own obligation, named by a bare contract
name. Whatever that name resolves to is conjoined into every proof that
triggers the policy, so a name that resolves to the wrong helper, or to one
the prover cannot call, is a fault in the layer and is reported as one.
"""

from pathlib import Path
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import ProverConfigError, prove_text

_STRICT = (
    "from clauz3.spec import ContractSpec, contract, effect\n\n"
    "@contract\n"
    "def nothing_sent() -> ContractSpec:\n"
    '    return effect("send").empty()\n'
)
_SENDS = 'def main() -> None:\n    send("eve@example.com", "hi")\n'


def _layer(tmp_path: Path, *, contracts: str = _STRICT, required: str) -> Path:
    package = tmp_path / f"required_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n\n@clauz3.trusted\ndef send(addr: str, msg: str) -> None: ...\n"
    )
    (package / "contracts.py").write_text(contracts)
    (package / "policy.py").write_text(
        "from clauz3.policy import domain_policy\n\n"
        f'POLICY = domain_policy(when_used="send", required=["{required}"])\n'
    )
    return package


def _prove(package: Path, body: str = _SENDS) -> list[Conclusion]:
    results = prove_text(
        f"from {package.name}.effects import send\n\n{body}",
        source_name="plan.py",
        trusted_roots=[package],
        import_paths=[package.parent],
    )
    return [result.proof.conclusion for result in results]


def test_a_single_required_contract_is_conjoined(tmp_path: Path) -> None:
    package = _layer(tmp_path, required="nothing_sent")

    assert _prove(package) == [Conclusion.FAIL]


def test_two_contracts_with_the_required_name_are_refused(tmp_path: Path) -> None:
    """Not resolved to whichever module sorts first.

    The weaker helper here is the alphabetically first one, so picking it
    proved a program the stricter contract forbids.
    """
    package = _layer(tmp_path, required="nothing_sent")
    (package / "aaa_legacy.py").write_text(
        "from clauz3.spec import ContractSpec, contract, no_guarantees\n\n"
        "@contract\n"
        "def nothing_sent() -> ContractSpec:\n"
        "    return no_guarantees()\n"
    )

    with pytest.raises(ProverConfigError) as exc_info:
        _prove(package)

    message = str(exc_info.value)
    assert "required contract 'nothing_sent'" in message
    assert f"{package.name}.aaa_legacy.nothing_sent" in message
    assert f"{package.name}.contracts.nothing_sent" in message


_WITH_PARAMETERS = (
    "from clauz3.spec import ContractSpec, contract, effect\n\n"
    "@contract\n"
    "def at_most(count: int) -> ContractSpec:\n"
    '    return effect("send").count() <= count\n\n'
    "@contract\n"
    "def at_most_default(count: int = 0) -> ContractSpec:\n"
    '    return effect("send").count() <= count\n\n'
    "@contract\n"
    "def keyword_only(*, count: int) -> ContractSpec:\n"
    '    return effect("send").count() <= count\n'
)


@pytest.mark.parametrize("name", ["at_most", "keyword_only"])
def test_a_required_contract_that_needs_arguments_is_refused(
    tmp_path: Path, name: str
) -> None:
    """A layer's mistake, reported as one.

    The obligation is stated as ``name()``. A helper that cannot be called
    that way used to reach the solver, which skipped the function and blamed
    the submitted program for a contract it never wrote.
    """
    package = _layer(tmp_path, contracts=_WITH_PARAMETERS, required=name)

    with pytest.raises(ProverConfigError) as exc_info:
        _prove(package)

    message = str(exc_info.value)
    assert f"required contract {name!r}" in message
    assert "without arguments" in message
    assert f"{package.name}.contracts.{name}" in message


def test_a_required_contract_whose_parameters_all_default_is_callable(
    tmp_path: Path,
) -> None:
    package = _layer(tmp_path, contracts=_WITH_PARAMETERS, required="at_most_default")

    assert _prove(package) == [Conclusion.FAIL]
    assert _prove(package, "def main() -> None:\n    pass\n") == [Conclusion.OK]
