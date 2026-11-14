"""Human requests scoped to their turns and owned response deliveries."""

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from gigas.runtime.codex._notifications import file_change_details
from gigas.runtime.codex.models import HumanRequest, Question, QuestionOption

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from gigas.runtime.codex.transport import JsonObject, JsonValue, RpcId, ServerRequest


def _object(value: "JsonValue") -> "JsonObject":
    if isinstance(value, dict):
        return value
    raise ValueError("Codexの応答形式が不正")


def _text(value: "JsonValue") -> str:
    if isinstance(value, str) and value:
        return value
    raise ValueError("Codexの応答形式が不正")


def _questions(value: "JsonValue") -> tuple[Question, ...]:
    if not isinstance(value, list) or not value:
        message = "質問の形式が不正"
        raise ValueError(message)
    questions: list[Question] = []
    for item in value:
        data = _object(item)
        if data.get("isSecret") is True:
            message = "入力を秘匿する質問形式には未対応"
            raise ValueError(message)
        choices = data.get("options")
        options: list[QuestionOption] = []
        if choices is not None:
            if not isinstance(choices, list):
                message = "質問の選択肢が不正"
                raise ValueError(message)
            for option in choices:
                choice = _object(option)
                label = _text(choice.get("label"))
                description = choice.get("description")
                if not isinstance(description, str):
                    message = "質問の選択肢が不正"
                    raise ValueError(message)  # noqa: TRY004 -- Invalid JSON field, not API type.
                options.append(QuestionOption(label, description))
        questions.append(
            Question(
                _text(data.get("id")),
                _text(data.get("header")),
                _text(data.get("question")),
                tuple(options),
                data.get("isOther") is True,
            )
        )
    if len({question.id for question in questions}) != len(questions):
        message = "質問IDが重複"
        raise ValueError(message)
    return tuple(questions)


@dataclass(slots=True)
class _Delivery:
    submitted: bool = False
    task: asyncio.Task[None] = field(init=False)


class HumanRequests:
    """Keep live requests, per-turn details and original-ID response deliveries."""

    def __init__(self) -> None:
        """Start without requests or confirmation details."""
        self._pending: dict[str, tuple[HumanRequest, str]] = {}
        self._seen: dict[RpcId, str] = {}
        self._file_details: dict[tuple[str, str], str] = {}
        self._deliveries: dict[RpcId, _Delivery] = {}

    @property
    def pending_tasks(self) -> tuple[asyncio.Task[None], ...]:
        """Answer and rejection deliveries owned until their transport writes finish."""
        return tuple(
            delivery.task for delivery in self._deliveries.values() if not delivery.task.done()
        )

    def cancel_unsubmitted(self) -> None:
        """Cancel accepted responses whose transport call has not started.

        Started writes retain ownership until their result is known; callers
        recover pending_tasks rather than cancelling those writes again.
        """
        for delivery in self._deliveries.values():
            if not delivery.submitted:
                delivery.task.cancel()

    @property
    def has_blocking(self) -> bool:
        """Whether a live request blocks runtime execution."""
        return any(request.is_blocking for request, _item_id in self._pending.values())

    def accept(self, event: "ServerRequest", params: "JsonObject") -> HumanRequest | None:
        """Decode a request once, consuming invalid IDs for its turn as well.

        The caller checks method and thread/turn ownership before calling.
        ValueError leaves the ID consumed but does not create a live request.
        """
        if event.id in self._seen or event.id in self._deliveries:
            return None
        self._seen[event.id] = _text(params.get("turnId"))
        request = self._decode_request(event, params)
        self._pending[request.token] = (request, _text(params.get("itemId")))
        return request

    def answer(
        self,
        token: str,
        decision: str | None = None,
        answers: "Mapping[str, str] | None" = None,
        *,
        respond: "Callable[[RpcId, JsonObject], Awaitable[None]]",
    ) -> asyncio.Task[None]:
        """Consume a valid answer and own its delivery before yielding the loop.

        Args:
            token: Token of a currently unanswered request.
            decision: One offered approval decision, or None for a question.
            answers: Question ID to answer text, or None for an approval.
            respond: Transport response using the original server request ID.

        Returns:
            Delivery task. Resolution or turn completion does not discard it.

        Raises:
            ValueError: The request is no longer live or the answer is invalid.
        """
        entry = self._pending.get(token)
        if entry is None:
            raise ValueError("この要求は回答を受け付けない")
        request, _item_id = entry
        payload = self._answer_payload(request, decision, answers)
        del self._pending[token]
        return self._start_delivery(request.rpc_id, lambda: respond(request.rpc_id, payload))

    def reject(
        self,
        request_id: "RpcId",
        reject: "Callable[[RpcId, int, str], Awaitable[None]]",
        code: int,
        message: str,
    ) -> asyncio.Task[None]:
        """Own a protocol rejection without blocking the synchronous receiver.

        Args:
            request_id: Original server request ID.
            reject: Transport rejection callback.
            code: JSON-RPC error code.
            message: Application-owned reason safe to send.

        Returns:
            Response task, shared if the same ID already has an in-flight delivery.
        """
        return self._start_delivery(request_id, lambda: reject(request_id, code, message))

    def _start_delivery(
        self, request_id: "RpcId", send: "Callable[[], Awaitable[None]]"
    ) -> asyncio.Task[None]:
        existing = self._deliveries.get(request_id)
        if existing is not None:
            return existing.task

        delivery = _Delivery()

        async def deliver() -> None:
            delivery.submitted = True
            await send()

        task = asyncio.create_task(deliver(), name="gigas-response-delivery")
        delivery.task = task
        self._deliveries[request_id] = delivery
        task.add_done_callback(lambda done: self._delivery_finished(request_id, done))
        return task

    def _delivery_finished(self, request_id: "RpcId", task: asyncio.Task[None]) -> None:
        delivery = self._deliveries.get(request_id)
        if delivery is not None and delivery.task is task:
            del self._deliveries[request_id]
        if not task.cancelled():
            task.exception()

    def resolve(self, token: str) -> HumanRequest | None:
        """Remove a live token while retaining its consumed RPC ID."""
        entry = self._pending.pop(token, None)
        return entry[0] if entry is not None else None

    def resolve_rpc_id(self, request_id: "RpcId") -> HumanRequest | None:
        """Resolve the live request matching a runtime dismissal."""
        token = next(
            (
                token
                for token, (request, _) in self._pending.items()
                if request.rpc_id == request_id
            ),
            None,
        )
        return self.resolve(token) if token is not None else None

    def expire(self) -> tuple[HumanRequest, ...]:
        """Expire live tokens without forgetting per-turn IDs or response deliveries."""
        requests = tuple(request for request, _item_id in self._pending.values())
        self._pending.clear()
        return requests

    def finish(self, turn_id: str) -> tuple[HumanRequest, ...]:
        """Expire one turn's requests and details, retaining all response deliveries."""
        requests = tuple(
            request for request, _ in self._pending.values() if request.turn_id == turn_id
        )
        for request in requests:
            del self._pending[request.token]
        self._seen = {rpc_id: turn for rpc_id, turn in self._seen.items() if turn != turn_id}
        self._file_details = {
            key: detail for key, detail in self._file_details.items() if key[0] != turn_id
        }
        return requests

    def update_file_changes(
        self, turn_id: str, item_id: str, changes: "JsonValue"
    ) -> tuple[HumanRequest, ...] | None:
        """Return live approvals without consuming them, or None for unchanged details."""
        key = (turn_id, item_id)
        previous = self._file_details.pop(key, None)
        try:
            details = file_change_details(changes)
        except ValueError:
            details = None
        if details is not None:
            self._file_details[key] = details
        if previous == details:
            return None
        return tuple(
            request
            for request, request_item in self._pending.values()
            if request.kind == "file" and request.turn_id == turn_id and request_item == item_id
        )

    def _decode_request(self, event: "ServerRequest", params: "JsonObject") -> HumanRequest:
        token = uuid.uuid4().hex
        thread_id = _text(params.get("threadId"))
        turn_id = _text(params.get("turnId"))
        item_id = _text(params.get("itemId"))
        if event.method == "item/tool/requestUserInput":
            blocking = params.get("isBlocking")
            if not isinstance(blocking, bool):
                message = "質問の待機形式が不正"
                raise ValueError(message)
            return HumanRequest(
                token,
                event.id,
                thread_id,
                turn_id,
                "question",
                questions=_questions(params.get("questions")),
                is_blocking=blocking,
            )
        started = params.get("startedAtMs")
        if not isinstance(started, int) or isinstance(started, bool):
            message = "承認要求の時刻が不正"
            raise ValueError(message)  # noqa: TRY004 -- Invalid JSON field, not API type.
        kind = "command" if event.method == "item/commandExecution/requestApproval" else "file"
        self._validate_approval(kind, (turn_id, item_id), params)
        offered = params.get("availableDecisions")
        if kind == "file" or offered is None:
            decisions = ("accept", "decline", "cancel")
        elif isinstance(offered, list):
            decisions = tuple(
                dict.fromkeys(
                    item
                    for item in offered
                    if isinstance(item, str) and item in {"accept", "decline", "cancel"}
                )
            )
        else:
            decisions = ()
        if not decisions:
            message = "承認要求の判断形式に未対応"
            raise ValueError(message)
        details = "\n".join(
            f"{label}: {value}"
            for key, label in (
                ("command", "コマンド"),
                ("cwd", "作業フォルダー"),
                ("environmentId", "環境"),
                ("reason", "理由"),
            )
            if isinstance(value := params.get(key), str) and value
        )
        if kind == "file" and (turn_id, item_id) in self._file_details:
            details = "\n".join(
                part for part in (details, self._file_details[turn_id, item_id]) if part
            )
        return HumanRequest(
            token,
            event.id,
            thread_id,
            turn_id,
            kind,
            details=details,
            decisions=decisions,
        )

    def _validate_approval(self, kind: str, item: tuple[str, str], params: "JsonObject") -> None:
        if kind == "command":
            if (
                params.get("kind", "command") != "command"
                or params.get("environmentId") not in (None, "local")
                or any(
                    params.get(key) is not None
                    for key in ("networkApprovalContext", "additionalPermissions")
                )
            ):
                message = "この承認形式は未対応"
                raise ValueError(message)
            if not all(
                isinstance(value := params.get(key), str) and value.strip()
                for key in ("command", "cwd")
            ):
                message = "コマンドの確認対象が不足"
                raise ValueError(message)
        elif params.get("grantRoot") is not None:
            message = "継続的な書込許可は未対応"
            raise ValueError(message)
        elif item not in self._file_details:
            message = "ファイル変更の確認対象が不足"
            raise ValueError(message)

    @staticmethod
    def _answer_payload(
        request: HumanRequest, decision: str | None, answers: "Mapping[str, str] | None"
    ) -> "JsonObject":
        if request.kind != "question":
            if decision not in request.decisions or answers is not None:
                message = "承認判断を選択する必要がある"
                raise ValueError(message)
            return {"decision": decision}
        if (
            decision is not None
            or answers is None
            or set(answers) != {q.id for q in request.questions}
        ):
            message = "すべての質問に回答する必要がある"
            raise ValueError(message)
        payload: JsonObject = {}
        for question in request.questions:
            text = answers[question.id]
            if not text.strip() or (
                question.options
                and not question.is_other
                and text not in {option.label for option in question.options}
            ):
                message = "質問の回答が無効"
                raise ValueError(message)
            payload[question.id] = {"answers": [text]}
        return {"answers": payload}
