import asyncio
import sys
from pathlib import Path

import pytest

from gigas.runtime.codex.transport import (
    RpcError,
    RpcNotification,
    ServerRequest,
    StdioTransport,
    TransportClosed,
    TransportError,
)

PEER = Path(__file__).parent / "peers" / "codex_transport_peer.py"


def test_server_requests_do_not_block_notifications_or_out_of_order_responses(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        transport = StdioTransport((sys.executable, "-u", str(PEER)), tmp_path)
        await transport.start()
        try:
            assert await transport.next_event() == ServerRequest(
                "review-1", "probe/review", {"text": "確認"}
            )
            assert await transport.next_event() == ServerRequest(7, "probe/review", None)
            assert await transport.next_event() == RpcNotification("probe/progress", {"step": 1})
            first, second = await asyncio.gather(
                transport.request("probe/first", {"input": "日本語"}),
                transport.request("probe/second", None),
            )
            assert first == {"method": "probe/first"}
            assert second == {"method": "probe/second"}
            await transport.respond("review-1", {"decision": "decline"})
            await transport.reject(7, -32601, "Unsupported request")
            assert await transport.next_event() == RpcNotification(
                "probe/replies",
                {
                    "messages": [
                        {"id": "review-1", "result": {"decision": "decline"}},
                        {"id": 7, "error": {"code": -32601, "message": "Unsupported request"}},
                    ]
                },
            )
            await transport.notify("probe/ready", {"ready": True})
            assert await transport.next_event() == RpcNotification(
                "probe/notified", {"method": "probe/ready", "params": {"ready": True}}
            )
        finally:
            await transport.close()

    asyncio.run(asyncio.wait_for(scenario(), timeout=10))


def test_eof_releases_all_requests_and_event_waiters(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport = StdioTransport((sys.executable, "-u", str(PEER), "eof"), tmp_path)
        await transport.start()
        try:
            results = await asyncio.gather(
                transport.request("probe/first", None),
                transport.request("probe/second", None),
                transport.next_event(),
                transport.next_event(),
                return_exceptions=True,
            )
            assert all(isinstance(result, TransportError) for result in results[:2])
            assert results[2:] == [TransportClosed("Peer closed stdout")] * 2
            assert await transport.next_event() == TransportClosed("Peer closed stdout")
            with pytest.raises(TransportError, match="Peer closed stdout"):
                await transport.request("probe/after-eof", None)
        finally:
            await transport.close()

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


@pytest.mark.parametrize(
    "mode",
    [
        "malformed",
        "non-object",
        "bool-id",
        "missing-result",
        "both",
        "long",
        "partial",
        "non-finite",
    ],
)
def test_invalid_peer_frame_fails_waiters_without_leaking_payload(
    tmp_path: Path, mode: str
) -> None:
    async def scenario() -> None:
        transport = StdioTransport((sys.executable, "-u", str(PEER), mode), tmp_path)
        await transport.start()
        try:
            with pytest.raises(TransportError, match="Invalid RPC frame") as failure:
                await transport.request("probe/invalid", None)
            event = await transport.next_event()
            assert event == TransportClosed("Invalid RPC frame")
            assert "TOP_SECRET" not in repr(failure.value)
            assert failure.value.__cause__ is None
        finally:
            await transport.close()

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


@pytest.mark.parametrize("cancel", [False, True], ids=["timeout", "cancel"])
def test_late_and_duplicate_responses_are_not_applied_to_later_requests(
    tmp_path: Path, *, cancel: bool
) -> None:
    async def scenario() -> None:
        transport = StdioTransport(
            (sys.executable, "-u", str(PEER), "late"), tmp_path, request_timeout=0.2
        )
        await transport.start()
        try:
            assert await transport.next_event() == RpcNotification("probe/ready", None)
            request = asyncio.create_task(transport.request("probe/slow", None))
            assert await transport.next_event() == RpcNotification("probe/held", None)
            if cancel:
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
            else:
                with pytest.raises(TransportError, match="RPC request timed out"):
                    await request
            assert await transport.request("probe/fast", None) == "current"
            assert await transport.request("probe/followup", None) == "followup"
        finally:
            await transport.close()

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_rpc_error_reports_only_code_and_local_method(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport = StdioTransport((sys.executable, "-u", str(PEER), "rpc-error"), tmp_path)
        await transport.start()
        try:
            with pytest.raises(RpcError) as failure:
                await transport.request("probe/rejected", None)
            assert failure.value.code == -32600
            assert failure.value.method == "probe/rejected"
            assert "TOP_SECRET" not in str(failure.value)
            assert await transport.request("probe/after-error", None) == "still-connected"
        finally:
            await transport.close()

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))
