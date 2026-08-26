from __future__ import annotations

from . import poc
from .db import connect, now_iso
from .rules import BusinessError, TAPD_STATUS_MAP


def tapd_status_to_poc_v5(story: dict, fallback: str = "新") -> str:
    """Map TAPD status using project display status first, then generic status codes."""
    # The current TAPD project uses the workflow: 待处理 -> 进行中 -> 完成.
    # Match the display value exactly first so a custom TAPD workflow does not
    # silently fall back to the previous TRM status.
    display_status = str(story.get("v_status") or "").strip()
    exact_display_map = {
        "待处理": "新",
        "进行中": "开发中",
        "完成": "已关闭",
    }
    if display_status in exact_display_map:
        return exact_display_map[display_status]

    values = []
    for key in ("v_status", "status", "step"):
        value = story.get(key)
        if value not in (None, ""):
            values.append(str(value).strip())
    text = " | ".join(values).lower()

    groups = [
        (("已拒绝", "需求终止", "已终止", "rejected", "reject", "cancelled", "canceled", "aborted"), "已拒绝"),
        (("已关闭", "已完成", "已发布", "发布完成", "closed", "done", "completed", "released"), "已关闭"),
        (("已验收", "已验证", "待发布", "发布中", "待验收", "accepted", "verified", "release", "releasing"), "已验收"),
        (("已实现", "测试中", "待测试", "测试", "验证中", "resolved", "testing", "test", "qa"), "测试中"),
        (("开发中", "实现中", "进行中", "处理中", "研发中", "待开发", "developing", "progressing", "in progress", "processing", "doing"), "开发中"),
        (("待处理", "新", "规划中", "待规划", "待排期", "已排期", "已评审", "未开始", "planning", "open", "new", "backlog", "pending", "todo"), "新"),
    ]
    for keys, mapped in groups:
        if any(str(key).lower() in text for key in keys):
            return mapped
    return fallback if fallback in TAPD_STATUS_MAP else "新"


def run_scheduled_tapd_sync_v5(force: bool = False):
    """Scheduled readback that records per-demand failures instead of swallowing them."""
    now = poc._now_dt()
    synced = 0
    with connect() as conn:
        interval = int(float(poc.get_setting(conn, "tapd_sync_interval_seconds", "1800")))
        rows = list(conn.execute(
            "SELECT * FROM demands WHERE tapd_id IS NOT NULL AND tapd_id<>'' AND status NOT IN ('已终止')"
        ))
        for demand in rows:
            last = poc._parse_iso(demand["tapd_last_sync_at"])
            if not force and last and (now - last).total_seconds() < interval:
                continue
            try:
                if poc.tapd_runtime_config(conn)["mode"] == "live":
                    payload = poc.build_live_sync_payload(conn, demand["id"], demand["tapd_id"])
                else:
                    payload = poc.build_mock_sync_payload(conn, demand["id"], demand["tapd_status"] or "新")
                poc.apply_tapd_payload(conn, demand["id"], payload, "定时任务", "background")
                synced += 1
            except BusinessError as exc:
                message = exc.message
                conn.execute(
                    "INSERT INTO tapd_sync_runs(demand_id,source,changed_count,success,message,created_at) VALUES (?,?,?,?,?,?)",
                    (demand["id"], "定时任务", 0, 0, message[:500], now_iso()),
                )
                conn.execute(
                    "UPDATE demands SET tapd_sync_status='失败',updated_at=? WHERE id=?",
                    (now_iso(), demand["id"]),
                )
                try:
                    poc._integration_log(conn, "tapd", "in", "readback", demand["tapd_id"], False, message[:500], "background")
                except Exception:
                    pass
            except Exception as exc:
                message = str(exc) or "TAPD定时回读失败"
                conn.execute(
                    "INSERT INTO tapd_sync_runs(demand_id,source,changed_count,success,message,created_at) VALUES (?,?,?,?,?,?)",
                    (demand["id"], "定时任务", 0, 0, message[:500], now_iso()),
                )
                conn.execute(
                    "UPDATE demands SET tapd_sync_status='失败',updated_at=? WHERE id=?",
                    (now_iso(), demand["id"]),
                )
    return synced
