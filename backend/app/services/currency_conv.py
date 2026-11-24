"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class CurrencyConvService(BaseModuleService):
    MODULE = "currency_conv"
    ENTITY = "汇率快照"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["快照日期", "源货币", "目标货币", "汇率值", "取数来源"]
    REQUIRED_FIELDS = ["快照日期", "源货币", "目标货币"]
    STATUS_ORDER = ["待生效", "已生效", "已过期"]
    ACTION_RULES = {"生效快照": "已生效", "过期快照": "已过期"}
    NEGATIVE_ACTIONS = []
