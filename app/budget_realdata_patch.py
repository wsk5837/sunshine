from __future__ import annotations

from .db import connect, now_iso


POSITIVE_TYPES = {"占用", "支出"}
NEGATIVE_TYPES = {"释放", "冲销"}


def ledger_used_amount(conn, budget_id: int) -> float:
    """Return the net budget usage from actual ledger transactions only."""
    used = 0.0
    rows = conn.execute(
        "SELECT txn_type,amount FROM budget_transactions WHERE budget_id=? ORDER BY id",
        (budget_id,),
    )
    for row in rows:
        amount = float(row["amount"] or 0)
        if row["txn_type"] in POSITIVE_TYPES:
            used += amount
        elif row["txn_type"] in NEGATIVE_TYPES:
            used -= amount
    return round(max(0.0, used), 2)


def reconcile_budget_execution_from_ledger() -> dict:
    """Remove demo used-budget baselines and rebuild execution from real ledger data.

    Historical seed values such as 3.2m/1.02m/620k were written directly into
    budgets.used_budget and had no matching budget_transactions. This function
    makes budget_transactions the single source of truth for execution amounts.
    """
    changed = 0
    with connect() as conn:
        budgets = list(conn.execute("SELECT id,used_budget,internal_used,digital_used FROM budgets ORDER BY id"))
        for budget in budgets:
            real_used = ledger_used_amount(conn, int(budget["id"]))
            old_used = round(float(budget["used_budget"] or 0), 2)
            old_internal = round(float(budget["internal_used"] or 0), 2)
            old_digital = round(float(budget["digital_used"] or 0), 2)

            # internal_used / digital_used in the original seed are also demo
            # numbers. There is currently no reliable real ledger dimension that
            # can split a transaction into these two buckets, so do not fabricate
            # a split: reset them until an actual categorized transaction source exists.
            if old_used != real_used or old_internal != 0 or old_digital != 0:
                conn.execute(
                    "UPDATE budgets SET used_budget=?,internal_used=0,digital_used=0 WHERE id=?",
                    (real_used, budget["id"]),
                )
                changed += 1

        # Keep current-period snapshots honest as well. Seeded historical trend
        # rows are removed because they were generated from demo used-budget values.
        conn.execute("DELETE FROM budget_execution_snapshots")
        from datetime import datetime
        now = datetime.now()
        month = now.strftime("%Y-%m")
        quarter = f"{now.year}Q{(now.month - 1) // 3 + 1}"
        for budget in conn.execute("SELECT id,total_budget,used_budget FROM budgets ORDER BY id"):
            for period_type, period in (("month", month), ("quarter", quarter)):
                conn.execute(
                    """INSERT INTO budget_execution_snapshots
                       (budget_id,department,period_type,period,used_amount,total_budget,recorded_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        budget["id"],
                        "真实流水汇总",
                        period_type,
                        period,
                        float(budget["used_budget"] or 0),
                        float(budget["total_budget"] or 0),
                        now_iso(),
                    ),
                )
    return {"budgets_reconciled": changed}
