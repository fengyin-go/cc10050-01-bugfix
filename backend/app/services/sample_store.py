"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class SampleStoreService(BaseModuleService):
    MODULE = "sample_store"
    ENTITY = "页面样本"
    OPERATION_TERM = "抓取"
    LIST_FIELDS = ["样本批次", "站点名称", "原始HTML摘要", "抓取时间", "样本指纹"]
    REQUIRED_FIELDS = ["样本批次", "站点名称", "原始HTML摘要"]
    STATUS_ORDER = ["已保存", "已解析", "已过期"]
    ACTION_RULES = {"标记解析": "已解析", "标记过期": "已过期"}
    NEGATIVE_ACTIONS = []
