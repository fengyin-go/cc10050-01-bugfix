"""货币换算器领域服务：管理按日期汇率快照的多币种价格口径统一，负责快照生效与过期治理。

数据链路位置：口径统一层，消费站点目录声明的生效货币，为比价计算提供
汇率换算依据，产出的统一币种价格供基线与比价使用。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class CurrencyConvService(BaseModuleService):
    MODULE = "currency_conv"
    ENTITY = "汇率快照"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["快照日期", "源货币", "目标货币", "汇率值", "取数来源"]
    REQUIRED_FIELDS = ["快照日期", "源货币", "目标货币"]
    STATUS_ORDER = ["待生效", "已生效", "已过期"]
    ACTION_RULES = {"生效快照": "已生效", "过期快照": "已过期"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：汇率快照与换算口径 ----

    def activate_snapshot(self, entry_id: int) -> dict[str, Any]:
        """生效汇率快照，同币对旧快照自动过期。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        rate = str(entry.get("汇率值", "") or "")
        try:
            value = float(rate)
        except ValueError:
            return {"ok": False, "message": "汇率值无效，不能生效"}
        if value <= 0:
            return {"ok": False, "message": "汇率值必须为正数"}
        expired = 0
        for row in store.rows(self.MODULE):
            if row.get("id") != entry.get("id") and row.get("status") == "已生效":
                if (row.get("源货币"), row.get("目标货币")) == (
                    entry.get("源货币"), entry.get("目标货币")
                ):
                    row["status"] = "已过期"
                    row["pending"] = False
                    expired += 1
        entry["status"] = "已生效"
        entry["pending"] = False
        return {"ok": True, "message": "快照已生效", "expired_old": expired}

    def convert_price(self, entry_id: int, amount: float) -> dict[str, Any]:
        """按生效快照把金额从源货币换算到目标货币。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已生效":
            return {"ok": False, "message": "快照不存在或未生效"}
        rate = float(entry.get("汇率值", "0") or 0)
        if rate <= 0:
            return {"ok": False, "message": "汇率值无效"}
        converted = round(amount * rate, 2)
        return {
            "ok": True,
            "message": "换算完成",
            "from": entry.get("源货币", ""),
            "to": entry.get("目标货币", ""),
            "amount": amount,
            "converted": converted,
            "snapshot_date": entry.get("快照日期", ""),
        }

    def effective_rate(self, source: str, target: str) -> float | None:
        """查询指定币对当前生效汇率。"""
        for row in store.rows(self.MODULE):
            if (row.get("status") == "已生效"
                    and row.get("源货币") == source and row.get("目标货币") == target):
                try:
                    return float(row.get("汇率值", "0") or 0)
                except ValueError:
                    return None
        return None

    def expire_by_date(self, before_date: str) -> dict[str, Any]:
        """按日期批量过期旧快照，并向变价历史同步口径变更。"""
        expired = 0
        for row in store.rows(self.MODULE):
            if row.get("status") == "已生效" and str(row.get("快照日期", "")) < before_date:
                row["status"] = "已过期"
                row["pending"] = False
                expired += 1
        if expired:
            hrows = store.rows("change_history")
            hid = max((int(r.get("id", 0)) for r in hrows), default=0) + 1
            hrows.append({
                "id": hid,
                "商品标识": "CURRENCY-ALL",
                "旧价格": "旧汇率快照",
                "新价格": f"已过期 {expired} 条",
                "变化幅度": "口径切换",
                "发现时间": datetime.now(timezone.utc).isoformat(),
                "status": "待核实",
                "pending": True,
                "abnormal": False,
            })
        return {"ok": True, "message": f"已过期 {expired} 条快照", "expired": expired}

    def ensure_site_currency(self, entry_id: int) -> dict[str, Any]:
        """检查站点生效货币是否具备可用快照。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "快照不存在"}
        currency = entry.get("源货币", "")
        covered = any(
            r.get("status") == "已生效"
            and (r.get("源货币") == currency or r.get("目标货币") == currency)
            for r in store.rows(self.MODULE)
        )
        if not covered:
            entry["pending"] = True
        return {
            "ok": True,
            "message": "货币覆盖检查完成",
            "currency": currency,
            "covered": covered,
        }

    def snapshot_for_export(self, entry_id: int) -> dict[str, Any]:
        """向比价导出通道提供换算口径说明。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已生效":
            return {"ok": False, "message": "快照不存在或未生效"}
        return {
            "ok": True,
            "message": "换算口径已提供",
            "rate": entry.get("汇率值", ""),
            "pair": f"{entry.get('源货币', '')}->{entry.get('目标货币', '')}",
            "source": entry.get("取数来源", ""),
            "date": entry.get("快照日期", ""),
        }
