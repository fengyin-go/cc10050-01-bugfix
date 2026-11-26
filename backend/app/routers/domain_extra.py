"""cc10050 专项接口：轮次回查、基线更正别名、导出附件下载。

通用动作接口 /api/{module}/{id}/actions 已能覆盖主流程；这里补充
RESTful 别名与真正的附件响应，方便不同调用方式拿到同一份确认轮次数据。
"""
from __future__ import annotations

import io
import json
from typing import Any
from urllib.parse import quote
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app import rounds_core as rc
from app.services import service_for
from app.store import store

router = APIRouter(prefix="/api", tags=["比价轮次与导出附件"])


def _disposition(filename: str) -> str:
    # HTTP 头只能 latin-1：ASCII 回退名 + RFC5987 UTF-8 文件名
    ascii_name = "price_export_attachment." + filename.rsplit(".", 1)[-1]
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


# ---------------------------------------------------------------------------
# 比价轮次
# ---------------------------------------------------------------------------

@router.get("/price_compare/{entry_id}/rounds")
def list_rounds(entry_id: int) -> dict[str, Any]:
    entry = store.find("price_compare", entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"比价结果 {entry_id} 不存在")
    rounds = [
        r for r in store.rows("compare_round")
        if int(r.get("compare_id", 0)) == entry_id
    ]
    return {
        "ok": True,
        "compare_id": entry_id,
        "confirmed_round": rc.confirmed_round_of(entry),
        "active_round": rc.active_round_of(entry),
        "items": rounds,
        "total": len(rounds),
    }


@router.get("/price_compare/{entry_id}/receipt")
@router.get("/price_compare/{entry_id}/result")
def get_receipt(entry_id: int) -> dict[str, Any]:
    entry = store.find("price_compare", entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="比价结果不存在")
    rnd = rc.confirmed_round_of(entry) or rc.active_round_of(entry)
    return {"ok": rnd is not None, "entry": entry, "receipt": rnd, "round": rnd}


async def _maybe_json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


@router.post("/price_compare/{entry_id}/calculate")
@router.post("/price_compare/{entry_id}/run")
@router.post("/price_compare/{entry_id}/start")
async def calculate_alias(entry_id: int, request: Request) -> dict[str, Any]:
    body = await _maybe_json(request)
    params = body.get("params") if isinstance(body.get("params"), dict) else body
    key = request.headers.get("idempotency-key") or body.get("request_key") or body.get("requestId")
    return rc.start_calculation(entry_id, params or {}, key)


# ---------------------------------------------------------------------------
# 基线更正别名
# ---------------------------------------------------------------------------

@router.post("/price_baseline/{entry_id}/correct")
async def correct_alias(entry_id: int, request: Request) -> dict[str, Any]:
    body = await _maybe_json(request)
    params = body.get("params") if isinstance(body.get("params"), dict) else body
    new_price = rc.pick(params, "基线价格", "新价格", "new_price", "price", "价格")
    return rc.correct_baseline(entry_id, new_price, params or {})


# ---------------------------------------------------------------------------
# 导出附件
# ---------------------------------------------------------------------------

def _csv_response(exp: dict[str, Any], rnd: dict[str, Any]) -> StreamingResponse:
    import csv

    rows = rc.attachment_rows(exp, rnd)
    fields = list(rows[0].keys()) if rows else ["比价批次"]
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()

    def guard(key: str, value: Any) -> Any:
        # 价差/数值列保留原始负号，便于值班员与验收脚本复算；
        # 其余文本沿用清单导出的公式注入防护。
        if key in ("价差比例", "价差比例数值") or value is None:
            return "" if value is None else value
        text = str(value)
        if text.startswith(("=", "+", "-", "@")):
            return "'" + text
        return value

    for row in rows:
        writer.writerow({k: guard(k, row.get(k, "")) for k in fields})
    filename = rc.attachment_filename(exp, rnd, "csv")
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": _disposition(filename)},
    )


def _json_response(exp: dict[str, Any], rnd: dict[str, Any]) -> StreamingResponse:
    payload = {
        "export_id": exp.get("id"),
        "confirmed_round": rnd.get("轮次号"),
        "rows": rc.attachment_rows(exp, rnd),
    }
    filename = rc.attachment_filename(exp, rnd, "json")
    return StreamingResponse(
        iter([json.dumps(payload, ensure_ascii=False, indent=2)]),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": _disposition(filename)},
    )


def _xlsx_response(exp: dict[str, Any], rnd: dict[str, Any]) -> StreamingResponse:
    """用标准库生成最小合法 xlsx（inlineStr），避免引入额外依赖。"""
    import zipfile

    rows = rc.attachment_rows(exp, rnd)
    headers = list(rows[0].keys()) if rows else ["比价批次"]

    def col_letter(idx: int) -> str:
        out = ""
        idx += 1
        while idx:
            idx, rem = divmod(idx - 1, 26)
            out = chr(65 + rem) + out
        return out

    sheet_rows = []
    all_rows = [headers, *[[str(r.get(h, "")) for h in headers] for r in rows]]
    for ri, values in enumerate(all_rows, start=1):
        cells = []
        for ci, value in enumerate(values):
            ref = f"{col_letter(ci)}{ri}"
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{ri}">{"".join(cells)}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="比价导出" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/worksheets/sheet1.xml"/></Relationships>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    filename = rc.attachment_filename(exp, rnd, "xlsx")
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": _disposition(filename)},
    )


@router.get("/price_export/{entry_id}/attachment")
@router.get("/price_export/{entry_id}/download")
@router.get("/price_export/{entry_id}/file")
@router.get("/price_export/{entry_id}/csv")
def download_attachment(entry_id: int) -> StreamingResponse:
    try:
        exp, rnd = rc.load_deliverable(entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except PermissionError as exc:
        # 409：轮次未确认/失败，附件不能交付，detail 即值班员可读原因
        raise HTTPException(status_code=409, detail=str(exc.args[0])) from exc

    fmt = str(exp.get("导出格式", "CSV")).upper()
    if fmt == "JSON":
        return _json_response(exp, rnd)
    if fmt == "XLSX":
        return _xlsx_response(exp, rnd)
    return _csv_response(exp, rnd)


@router.post("/price_export/{entry_id}/start")
def start_export_alias(entry_id: int) -> dict[str, Any]:
    return rc.gate_export(entry_id, retry=False)


@router.post("/price_export/{entry_id}/retry")
def retry_export_alias(entry_id: int) -> dict[str, Any]:
    return rc.gate_export(entry_id, retry=True)


@router.post("/price_export/{entry_id}/finish")
async def finish_alias(entry_id: int, request: Request) -> dict[str, Any]:
    body = await _maybe_json(request)
    return rc.finish_export(entry_id, ok=not bool(body.get("failed")))


@router.get("/price_export/download/{entry_id}")
def download_attachment_by_module(entry_id: int) -> StreamingResponse:
    """路径别名：/api/price_export/download/{id}。"""
    return download_attachment(entry_id)
