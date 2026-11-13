"""Assemble the pinned Codex runtime with explicit integration settings."""

import os
from typing import TYPE_CHECKING

from codex_cli_bin import bundled_codex_path, bundled_path_dir

from gigas.runtime.codex.connection import CodexConnection
from gigas.runtime.codex.transport import StdioTransport

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from gigas.runtime.codex.models import ConnectionEvent, ConnectionOptions, ThreadConfiguration


def create_connection(
    options: "ConnectionOptions",
    emit: "Callable[[ConnectionEvent], None]",
    *,
    codex_home: "Path | None" = None,
    prepare_turn: "Callable[[str, str | None], Awaitable[ThreadConfiguration]] | None" = None,
    on_thread_started: "Callable[[str], Awaitable[None]] | None" = None,
) -> CodexConnection:
    """Create a connection without starting the bundled runtime.

    Args:
        options: Runtime working directory and model/authentication preferences.
        emit: Fast synchronous callback receiving typed connection events.
        codex_home: Optional isolated runtime home. None preserves the runtime default.
        prepare_turn: Optional host archive/MCP preparation for each prompt and
            its existing thread ID, or None before the first thread.
        on_thread_started: Optional host recording awaited before the first turn.

    Returns:
        A connection whose open method launches the pinned app-server.

    Raises:
        FileNotFoundError: The installed runtime package is incomplete.
    """
    command = [str(bundled_codex_path())]
    for override in (
        "features.apps=false",
        "features.plugins=false",
        "features.memories=false",
        "features.hooks=false",
        "features.codex_hooks=false",
        "features.plugin_hooks=false",
        "skills.include_instructions=false",
        "project_doc_max_bytes=0",
        "notify=[]",
        'web_search="disabled"',
        "features.default_mode_request_user_input=true",
        "tools.update_plan.enabled=true",
    ):
        command.extend(["--config", override])
    command.extend(["app-server", "--listen", "stdio://"])
    env: dict[str, str] = {}
    if codex_home is not None:
        env["CODEX_HOME"] = str(codex_home)
    path_dir = bundled_path_dir()
    if path_dir is not None:
        path_key = next((key for key in os.environ if key.upper() == "PATH"), "PATH")
        env[path_key] = str(path_dir) + os.pathsep + os.environ.get(path_key, "")
    return CodexConnection(
        StdioTransport(command, options.cwd, env),
        options,
        emit,
        prepare_turn=prepare_turn,
        on_thread_started=on_thread_started,
    )
