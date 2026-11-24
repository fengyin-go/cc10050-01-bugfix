"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class SpecNormalizerService(BaseModuleService):
    MODULE = "spec_normalizer"
    ENTITY = "规格归一规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则名称", "源规格文本", "归一规格", "单位换算", "映射优先级"]
    REQUIRED_FIELDS = ["规则名称", "源规格文本", "归一规格"]
    STATUS_ORDER = ["草稿", "已启用", "已停用"]
    ACTION_RULES = {"启用规则": "已启用", "停用规则": "已停用"}
    NEGATIVE_ACTIONS = ["停用规则"]
