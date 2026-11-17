"""Database reads touch only the named source and return the declared types."""

import importlib
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import duckdb
import pytest

import clauz3
from clauz3.prover import _selected_layer_context

LIBRARY = Path(__file__).resolve().parents[1] / "src/clauz3/stdlib/db"
ROOTS = [LIBRARY / "tools/db/trusted"]


class User(clauz3.Row):
    email: str


@pytest.fixture
def db_effects() -> Iterator[ModuleType]:
    with _selected_layer_context(ROOTS, [LIBRARY]):
        yield importlib.import_module("tools.db.trusted.effects")


def test_database_table_name_is_never_read_as_a_file(
    db_effects: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A table called passwords.csv is a table, not ./passwords.csv."""
    (tmp_path / "data").mkdir()
    (tmp_path / "cwd").mkdir()
    (tmp_path / "cwd" / "passwords.csv").write_text("email\nsecret@example.com\n")
    path = str(tmp_path / "data" / "app.duckdb")
    db_effects.create_table(User, path, "users")
    monkeypatch.chdir(tmp_path / "cwd")
    with pytest.raises((duckdb.PermissionException, duckdb.CatalogException)):
        db_effects.read_rows(User, path, "passwords.csv", {})


def test_stored_view_cannot_read_outside_files(
    db_effects: ModuleType, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.csv"
    outside.write_text("email\noutside@example.com\n")
    path = str(tmp_path / "app.duckdb")
    with duckdb.connect(path) as con:
        con.execute("CREATE TABLE users (email VARCHAR)")
        con.execute("INSERT INTO users VALUES ('inside@example.com')")
        con.execute("CREATE VIEW emails AS SELECT email FROM users")
        con.execute(
            f"CREATE VIEW leak AS SELECT * FROM read_csv_auto('{outside.as_posix()}')"
        )
    with pytest.raises(duckdb.PermissionException):
        db_effects.read_rows(User, path, "leak", {})
    # Tables and views over the database's own tables still read.
    for table in ("users", "emails"):
        rows = db_effects.read_rows(User, path, table, {})
        assert [row.email for row in rows] == ["inside@example.com"]


def test_database_reads_do_not_write(db_effects: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "app.duckdb"
    db_effects.create_table(User, str(path), "users")
    before = path.read_bytes()
    assert db_effects.read_rows(User, str(path), "users", {}) == []
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["app.duckdb"]


def test_csv_and_parquet_sources_still_read(
    db_effects: ModuleType, tmp_path: Path
) -> None:
    csv = tmp_path / "users.csv"
    csv.write_text("email\nfile@example.com\n")
    tables = tmp_path / "tables"
    tables.mkdir()
    with duckdb.connect(":memory:") as con:
        con.execute(
            "COPY (SELECT 'parquet@example.com' AS email) TO "
            f"'{(tables / 'users.parquet').as_posix()}' (FORMAT PARQUET)"
        )
    sources = [
        (str(csv), "file@example.com"),
        (str(tmp_path), "file@example.com"),
        (str(tables), "parquet@example.com"),
        (str(tables / "users.parquet"), "parquet@example.com"),
    ]
    for source, email in sources:
        rows = db_effects.read_rows(User, source, "users", {})
        assert [row.email for row in rows] == [email]


@pytest.fixture
def home_and_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A home directory and a separate working directory."""
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    (home / "secrets").mkdir(parents=True)
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(cwd)
    return home, cwd


def test_reads_do_not_expand_a_tilde(
    db_effects: ModuleType, home_and_cwd: tuple[Path, Path]
) -> None:
    """``~/x`` is the relative path the path contracts compare, not $HOME/x."""
    home, cwd = home_and_cwd
    (home / "secrets" / "users.csv").write_text("email\nhome@example.com\n")
    db_effects.create_table(User, str(home / "app.duckdb"), "users")
    with pytest.raises(duckdb.IOException):
        db_effects.read_rows(User, "~/secrets/users.csv", "users", {})
    with pytest.raises(duckdb.IOException):
        db_effects.read_rows(User, "~/app.duckdb", "users", {})
    with pytest.raises(ValueError, match="unsupported database source"):
        db_effects.read_rows(User, "~/secrets", "users", {})

    (cwd / "~" / "secrets").mkdir(parents=True)
    (cwd / "~" / "secrets" / "users.csv").write_text("email\nliteral@example.com\n")
    for source in ("~/secrets/users.csv", "~/secrets"):
        rows = db_effects.read_rows(User, source, "users", {})
        assert [row.email for row in rows] == ["literal@example.com"]


def test_writes_do_not_expand_a_tilde(
    db_effects: ModuleType, home_and_cwd: tuple[Path, Path]
) -> None:
    home, cwd = home_and_cwd
    with pytest.raises(duckdb.IOException):
        db_effects.create_table(User, "~/new.duckdb", "users")
    assert not (home / "new.duckdb").exists()

    (cwd / "~").mkdir()
    db_effects.create_table(User, "~/new.duckdb", "users")
    db_effects.insert_row("~/new.duckdb", "users", User(email="literal@example.com"))
    assert (cwd / "~" / "new.duckdb").is_file()
    assert not (home / "new.duckdb").exists()
    rows = db_effects.read_rows(User, "~/new.duckdb", "users", {})
    assert [row.email for row in rows] == ["literal@example.com"]


class Consent(clauz3.Row):
    email: str
    consented: bool


class Count(clauz3.Row):
    email: str
    n: int


def test_text_is_not_read_as_a_bool(db_effects: ModuleType, tmp_path: Path) -> None:
    """A column DuckDB reads as text must not turn "false" into True."""
    (tmp_path / "users.csv").write_text(
        "email,consented\na@example.com,false\nb@example.com,unknown\n"
    )
    with pytest.raises(TypeError, match="column 'consented' must contain bool"):
        db_effects.read_rows(Consent, str(tmp_path), "users", {})


def test_decimals_are_not_truncated_to_int(
    db_effects: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "counts.csv").write_text("email,n\na@example.com,3.9\n")
    with pytest.raises(TypeError, match="column 'n' must contain int"):
        db_effects.read_rows(Count, str(tmp_path), "counts", {})


def test_bool_and_int_do_not_stand_in_for_each_other(
    db_effects: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "users.csv").write_text("email,consented\na@example.com,1\n")
    (tmp_path / "counts.csv").write_text("email,n\na@example.com,true\n")
    with pytest.raises(TypeError, match="must contain bool, but the source holds int"):
        db_effects.read_rows(Consent, str(tmp_path), "users", {})
    with pytest.raises(TypeError, match="must contain int, but the source holds bool"):
        db_effects.read_rows(Count, str(tmp_path), "counts", {})


def test_declared_types_are_returned_exactly(
    db_effects: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "users.csv").write_text(
        "email,consented\na@example.com,false\nb@example.com,true\n"
    )
    (tmp_path / "counts.csv").write_text("email,n\na@example.com,0\nb@example.com,7\n")
    users = db_effects.read_rows(Consent, str(tmp_path), "users", {})
    counts = db_effects.read_rows(Count, str(tmp_path), "counts", {})
    assert [row.consented for row in users] == [False, True]
    assert [row.n for row in counts] == [0, 7]
    assert all(type(row.consented) is bool for row in users)
    assert all(type(row.n) is int for row in counts)


def test_str_fields_render_other_column_types(
    db_effects: ModuleType, tmp_path: Path
) -> None:
    """Text is the one lossless target: a date or number column reads as text."""
    (tmp_path / "users.csv").write_text("email\n2026-01-05\n")
    (tmp_path / "counts.csv").write_text("email\n3.9\n")
    for table, text in (("users", "2026-01-05"), ("counts", "3.9")):
        rows = db_effects.read_rows(User, str(tmp_path), table, {})
        assert [row.email for row in rows] == [text]
