from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from pathlib import Path

from .db import BASE_DIR, connect, is_postgres_backend, now_iso


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MIGRATION_TABLE = "trm_data_migrations"


def _quote(identifier: str) -> str:
    if not _IDENTIFIER.fullmatch(identifier):
        raise ValueError(f"不安全的数据库标识符：{identifier!r}")
    return f'"{identifier}"'


def _source_tables(source: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in source.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name NOT LIKE 'sqlite_%'
               ORDER BY name"""
        )
    ]


def _dependency_order(source: sqlite3.Connection, tables: list[str]) -> list[str]:
    known = set(tables)
    dependencies: dict[str, set[str]] = {}
    for table in tables:
        parents = {
            row[2]
            for row in source.execute(f"PRAGMA foreign_key_list({_quote(table)})")
            if row[2] in known and row[2] != table
        }
        dependencies[table] = parents

    ordered: list[str] = []
    remaining = set(tables)
    while remaining:
        ready = sorted(table for table in remaining if not (dependencies[table] & remaining))
        if not ready:
            ready = [sorted(remaining)[0]]
        ordered.extend(ready)
        remaining.difference_update(ready)
    return ordered


def _source_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bootstrap_sqlite_to_postgres(source_path: Path, *, force: bool = False) -> dict:
    """Atomically replace PostgreSQL seed rows with the bundled SQLite dataset."""
    if not is_postgres_backend():
        return {"status": "skipped", "reason": "not_postgresql"}
    source_path = Path(source_path)
    if not source_path.is_file():
        raise RuntimeError(f"SQLite 迁移源不存在：{source_path}")

    fingerprint = _source_fingerprint(source_path)
    # A stable marker is deliberate: a later code deployment must never replace
    # live Neon data merely because the bundled SQLite file changed.
    marker = "sqlite-bootstrap:v1"
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    try:
        source_tables = _source_tables(source)
        ordered_tables = _dependency_order(source, source_tables)
        with connect() as target:
            target.execute("SELECT pg_advisory_xact_lock(846721903)")
            target.execute(
                f"""CREATE TABLE IF NOT EXISTS {MIGRATION_TABLE}(
                    migration_key TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    row_count INTEGER NOT NULL DEFAULT 0
                )"""
            )
            existing = target.execute(
                f"SELECT migration_key,row_count,applied_at FROM {MIGRATION_TABLE} WHERE migration_key=?",
                (marker,),
            ).fetchone()
            if existing and not force:
                return {"status": "already_applied", "rows": existing["row_count"], "applied_at": existing["applied_at"]}

            target_tables = {
                row["table_name"]
                for row in target.execute(
                    """SELECT table_name FROM information_schema.tables
                       WHERE table_schema=current_schema() AND table_type='BASE TABLE'"""
                )
            }
            missing = sorted(set(source_tables) - target_tables)
            if missing:
                raise RuntimeError("PostgreSQL 尚未完成建表，缺少：" + "、".join(missing))

            clear_tables = sorted(table for table in target_tables if table != MIGRATION_TABLE)
            if clear_tables:
                quoted = ",".join(_quote(table) for table in clear_tables)
                target.execute(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")

            total_rows = 0
            for table in ordered_tables:
                columns = [row[1] for row in source.execute(f"PRAGMA table_info({_quote(table)})")]
                if not columns:
                    continue
                rows = source.execute(f"SELECT * FROM {_quote(table)}").fetchall()
                if not rows:
                    continue
                column_sql = ",".join(_quote(column) for column in columns)
                placeholders = ",".join("?" for _ in columns)
                target.executemany(
                    f"INSERT INTO {_quote(table)} ({column_sql}) VALUES ({placeholders})",
                    [tuple(row[column] for column in columns) for row in rows],
                )
                total_rows += len(rows)

                if "id" in columns:
                    sequence_row = target.execute(
                        "SELECT pg_get_serial_sequence(?, 'id') AS sequence_name",
                        (table,),
                    ).fetchone()
                    sequence_name = sequence_row["sequence_name"] if sequence_row else None
                    if not sequence_name:
                        continue
                    target.execute(
                        f"""SELECT setval(
                            ?::regclass,
                            COALESCE(MAX(id), 1),
                            COUNT(*) > 0
                        ) FROM {_quote(table)}""",
                        (sequence_name,),
                    )

            target.execute(f"DELETE FROM {MIGRATION_TABLE} WHERE migration_key=?", (marker,))
            target.execute(
                f"""INSERT INTO {MIGRATION_TABLE}(migration_key,source_name,applied_at,row_count)
                    VALUES (?,?,?,?)""",
                (marker, f"{source_path.name}:{fingerprint[:12]}", now_iso(), total_rows),
            )
            return {"status": "applied", "rows": total_rows, "tables": len(source_tables)}
    finally:
        source.close()


def auto_bootstrap_postgres() -> dict:
    enabled = os.getenv("TRM_AUTO_MIGRATE_SQLITE", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return {"status": "skipped", "reason": "disabled"}
    configured = os.getenv("TRM_BOOTSTRAP_SQLITE_PATH", "").strip()
    source_path = Path(configured) if configured else BASE_DIR / "data" / "trm_system.db"
    return bootstrap_sqlite_to_postgres(source_path)
