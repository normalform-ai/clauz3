"""Trusted database read effects.

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
from clauz3._path_safety import has_no_parent_segments, is_single_path_component

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
