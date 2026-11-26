"""比价轮次核心：申请幂等、输入快照、一轮确认与导出绑定。

设计要点（对应 cc10050 验收）：

* 同一比价申请（price_compare 行 / request_key）至多有一个“已确认”轮次，
  重复提交“开始计算”只会回放同一个确认轮次。
* 轮次确认时把参与运算的生效基线（金额/版本）与同批站点报价整体快照，
  此后基线再被更正也不会影响该轮结果，杜绝混用更正前后的值。
* 基线更正与取数并发时，严格确认会比对基线版本，版本不一致即报“两次取数冲突”，
  可在更正完成后重取并确认；导出任务始终绑定同一个轮次身份。
* 缺有效基线或没有可比较报价时，轮次确认失败并记录原因，导出通道不得交付。
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from typing import Any

from app.store import store

# 所有状态变更都在同一把进程锁内完成：同步路由跑在 anyio 线程池，
# 锁可以保证并发“开始计算 / 更正基线 / 开始导出”被串行化。
DOMAIN_LOCK = threading.RLock()

# request_key -> 首次受理时的完整回执（幂等回放）
IDEMPOTENCY: dict[str, dict[str, Any]] = {}

ROUND_PENDING = "待确认"
ROUND_CONFIRMED = "已确认"
ROUND_CONFLICT = "取数冲突"
ROUND_FAILED = "计算失败"

_QUOTE_TABLES = ("site_quote", "price_quote", "station_quote", "quotes")


def reset_runtime() -> None:
    IDEMPOTENCY.clear()


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def next_id(table: str) -> int:
    rows = store.rows(table)
    return max((int(r.get("id", 0) or 0) for r in rows), default=0) + 1


def to_number(value: Any) -> float | None:
    """宽容地把 '¥1,20.50元' / '110' / 110.0 解析成 float。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "").replace("，", "")
    text = re.sub(r"[¥￥$元\s]", "", text)
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def pct_text(ratio: float) -> str:
    """0.1 -> '10%'，-0.12 -> '-12%'，非整数保留至多两位小数。"""
    pct = round(float(ratio) * 100.0, 2)
    text = f"{pct:.2f}".rstrip("0").rstrip(".")
    if text in ("-0", ""):
        text = "0"
    return f"{text}%"


def amount_text(value: Any) -> str:
    num = to_number(value)
    if num is None:
        return str(value if value is not None else "")
    return f"{num:.2f}"


def idem_key(namespace: str, entry_id: int, request_key: str | None) -> str | None:
    if not request_key:
        return None
    return f"{namespace}:{entry_id}:{request_key}"


def remember(key: str | None, outcome: dict[str, Any]) -> dict[str, Any]:
    if key:
        IDEMPOTENCY[key] = json.loads(json.dumps(outcome, ensure_ascii=False, default=str))
    return outcome


def replay(key: str | None) -> dict[str, Any] | None:
    if key and key in IDEMPOTENCY:
        cached = json.loads(json.dumps(IDEMPOTENCY[key], ensure_ascii=False))
        cached["duplicate"] = True
        cached["idempotent_replay"] = True
        return cached
    return None


def merged_params(payload: Any) -> dict[str, Any]:
    """从 ActionPayload（含 extra 字段）中合并出参数字典。"""
    params: dict[str, Any] = {}
    extra = getattr(payload, "model_extra", None)
    if isinstance(extra, dict):
        params.update(extra)
    values = getattr(payload, "values", None)
    if isinstance(values, dict):
        params.update(values)
    explicit = getattr(payload, "params", None)
    if isinstance(explicit, dict):
        params.update(explicit)
    remark = getattr(payload, "remark", None)
    if remark and "remark" not in params:
        params["remark"] = remark
    return params


def pick(params: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in params and params[name] not in (None, ""):
            return params[name]
    return None


# ---------------------------------------------------------------------------
# 基线
# ---------------------------------------------------------------------------

def baseline_version(row: dict[str, Any]) -> int:
    try:
        return int(row.get("版本") or 1)
    except (TypeError, ValueError):
        return 1


def find_effective_baseline(goods_id: str, spec: str | None = None) -> dict[str, Any] | None:
    if goods_id in (None, ""):
        return None
    candidates = [
        b for b in store.rows("price_baseline")
        if str(b.get("商品标识", "")) == str(goods_id) and b.get("status") == "已生效"
    ]
    if not candidates:
        return None
    if spec:
        exact = [b for b in candidates if str(b.get("归一规格", "") or "") == str(spec)]
        if exact:
            candidates = exact
    candidates.sort(
        key=lambda b: (
            baseline_version(b),
            str(b.get("更新时间", "") or ""),
            int(b.get("id", 0) or 0),
        ),
        reverse=True,
    )
    return candidates[0]


def correct_baseline(entry_id: int, new_price: Any, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """更正基线价格并递增版本；已确认轮次持有的是快照，不受影响。"""
    params = params or {}
    row = store.find("price_baseline", entry_id)
    if row is None:
        return {"ok": False, "message": f"价格基线 {entry_id} 不存在", "entry": None}
    price = to_number(new_price)
    if price is None or price <= 0:
        return {"ok": False, "message": f"基线价格必须是正数，收到：{new_price!r}", "entry": row}

    hold = bool(pick(params, "hold", "pending", "defer") ) or str(
        pick(params, "phase") or ""
    ) in ("begin", "hold", "pending")
    commit = str(pick(params, "phase") or "") in ("commit", "finish", "done")

    with DOMAIN_LOCK:
        old_price = to_number(row.get("基线价格"))
        version = baseline_version(row)
        rev_id = next_id("baseline_revision")
        revision = {
            "id": rev_id,
            "baseline_id": entry_id,
            "商品标识": row.get("商品标识", ""),
            "归一规格": row.get("归一规格", ""),
            "old_price": old_price,
            "new_price": price,
            "from_version": version,
            "to_version": version + 1,
            "更正时间": now_text(),
            "status": "更正中" if hold and not commit else "已更正",
        }
        store.rows("baseline_revision").append(revision)

        if hold and not commit:
            row["更正状态"] = "更正中"
            row["待更正价格"] = price
            row["pending"] = True
            row["status"] = "计算中"
            return {
                "ok": True,
                "message": f"基线 {row.get('商品标识')} 已挂起更正：{amount_text(old_price)} → {amount_text(price)}，更正完成前相关轮次暂不确认",
                "entry": row,
                "revision": revision,
                "holding": True,
            }

        row["基线价格"] = price
        row["版本"] = version + 1
        row["status"] = "已生效"
        row["pending"] = False
        row["abnormal"] = False
        row["更新时间"] = now_text()
        row.pop("更正状态", None)
        row.pop("待更正价格", None)
        if commit:
            revision["status"] = "已更正"
        return {
            "ok": True,
            "message": f"基线已更正并生效：{amount_text(old_price)} → {amount_text(price)}（v{version}→v{version + 1}）",
            "entry": row,
            "revision": revision,
        }


# ---------------------------------------------------------------------------
# 站点报价
# ---------------------------------------------------------------------------

def _normalize_quote(raw: Any, index: int = 0) -> dict[str, Any]:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return {"站点名称": f"站点{index + 1}", "报价": float(raw), "归一规格": "", "可比较": True}
    if not isinstance(raw, dict):
        return {"站点名称": str(raw), "报价": to_number(raw), "归一规格": "", "可比较": True}
    site = pick(raw, "站点名称", "站点", "站点名", "site", "site_name", "name") or f"站点{index + 1}"
    price = pick(raw, "报价", "站点报价", "价格", "quote", "price", "amount")
    spec = pick(raw, "归一规格", "规格", "spec", "standard_spec") or ""
    batch = pick(raw, "比价批次", "批次", "batch") or ""
    comparable = pick(raw, "可比较", "comparable", "is_comparable")
    status = raw.get("status", "")
    return {
        "站点名称": str(site),
        "报价": price,
        "报价数值": to_number(price),
        "归一规格": str(spec),
        "比价批次": str(batch),
        "可比较": comparable,
        "status": str(status or ""),
    }


_QUOTE_PAIR_RE = re.compile(r"([^,，;；\s:：=]+)\s*[:：=]\s*([0-9]+(?:\.[0-9]+)?)")


def _parse_embedded_quotes(text: Any) -> list[dict[str, Any]]:
    if text is None:
        return []
    if isinstance(text, list):
        return [_normalize_quote(q, i) for i, q in enumerate(text)]
    value = str(text).strip()
    if not value:
        return []
    # 可能是 JSON
    if value[0] in "[{":
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [_normalize_quote(q, i) for i, q in enumerate(parsed)]
            if isinstance(parsed, dict) and isinstance(parsed.get("quotes"), list):
                return [_normalize_quote(q, i) for i, q in enumerate(parsed["quotes"])]
        except (ValueError, TypeError):
            pass
    return [
        {"站点名称": m.group(1).strip(), "报价": float(m.group(2)),
         "报价数值": float(m.group(2)), "归一规格": "", "可比较": True}
        for m in _QUOTE_PAIR_RE.finditer(value)
    ]


def gather_quotes(
    goods_id: str,
    spec: str | None,
    batch: str | None,
    params: dict[str, Any] | None,
    row: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """汇总本轮报价来源：动作参数 > 报价表（含可能的别名表）> 行内登记文本。"""
    params = params or {}
    raw_quotes = pick(params, "quotes", "site_quotes", "quote_list", "报价列表", "站点报价")
    quotes: list[dict[str, Any]] = []

    if raw_quotes is not None:
        if isinstance(raw_quotes, str):
            quotes.extend(_parse_embedded_quotes(raw_quotes))
        else:
            quotes.extend(_normalize_quote(q, i) for i, q in enumerate(raw_quotes))
    else:
        for table in _QUOTE_TABLES:
            if not store.has_table(table):
                continue
            for q in store.rows(table):
                if goods_id and str(q.get("商品标识", "")) != str(goods_id):
                    continue
                quotes.append(_normalize_quote(q, len(quotes)))
        # 行内字段：站点报价 / 报价明细 / quotes
        if row:
            for key in ("站点报价", "报价明细", "quotes", "报价JSON"):
                if row.get(key):
                    quotes.extend(_parse_embedded_quotes(row.get(key)))

    # 同批优先：带明确批次时排除“其它批次”的报价；未标批次的报价视为本批可用
    if batch:
        scoped = [
            q for q in quotes
            if not q.get("比价批次") or q["比价批次"] == batch
        ]
        if scoped:
            quotes = scoped
    return quotes


def classify_quotes(
    quotes: list[dict[str, Any]], spec: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    comparable: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for q in quotes:
        reason = ""
        flag = q.get("可比较", None)
        if isinstance(flag, bool) and flag is False:
            reason = "标记为不可比较"
        elif flag is not None and str(flag).strip() in ("否", "0", "false", "False", "不可比较"):
            reason = "标记为不可比较"
        elif "不可比较" in str(q.get("status", "")):
            reason = f"状态为{q.get('status')}"
        elif spec and q.get("归一规格") and q["归一规格"] != spec:
            reason = f"规格不一致（{q['归一规格']} ≠ {spec}）"
        elif q.get("报价数值") is None:
            reason = "报价不是有效数值"
        elif q["报价数值"] <= 0:
            reason = "报价必须为正数"
        item = {**q}
        if reason:
            item["不可比较原因"] = reason
            rejected.append(item)
        else:
            comparable.append(item)
    return comparable, rejected


# ---------------------------------------------------------------------------
# 轮次
# ---------------------------------------------------------------------------

def round_row(round_id: int | None) -> dict[str, Any] | None:
    if not round_id:
        return None
    return store.find("compare_round", int(round_id))


def confirmed_round_of(entry: dict[str, Any]) -> dict[str, Any] | None:
    rnd = round_row(entry.get("confirmed_round_id"))
    if rnd and rnd.get("state") == ROUND_CONFIRMED:
        return rnd
    # 兜底：按轮次号查
    no = entry.get("确认轮次")
    if no:
        for r in store.rows("compare_round"):
            if r.get("轮次号") == no and r.get("state") == ROUND_CONFIRMED:
                return r
    return None


def active_round_of(entry: dict[str, Any]) -> dict[str, Any] | None:
    rnd = round_row(entry.get("active_round_id"))
    if rnd:
        return rnd
    return confirmed_round_of(entry)


def _new_round(entry: dict[str, Any]) -> dict[str, Any]:
    cid = int(entry.get("id", 0))
    seq = sum(1 for r in store.rows("compare_round") if int(r.get("compare_id", 0)) == cid) + 1
    rnd = {
        "id": next_id("compare_round"),
        "轮次号": f"PC{cid}-R{seq}",
        "request_key": entry.get("_request_key", ""),
        "compare_id": cid,
        "比价批次": entry.get("比价批次", ""),
        "商品标识": entry.get("商品标识", ""),
        "归一规格": entry.get("归一规格", ""),
        "state": ROUND_PENDING,
        "baseline_id": None,
        "baseline_version": None,
        "基线金额": None,
        "quotes": [],
        "rejected_quotes": [],
        "lowest_site": "",
        "lowest_price": None,
        "diff_ratio": None,
        "价差比例": "",
        "fail_reason": "",
        "read_log": [],
        "created_at": now_iso(),
        "confirmed_at": "",
    }
    store.rows("compare_round").append(rnd)
    entry["active_round_id"] = rnd["id"]
    entry["当前轮次"] = rnd["轮次号"]
    return rnd


class SnapshotError(Exception):
    pass


def take_snapshot(entry: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """原子读取本轮的基线与报价并完成核算；任何输入缺失都抛 SnapshotError。"""
    goods = str(pick(params, "商品标识") or entry.get("商品标识") or "")
    spec = str(pick(params, "归一规格", "spec") or entry.get("归一规格") or "")
    batch = str(entry.get("比价批次") or "")

    baseline = find_effective_baseline(goods, spec or None)
    if baseline is None:
        raise SnapshotError(f"商品 {goods} 缺少已生效的有效基线，本轮无法核算")
    baseline_price = to_number(baseline.get("基线价格"))
    if baseline_price is None or baseline_price <= 0:
        raise SnapshotError(
            f"商品 {goods} 的生效基线价格无效（{baseline.get('基线价格')!r}），本轮无法核算"
        )
    spec = spec or str(baseline.get("归一规格", "") or "")

    quotes = gather_quotes(goods, spec or None, batch or None, params, entry)
    comparable, rejected = classify_quotes(quotes, spec or None)
    if not quotes:
        raise SnapshotError(
            f"商品 {goods}（规格 {spec or '未指定'}）没有取到任何站点报价，本轮无法核算"
        )
    if not comparable:
        details = "；".join(
            f"{q.get('站点名称')}：{q.get('不可比较原因', '不可比较')}" for q in rejected
        ) or "全部报价均不满足同规格/有效价格条件"
        raise SnapshotError(f"商品 {goods} 的所有报价均不可比较：{details}")

    lowest = min(comparable, key=lambda q: float(q["报价数值"]))
    lowest_price = float(lowest["报价数值"])
    ratio = (lowest_price - baseline_price) / baseline_price
    return {
        "归一规格": spec,
        "baseline_id": int(baseline.get("id", 0)),
        "baseline_version": baseline_version(baseline),
        "baseline_row": baseline,
        "基线金额": baseline_price,
        "quotes": comparable,
        "rejected_quotes": rejected,
        "lowest_site": str(lowest.get("站点名称", "")),
        "lowest_price": lowest_price,
        "diff_ratio": ratio,
        "价差比例": pct_text(ratio),
        "read_at": now_iso(),
    }


def _apply_snapshot_to_round(rnd: dict[str, Any], snap: dict[str, Any]) -> None:
    baseline = snap.pop("baseline_row", None)
    rnd["归一规格"] = snap.get("归一规格", "")
    rnd["baseline_id"] = snap["baseline_id"]
    rnd["baseline_version"] = snap["baseline_version"]
    rnd["基线金额"] = snap["基线金额"]
    rnd["quotes"] = snap["quotes"]
    rnd["rejected_quotes"] = snap["rejected_quotes"]
    rnd["lowest_site"] = snap["lowest_site"]
    rnd["lowest_price"] = snap["lowest_price"]
    rnd["diff_ratio"] = snap["diff_ratio"]
    rnd["价差比例"] = snap["价差比例"]
    rnd["read_at"] = snap["read_at"]
    rnd.setdefault("read_log", []).append({
        "at": snap["read_at"],
        "baseline_id": snap["baseline_id"],
        "baseline_version": snap["baseline_version"],
        "基线金额": snap["基线金额"],
        "quote_count": len(snap["quotes"]),
    })
    if baseline is not None:
        rnd["baseline_更新时间"] = baseline.get("更新时间", "")


def _write_confirmed_to_entry(entry: dict[str, Any], rnd: dict[str, Any]) -> None:
    entry["status"] = "已计算"
    entry["pending"] = False
    entry["abnormal"] = False
    entry["最低价站点"] = rnd["lowest_site"]
    entry["最低报价"] = rnd["lowest_price"]
    entry["价差比例"] = rnd["价差比例"]
    entry["价差比例数值"] = rnd["diff_ratio"]
    entry["diff_ratio"] = rnd["diff_ratio"]
    entry["lowest_price"] = rnd["lowest_price"]
    entry["lowest_site"] = rnd["lowest_site"]
    entry["baseline_price"] = rnd["基线金额"]
    entry["基线金额"] = rnd["基线金额"]
    entry["基线版本"] = rnd["baseline_version"]
    entry["归一规格"] = rnd.get("归一规格", entry.get("归一规格", ""))
    entry["确认轮次"] = rnd["轮次号"]
    entry["round_no"] = rnd["轮次号"]
    entry["round_id"] = rnd["id"]
    entry["confirmed_round_id"] = rnd["id"]
    entry["active_round_id"] = rnd["id"]
    entry["计算时间"] = now_text()
    entry["失败原因"] = ""
    entry["进度说明"] = f"轮次 {rnd['轮次号']} 已确认：最低价 {rnd['lowest_site']} {amount_text(rnd['lowest_price'])}，对照基线 {amount_text(rnd['基线金额'])}（v{rnd['baseline_version']}），价差 {rnd['价差比例']}"


def _fail_entry(entry: dict[str, Any], reason: str, rnd: dict[str, Any] | None = None) -> dict[str, Any]:
    entry["status"] = "计算失败"
    entry["pending"] = False
    entry["abnormal"] = True
    entry["失败原因"] = reason
    entry["进度说明"] = reason
    if rnd is not None:
        rnd["state"] = ROUND_FAILED
        rnd["fail_reason"] = reason
    return {"ok": False, "message": reason, "entry": entry, "receipt": rnd, "round": rnd}


def start_calculation(
    entry_id: int,
    params: dict[str, Any],
    request_key: str | None = None,
) -> dict[str, Any]:
    """受理“开始计算”。

    默认同步完成取数+确认（一轮）；params.defer/phase=prepare 时只取数暂存，
    等待后续 confirm_round 做带基线版本校验的确认。
    """
    key = idem_key("compare", entry_id, request_key)
    cached = replay(key)
    if cached is not None:
        return cached

    entry = store.find("price_compare", entry_id)
    if entry is None:
        return {"ok": False, "message": f"比价结果 {entry_id} 不存在", "entry": None}

    with DOMAIN_LOCK:
        confirmed = confirmed_round_of(entry)
        if confirmed is not None and not pick(params, "force", "recompute"):
            outcome = {
                "ok": True,
                "message": f"同一申请已确认轮次 {confirmed['轮次号']}，重复提交不重复确认，仍返回该轮结果",
                "entry": entry,
                "receipt": confirmed,
                "round": confirmed,
                "duplicate": True,
            }
            return remember(key, outcome)

        defer = bool(pick(params, "defer", "wait_confirm", "pending_confirm")) or str(
            pick(params, "phase") or ""
        ) in ("prepare", "pending", "defer")

        rnd = active_round_of(entry)
        if rnd is None or rnd.get("state") == ROUND_CONFIRMED:
            rnd = _new_round(entry)
        try:
            snap = take_snapshot(entry, params)
        except SnapshotError as exc:
            outcome = _fail_entry(entry, str(exc), rnd)
            outcome["receipt"] = rnd
            return remember(key, outcome)

        _apply_snapshot_to_round(rnd, snap)

        if defer:
            rnd["state"] = ROUND_PENDING
            entry["status"] = "排队中"
            entry["pending"] = True
            entry["abnormal"] = False
            entry["active_round_id"] = rnd["id"]
            entry["进度说明"] = (
                f"轮次 {rnd['轮次号']} 已完成取数（基线 {amount_text(rnd['基线金额'])} "
                f"v{rnd['baseline_version']}、{len(rnd['quotes'])} 条可比报价），尚未确认；"
                "基线若在此后更正，确认时会进行两次取数一致性校验"
            )
            entry["失败原因"] = ""
            outcome = {
                "ok": True,
                "confirmed": False,
                "waiting": True,
                "message": f"轮次 {rnd['轮次号']} 已暂存取数，等待确认",
                "entry": entry,
                "receipt": rnd,
                "round": rnd,
            }
            return remember(key, outcome)

        rnd["state"] = ROUND_CONFIRMED
        rnd["confirmed_at"] = now_iso()
        _write_confirmed_to_entry(entry, rnd)
        outcome = {
            "ok": True,
            "confirmed": True,
            "message": "比价计算完成并确认一轮",
            "entry": entry,
            "receipt": rnd,
            "round": rnd,
            "baseline_price": rnd["基线金额"],
            "lowest_site": rnd["lowest_site"],
            "lowest_price": rnd["lowest_price"],
            "diff_ratio": rnd["diff_ratio"],
            "round_no": rnd["轮次号"],
        }
        return remember(key, outcome)


def confirm_round(
    entry_id: int,
    params: dict[str, Any],
    request_key: str | None = None,
) -> dict[str, Any]:
    """严格确认暂存轮次：基线版本在两次取数间变化则报冲突，绝不混用。"""
    entry = store.find("price_compare", entry_id)
    if entry is None:
        return {"ok": False, "message": f"比价结果 {entry_id} 不存在", "entry": None}

    with DOMAIN_LOCK:
        confirmed = confirmed_round_of(entry)
        if confirmed is not None:
            return {
                "ok": True,
                "message": f"轮次 {confirmed['轮次号']} 已确认，重复确认被忽略",
                "entry": entry,
                "receipt": confirmed,
                "round": confirmed,
                "duplicate": True,
            }
        rnd = active_round_of(entry)
        if rnd is None:
            # 没有暂存轮次：直接按当前数据计算确认
            return start_calculation(entry_id, params, request_key)

        rebased = bool(pick(params, "rebase", "force", "latest"))

        # 第二次取数：只核对基线身份/版本/金额是否仍与首次快照一致
        goods = str(entry.get("商品标识", ""))
        spec = rnd.get("归一规格") or None
        current_baseline = find_effective_baseline(goods, spec)
        if current_baseline is None:
            reason = f"商品 {goods} 缺少已生效的有效基线，轮次不能确认"
            return _fail_entry(entry, reason, rnd)
        current_version = baseline_version(current_baseline)
        current_price = to_number(current_baseline.get("基线价格"))
        changed = (
            int(current_baseline.get("id", 0)) != int(rnd.get("baseline_id") or 0)
            or current_version != int(rnd.get("baseline_version") or 0)
            or (rnd.get("基线金额") is not None and abs(current_price - float(rnd["基线金额"])) > 1e-9)
        )

        if changed and not rebased:
            old_desc = f"{amount_text(rnd.get('基线金额'))}（v{rnd.get('baseline_version')}）"
            new_desc = f"{amount_text(current_price)}（v{current_version}）"
            reason = (
                f"两次取数冲突：轮次 {rnd['轮次号']} 首次取数基线为 {old_desc}，"
                f"确认前已被更正为 {new_desc}；为避免混用更正前后的值，本轮未确认，"
                "请在更正完成后重取最新基线并确认（重试计算）"
            )
            rnd["state"] = ROUND_CONFLICT
            rnd["fail_reason"] = reason
            rnd.setdefault("read_log", []).append({
                "at": now_iso(), "conflict": True,
                "baseline_version": current_version, "基线金额": current_price,
            })
            entry["status"] = "排队中"
            entry["pending"] = True
            entry["abnormal"] = True
            entry["失败原因"] = reason
            entry["进度说明"] = reason
            return {
                "ok": False,
                "conflict": True,
                "waiting": True,
                "message": reason,
                "entry": entry,
                "receipt": rnd,
                "round": rnd,
            }

        if changed and rebased:
            try:
                snap = take_snapshot(entry, {**params, "归一规格": rnd.get("归一规格")})
            except SnapshotError as exc:
                return _fail_entry(entry, str(exc), rnd)
            _apply_snapshot_to_round(rnd, snap)

        if rnd.get("diff_ratio") is None:
            try:
                snap = take_snapshot(entry, params)
            except SnapshotError as exc:
                return _fail_entry(entry, str(exc), rnd)
            _apply_snapshot_to_round(rnd, snap)

        rnd["state"] = ROUND_CONFIRMED
        rnd["confirmed_at"] = now_iso()
        _write_confirmed_to_entry(entry, rnd)
        return {
            "ok": True,
            "confirmed": True,
            "rebased": changed and rebased,
            "message": f"轮次 {rnd['轮次号']} 已确认",
            "entry": entry,
            "receipt": rnd,
            "round": rnd,
        }


def retry_calculation(entry_id: int, params: dict[str, Any], request_key: str | None = None) -> dict[str, Any]:
    """重试计算：同一申请已有确认轮次则幂等回放；否则重取并确认唯一一轮。"""
    entry = store.find("price_compare", entry_id)
    if entry is None:
        return {"ok": False, "message": f"比价结果 {entry_id} 不存在", "entry": None}
    with DOMAIN_LOCK:
        confirmed = confirmed_round_of(entry)
        if confirmed is not None:
            return {
                "ok": True,
                "message": f"该申请已确认轮次 {confirmed['轮次号']}，重试仍绑定同一轮结果",
                "entry": entry,
                "receipt": confirmed,
                "round": confirmed,
                "duplicate": True,
            }
        # 重试允许在冲突/失败后基于最新数据重取，沿用同一个轮次身份
        return confirm_round(entry_id, {"rebase": True, **params}, request_key)


# ---------------------------------------------------------------------------
# 导出绑定与交付
# ---------------------------------------------------------------------------

def find_compare_for_export(exp: dict[str, Any]) -> dict[str, Any] | None:
    cid = exp.get("compare_id") or exp.get("比价结果ID") or exp.get("apply_id")
    if cid not in (None, ""):
        try:
            row = store.find("price_compare", int(cid))
            if row:
                return row
        except (TypeError, ValueError):
            pass

    batch = str(exp.get("比价批次") or "")
    if batch:
        for c in store.rows("price_compare"):
            if str(c.get("比价批次", "")) == batch:
                return c

    scope = str(exp.get("商品范围") or exp.get("商品标识") or "").strip()
    goods = str(exp.get("商品标识") or "").strip()
    compares = store.rows("price_compare")
    if goods:
        exact = [c for c in compares if str(c.get("商品标识", "")) == goods]
        if exact:
            return _prefer_compare(exact)
    if scope:
        exact = [c for c in compares if str(c.get("商品标识", "")) == scope]
        if exact:
            return _prefer_compare(exact)
        fuzzy = [
            c for c in compares
            if scope in str(c.get("商品标识", "")) or str(c.get("商品标识", "")) in scope
        ]
        if fuzzy:
            return _prefer_compare(fuzzy)
    return None


def _prefer_compare(rows_list: list[dict[str, Any]]) -> dict[str, Any]:
    confirmed = [c for c in rows_list if confirmed_round_of(c) is not None]
    pool = confirmed or rows_list
    return sorted(pool, key=lambda c: int(c.get("id", 0) or 0), reverse=True)[0]


def get_or_create_bound_export(compare_id: int) -> dict[str, Any]:
    """从比价行进入导出通道：同一比价申请复用同一笔导出申请。"""
    compare = store.find("price_compare", compare_id)
    if compare is None:
        raise KeyError(compare_id)
    with DOMAIN_LOCK:
        for exp in store.rows("price_export"):
            if int(exp.get("compare_id", 0) or 0) == compare_id:
                return exp
        eid = next_id("price_export")
        exp = {
            "id": eid,
            "任务名称": f"导出 {compare.get('比价批次', '')}",
            "商品范围": compare.get("商品标识", ""),
            "商品标识": compare.get("商品标识", ""),
            "比价批次": compare.get("比价批次", ""),
            "导出格式": "CSV",
            "时间范围": datetime.now().date().isoformat(),
            "生成状态": "排队中",
            "status": "排队中",
            "pending": True,
            "abnormal": False,
            "compare_id": compare_id,
            "来源": "price_compare",
            "确认轮次": "",
            "等待轮次": compare.get("确认轮次", "") or (
                active_round_of(compare).get("轮次号", "") if active_round_of(compare) else ""
            ),
            "基线金额": "",
            "最低价站点": "",
            "最低报价": "",
            "价差比例": "",
            "失败原因": "",
            "进度说明": "导出申请已创建，等待比价轮次确认",
        }
        store.rows("price_export").append(exp)
        return exp


def _populate_export_from_round(exp: dict[str, Any], compare: dict[str, Any], rnd: dict[str, Any]) -> None:
    exp["compare_id"] = int(compare.get("id", exp.get("compare_id", 0)) or 0)
    exp["比价批次"] = rnd.get("比价批次") or compare.get("比价批次", "")
    exp["商品标识"] = rnd.get("商品标识") or compare.get("商品标识", "")
    if not exp.get("商品范围"):
        exp["商品范围"] = exp["商品标识"]
    exp["确认轮次"] = rnd["轮次号"]
    exp["round_no"] = rnd["轮次号"]
    exp["round_id"] = rnd["id"]
    exp["confirmed_round_id"] = rnd["id"]
    exp["基线金额"] = rnd["基线金额"]
    exp["baseline_price"] = rnd["基线金额"]
    exp["基线版本"] = rnd.get("baseline_version")
    exp["最低价站点"] = rnd["lowest_site"]
    exp["lowest_site"] = rnd["lowest_site"]
    exp["最低报价"] = rnd["lowest_price"]
    exp["lowest_price"] = rnd["lowest_price"]
    exp["价差比例"] = rnd["价差比例"]
    exp["diff_ratio"] = rnd.get("diff_ratio")
    exp["价差比例数值"] = rnd.get("diff_ratio")
    exp["归一规格"] = rnd.get("归一规格", "")
    exp["pending"] = False
    exp["abnormal"] = False
    exp["失败原因"] = ""
    exp["进度说明"] = (
        f"已绑定确认轮次 {rnd['轮次号']}：最低价 {rnd['lowest_site']} "
        f"{amount_text(rnd['lowest_price'])}，基线 {amount_text(rnd['基线金额'])}，"
        f"价差 {rnd['价差比例']}"
    )


def gate_export(entry_id: int, *, retry: bool = False, defer: bool = False) -> dict[str, Any]:
    """开始/重试导出：必须能解析到该申请唯一的确认轮次，否则失败并说明等候原因。"""
    exp = store.find("price_export", entry_id)
    if exp is None:
        return {"ok": False, "message": f"导出任务 {entry_id} 不存在", "entry": None}

    with DOMAIN_LOCK:
        compare = find_compare_for_export(exp)
        if compare is None:
            explicit_link = bool(
                exp.get("compare_id")
                or exp.get("比价批次")
                or exp.get("round_id")
                or exp.get("来源") == "price_compare"
            )
            if not explicit_link:
                # 通用/历史导出任务没有绑定具体比价申请：保留脚手架的通用流转，
                # 不影响“关联到比价批次”的导出必须凭确认轮次交付的约束。
                exp["status"] = "导出中"
                exp["生成状态"] = exp.get("生成状态") or "排队中"
                exp["pending"] = True
                exp["abnormal"] = False
                return {
                    "ok": True,
                    "message": "导出任务已开始（通用清单通道，未绑定比价确认轮次）",
                    "entry": exp,
                    "export_task": exp,
                }
            reason = (
                f"导出任务 {exp.get('任务名称', entry_id)} 的商品范围 "
                f"「{exp.get('商品范围', '')}」关联不到比价申请/批次，不能交付"
            )
            exp["status"] = "导出失败"
            exp["生成状态"] = "失败"
            exp["abnormal"] = True
            exp["pending"] = False
            exp["失败原因"] = reason
            exp["进度说明"] = reason
            return {"ok": False, "message": reason, "entry": exp, "export_task": exp, "waiting": False}

        exp["compare_id"] = int(compare.get("id", 0))
        exp["比价批次"] = compare.get("比价批次", exp.get("比价批次", ""))
        rnd = confirmed_round_of(compare)

        if rnd is None:
            active = active_round_of(compare)
            round_no = (active or {}).get("轮次号") or compare.get("确认轮次") or ""
            base_reason = compare.get("失败原因") or ""
            if active is not None and active.get("state") == ROUND_CONFLICT:
                why = active.get("fail_reason") or "基线两次取数冲突尚未解决"
                reason = f"仍需等候：绑定轮次 {round_no} 处于取数冲突，{why}"
            elif compare.get("status") == "计算失败" or base_reason:
                reason = f"暂不能交付：比价申请已计算失败（{base_reason}）；请先补齐基线/报价并重试计算"
            elif active is not None:
                reason = f"仍需等候：绑定的比价轮次 {round_no} 尚未确认（{active.get('state')}），确认后可重试本导出申请"
            else:
                reason = "仍需等候：该批尚未开始/确认比价轮次，请先在比价计算器提交开始计算"
            exp["status"] = "导出失败"
            exp["生成状态"] = "未交付"
            exp["abnormal"] = True
            exp["pending"] = False
            exp["等待轮次"] = round_no
            exp["失败原因"] = reason
            exp["进度说明"] = reason
            return {
                "ok": False,
                "message": reason,
                "entry": exp,
                "export_task": exp,
                "waiting": True,
                "bound_round": active,
                "round": active,
            }

        _populate_export_from_round(exp, compare, rnd)

        if defer:
            exp["status"] = "导出中"
            exp["生成状态"] = "生成中"
            exp["pending"] = True
            return {
                "ok": True,
                "message": "导出任务已开始，正在按确认轮次生成附件",
                "entry": exp,
                "export_task": exp,
                "bound_round": rnd,
                "round": rnd,
            }

        exp["status"] = "已完成"
        exp["生成状态"] = "成功"
        exp["completed_at"] = now_iso()
        exp["attachment_name"] = attachment_filename(exp, rnd)
        verb = "重试" if retry else "开始"
        return {
            "ok": True,
            "message": f"导出{verb}成功：附件已按确认轮次 {rnd['轮次号']} 生成，可直接下载",
            "entry": exp,
            "export_task": exp,
            "receipt": rnd,
            "round": rnd,
            "bound_round": rnd,
        }


def finish_export(entry_id: int, ok: bool = True) -> dict[str, Any]:
    exp = store.find("price_export", entry_id)
    if exp is None:
        return {"ok": False, "message": "导出任务不存在", "entry": None}
    with DOMAIN_LOCK:
        if ok:
            compare = find_compare_for_export(exp)
            rnd = confirmed_round_of(compare) if compare else None
            if rnd is None:
                reason = "比价确认轮次不存在或未确认，不能完成导出"
                exp["status"] = "导出失败"
                exp["生成状态"] = "失败"
                exp["失败原因"] = reason
                return {"ok": False, "message": reason, "entry": exp}
            _populate_export_from_round(exp, compare, rnd)
            exp["status"] = "已完成"
            exp["生成状态"] = "成功"
            exp["completed_at"] = now_iso()
            exp["attachment_name"] = attachment_filename(exp, rnd)
        else:
            exp["status"] = "导出失败"
            exp["生成状态"] = "失败"
            exp["abnormal"] = True
        exp["pending"] = False
        return {"ok": True, "message": "导出状态已回写", "entry": exp, "export_task": exp}


def load_deliverable(exp_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """返回 (导出任务, 已确认轮次)；不可交付时抛 PermissionError(reason)。"""
    exp = store.find("price_export", exp_id)
    if exp is None:
        raise KeyError(f"导出任务 {exp_id} 不存在")
    rnd = round_row(exp.get("confirmed_round_id"))
    if rnd is None or rnd.get("state") != ROUND_CONFIRMED:
        compare = find_compare_for_export(exp)
        rnd = confirmed_round_of(compare) if compare else None
    if rnd is None or rnd.get("state") != ROUND_CONFIRMED:
        reason = exp.get("失败原因") or "该导出申请尚未绑定已确认的比价轮次，不能下载或发出"
        raise PermissionError(reason)
    return exp, rnd


def attachment_filename(exp: dict[str, Any], rnd: dict[str, Any], ext: str = "csv") -> str:
    batch = re.sub(r"[^\w一-龥-]+", "_", str(rnd.get("比价批次") or exp.get("id")))[:60]
    return f"比价导出_{batch}_{rnd['轮次号']}.{ext}"


def attachment_rows(exp: dict[str, Any], rnd: dict[str, Any]) -> list[dict[str, Any]]:
    quote_detail = "；".join(
        f"{q.get('站点名称')}:{amount_text(q.get('报价数值'))}" for q in rnd.get("quotes", [])
    )
    return [{
        "导出任务ID": exp.get("id"),
        "任务名称": exp.get("任务名称", ""),
        "比价批次": rnd.get("比价批次", ""),
        "商品标识": rnd.get("商品标识", ""),
        "归一规格": rnd.get("归一规格", ""),
        "确认轮次": rnd.get("轮次号", ""),
        "最低价站点": rnd.get("lowest_site", ""),
        "最低报价": amount_text(rnd.get("lowest_price")),
        "站点报价明细": quote_detail,
        "基线金额": amount_text(rnd.get("基线金额")),
        "基线版本": rnd.get("baseline_version", ""),
        "价差比例": rnd.get("价差比例", ""),
        "价差比例数值": rnd.get("diff_ratio", ""),
        "轮次确认时间": rnd.get("confirmed_at", ""),
    }]
