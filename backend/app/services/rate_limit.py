"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class RateLimitService(BaseModuleService):
    MODULE = "rate_limit"
    ENTITY = "频控规则"
    OPERATION_TERM = "抓取"
    LIST_FIELDS = ["规则名称", "目标站点", "每分钟上限", "并发上限", "退避策略"]
    REQUIRED_FIELDS = ["规则名称", "目标站点", "每分钟上限"]
    STATUS_ORDER = ["待生效", "已生效", "已暂停"]
    ACTION_RULES = {"启用频控": "已生效", "暂停频控": "已暂停"}
    NEGATIVE_ACTIONS = []
