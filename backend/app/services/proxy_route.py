"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class ProxyRouteService(BaseModuleService):
    MODULE = "proxy_route"
    ENTITY = "代理线路"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["线路标识", "线路区域", "可用性评分", "最近成功", "连续失败"]
    REQUIRED_FIELDS = ["线路标识", "线路区域", "可用性评分"]
    STATUS_ORDER = ["可用", "降级", "不可用"]
    ACTION_RULES = {"启用线路": "可用", "禁用线路": "不可用"}
    NEGATIVE_ACTIONS = []
