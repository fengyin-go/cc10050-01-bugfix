"""商品登记中心领域服务：聚合各平台商品为统一档案，负责重复档案的合并与疑似重复标记。

数据链路位置：主数据层，消费规格匹配器的匹配结果与各站点商品数据，
产出的统一商品档案供比价计算与价格基线引用。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class GoodsRegistryService(BaseModuleService):
    MODULE = "goods_registry"
    ENTITY = "商品档案"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["商品标识", "商品名称", "品牌", "类目", "登记来源"]
    REQUIRED_FIELDS = ["商品标识", "商品名称", "品牌"]
    STATUS_ORDER = ["待合并", "已登记", "已合并", "疑似重复"]
    ACTION_RULES = {"登记商品": "已登记", "合并档案": "已合并", "标记重复": "疑似重复"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：档案聚合与重复治理 ----

    def register_goods(self, entry_id: int) -> dict[str, Any]:
        """登记商品档案，校验品牌与名称后进入已登记状态。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        name = entry.get("商品名称", "")
        brand = entry.get("品牌", "")
        if not name or not brand:
            return {"ok": False, "message": "商品名称与品牌必须齐备"}
        duplicates = [
            g.get("商品标识", "") for g in store.rows(self.MODULE)
            if g.get("id") != entry.get("id")
            and g.get("商品名称") == name and g.get("品牌") == brand
        ]
        if duplicates:
            entry["status"] = "疑似重复"
            entry["abnormal"] = True
            return {"ok": False, "message": "检测到重复档案", "duplicates": duplicates}
        entry["status"] = "已登记"
        entry["pending"] = False
        return {"ok": True, "message": f"商品 {name} 已登记"}

    def merge_duplicates(self, entry_id: int, keep_id: int) -> dict[str, Any]:
        """把疑似重复档案合并到目标档案。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "档案不存在"}
        target = store.find(self.MODULE, keep_id)
        if target is None:
            return {"ok": False, "message": f"目标档案 {keep_id} 不存在"}
        if entry.get("商品标识") == target.get("商品标识"):
            return {"ok": False, "message": "不能合并到自身"}
        moved_specs = 0
        for match in store.rows("sku_match"):
            if match.get("商品档案") == entry.get("商品标识"):
                match["商品档案"] = target.get("商品标识", "")
                moved_specs += 1
        entry["status"] = "已合并"
        entry["pending"] = False
        return {
            "ok": True,
            "message": "档案已合并",
            "kept": target.get("商品标识", ""),
            "moved_matches": moved_specs,
        }

    def feed_from_match(self, entry_id: int) -> dict[str, Any]:
        """消费已匹配记录把站点商品回填到登记来源。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已登记":
            return {"ok": False, "message": "档案不存在或未登记"}
        goods_id = entry.get("商品标识", "")
        matched = [
            m for m in store.rows("sku_match")
            if m.get("商品档案") == goods_id and m.get("status") == "已匹配"
        ]
        sources = {str(m.get("匹配批次", "")) for m in matched}
        if sources:
            entry["登记来源"] = ",".join(sorted(sources))[:60]
        return {
            "ok": True,
            "message": "匹配来源已回填",
            "matched_count": len(matched),
            "sources": sorted(sources),
        }

    def resolve_baseline_link(self, entry_id: int) -> dict[str, Any]:
        """检查档案是否有生效价格基线。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "档案不存在"}
        goods_id = entry.get("商品标识", "")
        baselines = [
            b for b in store.rows("price_baseline")
            if b.get("商品标识") == goods_id and b.get("status") == "已生效"
        ]
        return {
            "ok": True,
            "message": "基线关联检查完成",
            "baseline_count": len(baselines),
            "has_effective": bool(baselines),
        }

    def mark_suspected(self, entry_id: int, reason: str) -> dict[str, Any]:
        """标记疑似重复并推送到人工复核队列。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "档案不存在"}
        entry["status"] = "疑似重复"
        entry["abnormal"] = True
        entry["pending"] = True
        rows = store.rows("match_review")
        rid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": rid,
            "记录编号": f"GR-{entry_id}-{rid}",
            "匹配记录": entry.get("商品标识", ""),
            "复核结论": reason,
            "复核角色": "商品运营",
            "复核时间": datetime.now(timezone.utc).isoformat(),
            "status": "待复核",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "疑似重复已标记并转人工复核", "review_id": rid}

    def registry_stats(self) -> dict[str, Any]:
        """输出档案中心统计快照供采集量指标消费。"""
        rows = store.rows(self.MODULE)
        by_status = {
            status: sum(1 for r in rows if r.get("status") == status)
            for status in self.STATUS_ORDER
        }
        return {
            "ok": True,
            "message": "档案统计完成",
            "total": len(rows),
            "by_status": by_status,
            "duplicates": by_status.get("疑似重复", 0),
        }
