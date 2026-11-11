import asyncio
import sys
from pathlib import Path

from gigas.runtime.codex.transport import RpcNotification, ServerRequest, StdioTransport

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
