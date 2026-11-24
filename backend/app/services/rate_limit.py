"""抓取频控领域服务：按站点维度限制抓取频率与并发，负责退避策略与限流计数。

数据链路位置：采集治理层，接收站点目录下发的默认频控，向爬取任务
提供启动前的频控准入判定，向代理线路输出限流压力。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class RateLimitService(BaseModuleService):
    MODULE = "rate_limit"
    ENTITY = "频控规则"
    OPERATION_TERM = "抓取"
    LIST_FIELDS = ["规则名称", "目标站点", "每分钟上限", "并发上限", "退避策略"]
    REQUIRED_FIELDS = ["规则名称", "目标站点", "每分钟上限"]
    STATUS_ORDER = ["待生效", "已生效", "已暂停"]
    ACTION_RULES = {"启用频控": "已生效", "暂停频控": "已暂停"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：频控准入与退避治理 ----

    def enable_limit(self, entry_id: int) -> dict[str, Any]:
        """启用频控规则，校验上限口径并检查站点有效性。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        per_minute = str(entry.get("每分钟上限", "") or "")
        concurrency = str(entry.get("并发上限", "") or "")
        if not per_minute.isdigit() or int(per_minute) <= 0:
            return {"ok": False, "message": "每分钟上限必须是正整数"}
        if not concurrency.isdigit() or int(concurrency) <= 0:
            entry["并发上限"] = "3"
        site = entry.get("目标站点", "")
        site_row = next(
            (s for s in store.rows("shop_site") if s.get("站点域名") == site), None
        )
        if site_row is None:
            return {"ok": False, "message": f"目标站点 {site} 不在站点目录"}
        entry["status"] = "已生效"
        entry["pending"] = False
        return {"ok": True, "message": f"频控规则 {entry.get('规则名称', '')} 已生效"}

    def admit_request(self, entry_id: int, current_qps: float) -> dict[str, Any]:
        """判定一次抓取请求是否放行。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已生效":
            return {"ok": False, "message": "频控规则不存在或未生效", "admitted": False}
        per_minute = int(str(entry.get("每分钟上限", "0") or 0))
        if per_minute <= 0:
            return {"ok": False, "message": "上限口径无效", "admitted": False}
        admitted = current_qps * 60 <= per_minute
        strategy = entry.get("退避策略", "固定退避")
        return {
            "ok": True,
            "message": "放行" if admitted else f"触发限流，按{strategy}退避",
            "admitted": admitted,
            "limit_per_minute": per_minute,
            "current_qps": current_qps,
        }

    def record_throttle(self, entry_id: int) -> dict[str, Any]:
        """记录一次限流并向采集量指标同步波动。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "频控规则不存在"}
        site = entry.get("目标站点", "")
        metrics = store.rows("collect_metrics")
        row = next((m for m in metrics if m.get("站点名称") == site), None)
        if row is not None:
            row["status"] = "量级波动"
            row["abnormal"] = True
        return {"ok": True, "message": "限流已记录", "site": site}

    def coordinate_with_proxy(self, entry_id: int) -> dict[str, Any]:
        """限流高压时请求代理线路切换出口。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "频控规则不存在"}
        candidates = [
            r for r in store.rows("proxy_route") if r.get("status") == "可用"
        ]
        if not candidates:
            entry["status"] = "已暂停"
            entry["pending"] = True
            return {"ok": False, "message": "无可用代理线路，频控已暂停"}
        best = max(candidates, key=lambda r: float(r.get("可用性评分", "0") or 0))
        return {
            "ok": True,
            "message": "代理出口已协调",
            "route": best.get("线路标识", ""),
            "region": best.get("线路区域", ""),
        }

    def backoff_plan(self, entry_id: int, failures: int) -> dict[str, Any]:
        """按退避策略计算下次重试等待时间。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "频控规则不存在"}
        strategy = entry.get("退避策略", "固定退避")
        if strategy == "指数退避":
            wait = min(2 ** max(failures, 0), 600)
        else:
            wait = 60
        return {
            "ok": True,
            "message": "退避计划已生成",
            "strategy": strategy,
            "wait_seconds": wait,
            "failures": failures,
        }

    def site_limit_overview(self, site: str) -> dict[str, Any]:
        """输出站点频控全貌供爬取任务启动检查。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("目标站点") == site]
        effective = [r for r in rows if r.get("status") == "已生效"]
        return {
            "ok": True,
            "message": "站点频控全貌已输出",
            "rules": len(rows),
            "effective": len(effective),
            "max_per_minute": max(
                (int(str(r.get("每分钟上限", "0") or 0)) for r in effective), default=0
            ),
        }
