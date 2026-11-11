"""Exercise the product connection against a signed-in Codex runtime.

This opt-in command sends a small real model request. It never logs credentials
or accepts an execution approval. It is not part of the offline CI suite.
"""

import argparse
import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

from gigas.runtime.codex.connection import CodexConnectionError
from gigas.runtime.codex.launch import create_connection
from gigas.runtime.codex.models import (
    ConnectionOptions,
    HumanRequest,
    RequestResolved,
    SessionStatus,
    TextEvent,
)

if TYPE_CHECKING:
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
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


async def _exercise(scenario: str, cwd: Path, home: Path | None) -> dict[str, object]:
    events: asyncio.Queue[ConnectionEvent] = asyncio.Queue()
    baseline = asyncio.all_tasks()
    connection = create_connection(
        ConnectionOptions(cwd=cwd, effort="low"), events.put_nowait, codex_home=home
    )
    assistant_text: dict[str, str] = {}
    requested = False
    resolved = False
    stale_rejected = False
    approval_decision = ""
    final_phase = ""
    observed_model = ""
    try:
        await connection.open()
        while not events.empty():
            initial = events.get_nowait()
            if isinstance(initial, SessionStatus):
                _require(initial.phase != "login_required", "ChatGPT login is required")
                observed_model = initial.model or observed_model
        await connection.send(PROMPTS[scenario])
        async with asyncio.timeout(120):
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
                        await connection.stop()
                        try:
                            await connection.answer(event.token, answers={})
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
                        await connection.answer(event.token, answers=answers)
                    elif scenario == "approval":
                        approval_decision = next(
                            (item for item in ("decline", "cancel") if item in event.decisions), ""
                        )
                        _require(
                            bool(approval_decision),
                            f"Expected a rejectable approval: {event.kind}, {event.decisions}",
                        )
                        await connection.answer(event.token, decision=approval_decision)
                    else:
                        await connection.stop()
                        message = "Unexpected human request in ordinary scenario"
                        raise RuntimeError(message)
                elif isinstance(event, RequestResolved):
                    resolved = True
                elif isinstance(event, SessionStatus):
                    observed_model = event.model or observed_model
                    _require(event.phase != "error", f"Connection failed: {event.detail}")
                    if event.phase in ("ready", "stopped"):
                        final_phase = event.phase
                        break
        text = "\n".join(assistant_text.values())
        if scenario == "ordinary":
            _require("GIGAS_U02_OK" in text, "Expected the model's verification response")
        elif scenario == "interrupt":
            _require(requested and stale_rejected, "No pending question was interrupted")
            _require(final_phase == "stopped", "Runtime did not confirm interruption")
        else:
            _require(requested and resolved, "No request/reply lifecycle was observed")
            if approval_decision == "cancel":
                _require(final_phase == "stopped", "Runtime did not confirm cancellation")
            else:
                _require(bool(text.strip()), "No model response followed the answer")
    finally:
        await connection.close()
    _require(asyncio.all_tasks() == baseline, "Connection tasks remain after close")
    return {
        "scenario": scenario,
        "model": observed_model,
        "phase": final_phase,
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
