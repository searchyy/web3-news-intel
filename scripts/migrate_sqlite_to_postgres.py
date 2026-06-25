from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, insert, inspect, select, text
from sqlalchemy.engine import Connection, Engine

import app.db.models  # noqa: F401  Ensures all model tables are registered.
from app.db.base import Base


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy local SQLite data into PostgreSQL.")
    parser.add_argument("--sqlite", required=True, help="SQLite file path or sqlite SQLAlchemy URL")
    parser.add_argument("--postgres", required=True, help="PostgreSQL SQLAlchemy URL")
    parser.add_argument(
        "--replace",
        action="store_true",
        help=(
            "Delete target table rows before copying. "
            "Use only after running alembic on the target DB."
        ),
    )
    args = parser.parse_args(argv)

    sqlite_url = _sqlite_url(args.sqlite)
    postgres_url = args.postgres
    if not postgres_url.startswith(("postgresql", "postgres://")):
        raise SystemExit("--postgres must be a PostgreSQL SQLAlchemy URL")

    source = create_engine(sqlite_url, future=True)
    target = create_engine(postgres_url, future=True, pool_pre_ping=True)
    try:
        copied = migrate(source, target, replace=args.replace)
    finally:
        source.dispose()
        target.dispose()
    for table_name, count in copied:
        print(f"{table_name}: {count}")
    print(f"copied_tables={len(copied)} copied_rows={sum(count for _, count in copied)}")
    return 0


def migrate(source: Engine, target: Engine, *, replace: bool) -> list[tuple[str, int]]:
    source_tables = set(inspect(source).get_table_names())
    tables = [table for table in Base.metadata.sorted_tables if table.name in source_tables]
    copied: list[tuple[str, int]] = []
    with source.connect() as source_conn, target.begin() as target_conn:
        if replace:
            _clear_target(target_conn, tables)
        for table in tables:
            source_columns = {
                column["name"] for column in inspect(source_conn).get_columns(table.name)
            }
            columns = [column for column in table.columns if column.name in source_columns]
            if not columns:
                continue
            rows = [dict(row._mapping) for row in source_conn.execute(select(*columns)).all()]
            if rows:
                target_conn.execute(insert(table), rows)
            copied.append((table.name, len(rows)))
        _reset_postgres_sequences(target_conn, tables)
    return copied


def _clear_target(conn: Connection, tables: list[Any]) -> None:
    for table in reversed(tables):
        conn.execute(table.delete())


def _reset_postgres_sequences(conn: Connection, tables: list[Any]) -> None:
    if conn.dialect.name != "postgresql":
        return
    for table in tables:
        integer_pks = [
            column for column in table.primary_key.columns if _column_python_type(column) is int
        ]
        if len(integer_pks) != 1:
            continue
        column = integer_pks[0]
        sequence = conn.scalar(
            text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
            {"table_name": table.name, "column_name": column.name},
        )
        if not sequence:
            continue
        max_value = conn.scalar(select(func.max(column)))
        if max_value is None:
            continue
        conn.execute(
            text("SELECT setval(:sequence_name, :value, true)"),
            {"sequence_name": sequence, "value": int(max_value)},
        )


def _column_python_type(column: Any) -> type[Any] | None:
    try:
        return column.type.python_type
    except NotImplementedError:
        return None


def _sqlite_url(value: str) -> str:
    if value.startswith("sqlite"):
        return value
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"SQLite file does not exist: {path}")
    return f"sqlite+pysqlite:///{path.as_posix()}"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
