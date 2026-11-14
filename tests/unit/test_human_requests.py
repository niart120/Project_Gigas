import asyncio

import pytest

from gigas.runtime.codex._human_requests import HumanRequests
from gigas.runtime.codex.transport import JsonObject, JsonValue, RpcId, ServerRequest


def question(request_id: RpcId, turn_id: str = "turn-1") -> ServerRequest:
    return ServerRequest(
        request_id,
        "item/tool/requestUserInput",
        {
            "threadId": "thread-1",
            "turnId": turn_id,
            "itemId": "item-1",
            "isBlocking": False,
            "questions": [
                {"id": "q", "header": "場所", "question": "どこへ行く?", "isOther": True}
            ],
        },
    )


def test_answer_keeps_original_id_and_delivery_until_write_finishes() -> None:
    async def scenario() -> None:
        requests = HumanRequests()
        event = question(17)
        assert event.params is not None
        request = requests.accept(event, event.params)
        assert request is not None
        entered = asyncio.Event()
        release = asyncio.Event()
        replies: list[tuple[RpcId, JsonObject]] = []

        async def respond(request_id: RpcId, payload: JsonObject) -> None:
            entered.set()
            await release.wait()
            replies.append((request_id, payload))

        delivery = requests.answer(request.token, answers={"q": "タマムシ"}, respond=respond)
        assert requests.pending_tasks == (delivery,)
        assert requests.resolve_rpc_id(17) is None
        assert requests.expire() == ()
        with pytest.raises(ValueError, match="回答を受け付けない"):
            requests.answer(request.token, answers={"q": "クチバ"}, respond=respond)
        await entered.wait()
        assert requests.pending_tasks == (delivery,)
        release.set()
        await delivery
        assert replies == [(17, {"answers": {"q": {"answers": ["タマムシ"]}}})]
        assert requests.pending_tasks == ()

    asyncio.run(scenario())


def test_stop_cancels_only_unsubmitted_answers() -> None:
    async def scenario() -> None:
        requests = HumanRequests()
        entered = asyncio.Event()
        release = asyncio.Event()
        replies: list[RpcId] = []

        async def respond(request_id: RpcId, _payload: JsonObject) -> None:
            entered.set()
            await release.wait()
            replies.append(request_id)

        def answer(request_id: RpcId) -> asyncio.Task[None]:
            event = question(request_id)
            assert event.params is not None
            request = requests.accept(event, event.params)
            assert request is not None
            return requests.answer(request.token, answers={"q": "先へ"}, respond=respond)

        unsent = answer("unsent")
        requests.cancel_unsubmitted()
        with pytest.raises(asyncio.CancelledError):
            await unsent
        assert not entered.is_set()
        submitted = answer("submitted")
        await entered.wait()
        requests.cancel_unsubmitted()
        assert requests.pending_tasks == (submitted,)
        release.set()
        await submitted
        assert replies == ["submitted"]

    asyncio.run(scenario())


def test_turn_end_expires_only_its_requests_and_keeps_started_delivery() -> None:
    async def scenario() -> None:
        requests = HumanRequests()
        old = question("old", "old-turn")
        old_waiting = question("old-waiting", "old-turn")
        new = question("new", "new-turn")
        assert old.params is not None
        assert old_waiting.params is not None
        assert new.params is not None
        answered = requests.accept(old, old.params)
        unanswered = requests.accept(old_waiting, old_waiting.params)
        latest = requests.accept(new, new.params)
        assert answered is not None
        assert unanswered is not None
        assert latest is not None
        release = asyncio.Event()

        async def respond(_request_id: RpcId, _payload: JsonObject) -> None:
            await release.wait()

        delivery = requests.answer(answered.token, answers={"q": "先へ"}, respond=respond)
        assert requests.finish("old-turn") == (unanswered,)
        assert requests.pending_tasks == (delivery,)
        assert requests.accept(old, old.params) is None
        assert requests.accept(new, new.params) is None
        assert requests.resolve_rpc_id("new") == latest
        release.set()
        await delivery
        assert requests.pending_tasks == ()

    asyncio.run(scenario())


def test_delivery_failure_remains_observable_without_reopening_answer() -> None:
    async def scenario() -> None:
        requests = HumanRequests()
        event = question(17)
        assert event.params is not None
        request = requests.accept(event, event.params)
        assert request is not None

        async def respond(_request_id: RpcId, _payload: JsonObject) -> None:
            message = "write failed"
            raise OSError(message)

        task = requests.answer(request.token, answers={"q": "先へ"}, respond=respond)
        with pytest.raises(OSError, match="write failed"):
            await task
        assert requests.pending_tasks == ()
        with pytest.raises(ValueError, match="回答を受け付けない"):
            requests.answer(request.token, answers={"q": "先へ"}, respond=respond)

    asyncio.run(scenario())


@pytest.mark.parametrize("answers", [{}, {"other": "yes"}, {"q": "  "}, {"q": "unknown"}])
def test_invalid_question_answer_does_not_consume_live_request(answers: dict[str, str]) -> None:
    async def scenario() -> None:
        requests = HumanRequests()
        event = question(17)
        assert event.params is not None
        event.params["questions"] = [
            {
                "id": "q",
                "header": "方向",
                "question": "どちらへ?",
                "options": [{"label": "右", "description": "進む"}],
            }
        ]
        request = requests.accept(event, event.params)
        assert request is not None
        replies: list[JsonObject] = []

        async def respond(_request_id: RpcId, payload: JsonObject) -> None:
            replies.append(payload)

        with pytest.raises(ValueError, match=r"質問|回答"):
            requests.answer(request.token, answers=answers, respond=respond)
        assert requests.pending_tasks == ()
        await requests.answer(request.token, answers={"q": "右"}, respond=respond)
        assert replies == [{"answers": {"q": {"answers": ["右"]}}}]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "questions",
    [
        [],
        [{"id": "q", "header": "場所", "question": "どこへ?", "isSecret": True}],
        [{"id": "q", "header": "場所", "question": "どこへ?", "options": "invalid"}],
        [
            {"id": "q", "header": "場所", "question": "どこへ?"},
            {"id": "q", "header": "場所", "question": "どこへ?"},
        ],
    ],
)
def test_invalid_question_is_consumed_without_a_live_request(questions: list[JsonValue]) -> None:
    requests = HumanRequests()
    event = question(17)
    assert event.params is not None
    event.params["questions"] = questions
    with pytest.raises(ValueError, match="質問"):
        requests.accept(event, event.params)
    assert requests.accept(event, event.params) is None
    assert requests.expire() == ()


def approval(request_id: RpcId, kind: str = "command", turn_id: str = "turn-1") -> ServerRequest:
    params: JsonObject = {
        "threadId": "thread-1",
        "turnId": turn_id,
        "itemId": "item-1",
        "startedAtMs": 1,
    }
    if kind == "command":
        params.update({"command": "Get-Date", "cwd": "C:/memory"})
    method = (
        "item/commandExecution/requestApproval"
        if kind == "command"
        else "item/fileChange/requestApproval"
    )
    return ServerRequest(request_id, method, params)


@pytest.mark.parametrize(
    ("kind", "extra"),
    [
        ("command", {"networkApprovalContext": {"host": "example.org"}}),
        ("command", {"additionalPermissions": {"network": {"enabled": True}}}),
        ("command", {"kind": "writeStdin"}),
        ("command", {"kind": "humanCommand"}),
        ("command", {"environmentId": "remote"}),
        ("command", {"command": None}),
        ("command", {"cwd": None}),
        ("file", {"grantRoot": "C:/private"}),
        ("file", {}),
    ],
)
def test_unsupported_or_incomplete_approval_has_no_live_token(kind: str, extra: JsonObject) -> None:
    requests = HumanRequests()
    event = approval("restricted", kind)
    assert event.params is not None
    event.params.update(extra)
    if kind == "file" and "grantRoot" in extra:
        requests.update_file_changes(
            "turn-1", "item-1", [{"path": "save.md", "diff": "+record", "kind": {"type": "add"}}]
        )
    with pytest.raises(ValueError, match=r"未対応|確認対象"):
        requests.accept(event, event.params)
    assert requests.expire() == ()


def test_only_offered_single_use_approval_decisions_are_accepted() -> None:
    async def scenario() -> None:
        requests = HumanRequests()
        event = approval("approval")
        assert event.params is not None
        event.params["availableDecisions"] = ["acceptForSession", "decline"]
        request = requests.accept(event, event.params)
        assert request is not None
        assert request.decisions == ("decline",)
        replies: list[JsonObject] = []

        async def respond(_request_id: RpcId, payload: JsonObject) -> None:
            replies.append(payload)

        with pytest.raises(ValueError, match="承認判断"):
            requests.answer(request.token, decision="accept", respond=respond)
        await requests.answer(request.token, decision="decline", respond=respond)
        assert replies == [{"decision": "decline"}]

    asyncio.run(scenario())


def test_file_details_belong_to_their_turn_and_report_only_changed_approvals() -> None:
    requests = HumanRequests()
    change: JsonValue = [
        {
            "path": "old.txt",
            "diff": "-old\n+new",
            "kind": {"type": "update", "move_path": "new.txt"},
        }
    ]
    requests.update_file_changes("old-turn", "item-1", change)
    requests.update_file_changes("new-turn", "item-1", change)
    old = approval("old", "file", "old-turn")
    new = approval("new", "file", "new-turn")
    assert old.params is not None
    assert new.params is not None
    old_request = requests.accept(old, old.params)
    new_request = requests.accept(new, new.params)
    assert old_request is not None
    assert new_request is not None
    assert "update: old.txt" in new_request.details
    assert "new.txt" in new_request.details
    assert requests.finish("old-turn") == (old_request,)
    assert requests.update_file_changes("new-turn", "item-1", change) is None
    assert requests.update_file_changes("old-turn", "item-1", []) is None
    changed = requests.update_file_changes(
        "new-turn", "item-1", [{"path": "other.txt", "diff": "", "kind": {"type": "delete"}}]
    )
    assert changed == (new_request,)
    assert requests.resolve(new_request.token) == new_request
    assert requests.expire() == ()


def test_rejection_owns_one_original_id_delivery_until_completion() -> None:
    async def scenario() -> None:
        requests = HumanRequests()
        release = asyncio.Event()
        replies: list[tuple[RpcId, int, str]] = []

        async def reject(request_id: RpcId, code: int, message: str) -> None:
            await release.wait()
            replies.append((request_id, code, message))

        task = requests.reject("request-17", reject, -32600, "停止処理中")
        assert requests.pending_tasks == (task,)
        assert requests.reject("request-17", reject, -32600, "停止処理中") is task
        requests.expire()
        requests.finish("turn-1")
        assert requests.pending_tasks == (task,)
        release.set()
        await task
        assert replies == [("request-17", -32600, "停止処理中")]
        assert requests.pending_tasks == ()

    asyncio.run(scenario())
