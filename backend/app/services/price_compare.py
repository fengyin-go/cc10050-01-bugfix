"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class PriceCompareService(BaseModuleService):
    MODULE = "price_compare"
    ENTITY = "比价结果"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["比价批次", "商品标识", "最低价站点", "价差比例", "计算时间"]
    REQUIRED_FIELDS = ["比价批次", "商品标识", "最低价站点"]
    STATUS_ORDER = ["排队中", "已计算", "计算失败"]
    ACTION_RULES = {"开始计算": "已计算", "重试计算": "计算失败"}
    NEGATIVE_ACTIONS = []
