"""Constructed rows keep their exact field values and never gain query provenance."""

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import prove_text


def test_row_constructor_fields() -> None:
    source = """import clauz3
class Record(clauz3.Row):
    text: str
    count: int
    flag: bool
def main() -> None:
    value = Record(flag=True, count=-3, text="café 😀")
    assert value.text == "café 😀"
    assert value.count == -3
    assert value.flag == True
"""
    results = prove_text(source)
    assert results[0].proof.conclusion is Conclusion.OK, results[0].proof
    execute(
        source,
        source_name="row.py",
        policy=build_policy(trusted_roots=[], import_roots=[]),
    )


@pytest.mark.parametrize(
    "constructor",
    [
        "Record()",
        'Record(value="x")',
        "Record(value=True)",
        "Record(value=1.0)",
        "Record(value=None)",
        "Record(1)",
        "Record(value=1, extra=2)",
    ],
)
def test_wrong_row_arguments_are_refused(constructor: str) -> None:
    source = f"""import clauz3
class Record(clauz3.Row):
    value: int
def main() -> None:
    value = {constructor}
"""
    results = prove_text(source)
    assert results[0].proof.conclusion is not Conclusion.OK


def test_constructor_does_not_hide_an_effect_argument() -> None:
    from pathlib import Path

    library = Path(__file__).resolve().parents[1] / "src/clauz3/stdlib/db"
    source = """import clauz3
from tools.db.trusted import contracts as db
from tools.db.trusted.effects import create_table
class Destination(clauz3.Row):
    path: str
@clauz3.guarantee(db.only_writes_databases([clauz3.FilePath("allowed.duckdb")]))
def main() -> None:
    destination = Destination(path="forbidden.duckdb")
    create_table(Destination, destination.path, "data")
"""
    results = prove_text(
        source, trusted_roots=[library / "tools/db/trusted"], import_paths=[library]
    )
    assert results[0].proof.conclusion is Conclusion.FAIL


def test_row_containers_refuse_without_crashing() -> None:
    source = """import clauz3
class Record(clauz3.Row):
    value: int
def main() -> None:
    rows = [Record(value=1), Record(value=2)]
"""
    results = prove_text(source)
    assert results[0].proof.conclusion is Conclusion.SKIP
    assert "containers of Row" in str(results[0].proof)


def test_constructed_rows_do_not_count_as_query_results() -> None:
    from pathlib import Path

    library = Path(__file__).resolve().parents[1] / "examples/email-from-db"
    source = """import clauz3
from tools.db.trusted import contracts as db
from tools.db.trusted.effects import send_email
from tools.db.trusted.schemas import UserRow
@clauz3.guarantee(db.addresses_from(UserRow, "email"))
def main() -> None:
    row = UserRow(
        name="Invented", email="invented@example.test", consented=True, role="fiction",
    )
    send_email(row.email, "fiction")
"""
    results = prove_text(
        source, trusted_roots=[library / "tools/db/trusted"], import_paths=[library]
    )
    assert results[0].proof.conclusion is Conclusion.FAIL, results[0].proof


@pytest.mark.parametrize("field", ["ctx", "var_name"])
def test_constructor_cannot_inject_evaluator_keywords(field: str) -> None:
    source = f"""import clauz3
class Record(clauz3.Row):
    {field}: int
def main() -> None:
    value = Record({field}=1)
"""
    results = prove_text(source)
    assert results[0].proof.conclusion is Conclusion.SKIP
    assert "reserved keyword" in str(results[0].proof)
