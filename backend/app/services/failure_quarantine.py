"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class FailureQuarantineService(BaseModuleService):
    MODULE = "failure_quarantine"
    ENTITY = "隔离记录"
    OPERATION_TERM = "解析"
    LIST_FIELDS = ["样本指纹", "失败阶段", "失败原因", "隔离时间", "重试次数"]
    REQUIRED_FIELDS = ["样本指纹", "失败阶段", "失败原因"]
    STATUS_ORDER = ["待处理", "重试中", "已丢弃", "已恢复"]
    ACTION_RULES = {"立即重试": "待处理", "丢弃样本": "已丢弃"}
    NEGATIVE_ACTIONS = []
