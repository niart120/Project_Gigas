"""ChatGPT authentication, login correlation and one owned usage refresh."""

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from gigas.runtime.codex.models import (
    AccountInfo,
    LoginEvent,
    RateLimitWindow,
    UsageBucket,
    UsageCredits,
    UsageEvent,
)
from gigas.runtime.codex.transport import TransportError

if TYPE_CHECKING:
    from collections.abc import Callable

    from gigas.runtime.codex.transport import JsonObject, JsonValue, RpcPeer


def _object(value: "JsonValue") -> "JsonObject":
    if isinstance(value, dict):
        return value
    raise ValueError("Codexの応答形式が不正")


def _text(value: "JsonValue") -> str:
    if isinstance(value, str) and value:
        return value
    raise ValueError("Codexの応答形式が不正")


def _optional_text(value: "JsonValue") -> str | None:
    return value if isinstance(value, str) else None


def _login_url(value: "JsonValue") -> str:
    url = _text(value)
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("ログインURLが不正")
    return url


def _usage_window(value: "JsonValue") -> RateLimitWindow | None:
    if value is None:
        return None
    window = _object(value)
    used = window.get("usedPercent")
    minutes, resets = window.get("windowDurationMins"), window.get("resetsAt")
    if type(used) is not int:
        raise ValueError("利用枠の応答形式が不正")
    return RateLimitWindow(
        used,
        minutes if type(minutes) is int else None,
        resets if type(resets) is int else None,
    )


def _usage_bucket(value: "JsonValue", key: str | None = None) -> UsageBucket:
    bucket = _object(value)
    credit_snapshot = None
    if bucket.get("credits") is not None:
        data = _object(bucket["credits"])
        has_credits, unlimited = data.get("hasCredits"), data.get("unlimited")
        if not isinstance(has_credits, bool) or not isinstance(unlimited, bool):
            raise ValueError("追加利用枠の応答形式が不正")
        credit_snapshot = UsageCredits(has_credits, unlimited, _optional_text(data.get("balance")))
    return UsageBucket(
        _optional_text(bucket.get("limitId")) or key,
        _optional_text(bucket.get("limitName")),
        _usage_window(bucket.get("primary")),
        _usage_window(bucket.get("secondary")),
        credit_snapshot,
    )


class AccountSession:
    """Own account facts and usage I/O; the connection owns phase and login tasks."""

    def __init__(
        self, peer: "RpcPeer", emit: "Callable[[AccountInfo | LoginEvent | UsageEvent], None]"
    ) -> None:
        """Keep dependencies without reading credentials or starting background work."""
        self._peer = peer
        self._emit = emit
        self._authenticated = False
        self._login_id: str | None = None
        self._login_starting = False
        self._login_completions: dict[str, bool] = {}
        self._usage = UsageEvent()
        self._usage_read: asyncio.Task[None] | None = None
        self._usage_refresh_pending = False

    @property
    def authenticated(self) -> bool:
        """Whether account/read confirmed ChatGPT authentication."""
        return self._authenticated

    @property
    def usage(self) -> UsageEvent:
        """Latest usage snapshot, retaining known permission through failed reads."""
        return self._usage

    @property
    def can_login(self) -> bool:
        """Whether an explicit login can begin without overlapping another attempt."""
        return not self._authenticated and self._login_id is None and not self._login_starting

    async def read_account(self) -> None:
        """Read and publish only the public ChatGPT account fields."""
        result = _object(await self._peer.request("account/read", {"refreshToken": False}))
        account = result.get("account")
        self._authenticated = isinstance(account, dict) and account.get("type") == "chatgpt"
        self._emit(
            AccountInfo(
                _optional_text(account.get("email")),
                _optional_text(account.get("planType")),
                _optional_text(account.get("workspaceName")),
                True,
            )
            if self._authenticated and isinstance(account, dict)
            else AccountInfo()
        )

    async def login(self) -> bool | None:
        """Start login; return an early completion result or None while it is pending.

        The connection checks can_login and owns this operation's task. Successful
        completion rereads account facts; it does not start model or usage reads.
        """
        self._login_starting = True
        try:
            result = _object(await self._peer.request("account/login/start", {"type": "chatgpt"}))
            self._login_id = _text(result.get("loginId"))
            completion = self._login_completions.get(self._login_id)
            if completion is not None:
                return await self._complete_login(completion)
            self._emit(LoginEvent(_login_url(result.get("authUrl"))))
        finally:
            self._login_starting = False
            self._login_completions.clear()
        return None

    async def accept_login_completion(self, params: "JsonObject") -> bool | None:
        """Accept the matching completion, caching only its outcome during login start."""
        login_id = params.get("loginId")
        if isinstance(login_id, str):
            success = params.get("success") is True
            if login_id == self._login_id:
                return await self._complete_login(success)
            if self._login_starting:
                self._login_completions[login_id] = success
        return None

    async def _complete_login(self, success: bool) -> bool:
        self._login_id = None
        if success:
            await self.read_account()
        return success

    def refresh_usage(self) -> None:
        """Coalesce full usage reads; the connection rejects calls after closing."""
        if not self._authenticated:
            return
        self._usage_refresh_pending = True
        if self._usage_read is None or self._usage_read.done():
            self._usage_read = asyncio.create_task(self._read_usage(), name="gigas-codex-usage")

    async def _read_usage(self) -> None:
        while self._usage_refresh_pending:
            self._usage_refresh_pending = False
            try:
                result = _object(await self._peer.request("account/rateLimits/read", {}))
                keyed = result.get("rateLimitsByLimitId")
                buckets = (
                    tuple(_usage_bucket(value, key) for key, value in keyed.items())
                    if isinstance(keyed, dict) and keyed
                    else (_usage_bucket(result.get("rateLimits")),)
                )
                allowed = result.get("ordinaryUsageAllowed")
                permission = allowed if isinstance(allowed, bool) else None
                if permission is None and self._usage.ordinary_usage_allowed is False:
                    permission = False
                self._usage = UsageEvent(buckets, permission, True)
            except (TransportError, ValueError):
                self._usage = UsageEvent(
                    self._usage.buckets, self._usage.ordinary_usage_allowed, False
                )
            self._emit(self._usage)

    def cancel_pending(self) -> None:
        """Request cancellation of the owned refresh before connection cleanup yields."""
        self._usage_refresh_pending = False
        if self._usage_read is not None:
            self._usage_read.cancel()

    async def close(self) -> None:
        """Join usage I/O and cancel a known login after the connection joins operations."""
        if self._usage_read is not None:
            await asyncio.gather(self._usage_read, return_exceptions=True)
        if self._login_id is not None:
            with suppress(TransportError, TimeoutError):
                async with asyncio.timeout(2):
                    await self._peer.request("account/login/cancel", {"loginId": self._login_id})
            self._login_id = None
