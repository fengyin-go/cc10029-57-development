"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None


class ResolutionItem(BaseModel):
    item_id: str | None = None
    content: str | None = None
    owner: str | None = None
    kind: str | None = None


class ResolutionAttachmentPayload(BaseModel):
    """纪要附件：本次有效的多条决议条目。"""
    items: list[dict[str, Any]] = Field(default_factory=list)


class ResolutionDispatchPayload(BaseModel):
    """整组决议分发请求。

    request_id 可由调用方指定，用于把一次请求的多次重试归到同一号；
    item_ids 为空时按 mode 选择本次要送出的条目。
    """
    request_id: str | None = None
    item_ids: list[str] = Field(default_factory=list)
    mode: str = "batch"
    max_items: int | None = None


class AttachmentResult(BaseModel):
    ok: bool
    message: str
    meeting_id: int | None = None
    request_id: str | None = None
    meeting_status: str | None = None
    dispatch_status: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)
    results: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)


class ClaimListResult(BaseModel):
    items: list[dict[str, Any]]
    total: int
