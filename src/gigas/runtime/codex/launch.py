"""Assemble the pinned Codex runtime with explicit integration settings."""

import json
import os
from typing import TYPE_CHECKING

from codex_cli_bin import bundled_codex_path, bundled_path_dir

from gigas.runtime.codex._execution_settings import config_overrides
from gigas.runtime.codex.connection import CodexConnection
from gigas.runtime.codex.transport import StdioTransport

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from gigas.runtime.codex.connection import RuntimeEvent
    from gigas.runtime.codex.models import ConnectionOptions


def create_connection(
    options: "ConnectionOptions",
    emit: "Callable[[RuntimeEvent], None]",
    *,
    codex_home: "Path | None" = None,
) -> CodexConnection:
    """Create a connection without starting the bundled runtime.

    Args:
        options: Runtime directory, model/authentication preferences and execution defaults.
            Standard AGENTS and Skills discovery remain enabled.
        emit: Fast synchronous callback receiving connection facts and conversation RPC events.
        codex_home: Optional isolated runtime home, created here if absent.
            None preserves the runtime default. Existing credentials are not copied.

    Returns:
        A connection whose open method launches the pinned app-server.

    Raises:
        FileNotFoundError: The installed runtime package is incomplete.
        OSError: The explicit runtime home cannot be created as a directory.
    """
    command = [str(bundled_codex_path())]
    for key, value in config_overrides(options.execution_settings).items():
        command.extend(["--config", f"{key}={json.dumps(value)}"])
    command.extend(["app-server", "--listen", "stdio://"])
    env: dict[str, str] = {"RUST_LOG": "warn", "LOG_FORMAT": "json"}
    if codex_home is not None:
        codex_home.mkdir(parents=True, exist_ok=True)
        env["CODEX_HOME"] = str(codex_home)
    path_dir = bundled_path_dir()
    if path_dir is not None:
        path_key = next((key for key in os.environ if key.upper() == "PATH"), "PATH")
        env[path_key] = str(path_dir) + os.pathsep + os.environ.get(path_key, "")
    return CodexConnection(
        StdioTransport(
            command,
            options.cwd,
            env,
            unset_env=("CODEX_WINDOWS_REGISTERED_CORE", "CODEX_WINDOWS_SANDBOX_PACKAGE_FAMILY"),
        ),
        options,
        emit,
    )
