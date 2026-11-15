"""Trusted database reads and structured DuckDB writes.

The prover records calls to ``read_rows`` and materializes the result as a
symbolic ``list[RowT]``. The body runs only under ``clauz3 run`` and uses
DuckDB as the execution engine for DuckDB files, CSV files, Parquet files, or
directories containing ``<table>.csv`` / ``<table>.parquet`` files.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import deal

import clauz3
from clauz3._path_safety import (
    has_no_parent_segments,
    is_plain_path,
    is_single_path_component,
)

RowT = TypeVar("RowT", bound=clauz3.Row)

MAX_ROWS = 100
_DUCKDB_SUFFIXES = {".duckdb", ".ddb", ".db"}
_CSV_SUFFIXES = {".csv"}
_PARQUET_SUFFIXES = {".parquet"}
_SUPPORTED_VALUE_TYPES = (str, int, bool)


@clauz3.requires(
    lambda schema, path, table, where: has_no_parent_segments(path),
    message="path must not contain parent-directory segments",
)
@clauz3.requires(
    lambda schema, path, table, where: is_single_path_component(table),
    message="table must be a single path component",
)
@clauz3.requires(
    lambda schema, path, table, where: len(path) > 0 and len(table) > 0,
    message="path and table must be non-empty",
)
@clauz3.ensures(lambda result: len(result) <= 100)
@deal.has("read")
@clauz3.trusted
def read_rows(
    schema: type[RowT],
    path: str,
    table: str,
    where: dict[str, object],
) -> list[RowT]:
    """Read up to 100 rows from ``path``/``table`` as instances of ``schema``.

    ``path`` may point to a DuckDB database, one CSV or Parquet file, or a
    directory with files named ``<table>.csv`` / ``<table>.parquet``. CSV and
    Parquet sources are queried through an in-memory DuckDB connection.
    """
    if not isinstance(schema, type) or not issubclass(schema, clauz3.Row):
        raise TypeError("schema must be a clauz3.Row subclass")

    duckdb = _duckdb()
    connection, relation_sql = _connect(duckdb, Path(path).expanduser(), table)
    try:
        columns = tuple(schema.__annotations__)
        if not columns:
            raise ValueError(f"{schema.__name__} declares no columns")
        where_sql, params = _where_clause(where)
        sql = (
            f"SELECT {_select_list(columns)} FROM {relation_sql}"
            f"{where_sql} LIMIT {MAX_ROWS}"
        )
        rows = connection.execute(sql, params).fetchall()
    finally:
        connection.close()

    return [_row_from_tuple(schema, columns, row) for row in rows]


@clauz3.requires(
    lambda schema, path, table: is_plain_path(path),
    message="database path must have plain path segments",
)
@clauz3.requires(
    lambda schema, path, table: is_single_path_component(table),
    message="table must be a single path component",
)
@clauz3.requires(
    lambda schema, path, table: len(path) > 0 and len(table) > 0,
    message="path and table must be non-empty",
)
@deal.has("read", "write")
@clauz3.trusted
def create_table(schema: type[RowT], path: str, table: str) -> None:
    """Create a new table from a Row schema, creating the DuckDB file if needed.

    Supports str, int and bool columns, all NOT NULL. An existing table is an
    error, never replaced. The parent directory must already exist. Writes
    accept .duckdb, .ddb and .db files only, with no tilde expansion. DuckDB
    may also write its adjacent .wal journal. No arbitrary SQL is accepted.
    """
    columns = _write_columns(schema)
    sql_types = {str: "VARCHAR", int: "BIGINT", bool: "BOOLEAN"}
    definitions = ", ".join(
        f"{_quote_identifier(name)} {sql_types[kind]} NOT NULL"
        for name, kind in columns.items()
    )
    _validate_write_target(path, table)
    connection = _write_connection(path)
    try:
        connection.execute(f"CREATE TABLE {_quote_identifier(table)} ({definitions})")
    finally:
        connection.close()


@clauz3.requires(
    lambda path, table, row: is_plain_path(path),
    message="database path must have plain path segments",
)
@clauz3.requires(
    lambda path, table, row: is_single_path_component(table),
    message="table must be a single path component",
)
@clauz3.requires(
    lambda path, table, row: len(path) > 0 and len(table) > 0,
    message="path and table must be non-empty",
)
@deal.has("read", "write")
@clauz3.trusted
def insert_row(path: str, table: str, row: RowT) -> None:
    """Append one typed Row to an existing DuckDB table atomically.

    Supports str, int and bool fields. Values are bound parameters, never SQL
    fragments. Existing rows are preserved. The file and table must already
    exist; writes may include the adjacent .wal journal. Each call commits
    independently, so a later failure does not undo earlier successful calls.
    """
    _validate_write_target(path, table)
    columns = _write_columns(type(row))
    values = []
    for name, kind in columns.items():
        value = getattr(row, name)
        if type(value) is not kind:
            raise TypeError(f"column {name!r} must contain {kind.__name__}")
        values.append(value)
    if not Path(path).is_file():
        raise FileNotFoundError(f"database does not exist: {path}")
    names = _select_list(tuple(columns))
    placeholders = ", ".join("?" for _ in columns)
    connection = _write_connection(path)
    try:
        connection.execute(
            f"INSERT INTO {_quote_identifier(table)} ({names}) VALUES ({placeholders})",
            values,
        )
    finally:
        connection.close()


def _write_columns(schema: type[clauz3.Row]) -> dict[str, type]:
    if not isinstance(schema, type) or not issubclass(schema, clauz3.Row):
        raise TypeError("schema must be a clauz3.Row subclass")
    columns = dict(schema.__annotations__)
    if not columns or any(
        kind not in _SUPPORTED_VALUE_TYPES for kind in columns.values()
    ):
        raise TypeError("a write schema needs str, int or bool columns")
    return columns


def _validate_write_target(path: str, table: str) -> None:
    clauz3.FilePath(path)
    if Path(path).suffix.lower() not in _DUCKDB_SUFFIXES:
        raise ValueError("writes require a .duckdb, .ddb or .db file")
    if not table or not is_single_path_component(table):
        raise ValueError("table must be a nonempty single path component")


def _write_connection(path: str):
    # No spill files, extension downloads, replacement scans or external
    # file/network access through expressions stored in an existing database.
    return _duckdb().connect(
        path,
        config={
            "enable_external_access": "false",
            "autoload_known_extensions": "false",
            "autoinstall_known_extensions": "false",
            "temp_directory": "",
        },
    )


def _duckdb():
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "stdlib:db requires the optional 'duckdb' package at run time. "
            "Install it in the environment that runs proved programs."
        ) from exc
    return duckdb


def _connect(duckdb, path: Path, table: str):
    if path.is_dir():
        connection = duckdb.connect(":memory:")
        return connection, _file_relation(_table_file(path, table))

    suffix = path.suffix.lower()
    if suffix in _DUCKDB_SUFFIXES:
        return duckdb.connect(str(path), read_only=True), _quote_identifier(table)
    if suffix in _CSV_SUFFIXES | _PARQUET_SUFFIXES:
        if table != path.stem:
            raise ValueError(
                f"table {table!r} must match file stem {path.stem!r} for "
                "single-file CSV/Parquet sources"
            )
        connection = duckdb.connect(":memory:")
        return connection, _file_relation(path)
    raise ValueError(
        f"unsupported database source {path!s}; expected DuckDB, CSV, Parquet, "
        "or a directory containing CSV/Parquet tables"
    )


def _table_file(root: Path, table: str) -> Path:
    candidates = [
        root / f"{table}.parquet",
        root / f"{table}.csv",
    ]
    existing = [candidate for candidate in candidates if candidate.is_file()]
    if len(existing) == 1:
        return existing[0]
    if existing:
        raise ValueError(f"ambiguous table {table!r}: both CSV and Parquet exist")
    raise FileNotFoundError(f"no CSV or Parquet file for table {table!r} in {root!s}")


def _file_relation(path: Path) -> str:
    suffix = path.suffix.lower()
    literal = _quote_literal(str(path))
    if suffix in _CSV_SUFFIXES:
        return f"read_csv_auto({literal})"
    if suffix in _PARQUET_SUFFIXES:
        return f"read_parquet({literal})"
    raise ValueError(f"unsupported table file {path!s}")


def _select_list(columns: tuple[str, ...]) -> str:
    return ", ".join(_quote_identifier(column) for column in columns)


def _where_clause(where: dict[str, object]) -> tuple[str, list[object]]:
    if not where:
        return "", []

    clauses: list[str] = []
    params: list[object] = []
    for key, value in where.items():
        if not isinstance(key, str) or not key:
            raise ValueError("where keys must be non-empty column names")
        if not isinstance(value, _SUPPORTED_VALUE_TYPES):
            raise TypeError("where values must be str, int, or bool")
        clauses.append(f"{_quote_identifier(key)} = ?")
        params.append(value)
    return " WHERE " + " AND ".join(clauses), params


def _row_from_tuple(
    schema: type[RowT],
    columns: tuple[str, ...],
    values: tuple[object, ...],
) -> RowT:
    data = {}
    for column, value in zip(columns, values, strict=True):
        if value is None:
            raise ValueError(f"column {column!r} was NULL")
        data[column] = _coerce_value(value, schema.__annotations__[column])
    return schema(**data)


def _coerce_value(value: object, expected: type) -> object:
    if expected is str:
        return str(value)
    if expected is int:
        return int(value)
    if expected is bool:
        return bool(value)
    raise TypeError(f"unsupported Row field type {expected!r}")


def _quote_identifier(value: str) -> str:
    if not value:
        raise ValueError("identifier must be non-empty")
    return '"' + value.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
