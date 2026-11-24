"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class PriceCrawlerService(BaseModuleService):
    MODULE = "price_crawler"
    ENTITY = "爬取任务"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["任务名称", "目标站点", "商品关键词", "抓取深度", "采集间隔"]
    REQUIRED_FIELDS = ["任务名称", "目标站点", "商品关键词"]
    STATUS_ORDER = ["草稿", "运行中", "已暂停", "采集失败"]
    ACTION_RULES = {"启动任务": "运行中", "暂停任务": "已暂停", "重试采集": "采集失败"}
    NEGATIVE_ACTIONS = []
