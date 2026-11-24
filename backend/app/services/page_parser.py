"""页面解析模板领域服务：管理商品页HTML选择器与字段映射，负责把原始页面解析为结构化价格字段。

数据链路位置：解析层，消费样本库中的页面样本，按模板选择器提取价格字段，
产出的结构化数据交给规格归一器与解析审计。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class PageParserService(BaseModuleService):
    MODULE = "page_parser"
    ENTITY = "解析模板"
    OPERATION_TERM = "解析"
    LIST_FIELDS = ["模板名称", "适用站点", "选择器路径", "字段映射", "解析版本"]
    REQUIRED_FIELDS = ["模板名称", "适用站点", "选择器路径"]
    STATUS_ORDER = ["草稿", "已启用", "已废弃"]
    ACTION_RULES = {"启用模板": "已启用", "废弃模板": "已废弃"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：模板解析与字段提取 ----

    def enable_template(self, entry_id: int) -> dict[str, Any]:
        """启用解析模板，校验选择器与字段映射完整性。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        selector = entry.get("选择器路径", "")
        mapping = entry.get("字段映射", "")
        if not selector or "price" not in selector:
            return {"ok": False, "message": "选择器路径缺少价格节点，不能启用"}
        if not mapping:
            return {"ok": False, "message": "字段映射为空，不能启用"}
        entry["status"] = "已启用"
        entry["pending"] = False
        return {"ok": True, "message": f"模板 {entry.get('模板名称', '')} 已启用"}

    def deprecate_template(self, entry_id: int) -> dict[str, Any]:
        """废弃模板并检查是否有站点仍依赖该模板。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "模板不存在"}
        site = entry.get("适用站点", "")
        pending_samples = [
            s for s in store.rows("sample_store")
            if s.get("站点名称") == site and s.get("status") == "已保存"
        ]
        entry["status"] = "已废弃"
        entry["abnormal"] = bool(pending_samples)
        return {
            "ok": True,
            "message": "模板已废弃",
            "blocking_samples": len(pending_samples),
        }

    def parse_sample(self, entry_id: int, sample_id: int) -> dict[str, Any]:
        """用模板解析指定样本，产出结构化字段并更新样本状态。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已启用":
            return {"ok": False, "message": "模板不存在或未启用"}
        sample = store.find("sample_store", sample_id)
        if sample is None:
            return {"ok": False, "message": f"样本 {sample_id} 不存在"}
        if sample.get("站点名称") != entry.get("适用站点"):
            return {"ok": False, "message": "样本站点与模板适用站点不匹配"}
        sample["status"] = "已解析"
        sample["pending"] = False
        return {
            "ok": True,
            "message": "样本解析完成",
            "fields": entry.get("字段映射", ""),
            "version": entry.get("解析版本", ""),
        }

    def report_to_audit(self, entry_id: int, hit_rate: float) -> dict[str, Any]:
        """向解析质量审计上报字段命中率。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "模板不存在"}
        rows = store.rows("parse_audit")
        aid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rate_text = f"{hit_rate:.2%}"
        status = "正常" if hit_rate >= 0.9 else "命中率下降"
        rows.append({
            "id": aid,
            "审计时间": datetime.now(timezone.utc).isoformat(),
            "解析模板": entry.get("模板名称", ""),
            "样本总数": "1",
            "字段命中率": rate_text,
            "失败原因分布": "" if hit_rate >= 0.9 else "选择器失配",
            "status": status,
            "pending": True,
            "abnormal": hit_rate < 0.9,
        })
        return {"ok": True, "message": "命中率已上报审计", "audit_id": aid, "rate": rate_text}

    def route_failures(self, entry_id: int, reason: str) -> dict[str, Any]:
        """解析失败时把样本送入失败隔离区。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "模板不存在"}
        rows = store.rows("failure_quarantine")
        fid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": fid,
            "样本指纹": f"parse-{entry_id}-{fid}",
            "失败阶段": "解析",
            "失败原因": reason,
            "隔离时间": datetime.now(timezone.utc).isoformat(),
            "重试次数": "0",
            "status": "待处理",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "失败样本已隔离", "quarantine_id": fid}

    def bump_version(self, entry_id: int) -> dict[str, Any]:
        """升级模板解析版本并触发基线重算。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "模板不存在"}
        current = str(entry.get("解析版本", "v1"))
        entry["解析版本"] = f"v{int(current.lstrip('v') or 1) + 1}"
        for base in store.rows("price_baseline"):
            if base.get("status") == "已生效":
                base["status"] = "待重算"
                base["pending"] = True
        return {
            "ok": True,
            "message": "解析版本已升级，基线已标记重算",
            "version": entry.get("解析版本"),
        }
