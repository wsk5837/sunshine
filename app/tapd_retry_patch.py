from __future__ import annotations

from datetime import datetime, timedelta

from . import poc
from .db import connect, now_iso
from .rules import BusinessError


def process_retry_jobs_v5(force: bool = False):
    """Retry real TAPD creation up to three attempts without crashing the worker."""
    now = datetime.now().astimezone()
    processed = 0
    with connect() as conn:
        rows = list(conn.execute("SELECT * FROM tapd_retry_jobs WHERE status='等待重试' ORDER BY id"))
        for row in rows:
            due = poc._parse_iso(row["next_retry_at"])
            if not force and due and due > now:
                continue
            attempt = int(row["attempt_count"] or 1) + 1
            retry_seconds = int(float(poc.get_setting(conn, "tapd_retry_seconds", "30")))
            success = False
            error = row["last_error"] or "TAPD调用失败"
            if bool(row["force_fail"]):
                error = f"模拟上游连接失败（第{attempt}次）"
            else:
                try:
                    records = poc.create_tapd_requirements(conn, int(row["demand_id"]), "background")
                    success = bool(records)
                    error = ""
                except BusinessError as exc:
                    error = exc.message
                except Exception as exc:
                    error = str(exc) or "TAPD调用失败"

            conn.execute(
                "INSERT INTO tapd_events(demand_id,event_type,success,attempt,request_id,message,created_at) VALUES (?,?,?,?,?,?,?)",
                (row["demand_id"], "CREATE", 1 if success else 0, attempt, "background", "创建成功" if success else error[:500], now_iso()),
            )
            if success:
                conn.execute(
                    "UPDATE tapd_retry_jobs SET status='成功',attempt_count=?,last_error='',next_retry_at=NULL,updated_at=? WHERE id=?",
                    (attempt, now_iso(), row["id"]),
                )
            elif attempt >= 3:
                conn.execute(
                    "UPDATE tapd_retry_jobs SET status='最终失败',attempt_count=?,last_error=?,next_retry_at=NULL,updated_at=? WHERE id=?",
                    (attempt, error[:1000], now_iso(), row["id"]),
                )
                conn.execute(
                    "UPDATE demands SET status='TAPD同步失败',current_node='TAPD同步失败',tapd_sync_status='失败',updated_at=? WHERE id=?",
                    (now_iso(), row["demand_id"]),
                )
                try:
                    poc._notification(
                        conn, int(row["demand_id"]), "error", "TAPD同步失败",
                        f"TAPD创建连续失败3次：{error[:500]}", "admin",
                        f"tapd-create-failed:{row['demand_id']}:{row['id']}",
                    )
                except Exception:
                    pass
            else:
                next_retry = (now + timedelta(seconds=retry_seconds)).isoformat(timespec="seconds")
                conn.execute(
                    "UPDATE tapd_retry_jobs SET attempt_count=?,next_retry_at=?,last_error=?,updated_at=? WHERE id=?",
                    (attempt, next_retry, error[:1000], now_iso(), row["id"]),
                )
            processed += 1
    return processed
