"""变价历史流水领域服务：记录商品价格的历次变化供追溯，负责变价确认、误报标记与口径切换沉淀。

数据链路位置：追溯沉淀层，消费比价计算器的价差结果与货币换算器的口径变更，
确认后的变价记录供告警规则与导出通道引用。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ChangeHistoryService(BaseModuleService):
    MODULE = "change_history"
    ENTITY = "变价记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["商品标识", "旧价格", "新价格", "变化幅度", "发现时间"]
    REQUIRED_FIELDS = ["商品标识", "旧价格", "新价格"]
    STATUS_ORDER = ["已确认", "待核实", "误报"]
    ACTION_RULES = {"确认变价": "已确认", "标记误报": "误报"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：变价确认与追溯 ----

    def confirm_change(self, entry_id: int) -> dict[str, Any]:
        """确认一次变价并校验价格口径有效。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        try:
            old_price = float(entry.get("旧价格", "0") or 0)
            new_price = float(entry.get("新价格", "0") or 0)
        except ValueError:
            return {"ok": False, "message": "旧价格/新价格数值无效"}
        if old_price <= 0:
            return {"ok": False, "message": "旧价格必须为正数"}
        ratio = (new_price - old_price) / old_price
        entry["变化幅度"] = f"{ratio:.2%}"
        entry["status"] = "已确认"
        entry["pending"] = False
        return {
            "ok": True,
            "message": "变价已确认",
            "ratio": f"{ratio:.2%}",
            "goods": entry.get("商品标识", ""),
        }

    def mark_false_positive(self, entry_id: int, reason: str) -> dict[str, Any]:
        """标记误报并回写告警规则联动关闭。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "变价记录不存在"}
        entry["status"] = "误报"
        entry["pending"] = False
        goods_id = entry.get("商品标识", "")
        closed = 0
        for rule in store.rows("price_alert"):
            if rule.get("监控商品") == goods_id and rule.get("status") == "已生效":
                rule["status"] = "已关闭"
                rule["pending"] = False
                closed += 1
        return {
            "ok": True,
            "message": f"误报已标记: {reason}",
            "closed_rules": closed,
        }

    def query_by_goods(self, goods_id: str, status: str | None = None) -> dict[str, Any]:
        """按商品查询变价历史并支持状态过滤。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("商品标识") == goods_id]
        if status:
            rows = [r for r in rows if r.get("status") == status]
        rows.sort(key=lambda r: str(r.get("发现时间", "")), reverse=True)
        return {
            "ok": True,
            "message": "变价历史查询完成",
            "goods": goods_id,
            "count": len(rows),
            "records": [
                {
                    "old": r.get("旧价格", ""),
                    "new": r.get("新价格", ""),
                    "ratio": r.get("变化幅度", ""),
                    "found_at": r.get("发现时间", ""),
                }
                for r in rows[:10]
            ],
        }

    def sync_currency_switch(self, goods_id: str, old_price: float, new_price: float) -> dict[str, Any]:
        """货币口径切换时重新核算变价幅度。"""
        if old_price <= 0:
            return {"ok": False, "message": "旧价格无效"}
        ratio = (new_price - old_price) / old_price
        affected = 0
        for row in store.rows(self.MODULE):
            if row.get("商品标识") == goods_id and row.get("status") == "已确认":
                row["变化幅度"] = f"{ratio:.2%}"
                row["发现时间"] = datetime.now(timezone.utc).isoformat()
                affected += 1
        return {
            "ok": True,
            "message": "口径切换已同步变价幅度",
            "affected": affected,
            "ratio": f"{ratio:.2%}",
        }

    def pending_for_alert(self, goods_id: str) -> dict[str, Any]:
        """把待核实记录交给告警规则消费。"""
        rows = [
            r for r in store.rows(self.MODULE)
            if r.get("商品标识") == goods_id and r.get("status") == "待核实"
        ]
        if not rows:
            return {"ok": False, "message": "没有待核实的变价记录", "count": 0}
        return {
            "ok": True,
            "message": "待核实记录已移交告警规则",
            "count": len(rows),
            "goods": goods_id,
        }

    def history_stats(self, days: int = 7) -> dict[str, Any]:
        """输出变价历史统计快照。"""
        rows = store.rows(self.MODULE)
        confirmed = sum(1 for r in rows if r.get("status") == "已确认")
        false_pos = sum(1 for r in rows if r.get("status") == "误报")
        return {
            "ok": True,
            "message": "变价统计完成",
            "total": len(rows),
            "confirmed": confirmed,
            "false_positive": false_pos,
            "pending": len(rows) - confirmed - false_pos,
        }
