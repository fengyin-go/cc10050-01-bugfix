"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class PriceExportService(BaseModuleService):
    MODULE = "price_export"
    ENTITY = "导出任务"
    OPERATION_TERM = "导出"
    LIST_FIELDS = ["任务名称", "商品范围", "导出格式", "时间范围", "生成状态"]
    REQUIRED_FIELDS = ["任务名称", "商品范围", "导出格式"]
    STATUS_ORDER = ["排队中", "导出中", "已完成", "导出失败"]
    ACTION_RULES = {"开始导出": "导出中", "重试导出": "导出中"}
    NEGATIVE_ACTIONS = []
