"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class ChangeHistoryService(BaseModuleService):
    MODULE = "change_history"
    ENTITY = "变价记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["商品标识", "旧价格", "新价格", "变化幅度", "发现时间"]
    REQUIRED_FIELDS = ["商品标识", "旧价格", "新价格"]
    STATUS_ORDER = ["已确认", "待核实", "误报"]
    ACTION_RULES = {"确认变价": "已确认", "标记误报": "误报"}
    NEGATIVE_ACTIONS = []
