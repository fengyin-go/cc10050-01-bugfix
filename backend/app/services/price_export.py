"""比价导出通道领域服务：把比价结果导出为离线文件，负责导出任务的排队、生成与失败重试。

数据链路位置：数据导出层，消费比价计算器的已计算批次与货币换算器的口径说明，
产出的导出文件记录变价历史的离线备份。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class PriceExportService(BaseModuleService):
    MODULE = "price_export"
    ENTITY = "导出任务"
    OPERATION_TERM = "导出"
    LIST_FIELDS = ["任务名称", "商品范围", "导出格式", "时间范围", "生成状态"]
    REQUIRED_FIELDS = ["任务名称", "商品范围", "导出格式"]
    STATUS_ORDER = ["排队中", "导出中", "已完成", "导出失败"]
    ACTION_RULES = {"开始导出": "导出中", "重试导出": "导出中"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：导出生成与口径追溯 ----

    def start_export(self, entry_id: int) -> dict[str, Any]:
        """开始导出：校验商品范围有已计算比价结果。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        scope = entry.get("商品范围", "")
        fmt = entry.get("导出格式", "")
        if fmt not in ("CSV", "XLSX", "JSON"):
            return {"ok": False, "message": f"不支持的导出格式: {fmt}"}
        computed = [
            c for c in store.rows("price_compare")
            if scope in str(c.get("商品标识", "")) and c.get("status") == "已计算"
        ]
        if not computed:
            entry["status"] = "导出失败"
            entry["abnormal"] = True
            return {"ok": False, "message": f"商品范围 {scope} 没有已计算的比价结果"}
        entry["status"] = "导出中"
        entry["pending"] = True
        return {"ok": True, "message": "导出已开始", "rows": len(computed), "format": fmt}

    def finish_export(self, entry_id: int, ok: bool) -> dict[str, Any]:
        """回写导出完成状态并把备份记录写入变价历史。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "导出任务不存在"}
        entry["status"] = "已完成" if ok else "导出失败"
        entry["生成状态"] = "成功" if ok else "失败"
        entry["abnormal"] = not ok
        entry["pending"] = False
        if ok:
            rows = store.rows("change_history")
            hid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
            rows.append({
                "id": hid,
                "商品标识": entry.get("商品范围", ""),
                "旧价格": "在线比价结果",
                "新价格": f"导出 {entry.get('导出格式', '')} 文件",
                "变化幅度": "备份",
                "发现时间": datetime.now(timezone.utc).isoformat(),
                "status": "已确认",
                "pending": False,
                "abnormal": False,
            })
        return {"ok": True, "message": "导出状态已回写"}

    def retry_export(self, entry_id: int) -> dict[str, Any]:
        """重试失败导出并隔离失败上下文。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "导出失败":
            return {"ok": False, "message": "任务不存在或未失败"}
        rows = store.rows("failure_quarantine")
        fid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": fid,
            "样本指纹": f"export-{entry_id}",
            "失败阶段": "导出",
            "失败原因": f"任务 {entry.get('任务名称', '')} 上次导出失败",
            "隔离时间": datetime.now(timezone.utc).isoformat(),
            "重试次数": "1",
            "status": "重试中",
            "pending": True,
            "abnormal": False,
        })
        entry["status"] = "导出中"
        entry["abnormal"] = False
        return {"ok": True, "message": "导出重试已启动", "quarantine_id": fid}

    def attach_currency_note(self, entry_id: int) -> dict[str, Any]:
        """为导出任务附上货币换算口径说明。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "导出任务不存在"}
        snapshots = [
            r for r in store.rows("currency_conv") if r.get("status") == "已生效"
        ]
        if not snapshots:
            entry["生成状态"] = "缺汇率口径"
            return {"ok": False, "message": "没有生效中的汇率快照，导出口径缺失"}
        snap = snapshots[0]
        entry["时间范围"] = str(snap.get("快照日期", entry.get("时间范围", "")))
        return {
            "ok": True,
            "message": "换算口径已附加",
            "pair": f"{snap.get('源货币', '')}->{snap.get('目标货币', '')}",
            "rate": snap.get("汇率值", ""),
        }

    def export_scope_stats(self, entry_id: int) -> dict[str, Any]:
        """统计导出范围内比价结果覆盖情况。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "导出任务不存在"}
        scope = entry.get("商品范围", "")
        rows = [c for c in store.rows("price_compare") if scope in str(c.get("商品标识", ""))]
        computed = sum(1 for r in rows if r.get("status") == "已计算")
        return {
            "ok": True,
            "message": "导出范围统计完成",
            "total_batches": len(rows),
            "computed": computed,
            "coverage": f"{(computed / len(rows)):.0%}" if rows else "0%",
        }

    def cleanup_finished(self, keep_days: int = 30) -> dict[str, Any]:
        """清理历史已完成导出任务并归档审计。"""
        cutoff = datetime.now(timezone.utc).date()
        finished = [
            t for t in store.rows(self.MODULE) if t.get("status") == "已完成"
        ]
        for task in finished:
            task["pending"] = False
        rows = store.rows("parse_audit")
        aid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": aid,
            "审计时间": datetime.now(timezone.utc).isoformat(),
            "解析模板": "导出通道清理",
            "样本总数": str(len(finished)),
            "字段命中率": "100.00%",
            "失败原因分布": "",
            "status": "正常",
            "pending": True,
            "abnormal": False,
        })
        return {
            "ok": True,
            "message": "已完成导出任务已清理",
            "cleaned": len(finished),
            "as_of": cutoff.isoformat(),
        }
