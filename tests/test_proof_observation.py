import ast
import importlib
import sys
from pathlib import Path
from types import ModuleType

import astroid
import pytest
import z3

from clauz3._vendor.deal_solver._proxies._row import QueryResultSort
from clauz3._vendor.deal_solver._strings import string_val
from clauz3.policy import DomainPolicy
from clauz3.prover import (
    ProverConfigError,
    _concrete_contract_argument,
    _concrete_contracts,
    prove_path,
    prove_text,
)
from clauz3.row import Row
from clauz3.spec import AllSpec, ComparisonSpec, DistinctSpec

ROOT = Path(__file__).resolve().parents[1]
RETURNS = ROOT / "tests/fixtures/trusted_returns"
DB = ROOT / "examples/email-from-db"
EMAIL = ROOT / "examples/email"


@pytest.mark.parametrize(
    ("module_loaded", "attribute"),
    [(False, "missing_helper"), (True, "missing_helper"), (False, "__class__")],
)
def test_unresolvable_concrete_contract_helper_reports_configuration_error(
    monkeypatch: pytest.MonkeyPatch, module_loaded: bool, attribute: str
) -> None:
    module_name = "observation_missing_contract"
    helper_name = f"{module_name}.{attribute}"
    monkeypatch.setattr("clauz3.guarantees.REGISTERED_CONTRACTS", {helper_name})
    if module_loaded:
        monkeypatch.setitem(sys.modules, module_name, ModuleType(module_name))
    else:
        monkeypatch.delitem(sys.modules, module_name, raising=False)
    source = f"""
import clauz3
from {module_name} import {attribute}

@clauz3.guarantee({attribute}())
def main() -> None:
    pass
"""
    with pytest.raises(ProverConfigError) as error:
        _concrete_contracts(source=source, source_name="<test>", target="main")
    assert str(error.value) == f"cannot resolve concrete contract helper {helper_name}"


def test_observation_is_opt_in_and_only_for_successful_proofs() -> None:
    plain = prove_path(
        RETURNS / "cases/post_pins_str_pass.py",
        trusted_roots=[RETURNS / "rtrusted"],
        import_roots=[RETURNS],
    )
    assert plain[0].ok
    assert plain[0].observation is None

    failed = prove_path(
        RETURNS / "cases/bool_branch_fail.py",
        trusted_roots=[RETURNS / "rtrusted"],
        import_roots=[RETURNS],
        capture_observations=True,
    )
    assert not failed[0].ok
    assert failed[0].observation is None


def test_observation_preserves_returns_through_branches_and_helpers() -> None:
    source = """
import clauz3
from rtrusted import contracts
from rtrusted.effects import is_allowed, lookup_bob, send_email

def lookup() -> str:
    return lookup_bob("key")

@clauz3.guarantee(contracts.only_bob())
def main() -> None:
    if is_allowed("key"):
        send_email(lookup(), "hi")
    else:
        send_email(lookup(), "bye")
"""
    result = prove_text(
        source,
        trusted_roots=[RETURNS / "rtrusted"],
        import_paths=[RETURNS],
        capture_observations=True,
    )[0]
    assert result.ok
    observation = result.observation
    assert observation is not None
    assert len(observation.effects) == 5
    assert len(observation.contracts) == 1
    assert observation.guarantees == ("contracts.only_bob()",)
    allowed, first_lookup, first_send, second_lookup, second_send = observation.effects
    assert allowed.qualified_name == "rtrusted.effects.is_allowed"
    assert first_lookup.qualified_name == "rtrusted.effects.lookup_bob"
    assert second_lookup.qualified_name == first_lookup.qualified_name
    assert first_send.result is second_send.result is None
    assert first_lookup.result is not None
    assert second_lookup.result is not None
    assert allowed.result is not None
    for lookup, expected_branch in (
        (first_lookup, True),
        (second_lookup, False),
    ):
        solver = z3.Solver()
        solver.add(observation.assumptions, allowed.result.expr == expected_branch)
        assert solver.check() == z3.sat
        assert z3.is_true(solver.model().eval(lookup.cond.expr))
        assert lookup.result is not None
        solver.add(lookup.result.expr != string_val("bob@example.com"))
        assert solver.check() == z3.unsat


def test_observation_retains_query_schema_length_and_loop_binders() -> None:
    result = prove_path(
        DB / "cases/newsletter_pass.py",
        trusted_roots=[DB / "tools/db/trusted"],
        import_roots=[DB],
        capture_observations=True,
    )[0]
    assert result.ok
    observation = result.observation
    assert observation is not None
    query, email = observation.effects
    assert isinstance(query.result, QueryResultSort)
    assert query.result.row_schema.__name__ == "UserRow"
    assert email.quantifiers[0].source is query.result
    assert len(observation.contracts) == 2
    count = observation.contracts[1]
    assert isinstance(count, ComparisonSpec)
    assert count.right == 100
    assert type(count.right) is int

    solver = z3.Solver()
    solver.add(observation.assumptions, query.result.length_expr == 2)
    assert solver.check() == z3.sat
    solver.add(query.result.length_expr > 100)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize("literal", ["é", "😀", "literal\\u{e9}"])
def test_native_contract_arguments_preserve_unicode_and_literal_escapes(
    literal: str,
) -> None:
    # No effects makes the guarantee provable even if its symbolic argument
    # conversion corrupts the literal; the independent oracle must retain it.
    source = f"""
import clauz3
from tools.db.trusted import contracts

@clauz3.guarantee(contracts.only_table({literal!r}))
def main() -> None:
    pass
"""
    result = prove_text(
        source,
        trusted_roots=[DB / "tools/db/trusted"],
        import_paths=[DB],
        capture_observations=True,
    )[0]
    assert result.ok
    assert result.observation is not None
    (contract,) = result.observation.contracts
    assert isinstance(contract, AllSpec)
    assert contract.predicate.env["table"] == literal


def test_observation_includes_required_policy_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies",
        lambda **_: [
            DomainPolicy(
                when_used=("send_email",),
                required=("unique_recipients",),
                domain_package="tools.email.trusted",
            )
        ],
    )
    result = prove_text(
        """
from tools.email.trusted.effects import send_email

def main() -> None:
    send_email("bob@example.com", "hi")
""",
        trusted_roots=[EMAIL / "tools/email/trusted"],
        import_paths=[EMAIL],
        capture_observations=True,
    )[0]
    assert result.ok
    assert result.observation is not None
    (contract,) = result.observation.contracts
    assert isinstance(contract, DistinctSpec)
    assert len(result.observation.guarantees) == 1


@pytest.mark.parametrize("alias", ["UserRow", "Selected"])
def test_native_row_argument_uses_entry_import_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias: str
) -> None:
    package = tmp_path / "observation_rows"
    package.mkdir()
    (package / "__init__.py").write_text("")
    for name, field_type in (("first", "int"), ("second", "str")):
        (package / f"{name}.py").write_text(
            f"import clauz3\nclass UserRow(clauz3.Row):\n    value: {field_type}\n"
        )
    monkeypatch.syspath_prepend(str(tmp_path))
    first = importlib.import_module("observation_rows.first")
    second = importlib.import_module("observation_rows.second")
    assert first.UserRow is not second.UserRow
    module = astroid.parse(f"from observation_rows.second import UserRow as {alias}\n")
    node = ast.parse(alias, mode="eval").body
    assert _concrete_contract_argument(node, module) is second.UserRow


def test_native_row_argument_can_resolve_inline_schema() -> None:
    module = astroid.parse("""
import clauz3
class InlineRow(clauz3.Row):
    name: str
    count: int
""")
    node = ast.parse("InlineRow", mode="eval").body
    schema = _concrete_contract_argument(node, module)
    assert isinstance(schema, type) and issubclass(schema, Row)
    assert schema.__annotations__ == {"name": str, "count": int}
    assert _concrete_contract_argument(node, module) is schema
