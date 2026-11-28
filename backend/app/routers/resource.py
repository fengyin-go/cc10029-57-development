"""统一资源接口：查询、导出、登记和动作按模块 key 分派到领域服务。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import (
    ActionPayload,
    ActionResult,
    AttachmentResult,
    ClaimListResult,
    EntryPayload,
    PageResult,
    ResolutionAttachmentPayload,
    ResolutionDispatchPayload,
)
from app.services import service_for
from app.services.improve_task import ImproveTaskService
from app.services.review_meeting import ReviewMeetingService

router = APIRouter(prefix="/api", tags=["业务资源"])


def require_service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


@router.get("/improve_task/claims", response_model=ClaimListResult)
def claim_list(
    owner: str | None = Query(default=None),
    meeting_id: int | None = Query(default=None),
    keyword: str | None = Query(default=None),
    include_claimed: bool = Query(default=True),
) -> ClaimListResult:
    """教师认领列表：任务带回来源决议、派发请求号和受理回执。"""
    service = require_service("improve_task")
    assert isinstance(service, ImproveTaskService)
    items = service.claim_list(
        owner=owner,
        meeting_id=meeting_id,
        keyword=keyword,
        include_claimed=include_claimed,
    )
    return ClaimListResult(items=items, total=len(items))


@router.get("/improve_task/{entry_id}/receipt", response_model=dict)
def task_receipt(entry_id: int) -> dict[str, Any]:
    """查看一条任务的受理回执和认领记录。"""
    service = require_service("improve_task")
    assert isinstance(service, ImproveTaskService)
    receipt = service.receipt_view(entry_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail=f"诊改任务 {entry_id} 不存在或已归档")
    return receipt


@router.get("/review_meeting/{meeting_id}/attachment", response_model=dict)
def meeting_attachment(meeting_id: int) -> dict[str, Any]:
    """读取纪要附件中的多条决议及其派发状态。"""
    service = require_service("review_meeting")
    assert isinstance(service, ReviewMeetingService)
    view = service.attachment_view(meeting_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"会议纪要 {meeting_id} 不存在或已归档")
    return view


@router.put("/review_meeting/{meeting_id}/attachment", response_model=AttachmentResult)
def save_meeting_attachment(
    meeting_id: int, payload: ResolutionAttachmentPayload
) -> AttachmentResult:
    """登记或修订纪要附件中的多条决议；已派发条目的任务关联保持不变。"""
    service = require_service("review_meeting")
    assert isinstance(service, ReviewMeetingService)
    entry = service.get_entry(meeting_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"会议纪要 {meeting_id} 不存在或已归档")
    try:
        items = service.save_attachment(meeting_id, payload.items)
    except ValueError as exc:
        return AttachmentResult(ok=False, message=str(exc), meeting_id=meeting_id)
    return AttachmentResult(
        ok=True,
        message=f"会议纪要附件已保存 {len(items)} 条决议",
        meeting_id=meeting_id,
        meeting_status=entry.get("status", ""),
        dispatch_status=entry.get("决议分发状态", ""),
        items=service.public_items(meeting_id),
        summary=service._dispatch_summary(entry),
    )


@router.post("/review_meeting/{meeting_id}/resolution-dispatch", response_model=AttachmentResult)
def dispatch_resolutions(
    meeting_id: int, payload: ResolutionDispatchPayload
) -> AttachmentResult:
    """从纪要附件整组分发决议，逐条生成诊改任务、教师待办和受理回执。"""
    service = require_service("review_meeting")
    assert isinstance(service, ReviewMeetingService)
    result = service.dispatch_resolutions(
        meeting_id,
        request_id=payload.request_id,
        item_ids=payload.item_ids,
        mode=payload.mode,
        max_items=payload.max_items,
    )
    if result.get("error") in ("not_found",):
        raise HTTPException(status_code=404, detail=result.get("message", "会议纪要不存在"))
    return AttachmentResult(**{
        key: value for key, value in result.items()
        if key in AttachmentResult.model_fields
    })


@router.get("/review_meeting/{meeting_id}/resolutions/{item_id}", response_model=dict)
def resolution_status(meeting_id: int, item_id: str) -> dict[str, Any]:
    """查询单条决议是否已获准办理，还是等待本次分发恢复。"""
    service = require_service("review_meeting")
    assert isinstance(service, ReviewMeetingService)
    status = service.resolution_status(meeting_id, item_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"会议纪要 {meeting_id} 中不存在决议条目 {item_id}")
    return status


@router.get("/{module}", response_model=PageResult[dict])
def list_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, total = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{module}/export")
def export_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, _ = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=1, size=1_000_000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *service.LIST_FIELDS, "status"])
    for item in items:
        values = [item.get("id"), *(item.get(field, "") for field in service.LIST_FIELDS),
                  item.get("status", "")]
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{module}.csv"'},
    )


@router.get("/{module}/{entry_id}", response_model=dict)
def get_entry(module: str, entry_id: int) -> dict[str, Any]:
    service = require_service(module)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_id} 不存在或已归档")
    return entry


@router.post("/{module}", response_model=ActionResult)
def create_entry(module: str, payload: EntryPayload) -> ActionResult:
    service = require_service(module)
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"{service.ENTITY}已登记", entry=entry)


@router.post("/{module}/{entry_id}/actions", response_model=ActionResult)
def run_action(module: str, entry_id: int, payload: ActionPayload) -> ActionResult:
    service = require_service(module)
    entry, message = service.run_action(entry_id, payload.action.strip())
    return ActionResult(ok=entry is not None, message=message, entry=entry)
