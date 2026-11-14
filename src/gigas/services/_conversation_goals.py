"""Correlate public Goal RPC results with notifications for one owning context."""

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from gigas.runtime.codex import _conversation_protocol as protocol
from gigas.runtime.codex.connection import CodexConnectionError, MessageDeliveryError

if TYPE_CHECKING:
    from collections.abc import Callable

    from gigas.runtime.codex.connection import CodexConnection
    from gigas.runtime.codex.models import GoalState
    from gigas.runtime.codex.transport import JsonObject


@dataclass(slots=True)
class _Reply:
    seen: list["GoalState | None"] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class GoalTracker:
    """Retain observed Goal facts and correlate one in-flight change without rollback."""

    def __init__(self, state: "GoalState | None" = None) -> None:
        """Start with the owner's observed Goal, without triggering any request."""
        self.state = state
        self._reply: _Reply | None = None

    def observe(self, state: "GoalState | None") -> None:
        """Apply a public fact and wake the owner waiting for its matching reply."""
        self.state = state
        if self._reply is not None:
            self._reply.seen.append(state)
            self._reply.changed.set()

    def closing(self) -> None:
        """Wake a pending change so its caller can detect connection closure."""
        if self._reply is not None:
            self._reply.changed.set()

    async def change(
        self,
        connection: "CodexConnection",
        method: str,
        params: "JsonObject",
        *,
        closing: "Callable[[], bool]",
    ) -> None:
        """Wait for the reported change while preserving later notifications."""
        reply = self._reply = _Reply()
        try:
            result = await connection.request(method, params)
            try:
                if method == "thread/goal/clear":
                    if not protocol.goal_clear_result(result):
                        return
                    expected = None
                else:
                    expected = protocol.goal_set_result(result)
            except CodexConnectionError as error:
                raise MessageDeliveryError("目標の変更結果を確認できません") from error
            while self.state != expected and expected not in reply.seen:
                if closing():
                    raise MessageDeliveryError("目標の変更通知を確認する前に切断しました")
                reply.changed.clear()
                await reply.changed.wait()
        finally:
            if self._reply is reply:
                self._reply = None
