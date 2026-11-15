"""Exercise product Project APIs against an isolated, unauthenticated app-server."""

import argparse
import asyncio
import json
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from gigas.runtime.codex.launch import create_connection
from gigas.runtime.codex.management import CodexManagement
from gigas.runtime.codex.models import ConnectionOptions


async def probe(root: Path) -> dict[str, object]:
    """Create and reopen a Project through the product client without executing a turn."""
    home = root / "codex_home"
    workspace = root / "攻略資料"
    connection = create_connection(ConnectionOptions(root), lambda _: None, codex_home=home)
    result: dict[str, object] = {"runtime": version("openai-codex-cli-bin")}
    project_id = ""
    try:
        await asyncio.wait_for(connection.open(), 30)
        result["unauthenticated"] = not connection.status.authenticated
        management = CodexManagement(connection)
        created = await management.create_project(
            "M1 isolated project", workspace, idempotency_key=str(uuid4())
        )
        project_id = created.id
        result["created_root_matches"] = created.root == workspace
        page = await management.list_projects(limit=1)
        result["listed"] = len(page.projects) == 1 and page.projects[0] == created
        result["cursor"] = page.next_cursor
        result["read_matches"] = await management.read_project(project_id) == created
    finally:
        await asyncio.wait_for(connection.close(), 20)
    result["first_closed"] = connection.status.phase == "closed"
    restarted = create_connection(ConnectionOptions(root), lambda _: None, codex_home=home)
    try:
        await asyncio.wait_for(restarted.open(), 30)
        loaded = await CodexManagement(restarted).read_project(project_id)
        result["restart_resolves_project"] = (
            loaded.id == project_id
            and loaded.root == workspace
            and loaded.name == "M1 isolated project"
        )
    finally:
        await asyncio.wait_for(restarted.close(), 20)
    result["second_closed"] = restarted.status.phase == "closed"
    return result


def main() -> None:
    """Run in a new directory and write non-secret success flags as evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args()
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / "攻略資料").mkdir()
    result = asyncio.run(probe(root))
    result["success"] = all(
        value is True for key, value in result.items() if key not in {"runtime", "cursor"}
    )
    args.result.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))  # noqa: T201 -- CLI evidence output.
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
