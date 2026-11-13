"""Windows sandbox readiness and one explicitly requested setup operation."""

import asyncio
from typing import TYPE_CHECKING

from gigas.runtime.codex.models import SandboxEvent
from gigas.runtime.codex.transport import RpcError, TransportError

if TYPE_CHECKING:
    from collections.abc import Callable

    from gigas.runtime.codex.transport import JsonObject, RpcPeer


class WindowsSandbox:
    """Own initial readiness and setup completion without connection phase."""

    def __init__(self, peer: "RpcPeer", emit: "Callable[[SandboxEvent], None]") -> None:
        """Retain RPC and fact boundaries without reading or preparing the sandbox."""
        self._peer = peer
        self._emit = emit
        self._snapshot = SandboxEvent()
        self._read_task: asyncio.Task[None] | None = None
        self._pending: asyncio.Future[str | None] | None = None

    @property
    def snapshot(self) -> SandboxEvent:
        """Return the latest readiness and explicit setup status."""
        return self._snapshot

    def start_read(self) -> None:
        """Start the initial readiness read once during connection opening."""
        self._read_task = asyncio.create_task(self._read(), name="gigas-codex-sandbox")

    async def require_ready(self) -> None:
        """Wait for initial readiness and reject an unprepared sandbox.

        Raises:
            ValueError: Readiness is unavailable or the sandbox needs setup.
        """
        if self._read_task is not None:
            await self._read_task
        if self._snapshot.readiness != "ready":
            message = (
                "サンドボックス環境の準備が必要です。"
                "設定 → Codex → サンドボックス環境から設定・更新してください。"
            )
            raise ValueError(message)

    async def setup(self) -> None:
        """Request setup once and confirm readiness after its completion.

        Unknown start acceptance keeps waiting without resending. The caller owns
        this coroutine; closing the connection ends local waiting, not OS setup.

        Raises:
            ValueError: Setup is already pending, rejected, failed, or its final
                readiness could not be retrieved.
        """
        if self._pending is not None:
            raise ValueError("実行環境の準備を開始できない状態")
        completion = asyncio.get_running_loop().create_future()
        self._pending = completion
        self._snapshot = SandboxEvent(self._snapshot.readiness, True)
        self._emit(self._snapshot)
        try:
            await self._run_setup(completion)
        except ValueError as error:
            self._snapshot = SandboxEvent(self._snapshot.readiness, False, str(error))
            self._emit(self._snapshot)
            raise
        else:
            self._snapshot = SandboxEvent(
                self._snapshot.readiness,
                reconnect_required=self._snapshot.readiness != "ready",
            )
            self._emit(self._snapshot)
        finally:
            self._pending = None

    async def _run_setup(self, completion: asyncio.Future[str | None]) -> None:
        if self._read_task is not None:
            await self._read_task
        self._require_pending(completion)
        try:
            response = await self._peer.request("windowsSandbox/setupStart", {"mode": "elevated"})
        except RpcError:
            raise ValueError("実行環境の準備開始が拒否された") from None
        except TransportError:
            response = None
        self._require_pending(completion)
        if isinstance(response, dict) and response.get("started") is False:
            raise ValueError("実行環境の準備が開始されなかった")
        if not isinstance(response, dict) or response.get("started") is not True:
            self._snapshot = SandboxEvent(
                self._snapshot.readiness, True, "準備開始の受付結果を確認できない"
            )
            self._emit(self._snapshot)
        error = await completion
        self._require_pending(completion)
        if error is not None:
            raise ValueError(error)
        await self._read()
        self._require_pending(completion)
        if self._snapshot.readiness is None:
            raise ValueError("準備後の実行環境を確認できない")

    def _require_pending(self, completion: asyncio.Future[str | None]) -> None:
        if self._pending is not completion:
            raise asyncio.CancelledError

    async def _read(self) -> None:
        try:
            result = await self._peer.request("windowsSandbox/readiness", {})
        except TransportError:
            result = None
        readiness = result.get("status") if isinstance(result, dict) else None
        if isinstance(readiness, str) and readiness in {"ready", "notConfigured", "updateRequired"}:
            self._snapshot = SandboxEvent(readiness, self._pending is not None)
        else:
            self._snapshot = SandboxEvent(
                None, self._pending is not None, "実行環境の準備状態を取得できない"
            )
        self._emit(self._snapshot)

    def accept_completion(self, params: "JsonObject") -> None:
        """Resolve the pending elevated setup from its public completion notification."""
        completion = self._pending
        success = params.get("success")
        if (
            completion is not None
            and not completion.done()
            and params.get("mode") == "elevated"
            and isinstance(success, bool)
        ):
            error = params.get("error")
            detail = error if isinstance(error, str) else ""
            completion.set_result(None if success else detail or "実行環境の準備に失敗")

    def cancel_pending(self) -> None:
        """Synchronously cancel initial reading and invalidate local setup waiting."""
        if self._read_task is not None:
            self._read_task.cancel()
        completion, self._pending = self._pending, None
        if completion is not None and not completion.done():
            completion.cancel()

    async def close(self) -> None:
        """Collect the cancelled initial read; the connection owns the setup task."""
        if self._read_task is not None:
            await asyncio.gather(self._read_task, return_exceptions=True)
