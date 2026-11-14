"""Own a conversation's workspace, recording and MCP endpoint on one loop."""

import asyncio
from functools import partial
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING

from gigas.game_io.observation import ObservationError
from gigas.mcp.game_io import TOOL_TIMEOUT_SECONDS, GameIoMcpServer
from gigas.runtime.codex.models import ThreadConfiguration
from gigas.services._game_io_tools import act_and_observe
from gigas.services._workspaces import _require_directory, resolve_workspace
from gigas.services.input import OperationResult
from gigas.services.observation import ObservationService
from gigas.services.recording import RecordingService
from gigas.storage.records import GameIoRecordWriter, RecordError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from gigas.game_io.capture import CaptureReader
    from gigas.game_io.input.models import InputStep
    from gigas.game_io.observation import Observation, ObservationResolution
    from gigas.runtime.codex.management import CodexManagement
    from gigas.services._game_io_tools import InputStart
    from gigas.storage.records import DialogueRecordType


class ConversationResources:
    """Acquire and close resources without owning a runtime connection or thread ID."""

    def __init__(
        self,
        data_root: "Path",
        reader: "CaptureReader",
        failed: "Callable[[str, str, str], None]",
    ) -> None:
        """Retain dependencies without opening directories or an HTTP listener."""
        self._data_root = data_root
        self._reader = reader
        self._failed = failed
        self._project_id: str | None = None
        self._cwd: Path | None = None
        self._writer: GameIoRecordWriter | None = None
        self._observations: ObservationService | None = None
        self._recording: RecordingService | None = None
        self._mcp: GameIoMcpServer | None = None
        self._resolution: ObservationResolution = "full_hd"

    @property
    def records_bound(self) -> bool:
        """Whether game I/O can use this conversation's recording services."""
        return self._observations is not None and self._recording is not None

    def set_resolution(self, resolution: "ObservationResolution") -> None:
        """Apply the setting to later observations, including an existing session."""
        self._resolution = resolution
        if self._observations is not None:
            self._observations.resolution = resolution

    def select_project(self, project_id: str) -> None:
        """Adopt an identity before a thread starts; the caller checks admission."""
        if self._writer is not None:
            raise RecordError("会話の記録中はプロジェクトを変更できません")
        self._project_id = project_id
        self._cwd = None

    async def prepare(
        self, management: "CodexManagement", start_input: "InputStart"
    ) -> ThreadConfiguration:
        """Prepare the workspace and MCP endpoint without creating recording files."""
        try:
            if self._cwd is None:
                preparation = asyncio.create_task(
                    resolve_workspace(management, self._data_root, project_id=self._project_id)
                )
                try:
                    self._cwd = await asyncio.shield(preparation)
                except asyncio.CancelledError:
                    self._cwd = await preparation
                    raise
            else:
                await asyncio.to_thread(_require_directory, self._cwd)
            if self._mcp is None:
                self._mcp = GameIoMcpServer(
                    self._observe, partial(self._act, start_input=start_input)
                )
                try:
                    await self._mcp.start()
                except BaseException:
                    await self._mcp.close()
                    self._mcp = None
                    raise
            prefix = f"mcp_servers.{self._mcp.server_name}."
            endpoint = self._mcp.config
            return ThreadConfiguration(
                self._cwd,
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
                project_id=self._project_id,
            )
        except Exception as error:
            self.report_failure(error)
            raise

    async def bind_records(self, thread_id: str) -> None:
        """Acquire records for the adopted thread before allowing game I/O or submission."""
        if self._cwd is None:
            raise RecordError("会話の作業場所が準備されていません")
        try:
            if self._writer is None:
                acquisition = asyncio.create_task(
                    asyncio.to_thread(_open_records, self._cwd, thread_id)
                )
                try:
                    self._writer = await asyncio.shield(acquisition)
                except asyncio.CancelledError:
                    # A worker can acquire the OS lock after cancellation; retain
                    # its result so stop/retry and disconnect share the same owner.
                    self._writer = await acquisition
                    raise
            else:
                await asyncio.to_thread(_require_directory, self._cwd)
            writer = self._writer
            if self._recording is None:
                self._recording = RecordingService(writer)
                self._recording.on_failure = lambda error: self._failed(
                    "", str(writer.path), recording_reason(error)
                )
            if self._observations is None:
                self._observations = ObservationService(
                    self._reader, self._recording, resolution=self._resolution
                )
        except Exception as error:
            self.report_failure(error)
            raise

    def record_dialogue(self, kind: "DialogueRecordType", text: str) -> None:
        """Queue an already selected body without interpreting submission acceptance."""
        if self._recording is not None:
            self._recording.submit_dialogue(kind, text)

    def report_failure(self, error: BaseException) -> None:
        """Report a failure with the current record destination or workspace."""
        path = self._writer.path if self._writer is not None else self._cwd
        self._failed("", str(path) if path is not None else "", recording_reason(error))

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
        """Close all thread resources, retaining only the selected Project identity."""
        await self.close()

    async def close(self) -> None:
        """Drain accepted work and release the writer even when HTTP shutdown fails."""
        try:
            await self.drain()
        finally:
            if self._writer is not None:
                await asyncio.to_thread(self._writer.close)
                self._writer = None
            self._cwd = None

    async def _observe(self) -> "Observation":
        if self._observations is None:
            raise ObservationError("BUSY", "会話の記録先が準備されていません")
        return await self._observations.observe()

    async def _act(
        self, sequence: "tuple[InputStep, ...]", *, start_input: "InputStart"
    ) -> "tuple[OperationResult, Observation | ObservationError | None]":
        if self._observations is None or self._recording is None:
            return OperationResult("INPUT_DISABLED", message="会話が準備されていません"), None
        return await act_and_observe(
            sequence,
            start_input=start_input,
            observations=self._observations,
            recording=self._recording,
        )


def _open_records(workspace: "Path", thread_id: str) -> GameIoRecordWriter:
    if not thread_id or thread_id in {".", ".."} or PureWindowsPath(thread_id).name != thread_id:
        raise RecordError("会話IDを記録先のフォルダー名として使用できません")
    _require_directory(workspace)
    parent = workspace / "records"
    parent.mkdir(exist_ok=True)
    path = parent / thread_id
    return GameIoRecordWriter.open(path) if path.exists() else GameIoRecordWriter.create(path)


def recording_reason(error: BaseException) -> str:
    """Return display-safe storage failure details."""
    if isinstance(error, RecordError | OSError):
        return str(error)
    return f"記録処理に失敗しました ({type(error).__name__})"
