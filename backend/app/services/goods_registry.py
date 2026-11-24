"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class GoodsRegistryService(BaseModuleService):
    MODULE = "goods_registry"
    ENTITY = "商品档案"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["商品标识", "商品名称", "品牌", "类目", "登记来源"]
    REQUIRED_FIELDS = ["商品标识", "商品名称", "品牌"]
    STATUS_ORDER = ["待合并", "已登记", "已合并", "疑似重复"]
    ACTION_RULES = {"登记商品": "已登记", "合并档案": "已合并", "标记重复": "疑似重复"}
    NEGATIVE_ACTIONS = []
