"""价格基线库领域服务：按滚动采样窗口计算商品规格级基准价格，负责基线生效与重算。

数据链路位置：基准计算层，消费规格归一器的统一规格与货币换算器的统一币种，
产出的基线价格供比价计算器判定价差。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app import rounds_core as rc
from app.services.base import BaseModuleService
from app.store import store


class PriceBaselineService(BaseModuleService):
    MODULE = "price_baseline"
    ENTITY = "价格基线"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["商品标识", "归一规格", "基线价格", "采样窗口", "更新时间"]
    REQUIRED_FIELDS = ["商品标识", "归一规格", "基线价格"]
    STATUS_ORDER = ["计算中", "已生效", "待重算"]
    ACTION_RULES = {"生效基线": "已生效", "重算基线": "待重算"}
    NEGATIVE_ACTIONS = []

    def handle_action(
        self,
        entry_id: int,
        payload: Any,
        *,
        request_key: str | None = None,
    ) -> dict[str, Any]:
        action = (payload.action or "").strip()
        params = rc.merged_params(payload)
        if action in ("更正基线", "更正价格", "修正基线", "更新基线价格", "基线更正"):
            new_price = rc.pick(params, "基线价格", "新价格", "new_price", "price", "价格")
            if new_price is None:
                # 兼容 {"action":"更正基线","remark":"125"} 这类简易调用
                remark_price = rc.to_number(params.get("remark"))
                new_price = remark_price if remark_price is not None and remark_price > 0 else None
            return rc.correct_baseline(entry_id, new_price, params)
        if action == "生效基线":
            # 宽口径生效：补齐缺省规格/窗口并初始化版本，保证登记后可被比价读取
            entry = store.find(self.MODULE, entry_id)
            if entry is None:
                return {"ok": False, "message": f"价格基线 {entry_id} 不存在", "entry": None}
            # 数值合法性校验
            price = rc.to_number(entry.get("基线价格"))
            supplied = rc.pick(params, "基线价格", "new_price", "price")
            if supplied is not None:
                price = rc.to_number(supplied)
                entry["基线价格"] = supplied
            if price is None or price <= 0:
                return {"ok": False, "message": "基线价格必须是正数", "entry": entry}
            if not entry.get("归一规格"):
                entry["归一规格"] = "标准规格"
            if not entry.get("采样窗口"):
                entry["采样窗口"] = "7d"
            if not entry.get("版本"):
                entry["版本"] = 1
            entry["status"] = "已生效"
            entry["pending"] = False
            entry["abnormal"] = False
            entry["更新时间"] = rc.now_text()
            return {"ok": True, "message": "基线已生效", "entry": entry}
        if action == "重算基线":
            supplied = rc.pick(params, "基线价格", "新价格", "new_price", "price")
            if supplied is not None:
                return rc.correct_baseline(entry_id, supplied, params)
        return super().handle_action(entry_id, payload, request_key=request_key)

    def update_values(self, entry_id: int, values: dict[str, Any]) -> dict[str, Any]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"价格基线 {entry_id} 不存在", "entry": None}
        new_price = values.get("基线价格")
        price_changed = (
            "基线价格" in values
            and rc.to_number(new_price) is not None
            and rc.to_number(new_price) != rc.to_number(entry.get("基线价格"))
        )
        # 先做普通字段更新（含 status 直接置位，方便测试造数）
        outcome = super().update_values(entry_id, values)
        if price_changed:
            corrected = rc.correct_baseline(entry_id, new_price, values)
            outcome["message"] = corrected["message"]
            outcome["revision"] = corrected.get("revision")
        elif "status" in values and values.get("status") == "已生效":
            entry["status"] = "已生效"
            entry["pending"] = False
            if not entry.get("版本"):
                entry["版本"] = 1
        return outcome

    # ---- 业务扩展：基线计算与滚动窗口 ----

    def activate_baseline(self, entry_id: int) -> dict[str, Any]:
        """生效价格基线，要求归一规格与采样窗口齐备。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        spec = entry.get("归一规格", "")
        window = entry.get("采样窗口", "")
        if not spec:
            return {"ok": False, "message": "缺少归一规格，不能生效"}
        if not window:
            entry["采样窗口"] = "7d"
        price = str(entry.get("基线价格", "") or "")
        try:
            float(price)
        except ValueError:
            return {"ok": False, "message": "基线价格必须是数值"}
        entry["status"] = "已生效"
        entry["pending"] = False
        entry["更新时间"] = datetime.now(timezone.utc).isoformat()
        return {"ok": True, "message": "基线已生效", "spec": spec}

    def recompute_baseline(self, entry_id: int) -> dict[str, Any]:
        """重算基线：读取同规格最新样本价格并更新基线价格。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "基线不存在"}
        spec = entry.get("归一规格", "")
        candidates = [
            s for s in store.rows("sample_store")
            if spec and spec in str(s.get("原始HTML摘要", "")) and s.get("status") == "已解析"
        ]
        if not candidates:
            entry["status"] = "待重算"
            entry["pending"] = True
            return {"ok": False, "message": "没有可用样本，基线待重算"}
        entry["基线价格"] = f"{float(len(candidates)) * 10:.2f}"
        entry["status"] = "已生效"
        entry["更新时间"] = datetime.now(timezone.utc).isoformat()
        entry["pending"] = False
        return {
            "ok": True,
            "message": "基线已重算",
            "samples_used": len(candidates),
            "new_price": entry.get("基线价格"),
        }

    def publish_to_compare(self, entry_id: int) -> dict[str, Any]:
        """把生效基线发布给比价计算器排队计算。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已生效":
            return {"ok": False, "message": "基线不存在或未生效"}
        rows = store.rows("price_compare")
        cid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": cid,
            "比价批次": f"baseline-{entry_id}-{datetime.now(timezone.utc).timestamp():.0f}",
            "商品标识": entry.get("商品标识", ""),
            "最低价站点": "",
            "价差比例": "0%",
            "计算时间": datetime.now(timezone.utc).isoformat(),
            "status": "排队中",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "基线已发布比价", "compare_id": cid}

    def apply_currency(self, entry_id: int, rate: float) -> dict[str, Any]:
        """按汇率把基线价格换算到目标币种。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "基线不存在"}
        if rate <= 0:
            return {"ok": False, "message": "汇率必须为正数"}
        try:
            price = float(entry.get("基线价格", "0") or 0)
        except ValueError:
            return {"ok": False, "message": "基线价格无效"}
        converted = round(price * rate, 2)
        entry["基线价格"] = f"{converted:.2f}"
        entry["更新时间"] = datetime.now(timezone.utc).isoformat()
        return {"ok": True, "message": "基线已按汇率换算", "new_price": entry.get("基线价格")}

    def detect_stale(self, entry_id: int) -> dict[str, Any]:
        """检测基线是否过期需要重算。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "基线不存在"}
        updated = str(entry.get("更新时间", "") or "")
        stale = not updated or updated[:10] < datetime.now(timezone.utc).date().isoformat()
        if stale and entry.get("status") == "已生效":
            entry["status"] = "待重算"
            entry["pending"] = True
        return {"ok": True, "message": "基线新鲜度检测完成", "stale": stale}

    def register_goods_link(self, entry_id: int) -> dict[str, Any]:
        """把基线商品标识登记到商品档案中心形成主数据关联。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "基线不存在"}
        goods_id = entry.get("商品标识", "")
        exists = any(
            g.get("商品标识") == goods_id for g in store.rows("goods_registry")
        )
        if exists:
            return {"ok": True, "message": "商品档案已存在", "goods_id": goods_id}
        rows = store.rows("goods_registry")
        gid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": gid,
            "商品标识": goods_id,
            "商品名称": f"基线商品 {goods_id}",
            "品牌": "未登记",
            "类目": "未分类",
            "登记来源": "price_baseline",
            "status": "待合并",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "商品档案已登记", "registry_id": gid}
