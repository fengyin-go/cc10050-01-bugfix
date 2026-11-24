"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class PriceAlertService(BaseModuleService):
    MODULE = "price_alert"
    ENTITY = "告警规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则名称", "监控商品", "价差阈值", "连续次数", "通知渠道"]
    REQUIRED_FIELDS = ["规则名称", "监控商品", "价差阈值"]
    STATUS_ORDER = ["待生效", "已生效", "已关闭"]
    ACTION_RULES = {"启用规则": "已生效", "关闭规则": "已关闭"}
    NEGATIVE_ACTIONS = []
