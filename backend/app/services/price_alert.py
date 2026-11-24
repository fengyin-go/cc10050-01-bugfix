"""变价告警规则领域服务：管理价差阈值与连续触发口径的告警规则，负责变价通知的判定与触发。

数据链路位置：告警判定层，消费比价计算器产出的价差比例与变价历史，
触发结果写入通知渠道并沉淀到变价历史流水。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class PriceAlertService(BaseModuleService):
    MODULE = "price_alert"
    ENTITY = "告警规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则名称", "监控商品", "价差阈值", "连续次数", "通知渠道"]
    REQUIRED_FIELDS = ["规则名称", "监控商品", "价差阈值"]
    STATUS_ORDER = ["待生效", "已生效", "已关闭"]
    ACTION_RULES = {"启用规则": "已生效", "关闭规则": "已关闭"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：阈值判定与告警触发 ----

    def enable_rule(self, entry_id: int) -> dict[str, Any]:
        """启用告警规则，校验阈值与连续次数口径。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        threshold = str(entry.get("价差阈值", "") or "")
        if not threshold.endswith("%"):
            return {"ok": False, "message": "价差阈值必须以百分号结尾"}
        try:
            value = float(threshold.rstrip("%"))
        except ValueError:
            return {"ok": False, "message": "价差阈值数值无效"}
        if value <= 0:
            return {"ok": False, "message": "价差阈值必须为正数"}
        times = str(entry.get("连续次数", "") or "")
        if not times.isdigit() or int(times) < 1:
            entry["连续次数"] = "3"
        entry["status"] = "已生效"
        entry["pending"] = False
        return {"ok": True, "message": f"规则 {entry.get('规则名称', '')} 已生效"}

    def evaluate_change(self, entry_id: int, ratio: float) -> dict[str, Any]:
        """按规则阈值判定一次变价是否触发告警。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已生效":
            return {"ok": False, "message": "规则不存在或未生效"}
        threshold = float(str(entry.get("价差阈值", "0%")).rstrip("%") or 0)
        triggered = abs(ratio) * 100 >= threshold
        if triggered:
            entry["pending"] = False
        return {
            "ok": True,
            "message": "阈值判定完成",
            "triggered": triggered,
            "ratio": f"{ratio:.2%}",
            "threshold": entry.get("价差阈值", ""),
            "channel": entry.get("通知渠道", "") if triggered else "",
        }

    def consume_change_history(self, entry_id: int) -> dict[str, Any]:
        """消费待核实的变价记录，累计连续触发次数。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "规则不存在"}
        goods_id = entry.get("监控商品", "")
        pending_rows = [
            h for h in store.rows("change_history")
            if h.get("商品标识") == goods_id and h.get("status") == "待核实"
        ]
        times = int(str(entry.get("连续次数", "3") or 3))
        fired = len(pending_rows) >= times
        for row in pending_rows:
            row["status"] = "已确认" if fired else "待核实"
            row["pending"] = not fired
        return {
            "ok": True,
            "message": "变价记录消费完成",
            "pending_count": len(pending_rows),
            "alert_fired": fired,
        }

    def write_history_receipt(self, entry_id: int, ratio: float) -> dict[str, Any]:
        """把告警触发结果写回变价历史形成闭环回执。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "规则不存在"}
        rows = store.rows("change_history")
        hid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": hid,
            "商品标识": entry.get("监控商品", ""),
            "旧价格": "基线",
            "新价格": "监控价",
            "变化幅度": f"{ratio:.2%}",
            "发现时间": datetime.now(timezone.utc).isoformat(),
            "status": "已确认",
            "pending": False,
            "abnormal": False,
        })
        return {"ok": True, "message": "告警回执已写入", "history_id": hid}

    def suspend_by_site(self, entry_id: int, site: str) -> dict[str, Any]:
        """站点封禁时联动关闭该站点相关的告警规则。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "规则不存在"}
        banned = any(
            s.get("status") == "已封禁" and s.get("站点域名") == site
            for s in store.rows("shop_site")
        )
        if banned:
            entry["status"] = "已关闭"
            entry["pending"] = False
        return {
            "ok": True,
            "message": "站点封禁联动检查完成",
            "rule_closed": banned,
            "site": site,
        }

    def rule_health(self, entry_id: int) -> dict[str, Any]:
        """统计规则今日触发次数并上报采集量指标。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "规则不存在"}
        today = datetime.now(timezone.utc).date().isoformat()
        fired = sum(
            1 for h in store.rows("change_history")
            if str(h.get("发现时间", "")).startswith(today) and h.get("status") == "已确认"
        )
        return {
            "ok": True,
            "message": "规则健康度统计完成",
            "fired_today": fired,
            "status": entry.get("status"),
        }
