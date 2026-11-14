"""Own a conversation's archive, observations and MCP endpoint on one loop."""

import asyncio
from functools import partial
from typing import TYPE_CHECKING
from uuid import uuid4

from gigas.mcp.game_io import TOOL_TIMEOUT_SECONDS, GameIoMcpServer
from gigas.runtime.codex.models import ThreadConfiguration
from gigas.services._game_io_tools import act_and_observe
from gigas.services.input import OperationResult
from gigas.services.observation import ObservationService
from gigas.services.recording import RecordingService
from gigas.storage.project import ProjectError, ProjectWriter

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from gigas.game_io.capture import CaptureReader
    from gigas.game_io.input.models import InputStep
    from gigas.game_io.observation import Observation, ObservationError, ObservationResolution
    from gigas.runtime.codex.transport import JsonObject
    from gigas.services._game_io_tools import InputStart


class ConversationResources:
    """Acquire and close archive resources; the service serializes connection changes."""

    def __init__(
        self,
        data_root: "Path",
        reader: "CaptureReader",
        failed: "Callable[[str, str, str], None]",
    ) -> None:
        """Retain dependencies without opening an archive or HTTP listener."""
        self._data_root = data_root.resolve()
        self._reader = reader
        self._failed = failed
        self._project_selected = False
        self._writer: ProjectWriter | None = None
        self._observations: ObservationService | None = None
        self._recording: RecordingService | None = None
        self._mcp: GameIoMcpServer | None = None
        self._resolution: ObservationResolution = "full_hd"

    def set_resolution(self, resolution: "ObservationResolution") -> None:
        """Apply the setting to later observations, including an existing session."""
        self._resolution = resolution
        if self._observations is not None:
            self._observations.resolution = resolution

    async def select_project(self, path: "Path", *, create: bool) -> None:
        """Select a writer while preserving the current one if acquisition fails."""
        if self._writer is not None and self._writer.path == path:
            if create:
                message = f"{path}: 既存のプロジェクトは新規作成できません"
                raise ProjectError(message)
            await asyncio.to_thread(self._writer.reload)
        else:
            operation = ProjectWriter.create if create else ProjectWriter.open
            candidate = await asyncio.to_thread(operation, path)
            try:
                if self._writer is not None:
                    await asyncio.to_thread(self._writer.close)
            except BaseException:
                await asyncio.to_thread(candidate.close)
                raise
            self._writer = candidate
        self._project_selected = True

    async def prepare(self, start_input: "InputStart") -> ThreadConfiguration:
        """Prepare conversation resources, reusing the endpoint until it is closed."""
        try:
            if self._writer is None:
                creation = asyncio.create_task(asyncio.to_thread(self._create_chat_archive))
                try:
                    self._writer = await asyncio.shield(creation)
                except asyncio.CancelledError:
                    # The filesystem operation cannot be canceled once submitted.
                    # Adopt its result so disconnect can close the acquired writer.
                    self._writer = await creation
                    raise
            writer = self._writer
            if self._recording is None:
                self._recording = RecordingService(writer)
                self._recording.on_failure = lambda error: self._failed(
                    "", str(writer.path), recording_reason(error)
                )
            await self._recording.ensure_memory()
            if self._observations is None:
                self._observations = ObservationService(
                    self._reader, self._recording, resolution=self._resolution
                )
            if self._mcp is None:
                self._mcp = GameIoMcpServer(
                    self._observations.observe,
                    partial(
                        _conversation_input,
                        start_input=start_input,
                        observations=self._observations,
                        recording=self._recording,
                    ),
                )
                try:
                    await self._mcp.start()
                except BaseException:
                    await self._mcp.close()
                    self._mcp = None
                    raise
            prefix = f"mcp_servers.{self._mcp.server_name}."
            endpoint = self._mcp.config
            configuration = ThreadConfiguration(
                writer.path,
                {
                    prefix + "url": endpoint["url"],
                    prefix + "http_headers": dict(endpoint["http_headers"]),
                    prefix + "enabled": True,
                    prefix + "enabled_tools": ["observe", "act_and_observe"],
                    prefix + "required": True,
                    prefix + "startup_timeout_sec": 10,
                    prefix + "tool_timeout_sec": TOOL_TIMEOUT_SECONDS,
                    prefix + "tools.act_and_observe.approval_mode": "approve",
                },
            )
        except Exception as error:
            path = self._writer.path if self._writer is not None else self._data_root / "chats"
            self._failed("", str(path), recording_reason(error))
            raise
        return configuration

    def _create_chat_archive(self) -> ProjectWriter:
        parent = self._data_root / "chats"
        parent.mkdir(parents=True, exist_ok=True)
        return ProjectWriter.create(parent / str(uuid4()))

    def record_thread(self, thread_id: str) -> None:
        """Associate observation and recording with the adopted thread."""
        if self._observations is None or self._recording is None:
            raise ProjectError("チャットの記録先が準備されていません")
        self._recording.submit_thread(thread_id)
        self._observations.thread_id = thread_id

    def record_dialogue(
        self,
        kind: str,
        payload: "JsonObject",
        *,
        turn_id: str | None,
    ) -> None:
        """Submit public dialogue for the thread held by the observation service."""
        observations = self._observations
        if (
            self._recording is not None
            and observations is not None
            and observations.thread_id is not None
        ):
            self._recording.submit_dialogue(
                "dialogue." + kind,
                payload,
                thread_id=observations.thread_id,
                turn_id=turn_id,
            )

    def report_failure(self, error: BaseException) -> None:
        """Report a failure with the current archive, when available."""
        path = str(self._writer.path) if self._writer is not None else ""
        self._failed("", path, recording_reason(error))

    async def close_endpoint(self) -> None:
        """Close HTTP intake while the service joins the runtime independently."""
        if self._mcp is not None:
            await self._mcp.close()

    async def drain(self) -> None:
        """Close the endpoint and recover encoding and saves, including after failures."""
        try:
            if self._mcp is not None:
                try:
                    await self._mcp.close()
                except Exception:
                    # The failed server task has already closed its listener.
                    self._mcp = None
                    raise
                self._mcp = None
        finally:
            if self._observations is not None:
                try:
                    await self._observations.drain()
                except Exception as error:  # noqa: BLE001 -- drain saves after encoding failure.
                    self.report_failure(error)
                self._observations = None
            if self._recording is not None:
                await self._recording.drain()
                self._recording = None

    async def disconnect(self) -> None:
        """Recover session resources, retaining an explicitly selected project writer."""
        try:
            await self.drain()
        finally:
            if not self._project_selected:
                await self._close_writer()

    async def close(self) -> None:
        """Recover the session and close even an explicitly selected project writer."""
        try:
            await self.drain()
        finally:
            await self._close_writer()

    async def _close_writer(self) -> None:
        if self._writer is not None:
            await asyncio.to_thread(self._writer.close)
            self._writer = None


def recording_reason(error: BaseException) -> str:
    """Return display-safe storage failure details."""
    if isinstance(error, ProjectError | OSError):
        return str(error)
    return f"記録処理に失敗しました ({type(error).__name__})"


async def _conversation_input(
    sequence: "tuple[InputStep, ...]",
    *,
    start_input: "InputStart",
    observations: ObservationService,
    recording: RecordingService,
) -> "tuple[OperationResult, Observation | ObservationError | None]":
    thread_id = observations.thread_id
    if thread_id is None:
        return OperationResult("INPUT_DISABLED", message="会話が準備されていません"), None
    return await act_and_observe(
        sequence,
        start_input=start_input,
        observations=observations,
        recording=recording,
        thread_id=thread_id,
    )
