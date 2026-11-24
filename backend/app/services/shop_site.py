"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class ShopSiteService(BaseModuleService):
    MODULE = "shop_site"
    ENTITY = "站点配置"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["站点名称", "站点域名", "采集区域", "语言标识", "生效货币"]
    REQUIRED_FIELDS = ["站点名称", "站点域名", "采集区域"]
    STATUS_ORDER = ["待验证", "已验证", "已封禁"]
    ACTION_RULES = {"验证站点": "已验证", "封禁站点": "已封禁"}
    NEGATIVE_ACTIONS = []
