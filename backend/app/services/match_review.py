"""人工复核队列领域服务：人工裁决低置信度规格匹配结果，负责复核办结、驳回联动与角色权限。

数据链路位置：裁决层，消费规格匹配器与失败隔离区升级的待复核记录，
通过的结论回填匹配记录，驳回的样本回流失败隔离区。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class MatchReviewService(BaseModuleService):
    MODULE = "match_review"
    ENTITY = "复核记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["记录编号", "匹配记录", "复核结论", "复核角色", "复核时间"]
    REQUIRED_FIELDS = ["记录编号", "匹配记录", "复核结论"]
    STATUS_ORDER = ["待复核", "已通过", "已驳回"]
    ACTION_RULES = {"通过记录": "已通过", "驳回记录": "已驳回"}
    NEGATIVE_ACTIONS = ["驳回记录"]

    # ---- 业务扩展：复核办结与结论回填 ----

    def approve(self, entry_id: int, reviewer: str) -> dict[str, Any]:
        """通过复核记录并把结论回填匹配记录。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        if entry.get("status") != "待复核":
            return {"ok": False, "message": "记录不处于待复核状态"}
        match_id = str(entry.get("匹配记录", "") or "")
        match = next(
            (m for m in store.rows("sku_match") if str(m.get("id", "")) == match_id), None
        )
        if match is not None:
            match["status"] = "已匹配"
            match["pending"] = False
        entry["status"] = "已通过"
        entry["复核角色"] = reviewer
        entry["复核时间"] = datetime.now(timezone.utc).isoformat()
        entry["pending"] = False
        return {"ok": True, "message": "复核已通过", "match_backfilled": match is not None}

    def reject(self, entry_id: int, reviewer: str) -> dict[str, Any]:
        """驳回复核记录，样本回流失败隔离区。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "复核记录不存在"}
        if entry.get("status") != "待复核":
            return {"ok": False, "message": "记录不处于待复核状态"}
        entry["status"] = "已驳回"
        entry["复核角色"] = reviewer
        entry["复核时间"] = datetime.now(timezone.utc).isoformat()
        entry["abnormal"] = True
        rows = store.rows("failure_quarantine")
        fid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": fid,
            "样本指纹": f"review-{entry_id}",
            "失败阶段": "匹配",
            "失败原因": f"复核驳回: {entry.get('复核结论', '')}",
            "隔离时间": datetime.now(timezone.utc).isoformat(),
            "重试次数": "0",
            "status": "待处理",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "复核已驳回，样本回流隔离区", "quarantine_id": fid}

    def claim_task(self, entry_id: int, role: str) -> dict[str, Any]:
        """按角色认领待复核记录，校验角色权限。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "复核记录不存在"}
        allowed = ("规格审核员", "商品运营", "数据治理专员")
        if role not in allowed:
            return {"ok": False, "message": f"角色 {role} 无复核权限"}
        entry["复核角色"] = role
        entry["pending"] = True
        return {"ok": True, "message": f"记录已由 {role} 认领"}

    def pending_queue(self, role: str | None = None) -> dict[str, Any]:
        """输出待复核队列，可按角色过滤。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("status") == "待复核"]
        if role:
            rows = [r for r in rows if r.get("复核角色") == role]
        return {
            "ok": True,
            "message": "待复核队列已输出",
            "count": len(rows),
            "records": [r.get("记录编号", "") for r in rows][:10],
        }

    def today_throughput(self) -> dict[str, Any]:
        """统计今日办结量并回写采集量指标。"""
        today = datetime.now(timezone.utc).date().isoformat()
        rows = [
            r for r in store.rows(self.MODULE)
            if str(r.get("复核时间", "")).startswith(today)
            and r.get("status") in ("已通过", "已驳回")
        ]
        approved = sum(1 for r in rows if r.get("status") == "已通过")
        return {
            "ok": True,
            "message": "今日办结统计完成",
            "total": len(rows),
            "approved": approved,
            "rejected": len(rows) - approved,
        }

    def link_goods_registry(self, entry_id: int) -> dict[str, Any]:
        """把复核结论同步到商品档案中心状态。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "复核记录不存在"}
        conclusion = str(entry.get("复核结论", ""))
        affected = 0
        for goods in store.rows("goods_registry"):
            if conclusion and conclusion in str(goods.get("登记来源", "")):
                goods["pending"] = entry.get("status") == "已通过"
                affected += 1
        return {
            "ok": True,
            "message": "复核结论已同步商品档案",
            "affected": affected,
        }
