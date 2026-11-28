"""整组决议分发专项测试：附件条目、逐条派发、断点补派与受理回执。"""
from __future__ import annotations

import pytest

from app.services.improve_task import ImproveTaskService
from app.services.review_meeting import ReviewMeetingService
from app.store import store


def clear_all() -> None:
    for name in store.module_names():
        store.rows(name).clear()


def signed_meeting(service: ReviewMeetingService, items, *, theme="M-37 期中教学复盘"):
    meeting, _ = service.create_entry({
        "会议主题": theme,
        "参会人员": "主任、王老师、李老师",
        "召开日期": "2026-11-20",
        "决议事项": "见附件",
    })
    service.save_attachment(meeting["id"], items)
    service.run_action(meeting["id"], "签认纪要")
    assert meeting["status"] == "已签认"
    return meeting


def tasks_for(meeting_id: int) -> list[dict]:
    return [
        row for row in store.rows("improve_task")
        if row.get("来源纪要") == f"meeting:{meeting_id}"
    ]


@pytest.fixture(autouse=True)
def _clean_store():
    clear_all()
    yield
    clear_all()


class TestGroupResolutionDispatch:
    def test_requires_signature_before_dispatch(self):
        service = ReviewMeetingService()
        meeting, _ = service.create_entry({
            "会议主题": "未签认纪要",
            "参会人员": "主任",
            "召开日期": "2026-11-20",
        })
        service.save_attachment(meeting["id"], [
            {"content": "补齐实践课案例", "owner": "王老师"},
        ])
        result, message = service.run_action(meeting["id"], "整组决议分发")
        assert result is None
        assert "先签认" in message
        assert tasks_for(meeting["id"]) == []

    def test_each_action_item_creates_its_own_task_and_receipt(self):
        service = ReviewMeetingService()
        meeting = signed_meeting(service, [
            {"content": "补齐实践课案例", "owner": "王老师"},
            {"content": "补齐实践课案例", "owner": "李老师"},
            {"content": "本学期实践课到课率下降的背景说明", "kind": "background"},
        ])

        result = service.dispatch_resolutions(meeting["id"])
        assert result["ok"] is True
        assert result["summary"]["created"] == 2
        assert result["summary"]["skipped"] == 1

        tasks = tasks_for(meeting["id"])
        assert len(tasks) == 2, "同名决议要按条目各建一项，不能被幂等检查合并"
        by_owner = {task["执行人员"]: task for task in tasks}
        assert set(by_owner) == {"王老师", "李老师"}
        for task in tasks:
            assert task["status"] == "待认领"
            assert task["受理回执"]["状态"] == "待受理"
            assert task["受理回执"]["派发请求号"] == result["request_id"]
            assert task["教师待办"]["任务号"] == task["id"]
        assert by_owner["王老师"]["来源决议"] != by_owner["李老师"]["来源决议"]

        # 背景说明条目不产生任务
        background = result["items"][2]
        assert background["条目类型"] == "背景说明"
        assert background["派发状态"] == "已跳过"
        assert background["任务号"] is None

        refreshed = store.find("review_meeting", meeting["id"])
        assert refreshed["status"] == "已分发"
        assert refreshed["决议分发状态"] == "已全部分发"

    def test_dispatch_is_idempotent_without_duplicate_tasks(self):
        service = ReviewMeetingService()
        meeting = signed_meeting(service, [
            {"content": "补齐实践课案例", "owner": "王老师"},
            {"content": "建立课堂观察台账", "owner": "李老师"},
        ])
        first = service.dispatch_resolutions(meeting["id"])
        second = service.dispatch_resolutions(meeting["id"])
        assert first["summary"]["created"] == 2
        assert second["summary"]["created"] == 0
        assert second["summary"]["reused"] == 2
        assert len(tasks_for(meeting["id"])) == 2
        assert [i["任务号"] for i in first["items"]] == [i["任务号"] for i in second["items"]]

    def test_interrupted_dispatch_keeps_first_task_and_reuses_number(self):
        service = ReviewMeetingService()
        task_service = ImproveTaskService()
        meeting = signed_meeting(service, [
            {"content": "补齐实践课案例", "owner": "王老师"},
            {"content": "补齐实践课案例", "owner": "李老师"},
        ])

        # 第一项送出后连接中断，第二项没有送到
        first = service.dispatch_resolutions(meeting["id"], max_items=1)
        first_task_id = first["items"][0]["任务号"]
        assert first_task_id is not None
        assert first["items"][1]["派发状态"] == "待派发"
        assert first["items"][1]["任务号"] is None

        # 第一项的责任人已经打开任务准备认领
        assert store.find("improve_task", first_task_id)["status"] == "待认领"
        task_service.run_action(first_task_id, "认领任务")
        assert store.find("improve_task", first_task_id)["status"] == "执行中"

        # 他需要知道第二项还没获准办理
        pending = service.resolution_status(meeting["id"], first["items"][1]["条目编号"])
        assert pending["已获准办理"] is False
        assert "等待本次分发恢复" in pending["hint"]
        approved = service.resolution_status(meeting["id"], first["items"][0]["条目编号"])
        assert approved["已获准办理"] is True
        assert approved["任务号"] == first_task_id

        # 重开原纪要补派第二项，第一项任务号保持不变
        retry = service.dispatch_resolutions(meeting["id"], mode="retry")
        assert retry["summary"]["created"] == 1
        assert retry["summary"]["reused"] == 0  # 补派只处理未送出的条目
        assert retry["items"][0]["任务号"] == first_task_id
        assert retry["items"][1]["任务号"] != first_task_id
        assert len(tasks_for(meeting["id"])) == 2

        # 教师凭原任务号仍能找到先前受理的决议和本次请求
        receipt = task_service.receipt_view(first_task_id)
        assert receipt["来源决议"] == first["items"][0]["条目编号"]
        assert receipt["受理回执"]["状态"] == "已受理"
        assert receipt["受理回执"]["派发请求号"] == first["request_id"]

        # 认领列表同时关联决议条目和对应请求
        claims = task_service.claim_list(meeting_id=meeting["id"])
        assert {claim["任务号"] for claim in claims} == {task_id for task_id in (first_task_id, retry["items"][1]["任务号"])}
        first_claim = next(claim for claim in claims if claim["任务号"] == first_task_id)
        assert first_claim["来源决议"] == first["items"][0]["条目编号"]
        assert first_claim["派发请求号"] == first["request_id"]
        assert first_claim["会议主题"] == "M-37 期中教学复盘"

    def test_partial_failure_isolated_and_retryable(self, monkeypatch):
        service = ReviewMeetingService()
        meeting = signed_meeting(service, [
            {"content": "补齐实践课案例", "owner": "王老师"},
            {"content": "补齐实践课案例", "owner": "李老师"},
        ])
        original = service._create_task_for_item

        def flaky(entry, item, request_id):
            if item["责任人"] == "李老师":
                raise RuntimeError("派发通道中断")
            return original(entry, item, request_id)

        monkeypatch.setattr(service, "_create_task_for_item", flaky)
        first = service.dispatch_resolutions(meeting["id"], mode="retry")
        assert first["ok"] is False
        assert first["summary"]["created"] == 1
        assert first["summary"]["failed"] == 1
        assert first["items"][1]["派发状态"] == "待补派"
        assert "派发通道中断" in first["items"][1]["失败原因"]

        refreshed = store.find("review_meeting", meeting["id"])
        assert refreshed["status"] == "已签认", "部分失败后纪要可重开补派"
        assert refreshed["决议分发状态"] == "部分分发"
        assert refreshed["abnormal"] is True

        # 已成功的第一项不受影响
        assert len(tasks_for(meeting["id"])) == 1
        failed_status = service.resolution_status(meeting["id"], first["items"][1]["条目编号"])
        assert failed_status["已获准办理"] is False
        assert "等待本次分发恢复" in failed_status["hint"]

        monkeypatch.setattr(service, "_create_task_for_item", original)
        second = service.dispatch_resolutions(meeting["id"], mode="retry")
        assert second["ok"] is True
        assert second["summary"]["created"] == 1
        assert second["items"][0]["任务号"] == first["items"][0]["任务号"]
        assert len(tasks_for(meeting["id"])) == 2
        assert store.find("review_meeting", meeting["id"])["status"] == "已分发"

    def test_missing_owner_draft_creates_no_fake_task(self):
        service = ReviewMeetingService()
        meeting = signed_meeting(service, [
            {"content": "待定负责人事项", "owner": ""},
            {"content": "补齐实践课案例", "owner": "王老师"},
        ])
        before = len(store.rows("improve_task"))
        result = service.dispatch_resolutions(meeting["id"])

        assert result["ok"] is False
        assert result["summary"]["needs_owner"] == 1
        assert result["summary"]["created"] == 1
        items = result["items"]
        assert items[0]["派发状态"] == "待补充"
        assert items[0]["任务号"] is None
        assert items[0]["已获准办理"] is False
        assert "未指定责任人" in items[0]["失败原因"]
        # 只创建了有责任人的那条，没有替草稿填出占位负责人
        tasks = tasks_for(meeting["id"])
        assert len(tasks) == 1
        assert tasks[0]["执行人员"] == "王老师"
        assert len(store.rows("improve_task")) == before + 1
        assert all(task["执行人员"] for task in tasks)

    def test_saving_attachment_preserves_dispatched_items(self):
        service = ReviewMeetingService()
        meeting = signed_meeting(service, [
            {"content": "补齐实践课案例", "owner": "王老师"},
            {"content": "建立课堂观察台账", "owner": "李老师"},
        ])
        result = service.dispatch_resolutions(meeting["id"])
        task_id = result["items"][0]["任务号"]

        # 前端重存只带了第一条，已派发的第二条也不能丢
        remaining = service.save_attachment(meeting["id"], [
            {"item_id": result["items"][0]["条目编号"],
             "content": "补齐实践课案例", "owner": "王老师"},
        ])
        assert len(remaining) == 2
        kept = next(item for item in remaining if item["条目编号"] == result["items"][1]["条目编号"])
        assert kept["任务号"] == result["items"][1]["任务号"]

        # 已派发条目的内容/责任人不能改，避免任务号指向另一项工作
        with pytest.raises(ValueError, match="不能修改决议内容"):
            service.save_attachment(meeting["id"], [
                {"item_id": result["items"][0]["条目编号"],
                 "content": "换一个完全不同的工作", "owner": "王老师"},
            ])
        with pytest.raises(ValueError, match="不能更换责任人"):
            service.save_attachment(meeting["id"], [
                {"item_id": result["items"][0]["条目编号"],
                 "content": "补齐实践课案例", "owner": "赵老师"},
            ])
        assert store.find("improve_task", task_id)["执行人员"] == "王老师"

    def test_legacy_single_task_dispatch_still_works(self):
        service = ReviewMeetingService()
        meeting, _ = service.create_entry({
            "会议主题": "旧版纪要",
            "参会人员": "主任",
            "召开日期": "2026-11-20",
            "决议事项": "需要成立专项教学改进任务组",
        })
        service.run_action(meeting["id"], "签认纪要")
        service.run_action(meeting["id"], "分发给教师")
        legacy = [
            row for row in store.rows("improve_task")
            if f"meeting:{meeting['id']}" in str(row.get("任务描述", ""))
        ]
        assert len(legacy) == 1


class TestAttachmentEditing:
    def test_new_item_after_interrupted_dispatch_gets_fresh_number(self):
        service = ReviewMeetingService()
        meeting = signed_meeting(service, [
            {"content": "补齐实践课案例", "owner": "王老师"},
            {"content": "补齐实践课案例", "owner": "李老师"},
        ])
        first = service.dispatch_resolutions(meeting["id"], max_items=1)
        first_id = first["items"][0]["任务号"]

        # 主任回到附件，在末尾追加第三项，同时原样带回前两项
        saved = service.save_attachment(meeting["id"], [
            {"item_id": first["items"][0]["条目编号"],
             "content": "补齐实践课案例", "owner": "王老师"},
            {"item_id": first["items"][1]["条目编号"],
             "content": "补齐实践课案例", "owner": "李老师"},
            {"content": "新增课堂观察台账", "owner": "赵老师"},
        ])
        ids = [item["条目编号"] for item in saved]
        assert len(ids) == len(set(ids)), "新增条目不能和已派发条目撞号"
        assert saved[0]["任务号"] == first_id

        retry = service.dispatch_resolutions(meeting["id"], mode="retry")
        assert retry["summary"]["created"] == 2
        assert retry["items"][0]["任务号"] == first_id
        assert retry["items"][1]["任务号"] is not None
        assert retry["items"][2]["任务号"] is not None
        assert len(tasks_for(meeting["id"])) == 3

    def test_dispatch_action_from_attachment_uses_group_mode(self):
        service = ReviewMeetingService()
        meeting = signed_meeting(service, [
            {"content": "补齐实践课案例", "owner": "王老师"},
            {"content": "建立课堂观察台账", "owner": "李老师"},
        ])
        result, message = service.run_action(meeting["id"], "整组决议分发")
        assert result is not None
        assert "整组决议分发完成" in message
        assert len(tasks_for(meeting["id"])) == 2

    def test_create_with_attachment_then_sign_and_dispatch(self):
        service = ReviewMeetingService()
        meeting, missing = service.create_entry({
            "会议主题": "带附件登记",
            "参会人员": "主任",
            "召开日期": "2026-11-22",
            "决议附件": [
                {"决议内容": "补齐实践课案例", "责任人": "王老师"},
                {"决议内容": "背景：到课率下降", "责任人": "", "条目类型": "背景说明"},
            ],
        })
        assert missing == []
        assert len(meeting["决议附件"]) == 2
        assert meeting["status"] == "草稿"

        result, message = service.run_action(meeting["id"], "签认纪要")
        assert result is not None
        dispatched = service.dispatch_resolutions(meeting["id"])
        assert dispatched["summary"]["created"] == 1
        assert dispatched["summary"]["skipped"] == 1

    def test_claim_list_filters_by_owner_and_request(self):
        service = ReviewMeetingService()
        task_service = ImproveTaskService()
        meeting = signed_meeting(service, [
            {"content": "补齐实践课案例", "owner": "王老师"},
            {"content": "补齐实践课案例", "owner": "李老师"},
        ])
        service.dispatch_resolutions(meeting["id"])
        wang = task_service.claim_list(owner="王老师")
        assert len(wang) == 1
        assert wang[0]["执行人员"] == "王老师"
        assert wang[0]["派发请求号"].startswith("REQ-")
        assert wang[0]["教师待办"]["状态"] == "待认领"

        task_service.run_action(wang[0]["任务号"], "认领任务")
        refreshed = task_service.claim_list(owner="王老师")[0]
        assert refreshed["受理回执"]["状态"] == "已受理"
        assert refreshed["可认领"] is False
