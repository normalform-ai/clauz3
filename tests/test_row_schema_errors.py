"""Invalid imported Row declarations are configuration errors, not tracebacks."""

from pathlib import Path

import pytest

from clauz3.cli import main
from clauz3.prover import ProverConfigError, prove_text
from clauz3.row import Row, RowSchemaError

ENTRY = "def main() -> None:\n    assert True\n"
UNSUPPORTED_FIELD = (
    "from clauz3 import Row\nclass InvalidRow(Row):\n    amount: float\n"
)


def _write_layer(tmp_path: Path, source: str, filename: str = "rows.py") -> Path:
    trusted = tmp_path / "row_schema_layer"
    trusted.mkdir()
    (trusted / filename).write_text(source)
    return trusted


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (UNSUPPORTED_FIELD, "InvalidRow.amount"),
        (
            "from __future__ import annotations\n" + UNSUPPORTED_FIELD,
            "InvalidRow.amount",
        ),
        (
            "from clauz3 import Row\nclass InvalidRow(Row):\n    amount: int = 1\n",
            "InvalidRow fields may not have class-level defaults",
        ),
    ],
    ids=["unsupported_type", "postponed_type", "field_default"],
)
def test_invalid_imported_schema_is_configuration_error(
    tmp_path: Path, source: str, message: str
) -> None:
    trusted = _write_layer(tmp_path, source)
    with pytest.raises(ProverConfigError, match=message) as caught:
        prove_text(ENTRY, trusted_roots=[trusted], import_paths=[tmp_path])

    assert isinstance(caught.value.__cause__, RowSchemaError)
    assert str(trusted / "rows.py") in str(caught.value)
    assert "row_schema_layer.rows" in str(caught.value)


def test_invalid_schema_in_package_initializer_is_configuration_error(
    tmp_path: Path,
) -> None:
    trusted = _write_layer(tmp_path, UNSUPPORTED_FIELD, "__init__.py")
    with pytest.raises(ProverConfigError, match="InvalidRow.amount") as caught:
        prove_text(ENTRY, trusted_roots=[trusted], import_paths=[tmp_path])

    assert isinstance(caught.value.__cause__, RowSchemaError)
    assert str(trusted / "__init__.py") in str(caught.value)


def test_invalid_schema_exits_two_with_configuration_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trusted = _write_layer(tmp_path, UNSUPPORTED_FIELD)
    entry = tmp_path / "plan.py"
    entry.write_text(ENTRY)

    result = main(
        [
            "prove",
            str(entry),
            "--trusted-root",
            str(trusted),
            "--import-root",
            str(tmp_path),
        ]
    )

    assert result == 2
    output = capsys.readouterr()
    assert "error: cannot import trusted module" in output.err
    assert "InvalidRow.amount" in output.err
    assert "docs/explanation/symbolic-iteration.md" in output.err
    assert "Traceback" not in output.err
    assert "proved!" not in output.out


def test_schema_error_preserves_direct_type_error_compatibility() -> None:
    with pytest.raises(TypeError, match="InvalidRow.amount") as caught:

        class InvalidRow(Row):
            amount: float

    assert isinstance(caught.value, RowSchemaError)


def test_unrelated_import_type_error_is_not_reclassified(tmp_path: Path) -> None:
    trusted = _write_layer(tmp_path, 'raise TypeError("unrelated import bug")\n')
    with pytest.raises(TypeError, match="unrelated import bug") as caught:
        prove_text(ENTRY, trusted_roots=[trusted], import_paths=[tmp_path])

    assert type(caught.value) is TypeError


def test_invalid_row_instance_during_import_is_not_a_schema_error(
    tmp_path: Path,
) -> None:
    trusted = _write_layer(
        tmp_path,
        "from clauz3 import Row\nclass ValidRow(Row):\n    amount: int\nValidRow()\n",
    )
    with pytest.raises(TypeError, match="missing fields") as caught:
        prove_text(ENTRY, trusted_roots=[trusted], import_paths=[tmp_path])

    assert type(caught.value) is TypeError
