"""Acquire one thread's resources and prove its start or resume configuration."""

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from gigas.runtime.codex import _conversation_protocol as protocol
from gigas.runtime.codex.connection import CodexConnectionError
from gigas.runtime.codex.management import CodexManagement
from gigas.services.conversations import StoredConversationTarget
from gigas.storage.records import RecordError

if TYPE_CHECKING:
    from collections.abc import Callable

    from gigas.runtime.codex.connection import CodexConnection
    from gigas.runtime.codex.models import (
        ExecutionSettings,
        GoalState,
        ModelSelection,
        ThreadConfiguration,
    )
    from gigas.runtime.codex.transport import JsonObject
    from gigas.services._conversation_resources import ConversationResources
    from gigas.services._conversation_selection import ConversationSelection
    from gigas.services._game_io_tools import InputStart


@dataclass(slots=True)
class PreparationAttempt:
    """One submission's resume request, distinct from its later message delivery."""

    resume_task: asyncio.Task[None] | None = None
    resume_submitted: bool = False

    def cancel_unsubmitted(self) -> None:
        """Cancel a queued resume before its RPC body has started."""
        if not self.resume_submitted and self.resume_task is not None:
            self.resume_task.cancel()


class ThreadPreparation:
    """Own the notification identity and separately validated loading result.

    A submitted resume can act before its reply. The ID is therefore assigned
    before that RPC, while loaded becomes true only after response validation.
    The caller's submission retains the resume task through cancellation or close.
    """

    def __init__(
        self,
        connection: "CodexConnection",
        resources: "ConversationResources",
        selection: "ConversationSelection",
        activate: "Callable[[GoalState | None], None]",
    ) -> None:
        """Bind dependencies without acquiring resources or adopting a saved Goal."""
        self._connection = connection
        self._resources = resources
        self._selection = selection
        self._activate = activate
        self.thread_id: str | None = None
        self.loaded = False

    def require_confirmed(self) -> None:
        """Reject an attempted resume whose execution configuration is unconfirmed."""
        if self.thread_id is not None and not self.loaded:
            message = "会話の再開が完了していません。Codex App Serverを切断して接続し直してください"
            raise CodexConnectionError(message)

    async def prepare(
        self,
        settings: "ExecutionSettings",
        start_input: "InputStart",
        attempt: PreparationAttempt,
    ) -> str:
        """Prepare new execution or resume saved execution once, preserving its workspace.

        Failures before a resume RPC allow another explicit preparation attempt.
        An ID without a validated load requires disconnect/reconnect; it is never
        treated as a new conversation or silently retried on the same connection.
        """
        self.require_confirmed()
        target = self._selection.target
        await self._connection.require_ready()
        try:
            if self.thread_id is not None:
                await self._resources.bind_records(self.thread_id)
                return self.thread_id
            if isinstance(target, StoredConversationTarget):
                configuration = await self._resources.prepare_existing(
                    target.cwd, start_input, project_id=target.project_id
                )
                await self._resources.bind_records(target.thread_id)
            else:
                configuration = await self._resources.prepare(
                    CodexManagement(self._connection), start_input, project_id=target.project_id
                )
            selection = self._connection.selection
            config = await self._connection.request(
                "config/read", {"includeLayers": False, "cwd": str(configuration.cwd)}
            )
            if isinstance(target, StoredConversationTarget):
                params = protocol.thread_resume_params(
                    target.thread_id, configuration, selection, config, settings=settings
                )
                task = asyncio.create_task(
                    self._resume(target, configuration, selection, settings, params, attempt),
                    name="gigas-thread-resume",
                )
                attempt.resume_task = task
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    attempt.cancel_unsubmitted()
                    await asyncio.gather(task, return_exceptions=True)
                    raise
                return target.thread_id
            params = protocol.thread_start_params(
                configuration, selection, config, settings=settings
            )
            result = await self._connection.request("thread/start", params)
            self.thread_id = protocol.validate_thread(
                result, configuration, selection, settings=settings
            )
            self.loaded = True
            self._selection.adopt_started(
                self.thread_id, configuration.cwd, configuration.project_id
            )
            self._activate(None)
            await self._resources.bind_records(self.thread_id)
        except (OSError, RecordError) as error:
            raise CodexConnectionError("会話の作業場所・記録先を準備できません") from error
        else:
            return self.thread_id

    async def _resume(
        self,
        target: StoredConversationTarget,
        configuration: "ThreadConfiguration",
        selection: "ModelSelection",
        settings: "ExecutionSettings",
        params: "JsonObject",
        attempt: PreparationAttempt,
    ) -> None:
        self.thread_id = target.thread_id
        self._activate(self._selection.goal)
        attempt.resume_submitted = True
        result = await self._connection.request("thread/resume", params)
        protocol.validate_thread(
            result, configuration, selection, settings=settings, expected_thread_id=target.thread_id
        )
        self.loaded = True
