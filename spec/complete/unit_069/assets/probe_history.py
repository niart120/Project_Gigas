"""Exercise product history APIs using a fresh home and no usable model endpoint."""

import argparse
import asyncio
import json
from contextlib import suppress
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from gigas.runtime.codex.launch import create_connection
from gigas.runtime.codex.management import CodexManagement
from gigas.runtime.codex.models import ConnectionOptions
from gigas.runtime.codex.transport import RpcError, RpcNotification

if TYPE_CHECKING:
    from gigas.runtime.codex.connection import CodexConnection, RuntimeEvent
    from gigas.runtime.codex.transport import JsonObject, JsonValue


def _object(value: "JsonValue") -> "JsonObject":
    if isinstance(value, dict):
        return value
    message = "Expected public object"
    raise TypeError(message)


def _text(value: "JsonValue") -> str:
    if isinstance(value, str):
        return value
    message = "Expected public text"
    raise TypeError(message)


async def _seed(
    connection: "CodexConnection",
    workspace: Path,
    project_id: str | None,
    submitted: asyncio.Event,
    completed: asyncio.Event,
) -> str:
    response = _object(
        await connection.request(
            "thread/start",
            {
                "cwd": str(workspace),
                "projectId": project_id,
                "model": "local-probe",
                "modelProvider": "m1_history_probe",
                "approvalPolicy": "never",
                "sandbox": "read-only",
            },
        )
    )
    thread_id = _text(_object(response.get("thread")).get("id"))
    for index in range(2):
        submitted.clear()
        completed.clear()
        started = _object(
            await connection.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [
                        {"type": "text", "text": f"M1 history fixture {index}; no model endpoint."}
                    ],
                },
            )
        )
        await asyncio.wait_for(submitted.wait(), 10)
        if not completed.is_set():
            # A terminal notification may race with this fixture-only interruption.
            with suppress(RpcError):
                await connection.request(
                    "turn/interrupt",
                    {
                        "threadId": thread_id,
                        "turnId": _text(_object(started.get("turn")).get("id")),
                    },
                )
        await asyncio.wait_for(completed.wait(), 30)
    return thread_id


async def probe(root: Path) -> dict[str, object]:
    """Read persistent history through the product client, then reopen the same home."""
    home = root / "codex_home"
    home.mkdir()
    (home / "config.toml").write_text(
        'model_provider = "m1_history_probe"\n'
        "[model_providers.m1_history_probe]\n"
        'name = "Unavailable local fixture"\n'
        'base_url = "http://127.0.0.1:9/v1"\n'
        'wire_api = "responses"\nrequires_openai_auth = false\n'
        "request_max_retries = 0\nstream_max_retries = 0\n",
        encoding="utf-8",
    )
    workspace = root / "workspace"
    workspace.mkdir()
    submitted, completed = asyncio.Event(), asyncio.Event()

    def emit(event: "RuntimeEvent") -> None:
        if isinstance(event, RpcNotification) and event.method == "turn/completed":
            completed.set()
        if isinstance(event, RpcNotification) and event.method == "item/completed":
            item = (event.params or {}).get("item")
            if isinstance(item, dict) and item.get("type") == "userMessage":
                submitted.set()

    connection = create_connection(ConnectionOptions(root), emit, codex_home=home)
    result: dict[str, object] = {"runtime": version("openai-codex-cli-bin")}
    try:
        await asyncio.wait_for(connection.open(), 30)
        result["unauthenticated"] = not connection.status.authenticated
        management = CodexManagement(connection)
        project = await management.create_project(
            "History fixture",
            workspace,
            idempotency_key=str(uuid4()),
        )
        unassigned = await _seed(connection, workspace, None, submitted, completed)
        assigned = await _seed(connection, workspace, project.id, submitted, completed)
        result["unassigned_filter"] = tuple(
            t.id for t in (await management.list_threads(None)).threads
        ) == (unassigned,)
        result["project_filter"] = tuple(
            t.id for t in (await management.list_threads(project.id)).threads
        ) == (assigned,)
        metadata = await management.read_thread(assigned)
        result["read_workspace_and_assignment"] = (
            metadata.cwd == workspace and metadata.project_id == project.id
        )
        page = await management.list_turns(assigned, limit=1)
        result["turn_page_has_cursor"] = len(page.turns) == 1 and page.next_cursor is not None
        older = await management.list_turns(assigned, limit=1, cursor=page.next_cursor)
        result["turn_cursor_reads_other_turn"] = (
            len(older.turns) == 1 and page.turns[0].id != older.turns[0].id
        )
        result["full_items_returned"] = all(
            t.items_view == "full" and any(i.get("type") == "userMessage" for i in t.items)
            for t in (*page.turns, *older.turns)
        )
        items = await management.list_items(assigned, turn_id=page.turns[0].id)
        result["item_wrapper_and_body_match"] = tuple(i.item for i in items.items) == page.turns[
            0
        ].items and all(i.turn_id == page.turns[0].id for i in items.items)
    finally:
        await asyncio.wait_for(connection.close(), 20)
    result["first_closed"] = connection.status.phase == "closed"
    restarted = create_connection(ConnectionOptions(root), lambda _: None, codex_home=home)
    try:
        await asyncio.wait_for(restarted.open(), 30)
        management = CodexManagement(restarted)
        thread = await management.read_thread(assigned)
        result["restart_metadata"] = (
            thread.id == assigned and thread.cwd == workspace and thread.project_id == project.id
        )
        turns = await management.list_turns(assigned)
        result["restart_history_without_resume"] = len(turns.turns) == 2
    finally:
        await asyncio.wait_for(restarted.close(), 20)
    result["second_closed"] = restarted.status.phase == "closed"
    return result


def main() -> None:
    """Write non-secret evidence from a new isolated runtime home."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args()
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    result = asyncio.run(probe(root))
    result["success"] = all(v is True for k, v in result.items() if k != "runtime")
    args.result.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))  # noqa: T201 -- CLI evidence output.
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
