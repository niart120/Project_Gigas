import asyncio
import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from gigas.runtime.codex.transport import (
    RpcError,
    RpcNotification,
    ServerRequest,
    StdioTransport,
    TransportClosed,
    TransportError,
    _ProcessProtocol,
)

PEER = Path(__file__).parent / "peers" / "codex_transport_peer.py"
# Windows virtualenv executables are launchers; use the interpreter process itself.
PEER_COMMAND = (
    str(Path(sys.base_prefix) / "python.exe") if sys.platform == "win32" else sys.executable,
    "-u",
    str(PEER),
)


def test_server_requests_do_not_block_notifications_or_out_of_order_responses(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        transport = StdioTransport(PEER_COMMAND, tmp_path)
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
        transport = StdioTransport((*PEER_COMMAND, "eof"), tmp_path)
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
        transport = StdioTransport((*PEER_COMMAND, mode), tmp_path)
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
        transport = StdioTransport((*PEER_COMMAND, "late"), tmp_path, request_timeout=0.2)
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
        transport = StdioTransport((*PEER_COMMAND, "rpc-error"), tmp_path)
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


def test_start_failure_is_sanitized_and_close_releases_event_waiters(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport = StdioTransport((str(tmp_path / "TOP_SECRET_missing.exe"),), tmp_path)
        try:
            with pytest.raises(TransportError, match="Process start failed") as failure:
                await transport.start()
            assert "TOP_SECRET" not in str(failure.value)
            assert failure.value.__cause__ is None
            assert await transport.next_event() == TransportClosed("Process start failed")
        finally:
            await asyncio.gather(transport.close(), transport.close())

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_transport_closed_before_start_cannot_launch_a_process(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport = StdioTransport((*PEER_COMMAND, "lifetime"), tmp_path)
        await transport.close()
        try:
            with pytest.raises(TransportError, match="Transport closed"):
                await transport.start()
            assert await transport.next_event() == TransportClosed("Transport closed")
        finally:
            await transport.close()

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


@pytest.mark.parametrize("force_kill", [False, True], ids=["terminate", "kill-fallback"])
def test_close_waits_for_child_exit_and_releases_all_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, force_kill: bool
) -> None:
    async def scenario() -> None:
        baseline = asyncio.all_tasks()
        terminated: list[int] = []
        killed: list[int] = []
        waited: list[int] = []
        loop = asyncio.get_running_loop()
        original_spawn = loop.subprocess_exec
        monkeypatch.setenv("GIGAS_REMOVED_FROM_CHILD", "parent-value")
        monkeypatch.setenv("GIGAS_PRESERVED_IN_CHILD", "preserved-value")

        async def observed_spawn(
            factory: Callable[[], _ProcessProtocol],
            *command: str,
            cwd: Path,
            env: dict[str, str],
            stdin: int,
            stdout: int,
            stderr: int,
            creationflags: int,
        ) -> tuple[asyncio.SubprocessTransport, _ProcessProtocol]:
            assert "GIGAS_REMOVED_FROM_CHILD" not in env
            assert env["GIGAS_PRESERVED_IN_CHILD"] == "preserved-value"
            assert os.environ["GIGAS_REMOVED_FROM_CHILD"] == "parent-value"
            process, protocol = await original_spawn(
                factory,
                *command,
                cwd=cwd,
                env=env,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                creationflags=creationflags,
            )
            original_terminate = process.terminate
            original_kill = process.kill
            original_exited = protocol.process_exited

            def terminate() -> None:
                terminated.append(process.get_pid())
                if not force_kill:
                    original_terminate()

            def kill() -> None:
                killed.append(process.get_pid())
                original_kill()

            def exited() -> None:
                waited.append(process.get_pid())
                assert process.get_returncode() is not None
                original_exited()

            monkeypatch.setattr(process, "terminate", terminate)
            monkeypatch.setattr(process, "kill", kill)
            monkeypatch.setattr(protocol, "process_exited", exited)
            return process, protocol

        monkeypatch.setattr(loop, "subprocess_exec", observed_spawn)
        transport = StdioTransport(
            (*PEER_COMMAND, "lifetime"),
            tmp_path,
            env={"GIGAS_TRANSPORT_TEST": "isolated"},
            close_timeout=0.1,
            unset_env=("gigas_removed_from_child",),
        )
        await asyncio.gather(transport.start(), transport.start())
        try:
            await transport.start()
            started = await transport.next_event()
            assert isinstance(started, RpcNotification)
            assert started.method == "probe/started"
            assert started.params is not None
            pid = started.params["pid"]
            assert started.params["cwd"] == str(tmp_path)
            assert started.params["environment"] == "isolated"
            assert started.params["console"] is False
            pending = asyncio.create_task(transport.request("probe/hold", None))
            assert await transport.next_event() == RpcNotification("probe/held", None)
            await asyncio.gather(transport.close(), transport.close())
            with pytest.raises(TransportError, match="Transport closed"):
                await pending
            assert terminated == [pid]
            assert killed == ([pid] if force_kill else [])
            assert waited == [pid]
            assert asyncio.all_tasks() == baseline
        finally:
            await transport.close()

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


@pytest.mark.parametrize("cancel_start", [False, True], ids=["close-during-start", "cancel-start"])
def test_process_created_during_start_cancellation_or_close_is_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, cancel_start: bool
) -> None:
    async def scenario() -> None:
        baseline = asyncio.all_tasks()
        loop = asyncio.get_running_loop()
        original_spawn = loop.subprocess_exec
        spawned = asyncio.Event()
        release = asyncio.Event()
        processes: list[tuple[asyncio.SubprocessTransport, _ProcessProtocol]] = []

        async def delayed_spawn(
            factory: Callable[[], _ProcessProtocol],
            *command: str,
            cwd: Path,
            env: dict[str, str],
            stdin: int,
            stdout: int,
            stderr: int,
            creationflags: int,
        ) -> tuple[asyncio.SubprocessTransport, _ProcessProtocol]:
            process, protocol = await original_spawn(
                factory,
                *command,
                cwd=cwd,
                env=env,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                creationflags=creationflags,
            )
            processes.append((process, protocol))
            spawned.set()
            await release.wait()
            return process, protocol

        monkeypatch.setattr(loop, "subprocess_exec", delayed_spawn)
        transport = StdioTransport((*PEER_COMMAND, "lifetime"), tmp_path)
        starting = asyncio.create_task(transport.start())
        closing: asyncio.Task[None] | None = None
        try:
            await spawned.wait()
            if cancel_start:
                starting.cancel()
            else:
                closing = asyncio.create_task(transport.close())
            assert await transport.next_event() == TransportClosed("Transport closed")
            release.set()
            expected = asyncio.CancelledError if cancel_start else TransportError
            with pytest.raises(expected):
                await starting
            if closing is not None:
                await closing
            assert len(processes) == 1
            assert processes[0][0].get_returncode() is not None
            assert asyncio.all_tasks() == baseline
        finally:
            release.set()
            await transport.close()
            for process, protocol in processes:
                if process.get_returncode() is None:
                    process.kill()
                process.close()
                await protocol.closed

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_cancelled_close_does_not_cancel_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        baseline = asyncio.all_tasks()
        original_exited = _ProcessProtocol.process_exited
        reaped = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def release_exit(protocol: _ProcessProtocol) -> None:
            await release.wait()
            original_exited(protocol)

        delayed: list[asyncio.Task[None]] = []

        def delayed_exit(protocol: _ProcessProtocol) -> None:
            nonlocal calls
            calls += 1
            reaped.set()
            delayed.append(asyncio.create_task(release_exit(protocol)))

        monkeypatch.setattr(_ProcessProtocol, "process_exited", delayed_exit)
        transport = StdioTransport((*PEER_COMMAND, "lifetime"), tmp_path)
        await transport.start()
        try:
            started = await transport.next_event()
            assert isinstance(started, RpcNotification)
            closing = asyncio.create_task(transport.close())
            await reaped.wait()
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
            release.set()
            await transport.close()
            await asyncio.gather(*delayed)
            assert calls == 1
            assert asyncio.all_tasks() == baseline
        finally:
            release.set()
            await transport.close()

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_close_releases_pipes_still_held_by_a_descendant(tmp_path: Path) -> None:
    async def scenario() -> None:
        baseline = asyncio.all_tasks()
        transport = StdioTransport((*PEER_COMMAND, "inherited-pipe"), tmp_path, close_timeout=0.1)
        await transport.start()
        closing: asyncio.Task[None] | None = None
        try:
            started = await transport.next_event()
            assert isinstance(started, RpcNotification)
            assert started.method == "probe/started"
            closing = asyncio.create_task(transport.close())
            await asyncio.wait_for(asyncio.shield(closing), timeout=1)
            assert asyncio.all_tasks() == baseline
        finally:
            (tmp_path / "release-child").touch()
            if closing is not None:
                await closing
            await transport.close()

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_transport_receives_two_mib_but_sends_at_most_one_mib(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport = StdioTransport((*PEER_COMMAND, "sized"), tmp_path)
        await transport.start()
        try:
            sent_size = await transport.request(
                "probe/length", {"payload": "x" * (1024 * 1024 - 100)}
            )
            assert isinstance(sent_size, int)
            assert 1024 * 1024 - 100 < sent_size <= 1024 * 1024
            result = await transport.request("probe/sized", {"size": 2 * 1024 * 1024})
            assert isinstance(result, str)
            assert 2 * 1024 * 1024 - 100 < len(result) < 2 * 1024 * 1024
            with pytest.raises(TransportError, match="Outgoing RPC frame exceeds 1 MiB"):
                await transport.request("probe/length", {"payload": "x" * (1024 * 1024)})
            assert isinstance(await transport.request("probe/length", {}), int)
            with pytest.raises(TransportError, match="Invalid RPC frame"):
                await transport.request("probe/sized", {"size": 2 * 1024 * 1024 + 1})
            assert await transport.next_event() == TransportClosed("Invalid RPC frame")
        finally:
            await transport.close()

    asyncio.run(asyncio.wait_for(scenario(), timeout=10))
