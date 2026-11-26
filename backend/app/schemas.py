"""统一资源接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionPayload(BaseModel):
    # 允许 {"action": "更正基线", "基线价格": 125} 这类顶层业务参数
    model_config = ConfigDict(extra="allow")

    action: str
    remark: str | None = None
    request_key: str | None = None
    requestId: str | None = None
    params: dict[str, Any] | None = None
    values: dict[str, Any] | None = None


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: bool
    message: str = ""
    entry: dict[str, Any] | None = None
    receipt: dict[str, Any] | None = None
    round: dict[str, Any] | None = None
    export_task: dict[str, Any] | None = None
    data: dict[str, Any] | None = None
    duplicate: bool | None = None
    conflict: bool | None = None
    waiting: bool | None = None


class EntryPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None
