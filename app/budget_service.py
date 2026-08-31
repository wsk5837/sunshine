"""预算占用、释放与工时审批的共享领域服务。

页面 API、审批流程和 MCP 都通过这里更新汇总字段，避免各入口形成不同口径。
"""

from __future__ import annotations

from collections import defaultdict

from .db import now_iso
from .rules import BusinessError


def _add_column(conn, table: str, column: str, ddl: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_budget_and_workflow_db(conn) -> None:
    """为旧数据库增量补齐真实预算占用和工时审批字段。"""
    _add_column(conn, "allocations", "budget_id", "INTEGER")
    _add_column(conn, "allocations", "ledger_status", "TEXT NOT NULL DEFAULT '待占用'")
    _add_column(conn, "allocations", "occupied_at", "TEXT")
    _add_column(conn, "allocations", "released_at", "TEXT")
    _add_column(conn, "budget_transactions", "allocation_id", "INTEGER")
    _add_column(conn, "budget_transactions", "event_key", "TEXT DEFAULT ''")
    _add_column(conn, "budget_transactions", "created_by", "TEXT DEFAULT ''")

    _add_column(conn, "demand_work_logs", "function_point_id", "INTEGER")
    _add_column(conn, "demand_work_logs", "approval_status", "TEXT NOT NULL DEFAULT '待审批'")
    _add_column(conn, "demand_work_logs", "submitted_by", "TEXT DEFAULT ''")
    _add_column(conn, "demand_work_logs", "approver", "TEXT DEFAULT ''")
    _add_column(conn, "demand_work_logs", "approval_comment", "TEXT DEFAULT ''")
    _add_column(conn, "demand_work_logs", "approved_at", "TEXT")

    # 升级前的人工工时已经计入 actual_hours，迁移时视为历史已审批，避免升级后汇总归零。
    conn.execute(
        """UPDATE demand_work_logs
           SET approval_status='已通过',approved_at=COALESCE(approved_at,created_at),
               submitted_by=COALESCE(NULLIF(submitted_by,''),created_by)
           WHERE approval_status IS NULL OR approval_status=''"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_allocations_budget_status ON allocations(budget_id,ledger_status,demand_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_work_logs_approval ON demand_work_logs(approval_status,demand_id,function_point_id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_budget_txn_event ON budget_transactions(event_key) WHERE event_key<>''"
    )
    conn.execute(
        """UPDATE allocations
           SET budget_id=(SELECT b.id FROM budgets b WHERE b.budget_name=allocations.expense_source LIMIT 1)
           WHERE budget_id IS NULL"""
    )


def function_point_allocation_coverage(conn, demand_id: int, *, require_complete: bool = False) -> dict:
    """校验并返回需求下每个功能点的预算分摊覆盖率。"""
    points = list(conn.execute(
        "SELECT id,fp_no,name,estimated_amount FROM function_points WHERE demand_id=? ORDER BY id",
        (demand_id,),
    ))
    rows = list(conn.execute(
        "SELECT function_point_id,ratio FROM allocations WHERE demand_id=? ORDER BY id", (demand_id,)
    ))
    point_by_id = {int(point["id"]): point for point in points}
    ratios = defaultdict(float)
    for row in rows:
        point_id = row["function_point_id"]
        if point_id is None or int(point_id) not in point_by_id:
            raise BusinessError(422, "BUD-4221", "每条费用分摊必须关联当前需求中的有效功能点")
        ratios[int(point_id)] += float(row["ratio"] or 0)
    items = []
    incomplete = []
    for point in points:
        point_id = int(point["id"])
        ratio = round(ratios.get(point_id, 0.0), 4)
        if ratio > 100.0001:
            raise BusinessError(
                422, "BUD-4221",
                f"功能点{point['fp_no']}分摊比例超过100%，当前为{ratio:.2f}%",
            )
        item = {
            "function_point_id": point_id,
            "fp_no": point["fp_no"],
            "name": point["name"],
            "estimated_amount": round(float(point["estimated_amount"] or 0), 2),
            "ratio": ratio,
            "complete": abs(ratio - 100.0) <= 0.01,
        }
        items.append(item)
        if not item["complete"]:
            incomplete.append(item)
    if require_complete:
        if not points:
            raise BusinessError(422, "BUD-4221", "尚未建立功能点，无法配置预算分摊")
        if incomplete:
            summary = "、".join(f"{item['fp_no']}({item['ratio']:.2f}%)" for item in incomplete[:5])
            suffix = "等" if len(incomplete) > 5 else ""
            raise BusinessError(
                422, "BUD-4221",
                f"每个功能点的预算分摊都必须合计100%，未完成：{summary}{suffix}",
                {"incomplete": incomplete},
            )
    return {
        "items": items,
        "function_point_count": len(points),
        "completed_count": len(points) - len(incomplete),
        "complete": bool(points) and not incomplete,
    }


def recalculate_demand_allocation_amounts(conn, demand_id: int) -> dict:
    """按每个功能点的评估金额重算其分摊金额，而不是按需求或项目总额计算。"""
    if not conn.execute("SELECT 1 FROM demands WHERE id=?", (demand_id,)).fetchone():
        raise BusinessError(404, "REQ-4040", "需求不存在")
    rows = list(conn.execute(
        """SELECT a.id,a.function_point_id,a.ratio,a.ledger_status,fp.estimated_amount
             FROM allocations a
             LEFT JOIN function_points fp ON fp.id=a.function_point_id AND fp.demand_id=a.demand_id
            WHERE a.demand_id=? ORDER BY a.function_point_id,a.id""",
        (demand_id,),
    ))
    if not rows:
        return {"amount": 0.0, "rows": 0}
    if any(row["ledger_status"] == "已占用" for row in rows):
        raise BusinessError(409, "BUD-4090", "费用分摊已经占用预算，不能重新计算")
    function_point_allocation_coverage(conn, demand_id)
    grouped = defaultdict(list)
    for row in rows:
        if row["function_point_id"] is None or row["estimated_amount"] is None:
            raise BusinessError(422, "BUD-4221", "费用分摊关联的功能点不存在")
        grouped[int(row["function_point_id"])].append(row)
    total = 0.0
    for point_rows in grouped.values():
        base = round(float(point_rows[0]["estimated_amount"] or 0), 2)
        ratio_sum = sum(float(row["ratio"] or 0) for row in point_rows)
        absorb_rounding = abs(ratio_sum - 100.0) <= 0.01
        allocated = 0.0
        for index, row in enumerate(point_rows):
            if absorb_rounding and index == len(point_rows) - 1:
                amount = round(base - allocated, 2)
            else:
                amount = round(base * float(row["ratio"] or 0) / 100, 2)
            allocated = round(allocated + amount, 2)
            total = round(total + amount, 2)
            conn.execute("UPDATE allocations SET amount=? WHERE id=?", (amount, row["id"]))
    return {"amount": total, "rows": len(rows)}


def _refresh_budget_snapshots(conn, budget_id: int, department: str) -> None:
    from datetime import datetime

    budget = conn.execute("SELECT total_budget,used_budget FROM budgets WHERE id=?", (budget_id,)).fetchone()
    if not budget:
        return
    now_dt = datetime.now()
    periods = (("month", now_dt.strftime("%Y-%m")), ("quarter", f"{now_dt.year}Q{(now_dt.month - 1) // 3 + 1}"))
    for period_type, period in periods:
        conn.execute(
            """INSERT INTO budget_execution_snapshots
               (budget_id,department,period_type,period,used_amount,total_budget,recorded_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(budget_id,department,period_type,period)
               DO UPDATE SET used_amount=excluded.used_amount,total_budget=excluded.total_budget,recorded_at=excluded.recorded_at""",
            (budget_id, department or "未归属部门", period_type, period,
             float(budget["used_budget"] or 0), float(budget["total_budget"] or 0), now_iso()),
        )


def _post_budget_transaction(
    conn,
    *,
    budget_id: int,
    txn_type: str,
    amount: float,
    reference_type: str,
    reference_id: str,
    description: str,
    department: str,
    event_key: str,
    allocation_id: int | None,
    actor: str,
) -> None:
    if conn.execute("SELECT 1 FROM budget_transactions WHERE event_key=?", (event_key,)).fetchone():
        return
    budget = conn.execute("SELECT * FROM budgets WHERE id=?", (budget_id,)).fetchone()
    if not budget:
        raise BusinessError(422, "BUD-4220", "费用分摊关联的预算不存在")
    used = float(budget["used_budget"] or 0)
    total = float(budget["total_budget"] or 0)
    if txn_type in ("占用", "支出"):
        if used + amount > total + 0.01:
            raise BusinessError(422, "BUD-4220", f"预算不足：{budget['budget_name']} 可用金额不足")
        used += amount
    elif txn_type in ("释放", "冲销"):
        used = max(0.0, used - amount)
    conn.execute("UPDATE budgets SET used_budget=? WHERE id=?", (round(used, 2), budget_id))
    conn.execute(
        """INSERT INTO budget_transactions
           (budget_id,txn_type,amount,reference_type,reference_id,description,department,allocation_id,event_key,created_by,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (budget_id, txn_type, round(amount, 2), reference_type, reference_id, description,
         department, allocation_id, event_key, actor, now_iso()),
    )
    _refresh_budget_snapshots(conn, budget_id, department)


def reserve_demand_allocations(conn, demand_id: int, actor: str) -> dict:
    """财务审批通过时真实占用每一条费用分摊对应的预算。"""
    demand = conn.execute("SELECT id,demand_no,estimated_amount,budget_amount FROM demands WHERE id=?", (demand_id,)).fetchone()
    if not demand:
        raise BusinessError(404, "REQ-4040", "需求不存在")
    rows = list(conn.execute("SELECT * FROM allocations WHERE demand_id=? ORDER BY id", (demand_id,)))
    if not rows:
        raise BusinessError(422, "BUD-4221", "尚未配置费用分摊，财务无法通过")
    function_point_allocation_coverage(conn, demand_id, require_complete=True)

    pending = [row for row in rows if row["ledger_status"] != "已占用"]
    if not pending:
        return {"reserved": 0.0, "rows": 0, "idempotent": True}

    required = defaultdict(float)
    resolved = []
    for row in pending:
        budget_id = row["budget_id"]
        budget = conn.execute("SELECT * FROM budgets WHERE id=?", (budget_id,)).fetchone() if budget_id else None
        if not budget:
            budget = conn.execute("SELECT * FROM budgets WHERE budget_name=?", (row["expense_source"],)).fetchone()
        if not budget:
            raise BusinessError(422, "BUD-4220", f"费用出处“{row['expense_source']}”未找到对应预算")
        amount = round(float(row["amount"] or 0), 2)
        required[int(budget["id"])] += amount
        resolved.append((row, budget, amount))

    for budget_id, amount in required.items():
        budget = conn.execute("SELECT * FROM budgets WHERE id=?", (budget_id,)).fetchone()
        remaining = float(budget["total_budget"] or 0) - float(budget["used_budget"] or 0)
        if amount > remaining + 0.01:
            raise BusinessError(422, "BUD-4220", f"预算不足：{budget['budget_name']} 需占用{amount:.2f}，可用{remaining:.2f}")

    reference_id = demand["demand_no"] or f"DRAFT-{demand_id}"
    total_reserved = 0.0
    for row, budget, amount in resolved:
        _post_budget_transaction(
            conn, budget_id=int(budget["id"]), txn_type="占用", amount=amount,
            reference_type="需求分摊", reference_id=reference_id,
            description=f"需求费用分摊：{row['expense_subject']} / {row['system_name'] or '未归属系统'}",
            department=row["department"], event_key=f"allocation:{row['id']}:reserve",
            allocation_id=int(row["id"]), actor=actor,
        )
        conn.execute(
            "UPDATE allocations SET budget_id=?,ledger_status='已占用',occupied_at=?,released_at=NULL WHERE id=?",
            (budget["id"], now_iso(), row["id"]),
        )
        total_reserved += amount
    return {"reserved": round(total_reserved, 2), "rows": len(resolved), "idempotent": False}


def release_demand_allocations(conn, demand_id: int, actor: str, reason: str) -> dict:
    rows = list(conn.execute(
        "SELECT * FROM allocations WHERE demand_id=? AND ledger_status='已占用' ORDER BY id", (demand_id,)
    ))
    demand = conn.execute("SELECT demand_no FROM demands WHERE id=?", (demand_id,)).fetchone()
    reference_id = (demand["demand_no"] if demand else "") or f"DRAFT-{demand_id}"
    released = 0.0
    for row in rows:
        if not row["budget_id"]:
            continue
        _post_budget_transaction(
            conn, budget_id=int(row["budget_id"]), txn_type="释放", amount=float(row["amount"] or 0),
            reference_type="需求分摊", reference_id=reference_id,
            description=reason, department=row["department"], event_key=f"allocation:{row['id']}:release",
            allocation_id=int(row["id"]), actor=actor,
        )
        conn.execute("UPDATE allocations SET ledger_status='已释放',released_at=? WHERE id=?", (now_iso(), row["id"]))
        released += float(row["amount"] or 0)
    return {"released": round(released, 2), "rows": len(rows)}


def recalculate_approved_work_hours(conn, demand_id: int) -> float:
    value = conn.execute(
        "SELECT COALESCE(SUM(hours),0) v FROM demand_work_logs WHERE demand_id=? AND approval_status='已通过'",
        (demand_id,),
    ).fetchone()["v"]
    actual = round(float(value or 0), 2)
    conn.execute(
        "UPDATE demands SET actual_hours=?,work_hour_source='审批工时',actual_hours_source='审批工时',updated_at=? WHERE id=?",
        (actual, now_iso(), demand_id),
    )
    return actual
