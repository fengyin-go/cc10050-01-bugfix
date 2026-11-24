"""代理线路池领域服务：按区域与可用性评分管理采集出口线路，负责线路健康度降级与熔断恢复。

数据链路位置：出口治理层，向爬取任务与频控规则提供可用线路选择，
连续失败时线路降级并把故障写入失败隔离区。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ProxyRouteService(BaseModuleService):
    MODULE = "proxy_route"
    ENTITY = "代理线路"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["线路标识", "线路区域", "可用性评分", "最近成功", "连续失败"]
    REQUIRED_FIELDS = ["线路标识", "线路区域", "可用性评分"]
    STATUS_ORDER = ["可用", "降级", "不可用"]
    ACTION_RULES = {"启用线路": "可用", "禁用线路": "不可用"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：线路选择与熔断治理 ----

    def enable_route(self, entry_id: int) -> dict[str, Any]:
        """启用代理线路，要求评分达到可用门槛。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        try:
            score = float(entry.get("可用性评分", "0") or 0)
        except ValueError:
            return {"ok": False, "message": "可用性评分无效"}
        if score < 60:
            return {"ok": False, "message": f"评分 {score} 低于可用门槛 60"}
        entry["status"] = "可用"
        entry["pending"] = False
        entry["连续失败"] = "0"
        return {"ok": True, "message": f"线路 {entry.get('线路标识', '')} 已启用"}

    def pick_route(self, region: str | None = None) -> dict[str, Any]:
        """按区域与评分挑选当前最优线路。"""
        candidates = [r for r in store.rows(self.MODULE) if r.get("status") in ("可用", "降级")]
        if region:
            candidates = [r for r in candidates if region in str(r.get("线路区域", ""))]
        if not candidates:
            return {"ok": False, "message": "没有可用线路"}
        best = max(candidates, key=lambda r: float(r.get("可用性评分", "0") or 0))
        return {
            "ok": True,
            "message": "线路已选择",
            "route_id": best.get("id"),
            "route": best.get("线路标识", ""),
            "region": best.get("线路区域", ""),
            "score": best.get("可用性评分", ""),
        }

    def report_failure(self, entry_id: int) -> dict[str, Any]:
        """记录一次线路失败，连续失败达到阈值自动降级。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "线路不存在"}
        fails = int(str(entry.get("连续失败", "0") or 0)) + 1
        entry["连续失败"] = str(fails)
        if fails >= 3 and entry.get("status") == "可用":
            entry["status"] = "降级"
            entry["abnormal"] = True
        if fails >= 5:
            entry["status"] = "不可用"
            entry["pending"] = False
        return {
            "ok": True,
            "message": "失败已记录",
            "consecutive_failures": fails,
            "status": entry.get("status"),
        }

    def report_success(self, entry_id: int) -> dict[str, Any]:
        """记录一次线路成功并恢复评分。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "线路不存在"}
        entry["连续失败"] = "0"
        entry["最近成功"] = datetime.now(timezone.utc).isoformat()
        if entry.get("status") == "降级":
            entry["status"] = "可用"
            entry["abnormal"] = False
        return {"ok": True, "message": "成功已记录", "status": entry.get("status")}

    def quarantine_dead_route(self, entry_id: int) -> dict[str, Any]:
        """不可用线路写入失败隔离区供事后追查。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "不可用":
            return {"ok": False, "message": "线路不存在或未熔断"}
        rows = store.rows("failure_quarantine")
        fid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": fid,
            "样本指纹": f"route-{entry_id}",
            "失败阶段": "出口",
            "失败原因": f"线路 {entry.get('线路标识', '')} 连续失败熔断",
            "隔离时间": datetime.now(timezone.utc).isoformat(),
            "重试次数": entry.get("连续失败", "0"),
            "status": "已丢弃",
            "pending": False,
            "abnormal": False,
        })
        return {"ok": True, "message": "熔断线路已归档", "quarantine_id": fid}

    def region_capacity(self, region: str) -> dict[str, Any]:
        """统计区域可用线路容量供频控协调。"""
        rows = [r for r in store.rows(self.MODULE) if region in str(r.get("线路区域", ""))]
        usable = [r for r in rows if r.get("status") == "可用"]
        return {
            "ok": True,
            "message": "区域容量已统计",
            "region": region,
            "total": len(rows),
            "usable": len(usable),
            "avg_score": round(
                sum(float(r.get("可用性评分", "0") or 0) for r in usable) / len(usable), 1
            ) if usable else 0.0,
        }
