"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class PriceBaselineService(BaseModuleService):
    MODULE = "price_baseline"
    ENTITY = "价格基线"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["商品标识", "归一规格", "基线价格", "采样窗口", "更新时间"]
    REQUIRED_FIELDS = ["商品标识", "归一规格", "基线价格"]
    STATUS_ORDER = ["计算中", "已生效", "待重算"]
    ACTION_RULES = {"生效基线": "已生效", "重算基线": "待重算"}
    NEGATIVE_ACTIONS = []
