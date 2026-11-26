"""比价计算器领域服务：跨站点计算商品价差比例与最低价站点，负责比价批次的调度与失败重试。

数据链路位置：比价计算层，消费价格基线库的生效基线与各站点采集价格，
产出的比价结果（确认轮次）供变价告警与比价导出通道消费。

并发/幂等/快照语义见 app.rounds_core。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app import rounds_core as rc
from app.services.base import BaseModuleService
from app.store import store


class PriceCompareService(BaseModuleService):
    MODULE = "price_compare"
    ENTITY = "比价结果"
    OPERATION_TERM = "计算"
    LIST_FIELDS = [
        "比价批次", "商品标识", "最低价站点", "价差比例", "计算时间",
        "归一规格", "最低报价", "基线金额", "基线版本", "确认轮次",
        "失败原因", "进度说明",
    ]
    REQUIRED_FIELDS = ["比价批次", "商品标识"]
    STATUS_ORDER = ["排队中", "已计算", "计算失败"]
    ACTION_RULES = {"开始计算": "已计算", "重试计算": "计算失败"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]):
        entry, missing = super().create_entry(values)
        if entry is not None:
            # 允许登记申请时直接携带本轮站点报价（quotes / 站点报价）
            quotes = values.get("quotes") or values.get("site_quotes") or values.get("报价列表")
            if quotes is not None:
                entry["quotes"] = quotes
            if values.get("站点报价") is not None:
                entry["站点报价"] = values.get("站点报价")
            entry.setdefault("status", "排队中")
        return entry, missing

    # ---- 统一动作分派（资源路由只调 handle_action） ----

    def handle_action(
        self,
        entry_id: int,
        payload: Any,
        *,
        request_key: str | None = None,
    ) -> dict[str, Any]:
        action = (payload.action or "").strip()
        params = rc.merged_params(payload)
        # 请求级幂等键：body.request_key / requestId / 申请单号 / header 透传
        key = (
            request_key
            or payload.request_key
            or payload.requestId
            or rc.pick(params, "申请单号", "apply_no", "apply_id", "idempotency_key")
        )
        if action == "开始计算":
            return rc.start_calculation(entry_id, params, key)
        if action == "重试计算":
            return rc.retry_calculation(entry_id, params, key)
        if action in ("确认本轮", "确认轮次", "确认计算", "确认结果"):
            return rc.confirm_round(entry_id, params, key)
        if action in ("暂存取数", "准备轮次", "prepare"):
            params["phase"] = "prepare"
            return rc.start_calculation(entry_id, params, key)
        if action in ("重取并确认", "按最新基线确认", "rebase"):
            return rc.confirm_round(entry_id, {**params, "rebase": True}, key)
        if action in ("进入比价导出通道", "推送导出", "发起导出", "创建导出", "push_to_export"):
            return self.push_to_export(entry_id)
        # 未识别动作回退到通用状态流转
        return super().handle_action(entry_id, payload, request_key=key)

    # ---- 业务扩展：跨站点比价与价差计算 ----

    def run_compare(self, entry_id: int) -> dict[str, Any]:
        """执行一轮比价计算（默认同步确认）。"""
        return rc.start_calculation(entry_id, {})

    def retry_failed(self, entry_id: int) -> dict[str, Any]:
        """重试失败批次：沿用申请并重取确认唯一轮次，同时写隔离上下文。"""
        outcome = rc.retry_calculation(entry_id, {})
        entry = outcome.get("entry")
        if entry is not None and entry.get("status") != "已计算":
            rows = store.rows("failure_quarantine")
            fid = rc.next_id("failure_quarantine")
            rows.append({
                "id": fid,
                "样本指纹": f"compare-{entry_id}",
                "失败阶段": "计算",
                "失败原因": entry.get("失败原因") or f"批次 {entry.get('比价批次', '')} 重试仍失败",
                "隔离时间": rc.now_iso(),
                "重试次数": "1",
                "status": "重试中",
                "pending": True,
                "abnormal": False,
            })
            outcome["quarantine_id"] = fid
        return outcome

    def push_to_export(self, entry_id: int) -> dict[str, Any]:
        """从比价行进入导出通道：同一申请复用同一笔导出任务并绑定轮次。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "批次不存在", "entry": None}
        try:
            exp = rc.get_or_create_bound_export(entry_id)
        except KeyError:
            return {"ok": False, "message": "批次不存在", "entry": None}
        rnd = rc.confirmed_round_of(entry)
        return {
            "ok": True,
            "message": (
                f"已进入比价导出通道，导出申请 #{exp['id']} 已关联"
                + (f"确认轮次 {rnd['轮次号']}" if rnd else "（轮次尚未确认，导出将先等候）")
            ),
            "entry": entry,
            "export_task": exp,
            "export_id": exp["id"],
            "round": rnd,
        }

    def rounds_of(self, entry_id: int) -> list[dict[str, Any]]:
        return [r for r in store.rows("compare_round") if int(r.get("compare_id", 0)) == entry_id]

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
