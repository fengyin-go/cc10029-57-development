"""复盘会议领域服务：纪要登记、签认，以及按纪要附件整组分发决议到诊改任务。

整组决议分发把“一份纪要派出一个任务”扩展为“附件里的每条有效决议各派一个任务”：

* 只有签认后的纪要可以分发；
* 每条行动决议按自己的条目编号、责任人分别建任务，同名决议不会互相覆盖；
* 背景说明条目只留档，不生成任务；
* 没有责任人的条目只标记待补充，不会填出占位负责人或假任务；
* 任务按（纪要编号，决议条目编号）幂等，补派时复用第一次的任务号；
* 每次派发有独立请求号，任务、教师待办和受理回执都可回溯到对应决议与请求。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

ACTION_KIND = "行动决议"
BACKGROUND_KIND = "背景说明"
NOT_DISPATCHED = "待派发"
DISPATCHED = "已派发"
SKIPPED = "已跳过"
RETRY = "待补派"
NEEDS_OWNER = "待补充"
MISSING_OWNER_MARKERS = {"", "未指定", "待定", "待补充", "无", "暂无", "-", "—", "/", "none", "null"}
BACKGROUND_KIND_MARKERS = {
    "background", "bg", "note", "info", "context",
    "背景", "背景说明", "说明", "仅说明", "情况说明", "备注",
}
CONTENT_KEYS = ("content", "决议内容", "事项", "title", "内容", "决议事项")
OWNER_KEYS = ("owner", "责任人", "负责人", "执行人员", "教师", "承办人")
KIND_KEYS = ("kind", "条目类型", "类型", "事件类型")
ID_KEYS = ("item_id", "条目编号", "决议编号", "编号", "code")
ATTACHMENT_KEYS = ("决议附件", "附件", "决议列表", "resolutions", "items", "entries")
ACTION_FLAG_KEYS = ("action", "is_action", "行动项", "是否行动项")
BACKGROUND_FLAG_KEYS = ("背景", "仅说明", "仅背景", "is_background")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _first(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in raw and raw[key] is not None:
            return raw[key]
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raw = _text(value).casefold()
    return raw in {"1", "true", "yes", "y", "是", "对", "行动", "action"}


def _falsy(value: Any) -> bool:
    if isinstance(value, bool):
        return not value
    raw = _text(value).casefold()
    return raw in {"0", "false", "no", "n", "否", "不是", "非行动", "background", "背景"}


class ReviewMeetingService(BaseModuleService):
    MODULE = "review_meeting"
    ENTITY = "会议纪要"
    OPERATION_TERM = "签认纪要"
    LIST_FIELDS = ["会议主题", "参会人员", "召开日期", "决议事项", "分歧观点", "记录人员", "下次会议", "备注说明"]
    REQUIRED_FIELDS = ["会议主题", "参会人员", "召开日期"]
    STATUS_ORDER = ["草稿", "待签认", "已签认", "已分发"]
    ACTION_RULES = {
        "签认纪要": "已签认",
        "提出异议": "草稿",
        "分发给教师": "已分发",
        "整组决议分发": "已分发",
        "补派失败决议": "已分发",
    }
    NEGATIVE_ACTIONS = []

    # ------------------------------------------------------------------
    # 登记
    # ------------------------------------------------------------------
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记纪要，允许登记时一并带入附件中的多条决议。"""
        entry, missing = super().create_entry(values)
        if entry is not None:
            raw_items = _first(values, *ATTACHMENT_KEYS)
            if raw_items:
                try:
                    self.save_attachment(entry["id"], raw_items)
                except ValueError:
                    # 登记阶段附件不合法不应阻断纪要本身；调用方可通过附件接口修正。
                    pass
        return entry, missing

    # ------------------------------------------------------------------
    # 动作
    # ------------------------------------------------------------------
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """执行动作，含整组决议分发、单任务兼容分发和签认约束。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        # 整组决议分发 / 补派：必须先签认，签认后按附件条目逐条派发。
        if action in ("整组决议分发", "补派失败决议", "补派决议", "分发决议", "批量分发"):
            if entry.get("status") not in ("已签认", "已分发"):
                return None, f"{self.ENTITY} {entry_id} 必须先签认纪要，才能整组分发决议"
            mode = "retry" if "补" in action else "batch"
            result = self.dispatch_resolutions(entry_id, mode=mode)
            if result.get("error"):
                return None, result.get("message", "整组决议分发失败")
            return self.get_entry(entry_id), result.get("message", "整组决议分发完成")

        # 签认纪要：草稿或待签认可签认。
        if action == "签认纪要":
            if entry.get("status") not in ("待签认", "草稿"):
                return None, f"{self.ENTITY} {entry_id} 当前状态为'{entry.get('status')}'，不可签认"

        # 传统“分发给教师”：附件里已有决议条目时走整组分发，否则保留原来的单任务联动。
        if action == "分发给教师":
            if entry.get("status") != "已签认":
                return None, f"{self.ENTITY} {entry_id} 必须先签认纪要，才能分发"
            if entry.get("决议附件"):
                result = self.dispatch_resolutions(entry_id, mode="retry")
                if result.get("error"):
                    return None, result.get("message", "整组决议分发失败")
                return self.get_entry(entry_id), result.get("message", "整组决议分发完成")

        result_entry, message = super().run_action(entry_id, action)

        # 保留旧链路：没有附件时，决议事项含“任务”仍生成一条诊改任务。
        if result_entry and action == "分发给教师":
            resolution = entry.get("决议事项", "")
            meeting_theme = entry.get("会议主题", "")
            if resolution and "任务" in str(resolution):
                self._maybe_create_improve_task(meeting_theme, resolution, entry_id)

        return result_entry, message

    # ------------------------------------------------------------------
    # 附件维护
    # ------------------------------------------------------------------
    def _extract_raw_items(self, raw_items: Any) -> list[Any]:
        """把附件入口的各种形状统一成待规范化的条目列表。"""
        if isinstance(raw_items, dict):
            nested = _first(raw_items, *ATTACHMENT_KEYS)
            if nested is not None and nested is not raw_items:
                return self._extract_raw_items(nested)
            return [raw_items]
        if isinstance(raw_items, (list, tuple)):
            return list(raw_items)
        if raw_items in (None, ""):
            return []
        if isinstance(raw_items, str):
            text = raw_items.strip()
            if text[:1] in "[{":
                try:
                    return self._extract_raw_items(json.loads(text))
                except (ValueError, TypeError):
                    pass
            # 纯文本附件：按行拆成条目，忽略空行。
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return lines or [text]
        return [raw_items]

    def _kind_from_raw(self, raw: dict[str, Any]) -> str:
        for key in BACKGROUND_FLAG_KEYS:
            if key in raw and _truthy(raw.get(key)):
                return BACKGROUND_KIND
        for key in ACTION_FLAG_KEYS:
            if key in raw:
                value = raw.get(key)
                if _falsy(value):
                    return BACKGROUND_KIND
                if _truthy(value):
                    return ACTION_KIND
        kind_value = _first(raw, *KIND_KEYS)
        if kind_value is not None:
            lowered = _text(kind_value).casefold()
            if any(marker in lowered for marker in BACKGROUND_KIND_MARKERS):
                return BACKGROUND_KIND
            return ACTION_KIND
        # 未标类型时，只认明确的背景标记，避免把"背景调查专项"这类工作误判为说明。
        content = _text(_first(raw, *CONTENT_KEYS))
        for prefix in ("背景：", "背景:", "背景说明：", "背景说明:", "仅作背景说明", "以下为背景"):
            if content.startswith(prefix):
                return BACKGROUND_KIND
        if content.endswith(("（背景）", "(背景)", "（仅说明）", "(仅说明)")):
            return BACKGROUND_KIND
        return ACTION_KIND

    def _owner_is_missing(self, owner: str) -> bool:
        return owner.strip().casefold() in MISSING_OWNER_MARKERS

    def _next_item_id(self, used: set[str], reserved: set[str] | None = None) -> str:
        # reserved 保存附件里已有条目的编号：新增条目不能借用它们，
        # 否则会和补存回来的已派发条目撞号，任务关联就指错了决议。
        taken = used | (reserved or set())
        serial = 1
        while f"R{serial}" in taken:
            serial += 1
        return f"R{serial}"

    def _match_prior(
        self,
        raw: dict[str, Any],
        index: int,
        prior_items: list[dict[str, Any]],
        prior_by_id: dict[str, dict[str, Any]],
        used_ids: set[str],
        explicit_id: str,
        content: str,
        owner: str,
    ) -> dict[str, Any] | None:
        if explicit_id and explicit_id not in used_ids:
            prior = prior_by_id.get(explicit_id)
            if prior is not None:
                return prior
        if explicit_id:
            return None
        # 前端未带条目编号时，先按内容+责任人找回原条目，再退回同位置条目。
        for candidate in prior_items:
            cid = _text(candidate.get("条目编号"))
            if cid in used_ids:
                continue
            if _text(candidate.get("决议内容")) == content and _text(candidate.get("责任人")) == owner:
                return candidate
        if index < len(prior_items):
            candidate = prior_items[index]
            cid = _text(candidate.get("条目编号"))
            # 已派发条目只认显式条目编号，避免把新插入的条目错认成旧条目而误报冲突。
            if cid and cid not in used_ids and not candidate.get("任务号"):
                return candidate
        return None

    def _normalise_item(
        self,
        raw: Any,
        index: int,
        prior_items: list[dict[str, Any]],
        prior_by_id: dict[str, dict[str, Any]],
        used_ids: set[str],
        reserved_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        if isinstance(raw, str):
            raw = {"决议内容": raw}
        elif not isinstance(raw, dict):
            raw = {"决议内容": _text(raw)}
        raw = dict(raw)

        explicit_id = _text(_first(raw, *ID_KEYS))
        has_content = any(key in raw for key in CONTENT_KEYS)
        has_owner = any(key in raw for key in OWNER_KEYS)
        has_kind = (
            any(key in raw for key in KIND_KEYS)
            or any(key in raw for key in ACTION_FLAG_KEYS)
            or any(key in raw for key in BACKGROUND_FLAG_KEYS)
        )
        incoming_content = _text(_first(raw, *CONTENT_KEYS))
        incoming_owner = _text(_first(raw, *OWNER_KEYS))

        prior = self._match_prior(
            raw, index, prior_items, prior_by_id, used_ids,
            explicit_id, incoming_content, incoming_owner,
        )
        if prior is not None:
            item_id = _text(prior.get("条目编号")) or self._next_item_id(used_ids, reserved_ids)
        elif explicit_id and explicit_id not in used_ids:
            item_id = explicit_id
        else:
            item_id = self._next_item_id(used_ids, reserved_ids)

        old_content = _text(prior.get("决议内容")) if prior else ""
        old_owner = _text(prior.get("责任人")) if prior else ""
        old_kind = _text(prior.get("条目类型")) if prior else ""
        content = incoming_content if has_content else old_content
        owner = incoming_owner if has_owner else old_owner
        if has_kind:
            kind = self._kind_from_raw(raw)
        elif prior:
            kind = old_kind or ACTION_KIND
        else:
            kind = self._kind_from_raw(raw)

        # 已派发条目的内容/责任人不能再改，否则教师手里的任务号会指向另一项工作。
        if prior is not None and prior.get("任务号"):
            if has_content and content != old_content:
                raise ValueError(
                    f"决议条目 {item_id} 已生成诊改任务 {prior.get('任务号')}，"
                    "不能修改决议内容；如需新增工作请添加新条目"
                )
            if has_owner and owner != old_owner:
                raise ValueError(
                    f"决议条目 {item_id} 已生成诊改任务 {prior.get('任务号')}，"
                    "不能更换责任人；如需转交请另行发起任务调整"
                )
            if has_kind and kind != old_kind:
                raise ValueError(
                    f"决议条目 {item_id} 已生成诊改任务 {prior.get('任务号')}，不能改条目类型"
                )

        item: dict[str, Any] = dict(prior) if prior else {}
        item["条目编号"] = item_id
        item["item_id"] = item_id
        item["决议内容"] = content
        item["content"] = content
        item["责任人"] = owner
        item["owner"] = owner
        item["条目类型"] = kind
        item["kind"] = "background" if kind == BACKGROUND_KIND else "action"
        item.setdefault("任务号", None)
        item.setdefault("task_id", None)
        item.setdefault("请求号", "")
        item.setdefault("request_id", "")
        item.setdefault("失败原因", "")
        item.setdefault("error", "")
        if "派发状态" not in item:
            item["派发状态"] = NOT_DISPATCHED

        # 未派发条目被修改后回到待派发；已派发条目保留原派发状态与任务号。
        if prior is not None and not prior.get("任务号"):
            core_changed = (
                _text(prior.get("决议内容")) != content
                or _text(prior.get("责任人")) != owner
                or _text(prior.get("条目类型")) != kind
            )
            if core_changed:
                item["派发状态"] = NOT_DISPATCHED
                item["任务号"] = None
                item["task_id"] = None
                item["请求号"] = ""
                item["request_id"] = ""
                item["失败原因"] = ""
                item["error"] = ""
        item["dispatch_status"] = item["派发状态"]
        return item

    def save_attachment(self, entry_id: int, raw_items: Any) -> list[dict[str, Any]]:
        """保存/更新纪要附件中的多条决议，保留已派发条目的任务关联。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            raise ValueError(f"{self.ENTITY} {entry_id} 不存在或已归档")

        incoming = self._extract_raw_items(raw_items)
        prior_items = [dict(item) for item in (entry.get("决议附件") or []) if isinstance(item, dict)]
        prior_by_id = {_text(item.get("条目编号")): item for item in prior_items if item.get("条目编号")}
        reserved_ids = set(prior_by_id)
        used_ids: set[str] = set()
        merged: list[dict[str, Any]] = []
        for index, raw in enumerate(incoming):
            item = self._normalise_item(
                raw, index, prior_items, prior_by_id, used_ids, reserved_ids
            )
            used_ids.add(item["条目编号"])
            merged.append(item)

        # 已派发的条目不能因为一次前端重存而凭空消失，任务和回执仍需可追溯。
        for prior in prior_items:
            item_id = _text(prior.get("条目编号"))
            if item_id and item_id not in used_ids and prior.get("任务号"):
                merged.append(dict(prior))
                used_ids.add(item_id)

        entry["决议附件"] = merged
        self._refresh_dispatch_state(entry)
        return merged

    def attachment_view(self, entry_id: int) -> dict[str, Any] | None:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None
        records = entry.get("决议分发记录") or []
        return {
            "meeting_id": entry_id,
            "meeting_status": entry.get("status", ""),
            "dispatch_status": entry.get("决议分发状态", "未分发"),
            "request_id": entry.get("决议分发请求号", ""),
            "items": [self._public_item(item, entry) for item in (entry.get("决议附件") or [])],
            "last_dispatch": records[-1] if records else None,
            "summary": self._dispatch_summary(entry),
        }

    # ------------------------------------------------------------------
    # 整组分发
    # ------------------------------------------------------------------
    def _next_request_id(self, entry: dict[str, Any], entry_id: int) -> str:
        records = entry.get("决议分发记录") or []
        return f"REQ-M{entry_id}-{len(records) + 1:02d}"

    def _find_linked_task(self, meeting_id: int, item_id: str) -> dict[str, Any] | None:
        source = f"meeting:{meeting_id}"
        for row in store.rows("improve_task"):
            if _text(row.get("来源纪要")) == source and _text(row.get("来源决议")) == item_id:
                return row
        # 兜底：早期版本只把关联写进任务描述。
        needle = f"[meeting:{meeting_id}/resolution:{item_id}]"
        for row in store.rows("improve_task"):
            if needle in str(row.get("任务描述", "")):
                return row
        return None

    def _create_task_for_item(
        self, entry: dict[str, Any], item: dict[str, Any], request_id: str
    ) -> dict[str, Any]:
        meeting_id = int(entry["id"])
        item_id = _text(item.get("条目编号"))
        content = _text(item.get("决议内容"))
        owner = _text(item.get("责任人"))
        task_rows = store.rows("improve_task")
        task_id = max((int(r.get("id", 0)) for r in task_rows), default=0) + 1
        now = _now()
        receipt = {
            "回执号": f"RCPT-{task_id}",
            "状态": "待受理",
            "责任人": owner,
            "决议条目": item_id,
            "决议内容": content,
            "来源纪要": f"meeting:{meeting_id}",
            "派发请求号": request_id,
            "生成时间": now,
            "受理时间": "",
            "受理人": "",
            "受理说明": "",
        }
        task = {
            "id": task_id,
            "任务描述": f"会议决议（{item_id}）：{content} [meeting:{meeting_id}/resolution:{item_id}]",
            "承接方案": f"复盘会议纪要 meeting:{meeting_id}",
            "执行人员": owner,
            "起止日期": "",
            "验收标准": "按会议决议完成并提交验收",
            "督办人员": "",
            "任务权重": "",
            "备注说明": "由复盘会议整组决议分发自动生成",
            "status": "待认领",
            "pending": True,
            "abnormal": False,
            "来源纪要": f"meeting:{meeting_id}",
            "来源决议": item_id,
            "决议内容": content,
            "派发请求号": request_id,
            "受理回执": receipt,
            "教师待办": {
                "任务号": task_id,
                "责任人": owner,
                "状态": "待认领",
                "决议条目": item_id,
                "决议内容": content,
                "派发请求号": request_id,
            },
        }
        task_rows.append(task)
        return task

    def dispatch_resolutions(
        self,
        entry_id: int,
        *,
        request_id: str | None = None,
        item_ids: list[str] | None = None,
        mode: str = "batch",
        max_items: int | None = None,
    ) -> dict[str, Any]:
        """按附件逐条分发决议；每条独立成败，失败不影响已经派出的条目。

        mode="batch" 处理本次选中的全部条目，mode="retry" 只补派尚未派出的条目。
        max_items 用于分批送出（例如连接中断后从下一项继续）。
        """
        entry = self.get_entry(entry_id)
        if entry is None:
            return {
                "ok": False,
                "error": "not_found",
                "message": f"{self.ENTITY} {entry_id} 不存在或已归档",
            }
        if entry.get("status") not in ("已签认", "已分发"):
            return {
                "ok": False,
                "error": "not_signed",
                "message": f"{self.ENTITY} {entry_id} 必须先签认纪要，才能整组分发决议",
            }
        if not entry.get("决议附件"):
            return {
                "ok": False,
                "error": "empty_attachment",
                "message": f"{self.ENTITY} {entry_id} 的附件中还没有决议条目",
            }

        try:
            self.save_attachment(entry_id, entry.get("决议附件"))
        except ValueError as exc:
            return {"ok": False, "error": "invalid_attachment", "message": str(exc)}

        items: list[dict[str, Any]] = entry["决议附件"]
        wanted = {str(value) for value in (item_ids or []) if str(value)}
        if wanted:
            targets = [item for item in items if _text(item.get("条目编号")) in wanted]
        elif mode == "retry":
            targets = [
                item for item in items
                if _text(item.get("派发状态")) not in (DISPATCHED, SKIPPED)
            ]
        else:
            targets = list(items)

        if max_items is not None:
            targets = targets[: max(0, int(max_items))]

        req_id = _text(request_id) or self._next_request_id(entry, entry_id)
        results: list[dict[str, Any]] = []
        summary = {
            "total": len(targets),
            "created": 0,
            "reused": 0,
            "skipped": 0,
            "needs_owner": 0,
            "failed": 0,
        }

        for item in targets:
            kind = _text(item.get("条目类型"))
            content = _text(item.get("决议内容"))
            owner = _text(item.get("责任人"))

            if kind == BACKGROUND_KIND:
                item["派发状态"] = SKIPPED
                item["dispatch_status"] = SKIPPED
                item["失败原因"] = "背景说明条目不产生诊改任务"
                item["error"] = item["失败原因"]
                summary["skipped"] += 1
                results.append(self._item_result(item, "skipped", entry))
                continue

            if self._owner_is_missing(owner):
                item["派发状态"] = NEEDS_OWNER
                item["dispatch_status"] = NEEDS_OWNER
                item["失败原因"] = "未指定责任人，未生成诊改任务"
                item["error"] = item["失败原因"]
                item["任务号"] = None
                item["task_id"] = None
                summary["needs_owner"] += 1
                results.append(self._item_result(item, "needs_owner", entry))
                continue

            if not content:
                item["派发状态"] = RETRY
                item["dispatch_status"] = RETRY
                item["失败原因"] = "决议内容为空，未生成诊改任务"
                item["error"] = item["失败原因"]
                summary["failed"] += 1
                results.append(self._item_result(item, "failed", entry))
                continue

            existing = self._find_linked_task(entry_id, _text(item.get("条目编号")))
            if existing is not None:
                item["任务号"] = int(existing.get("id", 0))
                item["task_id"] = item["任务号"]
                item["派发状态"] = DISPATCHED
                item["dispatch_status"] = DISPATCHED
                item["请求号"] = _text(existing.get("派发请求号")) or req_id
                item["request_id"] = item["请求号"]
                item["失败原因"] = ""
                item["error"] = ""
                summary["reused"] += 1
                results.append(self._item_result(item, "reused", entry, task_id=item["任务号"]))
                continue

            try:
                task = self._create_task_for_item(entry, item, req_id)
            except Exception as exc:  # 单条失败不拖累同批其他决议
                item["派发状态"] = RETRY
                item["dispatch_status"] = RETRY
                item["失败原因"] = f"派发失败：{exc}"
                item["error"] = item["失败原因"]
                summary["failed"] += 1
                results.append(self._item_result(item, "failed", entry))
                continue

            item["任务号"] = int(task["id"])
            item["task_id"] = item["任务号"]
            item["派发状态"] = DISPATCHED
            item["dispatch_status"] = DISPATCHED
            item["请求号"] = req_id
            item["request_id"] = req_id
            item["失败原因"] = ""
            item["error"] = ""
            summary["created"] += 1
            results.append(self._item_result(item, "created", entry, task_id=int(task["id"])))

        entry["决议附件"] = items
        pending = [
            item for item in items
            if _text(item.get("条目类型")) != BACKGROUND_KIND
            and _text(item.get("派发状态")) == NOT_DISPATCHED
        ]
        summary["pending"] = len(pending)
        record = {
            "请求号": req_id,
            "时间": _now(),
            "模式": mode,
            "汇总": dict(summary),
            "条目": results,
        }
        entry["决议分发请求号"] = req_id
        entry.setdefault("决议分发记录", []).append(record)
        self._refresh_dispatch_state(entry)

        incomplete = summary["failed"] + summary["needs_owner"] + summary["pending"]
        ok = incomplete == 0
        if ok:
            message = (
                f"整组决议分发完成：新建 {summary['created']} 项，复用 {summary['reused']} 项，"
                f"跳过 {summary['skipped']} 项背景说明"
            )
        else:
            message = (
                f"整组决议分发未全部完成：新建 {summary['created']} 项，复用 {summary['reused']} 项，"
                f"待补派 {summary['failed'] + summary['pending']} 项，"
                f"待补充责任人 {summary['needs_owner']} 项；"
                f"已派出的任务不受影响，可重开原纪要补派"
            )
        return {
            "ok": ok,
            "message": message,
            "meeting_id": entry_id,
            "request_id": req_id,
            "meeting_status": entry.get("status", ""),
            "dispatch_status": entry.get("决议分发状态", "未分发"),
            "items": [self._public_item(item, entry) for item in items],
            "results": results,
            "summary": summary,
        }

    def resolution_status(self, entry_id: int, item_id: str) -> dict[str, Any] | None:
        """查询单个决议条目的办理状态，回答“已获准办理还是等待分发恢复”。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return None
        for item in entry.get("决议附件") or []:
            if _text(item.get("条目编号")) != _text(item_id):
                continue
            task = None
            if item.get("任务号") is not None:
                task = store.find("improve_task", int(item.get("任务号")))
            return {
                **self._public_item(item, entry),
                "receipt": (task or {}).get("受理回执"),
            }
        return None

    def public_items(self, entry_id: int) -> list[dict[str, Any]]:
        entry = self.get_entry(entry_id)
        if entry is None:
            return []
        return [self._public_item(item, entry) for item in (entry.get("决议附件") or [])]

    def _public_item(self, item: dict[str, Any], entry: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "条目编号": _text(item.get("条目编号")),
            "决议内容": _text(item.get("决议内容")),
            "责任人": _text(item.get("责任人")),
            "条目类型": _text(item.get("条目类型")),
            "派发状态": _text(item.get("派发状态")),
            "任务号": item.get("任务号"),
            "请求号": _text(item.get("请求号")),
            "失败原因": _text(item.get("失败原因")),
            "已获准办理": item.get("任务号") is not None,
            "hint": self._item_hint(item, entry),
            "meeting_status": (entry or {}).get("status", ""),
        }

    def _item_hint(self, item: dict[str, Any], entry: dict[str, Any] | None = None) -> str:
        status = _text(item.get("派发状态"))
        if item.get("任务号") is not None:
            return f"已获准办理，任务号 {item.get('任务号')}，教师可凭该任务号认领"
        if _text(item.get("条目类型")) == BACKGROUND_KIND:
            return "背景说明条目，不产生诊改任务"
        if status == NEEDS_OWNER:
            return "尚未派发：缺少责任人，需补充责任人后再分发"
        if status == RETRY:
            return "尚未获准办理：本次派发未完成，请等待本次分发恢复后补派"
        if status == NOT_DISPATCHED:
            if (entry or {}).get("决议分发记录"):
                return "尚未获准办理：本次分发未送到这一项，请等待本次分发恢复后补派"
            return "尚未派发：等待主任从纪要附件送出"
        return "尚未派发：等待本次分发送出"

    def _item_result(
        self, item: dict[str, Any], outcome: str, entry: dict[str, Any] | None = None, **extra: Any
    ) -> dict[str, Any]:
        result = {
            "条目编号": _text(item.get("条目编号")),
            "决议内容": _text(item.get("决议内容")),
            "责任人": _text(item.get("责任人")),
            "条目类型": _text(item.get("条目类型")),
            "派发状态": _text(item.get("派发状态")),
            "任务号": item.get("任务号"),
            "请求号": _text(item.get("请求号")),
            "失败原因": _text(item.get("失败原因")),
            "已获准办理": item.get("任务号") is not None,
            "hint": self._item_hint(item, entry),
            "outcome": outcome,
        }
        result.update(extra)
        return result

    def _dispatch_summary(self, entry: dict[str, Any]) -> dict[str, int]:
        items = entry.get("决议附件") or []
        actionable = [item for item in items if _text(item.get("条目类型")) != BACKGROUND_KIND]
        return {
            "total": len(items),
            "actionable": len(actionable),
            "dispatched": sum(1 for item in actionable if _text(item.get("派发状态")) == DISPATCHED),
            "pending": sum(1 for item in actionable if _text(item.get("派发状态")) in (NOT_DISPATCHED, RETRY)),
            "needs_owner": sum(1 for item in actionable if _text(item.get("派发状态")) == NEEDS_OWNER),
            "background": len(items) - len(actionable),
        }

    def _refresh_dispatch_state(self, entry: dict[str, Any]) -> None:
        """根据附件条目的派发结果刷新纪要整体状态。"""
        items = entry.get("决议附件") or []
        if not items:
            entry["决议分发状态"] = "未分发"
            return
        actionable = [item for item in items if _text(item.get("条目类型")) != BACKGROUND_KIND]
        if not actionable:
            if entry.get("status") in ("已签认", "已分发"):
                entry["status"] = "已分发"
                entry["pending"] = False
            entry["abnormal"] = False
            entry["决议分发状态"] = "无需派发"
            return
        dispatched = [item for item in actionable if _text(item.get("派发状态")) == DISPATCHED]
        failed = [
            item for item in actionable
            if _text(item.get("派发状态")) in (RETRY, NEEDS_OWNER)
        ]
        if len(dispatched) == len(actionable):
            entry["status"] = "已分发"
            entry["pending"] = False
            entry["abnormal"] = False
            entry["决议分发状态"] = "已全部分发"
            return
        entry["决议分发状态"] = "部分分发" if dispatched else "待分发"
        entry["abnormal"] = bool(failed)
        if entry.get("status") == "已分发":
            # 新增或补派失败后回到已签认，允许重开原纪要继续补派。
            entry["status"] = "已签认"
        if entry.get("status") == "已签认":
            entry["pending"] = True

    # ------------------------------------------------------------------
    # 兼容旧链路
    # ------------------------------------------------------------------
    def _maybe_create_improve_task(self, theme: str, resolution: str, meeting_id: int) -> None:
        """根据会议决议创建单条诊改任务：旧数据没有附件时保留原行为。"""
        task_rows = store.rows("improve_task")
        for row in task_rows:
            existing_desc = str(row.get("任务描述", ""))
            if f"meeting:{meeting_id}" in existing_desc:
                return

        new_id = max((int(r.get("id", 0)) for r in task_rows), default=0) + 1
        task_rows.append({
            "id": new_id,
            "任务描述": f"会议决议：{theme} [meeting:{meeting_id}]",
            "承接方案": resolution,
            "执行人员": "",
            "起止日期": "",
            "验收标准": "",
            "督办人员": "",
            "任务权重": "",
            "备注说明": "由复盘会议决议自动生成",
            "status": "待认领",
            "pending": True,
            "abnormal": False,
        })
