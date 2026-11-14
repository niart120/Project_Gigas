"""Exercise product conversation execution against a signed-in Codex runtime.

This opt-in command sends a small real model request. It never logs credentials
or accepts an execution approval. It is not part of the offline CI suite.
"""

import argparse
import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.runtime.codex.connection import CodexConnectionError
from gigas.runtime.codex.launch import create_connection
from gigas.runtime.codex.models import (
    ConnectionOptions,
    DialogueEvent,
    HumanRequest,
    RequestResolved,
    SessionStatus,
    TextEvent,
)
from gigas.services._conversation_execution import ConversationExecution
from gigas.services._conversation_resources import ConversationResources
from gigas.services.input import InputService

if TYPE_CHECKING:
    from gigas.runtime.codex.connection import RuntimeEvent
    from gigas.runtime.codex.models import ConnectionEvent

PROMPTS = {
    "ordinary": "Reply with exactly GIGAS_U02_OK. Do not use tools.",
    "question": (
        "This is a user-input integration check. Use the request_user_input tool to ask "
        "one question: choose Alpha or Beta. Do not choose for me. After receiving my "
        "answer, reply with the chosen label. Do not use any other tools."
    ),
    "interrupt": (
        "Use request_user_input to ask me to choose Alpha or Beta. Wait for the answer. "
        "This is an interruption integration check. Do not use any other tools."
    ),
    "approval": (
        "This is a human-approval integration check. Use the shell execution tool to run "
        "PowerShell Write-Output GIGAS_U02_APPROVAL and explicitly request escalated "
        "sandbox permissions with a justification. Do not run it without approval. "
        "If permission is declined, report that and finish without trying again."
    ),
    "file-approval": (
        "This is a file-approval integration check. Use apply_patch to add only the file "
        "../gigas-u02-approval-check.txt outside the writable memory directory, "
        "containing GIGAS_U02_FILE. "
        "Request the required approval before writing. If permission is declined, "
        "report that and finish without trying again. Do not use shell commands."
    ),
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


async def _exercise(scenario: str, cwd: Path, home: Path | None) -> dict[str, object]:
    baseline = asyncio.all_tasks()
    events: asyncio.Queue[ConnectionEvent] = asyncio.Queue()
    archive = cwd / "checks" / str(uuid4())
    archive.parent.mkdir(parents=True, exist_ok=True)
    probe_file = archive / "gigas-u02-approval-check.txt"
    storage_failures: list[str] = []
    resources = ConversationResources(
        cwd,
        CaptureReader(CaptureSnapshot),
        lambda _operation, _path, reason: storage_failures.append(reason),
    )
    inputs = InputService()
    closing: asyncio.Task[None] | None = None

    def receive(event: "RuntimeEvent") -> None:
        execution.receive(event)

    connection = create_connection(
        ConnectionOptions(cwd=cwd, effort="low"), receive, codex_home=home
    )

    async def release() -> None:
        outcomes: list[object] = list(
            await asyncio.gather(
                connection.close(), resources.close_endpoint(), return_exceptions=True
            )
        )
        outcomes.extend(
            await asyncio.gather(execution.drain(), inputs.drain_input(), return_exceptions=True)
        )
        try:
            await resources.close()
        finally:
            await inputs.close()
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome

    def request_close() -> asyncio.Task[None]:
        nonlocal closing
        if closing is None:
            closing = asyncio.create_task(release())
            execution.connection_closing()
            inputs.stop()
        return closing

    execution = ConversationExecution(
        connection,
        resources,
        inputs,
        events.put_nowait,
        closing=lambda: closing is not None,
        request_close=request_close,
    )
    assistant_text: dict[str, str] = {}
    requested = False
    resolved = False
    stale_rejected = False
    approval_decision = ""
    final_status = ""
    stop_completed = False
    observed_model = ""
    try:
        await resources.select_project(archive, create=True)
        await connection.open()
        while not events.empty():
            initial = events.get_nowait()
            if isinstance(initial, SessionStatus):
                _require(initial.phase != "login_required", "ChatGPT login is required")
                observed_model = initial.model or observed_model
        async with asyncio.timeout(120):
            turn_id = await execution.send(PROMPTS[scenario])
            _require(turn_id is not None, "Message was not submitted")
            while True:
                event = await events.get()
                if isinstance(event, TextEvent) and event.role == "assistant":
                    prior = "" if event.replace else assistant_text.get(event.item_id, "")
                    assistant_text[event.item_id] = prior + event.text
                elif isinstance(event, HumanRequest):
                    requested = True
                    # Keep the real request pending while the connection keeps receiving.
                    await asyncio.sleep(2)
                    if scenario == "interrupt":
                        await execution.stop()
                        stop_completed = True
                        try:
                            await execution.answer(event.token, answers={})
                        except CodexConnectionError:
                            stale_rejected = True
                    elif scenario == "question":
                        _require(bool(event.questions), "Expected a user question")
                        answers = {
                            question.id: (
                                question.options[0].label if question.options else "Alpha"
                            )
                            for question in event.questions
                        }
                        await execution.answer(event.token, answers=answers)
                    elif scenario in {"approval", "file-approval"}:
                        _require(
                            event.kind == ("file" if scenario == "file-approval" else "command"),
                            f"Unexpected approval kind: {event.kind}",
                        )
                        approval_decision = next(
                            (item for item in ("decline", "cancel") if item in event.decisions), ""
                        )
                        _require(
                            bool(approval_decision),
                            f"Expected a rejectable approval: {event.kind}, {event.decisions}",
                        )
                        await execution.answer(event.token, decision=approval_decision)
                    else:
                        await execution.stop()
                        message = "Unexpected human request in ordinary scenario"
                        raise RuntimeError(message)
                elif isinstance(event, RequestResolved):
                    resolved = True
                elif isinstance(event, SessionStatus):
                    observed_model = event.model or observed_model
                    _require(
                        event.phase not in {"error", "closed"},
                        f"Connection ended before turn completion: {event.detail}",
                    )
                elif (
                    isinstance(event, DialogueEvent)
                    and event.kind == "turn_completed"
                    and event.turn_id == turn_id
                ):
                    final_status = str(event.payload.get("status", ""))
                    _require(
                        final_status in {"completed", "interrupted"},
                        f"Turn ended without success or interruption: {final_status}",
                    )
                    break
        text = "\n".join(assistant_text.values())
        if scenario == "ordinary":
            _require("GIGAS_U02_OK" in text, "Expected the model's verification response")
        elif scenario == "interrupt":
            _require(requested and stale_rejected, "No pending question was interrupted")
            _require(stop_completed and final_status == "interrupted", "Stop did not complete")
        else:
            _require(requested and resolved, "No request/reply lifecycle was observed")
            if approval_decision != "cancel":
                _require(bool(text.strip()), "No model response followed the answer")
    finally:
        await asyncio.shield(request_close())
    if scenario == "file-approval":
        _require(not probe_file.exists(), "File was written without approval")
    _require(asyncio.all_tasks() == baseline, "Connection tasks remain after close")
    _require(not storage_failures, f"Archive failed: {storage_failures}")
    return {
        "scenario": scenario,
        "model": observed_model,
        "turn_status": final_status,
        "stop_completed": stop_completed,
        "archive": str(archive),
        "request_observed": requested,
        "request_resolved": resolved,
        "stale_answer_rejected": stale_rejected,
        "approval_decision": approval_decision,
        "assistant_characters": sum(map(len, assistant_text.values())),
        "closed": True,
    }


def main() -> None:
    """Run an explicitly selected live scenario and emit a credential-free result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=PROMPTS)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path)
    arguments = parser.parse_args()
    cwd = arguments.cwd.resolve()
    cwd.mkdir(parents=True, exist_ok=True)
    home = arguments.codex_home.resolve() if arguments.codex_home else None
    result = asyncio.run(_exercise(arguments.scenario, cwd, home))
    print(json.dumps(result, ensure_ascii=False))  # noqa: T201 -- CLI verification result


if __name__ == "__main__":
    main()
