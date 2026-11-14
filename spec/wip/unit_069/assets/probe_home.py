"""Check first startup with an empty home through the product connection factory.

This probe never calls login, creates a thread, or invokes a model or game device.
Use a new output directory on each run; it is retained for inspection.
"""

import argparse
import asyncio
import json
from importlib.metadata import version
from pathlib import Path

from gigas.runtime.codex.connection import RuntimeEvent
from gigas.runtime.codex.launch import create_connection
from gigas.runtime.codex.models import ConnectionOptions, SessionStatus


async def probe(root: Path) -> dict[str, object]:
    """Start and close the pinned runtime without supplying any credentials."""
    cwd = root / "runtime"
    home = root / "codex_home"
    phases: list[str] = []

    def receive(event: RuntimeEvent) -> None:
        if isinstance(event, SessionStatus):
            phases.append(event.phase)

    result: dict[str, object] = {
        "runtime_package_version": version("openai-codex-cli-bin"),
        "factory": "gigas.runtime.codex.launch.create_connection",
        "home_initially_absent": not home.exists(),
        "credential_copy": False,
        "login_requested": False,
        "thread_created": False,
        "model_inference_requested": False,
        "game_device_used": False,
    }
    connection = create_connection(ConnectionOptions(cwd), receive, codex_home=home)
    result["home_created_by_factory"] = home.is_dir()
    try:
        await asyncio.wait_for(connection.open(), timeout=30)
        result["open_returned"] = True
        result["phase_after_open"] = connection.status.phase
        result["authenticated"] = connection.status.authenticated
        result["selection_available"] = bool(connection.selection.choices)
    except Exception as error:  # noqa: BLE001 -- persist only a safe exception category.
        result["open_returned"] = False
        result["error_type"] = type(error).__name__
    finally:
        await asyncio.wait_for(connection.close(), timeout=20)
    result["phase_after_close"] = connection.status.phase
    result["phases"] = phases
    result["auth_file_created"] = (home / "auth.json").exists()
    result["success"] = (
        result.get("open_returned") is True
        and result.get("phase_after_open") == "login_required"
        and result.get("authenticated") is False
        and result["phase_after_close"] == "closed"
        and result["home_created_by_factory"] is True
        and result["auth_file_created"] is False
    )
    return result


def main() -> None:
    """Write only sanitized evidence, preserving the isolated runtime directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args()
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / "runtime").mkdir()
    result = asyncio.run(probe(root))
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    args.result.write_text(text, encoding="utf-8")
    print(text, end="")  # noqa: T201 -- CLI evidence output.
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
