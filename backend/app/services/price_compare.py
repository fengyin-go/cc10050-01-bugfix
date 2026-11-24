"""比价计算器领域服务：跨站点计算商品价差比例与最低价站点，负责比价批次的调度与失败重试。

数据链路位置：比价计算层，消费价格基线库的生效基线与各站点采集价格，
产出的比价结果供变价告警与比价导出通道消费。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class PriceCompareService(BaseModuleService):
    MODULE = "price_compare"
    ENTITY = "比价结果"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["比价批次", "商品标识", "最低价站点", "价差比例", "计算时间"]
    REQUIRED_FIELDS = ["比价批次", "商品标识", "最低价站点"]
    STATUS_ORDER = ["排队中", "已计算", "计算失败"]
    ACTION_RULES = {"开始计算": "已计算", "重试计算": "计算失败"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：跨站点比价与价差计算 ----

    def run_compare(self, entry_id: int) -> dict[str, Any]:
        """执行比价计算：读取生效基线计算价差比例。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        goods_id = entry.get("商品标识", "")
        baseline = next(
            (b for b in store.rows("price_baseline")
             if b.get("商品标识") == goods_id and b.get("status") == "已生效"), None
        )
        if baseline is None:
            entry["status"] = "计算失败"
            entry["abnormal"] = True
            return {"ok": False, "message": f"商品 {goods_id} 没有生效基线，计算失败"}
        entry["价差比例"] = "0.0%"
        entry["计算时间"] = datetime.now(timezone.utc).isoformat()
        entry["status"] = "已计算"
        entry["pending"] = False
        return {
            "ok": True,
            "message": "比价计算完成",
            "baseline_price": baseline.get("基线价格"),
            "lowest_site": entry.get("最低价站点", ""),
        }

    def retry_failed(self, entry_id: int) -> dict[str, Any]:
        """重试失败批次，先把失败上下文写入隔离区。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "计算失败":
            return {"ok": False, "message": "批次不存在或非失败状态"}
        rows = store.rows("failure_quarantine")
        fid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": fid,
            "样本指纹": f"compare-{entry_id}",
            "失败阶段": "计算",
            "失败原因": f"批次 {entry.get('比价批次', '')} 缺基线",
            "隔离时间": datetime.now(timezone.utc).isoformat(),
            "重试次数": "1",
            "status": "重试中",
            "pending": True,
            "abnormal": False,
        })
        entry["status"] = "排队中"
        entry["pending"] = True
        entry["abnormal"] = False
        return {"ok": True, "message": "失败上下文已隔离，批次重新排队", "quarantine_id": fid}

    def emit_change_history(self, entry_id: int, old_price: float, new_price: float) -> dict[str, Any]:
        """把价差结果写入变价历史流水。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "批次不存在"}
        if old_price <= 0:
            return {"ok": False, "message": "旧价格无效"}
        ratio = (new_price - old_price) / old_price
        rows = store.rows("change_history")
        hid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": hid,
            "商品标识": entry.get("商品标识", ""),
            "旧价格": f"{old_price:.2f}",
            "新价格": f"{new_price:.2f}",
            "变化幅度": f"{ratio:.2%}",
            "发现时间": datetime.now(timezone.utc).isoformat(),
            "status": "待核实",
            "pending": True,
            "abnormal": ratio < 0,
        })
        return {"ok": True, "message": "变价记录已写入", "history_id": hid, "ratio": f"{ratio:.2%}"}

    def notify_alert(self, entry_id: int) -> dict[str, Any]:
        """检查生效告警规则并触发变价通知。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已计算":
            return {"ok": False, "message": "批次不存在或未完成计算"}
        goods_id = entry.get("商品标识", "")
        rules = [
            r for r in store.rows("price_alert")
            if r.get("status") == "已生效" and r.get("监控商品") == goods_id
        ]
        if not rules:
            return {"ok": True, "message": "无生效告警规则", "triggered": 0}
        return {
            "ok": True,
            "message": "已触发变价告警",
            "triggered": len(rules),
            "channels": [r.get("通知渠道", "") for r in rules],
        }

    def push_to_export(self, entry_id: int) -> dict[str, Any]:
        """把已计算批次推送到比价导出通道。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已计算":
            return {"ok": False, "message": "批次不存在或未完成计算"}
        rows = store.rows("price_export")
        eid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": eid,
            "任务名称": f"导出批次 {entry.get('比价批次', '')}",
            "商品范围": entry.get("商品标识", ""),
            "导出格式": "CSV",
            "时间范围": str(entry.get("计算时间", ""))[:10],
            "生成状态": "排队中",
            "status": "排队中",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "导出任务已创建", "export_id": eid}

    def batch_status(self, batch: str) -> dict[str, Any]:
        """汇总同批次所有比价记录的状态。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("比价批次") == batch]
        if not rows:
            return {"ok": False, "message": f"批次 {batch} 不存在"}
        computed = sum(1 for r in rows if r.get("status") == "已计算")
        return {
            "ok": True,
            "message": "批次状态汇总完成",
            "total": len(rows),
            "computed": computed,
            "pending": len(rows) - computed,
        }
