"""只读台账服务：比价确认轮次与基线更正版本记录。

轮次/版本由 rounds_core 在业务动作中写入，这里主要让通用资源路由
（GET /api/compare_round、/api/baseline_revision）可以直接回查。
"""
from __future__ import annotations

from app.services.base import BaseModuleService


class CompareRoundService(BaseModuleService):
    MODULE = "compare_round"
    ENTITY = "比价轮次"
    LIST_FIELDS = [
        "轮次号", "compare_id", "比价批次", "商品标识", "归一规格", "state",
        "baseline_id", "baseline_version", "基线金额",
        "lowest_site", "lowest_price", "diff_ratio", "价差比例",
        "fail_reason", "created_at", "confirmed_at",
    ]
    REQUIRED_FIELDS: list[str] = []
    STATUS_ORDER = ["待确认", "已确认", "取数冲突", "计算失败"]
    ACTION_RULES: dict[str, str] = {}


class BaselineRevisionService(BaseModuleService):
    MODULE = "baseline_revision"
    ENTITY = "基线更正记录"
    LIST_FIELDS = [
        "baseline_id", "商品标识", "归一规格", "old_price", "new_price",
        "from_version", "to_version", "更正时间", "status",
    ]
    REQUIRED_FIELDS: list[str] = []
    STATUS_ORDER = ["已更正", "更正中"]
    ACTION_RULES: dict[str, str] = {}
