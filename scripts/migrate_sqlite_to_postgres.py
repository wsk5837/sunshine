#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="将 TRM SQLite 数据完整迁移到已配置的 PostgreSQL")
    parser.add_argument("--source", type=Path, default=ROOT / "data" / "trm_system.db")
    parser.add_argument("--force", action="store_true", help="清空目标业务表并重新迁移；仅用于明确选定的新试点数据库")
    args = parser.parse_args()

    if not os.getenv("DATABASE_URL", "").strip():
        parser.error("请先在当前终端设置 DATABASE_URL；脚本不会读取或打印密码")
    if not args.force:
        parser.error("为防止覆盖已有云端数据，必须显式添加 --force")
    os.environ["TRM_DATABASE_BACKEND"] = "postgresql"

    from app.auth import init_auth_db
    from app.budget_service import init_budget_and_workflow_db
    from app.db import connect, init_db
    from app.entry import init_patch_db
    from app.extended import init_extended_db
    from app.poc import init_poc_db
    from app.sqlite_postgres_migration import bootstrap_sqlite_to_postgres
    from app.trm_mcp import init_trm_mcp_db
    from app.v4 import init_v4_db

    init_db()
    init_extended_db()
    init_v4_db()
    init_auth_db()
    init_poc_db()
    with connect() as conn:
        init_budget_and_workflow_db(conn)
        init_patch_db(conn)
    init_trm_mcp_db()
    result = bootstrap_sqlite_to_postgres(args.source, force=True)
    print(f"迁移完成：{result.get('rows', 0)} 行，{result.get('tables', 0)} 张表。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
