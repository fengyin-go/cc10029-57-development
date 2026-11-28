"""诊改任务领域服务：任务分解、认领、执行、验收，含跨模块关联方案校验。

会议整组决议分发来的任务会带有 `来源纪要` / `来源决议` / `派发请求号`，
认领时同步写回受理回执，教师凭第一次拿到的任务号即可找到原决议。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.services.cross_module import check_entity_status
from app.store import store


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


class ImproveTaskService(BaseModuleService):
    MODULE = "improve_task"
    ENTITY = "诊改任务"
    OPERATION_TERM = "认领任务"
    LIST_FIELDS = ["任务描述", "承接方案", "执行人员", "起止日期", "验收标准", "督办人员", "任务权重", "备注说明"]
    REQUIRED_FIELDS = ["任务描述", "承接方案", "执行人员"]
    STATUS_ORDER = ["草稿", "待认领", "执行中", "已验收"]
    ACTION_RULES = {"认领任务": "执行中", "启动执行": "执行中", "申请验收": "已验收"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """创建诊改任务：承接方案引用必须是已审核通过的改进方案。"""
        source_plan = str(values.get("承接方案", "") or "").strip()

        # 尝试解析方案ID引用
        if source_plan.startswith("plan:"):
            plan_id_str = source_plan.split(":", 1)[1]
            try:
                plan_id = int(plan_id_str)
                check_entity_status(
                    "improve_plan", plan_id, ["审核通过"],
                    "诊改任务的承接方案"
                )
            except ValueError:
                pass

        return super().create_entry(values)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """执行动作，含状态推进约束和受理回执写回。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        # 认领任务：只能从待认领状态认领
        if action == "认领任务":
            if entry.get("status") not in ("待认领", "草稿"):
                return None, f"{self.ENTITY} {entry_id} 当前状态为'{entry.get('status')}'，不可认领"
            if not _text(entry.get("执行人员")):
                return None, f"{self.ENTITY} {entry_id} 缺少执行人员，无法认领"

        # 申请验收：必须已在执行中
        if action == "申请验收":
            if entry.get("status") != "执行中":
                return None, f"{self.ENTITY} {entry_id} 当前状态为'{entry.get('status')}'，必须先执行再申请验收"

        result, message = super().run_action(entry_id, action)

        # 认领成功的同时生成受理回执，回执保留原任务号与派发请求号。
        if result is not None and action in ("认领任务", "启动执行"):
            self._accept_receipt(result, action)

        return result, message

    def _accept_receipt(self, task: dict[str, Any], action: str) -> None:
        owner = _text(task.get("执行人员")) or "执行人员"
        now = _now()
        receipt = task.get("受理回执")
        if not isinstance(receipt, dict):
            receipt = {
                "回执号": f"RCPT-{task.get('id')}",
                "决议条目": _text(task.get("来源决议")),
                "决议内容": _text(task.get("决议内容")),
                "来源纪要": _text(task.get("来源纪要")),
                "派发请求号": _text(task.get("派发请求号")),
                "生成时间": "",
            }
        receipt["状态"] = "已受理"
        receipt["受理时间"] = now
        receipt["受理人"] = owner
        receipt["受理说明"] = f"教师已{action}，任务进入执行"
        task["受理回执"] = receipt

        todo = task.get("教师待办")
        if not isinstance(todo, dict):
            todo = {
                "任务号": task.get("id"),
                "责任人": owner,
                "决议条目": _text(task.get("来源决议")),
                "决议内容": _text(task.get("决议内容")),
                "派发请求号": _text(task.get("派发请求号")),
            }
        todo["状态"] = "已受理"
        todo["受理时间"] = now
        task["教师待办"] = todo

        task.setdefault("认领记录", []).append({
            "任务号": task.get("id"),
            "操作": action,
            "受理人": owner,
            "时间": now,
            "决议条目": _text(task.get("来源决议")),
            "派发请求号": _text(task.get("派发请求号")),
        })

    # ------------------------------------------------------------------
    # 认领列表与回执视图
    # ------------------------------------------------------------------
    def claim_list(
        self,
        *,
        owner: str | None = None,
        meeting_id: int | None = None,
        keyword: str | None = None,
        include_claimed: bool = True,
    ) -> list[dict[str, Any]]:
        """教师待办/认领列表：每条任务带回对应的决议条目和派发请求。"""
        rows = list(store.rows(self.MODULE))
        results: list[dict[str, Any]] = []
        for row in rows:
            if not include_claimed and row.get("status") not in ("待认领", "草稿"):
                continue
            source_meeting = _text(row.get("来源纪要"))
            source_item = _text(row.get("来源决议"))
            # 只展示可直接认领或已受理的会议任务；普通任务也保留。
            if owner and _text(row.get("执行人员")) != owner:
                continue
            if meeting_id is not None and source_meeting != f"meeting:{meeting_id}":
                continue
            if keyword:
                needle = keyword.casefold()
                haystack = " ".join(
                    _text(row.get(key)) for key in
                    ("任务描述", "执行人员", "来源纪要", "来源决议", "决议内容", "派发请求号")
                ).casefold()
                if needle not in haystack:
                    continue
            meeting_title = ""
            resolution_status = ""
            if source_meeting.startswith("meeting:"):
                meeting = store.find("review_meeting", int(source_meeting.split(":", 1)[1]))
                if meeting is not None:
                    meeting_title = _text(meeting.get("会议主题"))
                    for item in meeting.get("决议附件") or []:
                        if _text(item.get("条目编号")) == source_item:
                            resolution_status = _text(item.get("派发状态"))
                            break
            results.append({
                "任务号": int(row.get("id", 0)),
                "任务描述": _text(row.get("任务描述")),
                "执行人员": _text(row.get("执行人员")),
                "status": row.get("status", ""),
                "来源纪要": source_meeting,
                "会议主题": meeting_title,
                "来源决议": source_item,
                "决议内容": _text(row.get("决议内容")),
                "决议派发状态": resolution_status,
                "派发请求号": _text(row.get("派发请求号")),
                "受理回执": row.get("受理回执"),
                "教师待办": row.get("教师待办"),
                "可认领": row.get("status") in ("待认领", "草稿"),
            })
        return results

    def receipt_view(self, entry_id: int) -> dict[str, Any] | None:
        task = self.get_entry(entry_id)
        if task is None:
            return None
        return {
            "任务号": int(task.get("id", 0)),
            "任务描述": _text(task.get("任务描述")),
            "执行人员": _text(task.get("执行人员")),
            "status": task.get("status", ""),
            "来源纪要": _text(task.get("来源纪要")),
            "来源决议": _text(task.get("来源决议")),
            "决议内容": _text(task.get("决议内容")),
            "派发请求号": _text(task.get("派发请求号")),
            "受理回执": task.get("受理回执"),
            "认领记录": task.get("认领记录") or [],
        }
