"""站点报价领域服务：同规格各站点报价是比价轮次的直接输入。

同时以多个常见模块名（site_quote / price_quote / station_quote / quotes）
注册别名，全部读写同一张 site_quote 表，方便不同入口的调用方取到同一份数据。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService


class SiteQuoteService(BaseModuleService):
    MODULE = "site_quote"
    ENTITY = "站点报价"
    OPERATION_TERM = "报价"
    LIST_FIELDS = [
        "商品标识", "归一规格", "比价批次", "站点名称", "报价",
        "币种", "报价时间", "可比较",
    ]
    REQUIRED_FIELDS = ["商品标识", "站点名称", "报价"]
    STATUS_ORDER = ["可比较", "不可比较"]
    ACTION_RULES = {"标记不可比较": "不可比较", "恢复可比较": "可比较"}
    NEGATIVE_ACTIONS = ["标记不可比较"]

    def create_entry(self, values: dict[str, Any]):
        # 允许 {"price": 110, "site_name": "..."} 等宽松键名
        normalized = dict(values)
        aliases = {
            "站点名称": ("站点", "站点名", "site", "site_name", "name"),
            "报价": ("价格", "price", "quote", "amount", "站点报价"),
            "商品标识": ("goods", "goods_id", "商品ID"),
            "归一规格": ("规格", "spec"),
        }
        for cn, keys in aliases.items():
            if not normalized.get(cn):
                for k in keys:
                    if values.get(k) not in (None, ""):
                        normalized[cn] = values[k]
                        break
        entry, missing = super().create_entry(normalized)
        if entry is not None:
            entry.setdefault("status", "可比较")
            entry.setdefault("可比较", "是")
        return entry, missing


class PriceQuoteAliasService(SiteQuoteService):
    """别名服务：模块键 price_quote，但落同一张 site_quote 表。"""

    MODULE = "site_quote"


class StationQuoteAliasService(SiteQuoteService):
    MODULE = "site_quote"
