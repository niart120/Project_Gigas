"""Exercise stored Goal get/clear using the unauthenticated history probe fixture."""

import argparse
import asyncio
import json
import tomllib
from importlib.metadata import version
from pathlib import Path

from gigas.runtime.codex.launch import create_connection
from gigas.runtime.codex.management import CodexManagement
from gigas.runtime.codex.models import ConnectionOptions


async def probe(root: Path) -> dict[str, object]:
    """Create a paused fixture Goal, then read and clear it after process restart."""
    home = root / "codex_home"
    config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    if config.get("model_provider") != "m1_history_probe":
        message = "Run probe_history.py first and supply its isolated output directory"
        raise ValueError(message)
    options = ConnectionOptions(root, model="missing-model-for-management-probe")
    connection = create_connection(options, lambda _: None, codex_home=home)
    result: dict[str, object] = {"runtime": version("openai-codex-cli-bin")}
    objective = "M1 stored Goal fixture; do not execute"
    try:
        await asyncio.wait_for(connection.open(), 30)
        management = CodexManagement(connection)
        thread = (await management.list_threads(None)).threads[0]
        result["initial_goal_absent"] = await management.get_goal(thread.id) is None
        await connection.request(
            "thread/goal/set",
            {
                "threadId": thread.id,
                "objective": objective,
                "status": "paused",
                "origin": "user",
            },
        )
        goal = await management.get_goal(thread.id)
        result["fixture_persisted"] = (
            goal is not None and goal.objective == objective and goal.status == "paused"
        )
    finally:
        await asyncio.wait_for(connection.close(), 20)
    result["first_closed"] = connection.status.phase == "closed"
    restarted = create_connection(options, lambda _: None, codex_home=home)
    try:
        await asyncio.wait_for(restarted.open(), 30)
        result["unauthenticated_and_not_ready"] = (
            not restarted.status.authenticated and not restarted.ready
        )
        result["invalid_model_does_not_block"] = not restarted.selection.model
        management = CodexManagement(restarted)
        goal = await management.get_goal(thread.id)
        result["cold_get"] = (
            goal is not None and goal.objective == objective and goal.status == "paused"
        )
        result["cold_clear"] = await management.clear_goal(thread.id)
        result["read_after_clear"] = await management.get_goal(thread.id) is None
        result["second_clear_reports_absent"] = not await management.clear_goal(thread.id)
        result["thread_still_not_loaded"] = (await management.read_thread(thread.id)).status.get(
            "type"
        ) == "notLoaded"
    finally:
        await asyncio.wait_for(restarted.close(), 20)
    result["second_closed"] = restarted.status.phase == "closed"
    return result


def main() -> None:
    """Write success flags without copying credentials or executing a model."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-root", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(probe(args.history_root.resolve()))
    result["success"] = all(value is True for key, value in result.items() if key != "runtime")
    args.result.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))  # noqa: T201 -- CLI evidence output.
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
