"""Small line-oriented peer for transport integration tests."""

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from threading import Event


def send(message: object) -> None:
    """Write one complete protocol frame."""
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def multiplex() -> None:
    """Hold server requests while answering client requests out of order."""
    send({"id": "review-1", "method": "probe/review", "params": {"text": "確認"}})
    send({"id": 7, "method": "probe/review", "params": None})
    send({"method": "probe/progress", "params": {"step": 1}})
    first = json.loads(sys.stdin.readline())
    second = json.loads(sys.stdin.readline())
    for request in (second, first):
        send({"id": request["id"], "result": {"method": request["method"]}})
    replies = [json.loads(sys.stdin.readline()), json.loads(sys.stdin.readline())]
    send({"method": "probe/replies", "params": {"messages": replies}})
    notification = json.loads(sys.stdin.readline())
    send({"method": "probe/notified", "params": notification})
    sys.stdin.read()


def eof() -> None:
    """Close without responding to either outstanding client request."""
    sys.stdin.readline()
    sys.stdin.readline()


def invalid(mode: str) -> None:
    """Return one invalid frame without exposing its payload through errors."""
    request = json.loads(sys.stdin.readline())
    frames = {
        "malformed": '{"secret":"TOP_SECRET", invalid}\n',
        "non-object": '["TOP_SECRET"]\n',
        "bool-id": '{"id":true,"method":"TOP_SECRET","params":{}}\n',
        "missing-result": json.dumps({"id": request["id"], "secret": "TOP_SECRET"}) + "\n",
        "both": json.dumps({"id": request["id"], "result": None, "error": {}}) + "\n",
        "long": json.dumps({"id": request["id"], "result": "TOP_SECRET" * 240000}) + "\n",
        "partial": '{"method":"TOP_SECRET"}',
        "non-finite": '{"method":"TOP_SECRET","params":{"value":NaN}}\n',
    }
    sys.stdout.write(frames[mode])
    sys.stdout.flush()


def late_response() -> None:
    """Release a held response only after the following request has arrived."""
    send({"method": "probe/ready", "params": None})
    slow = json.loads(sys.stdin.readline())
    send({"method": "probe/held", "params": None})
    fast = json.loads(sys.stdin.readline())
    send({"id": slow["id"], "result": "old"})
    send({"id": fast["id"], "result": "current"})
    followup = json.loads(sys.stdin.readline())
    send({"id": fast["id"], "result": "duplicate"})
    send({"id": "unknown", "result": "unrelated"})
    send({"id": followup["id"], "result": "followup"})
    sys.stdin.read()


def rpc_error() -> None:
    """Send a rejection containing sensitive diagnostic fields."""
    request = json.loads(sys.stdin.readline())
    send(
        {
            "id": request["id"],
            "error": {"code": -32600, "message": "TOP_SECRET", "data": {"token": "TOP_SECRET"}},
        }
    )
    request = json.loads(sys.stdin.readline())
    send({"id": request["id"], "result": "still-connected"})
    sys.stdin.read()


def sized_frames() -> None:
    """Report input byte counts or return an exactly sized ASCII response."""
    for line in sys.stdin:
        request = json.loads(line)
        if request["method"] == "probe/length":
            send({"id": request["id"], "result": len(line.encode("utf-8"))})
        else:
            frame = {"id": request["id"], "result": ""}
            overhead = len(json.dumps(frame)) + 1
            frame["result"] = "x" * (request["params"]["size"] - overhead)
            sys.stdout.buffer.write((json.dumps(frame) + "\n").encode("utf-8"))
            sys.stdout.buffer.flush()


def lifetime() -> None:
    """Remain alive after stdin closes until explicitly terminated."""
    console = False
    if sys.platform == "win32":
        console = bool(ctypes.windll.kernel32.GetConsoleWindow())
    sys.stderr.write("TOP_SECRET" * 32768)
    sys.stderr.flush()
    send(
        {
            "method": "probe/started",
            "params": {
                "pid": os.getpid(),
                "cwd": str(Path.cwd()),
                "environment": os.environ.get("GIGAS_TRANSPORT_TEST"),
                "console": console,
            },
        }
    )
    if sys.stdin.readline():
        send({"method": "probe/held", "params": None})
    Event().wait()


def inherited_pipe() -> None:
    """Leave a child holding output pipes after this peer is terminated."""
    subprocess.Popen(  # noqa: S603 -- fixed test peer and temporary release path
        [sys.executable, __file__, "inherited-pipe-child", str(Path.cwd() / "release-child")],
        stdin=subprocess.DEVNULL,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    send({"method": "probe/started", "params": {"pid": os.getpid()}})
    Event().wait()


def inherited_pipe_child() -> None:
    """Keep inherited handles open until the test releases them or times out."""
    release = Path(sys.argv[2])
    deadline = time.monotonic() + 10
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(0.01)


def dialogue() -> None:
    """Validate one GUI dialogue without recording RPC data."""
    thread_id, turn_id = "dialogue-thread", "dialogue-turn"
    for raw in sys.stdin.buffer:
        request = json.loads(raw)
        method, params = request.get("method"), request.get("params")
        if method == "initialized":
            continue
        if method == "initialize":
            result = {"userAgent": "gigas-dialogue-peer"}
        elif method == "account/read":
            result = {"account": {"type": "chatgpt"}, "requiresOpenaiAuth": True}
        elif method == "account/rateLimits/read":
            result = {"rateLimits": {}, "ordinaryUsageAllowed": True}
        elif method == "windowsSandbox/readiness":
            result = {"status": "ready"}
        elif method == "config/read":
            result = {"config": {"mcp_servers": {}}}
        elif method == "model/list":
            result = {
                "data": [
                    {
                        "id": "fixture-model",
                        "model": "fixture-model",
                        "isDefault": True,
                        "defaultReasoningEffort": "low",
                        "supportedReasoningEfforts": [{"reasoningEffort": "low"}],
                    }
                ],
                "nextCursor": None,
            }
        elif method == "thread/start":
            assert params["sandbox"] in {"read-only", "workspace-write"}
            assert params["approvalPolicy"] == "on-request"
            assert params["approvalsReviewer"] == "user"
            result = {
                "thread": {"id": thread_id},
                "model": params["model"],
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
                "cwd": params["cwd"],
                "sandbox": {
                    "type": "workspaceWrite",
                    "writableRoots": [],
                    "networkAccess": False,
                    "excludeTmpdirEnvVar": True,
                    "excludeSlashTmp": True,
                }
                if params["sandbox"] == "workspace-write"
                else {"type": "readOnly"},
            }
        else:
            assert method == "turn/start"
            assert params["threadId"] == thread_id
            assert params["input"] == [{"type": "text", "text": "開始 日本語🙂"}]
            send({"id": request["id"], "result": {"turn": {"id": turn_id, "status": "inProgress"}}})
            break
        send({"id": request["id"], "result": result})
    else:
        return

    def notice(method: str, fields: dict[str, object]) -> None:
        params = {"threadId": thread_id, **fields}
        if method not in {"turn/started", "turn/completed"}:
            params["turnId"] = turn_id
        send({"method": method, "params": params})

    notice("turn/started", {"turn": {"id": turn_id, "status": "inProgress"}})
    notice("turn/plan/updated", {"plan": [{"step": "経路確認", "status": "inProgress"}]})
    notice(
        "item/reasoning/summaryTextDelta",
        {
            "itemId": "reason",
            "summaryIndex": 0,
            "delta": "公開要約",
        },
    )
    notice(
        "item/completed",
        {
            "item": {
                "id": "reason",
                "type": "reasoning",
                "summary": ["公開要約"],
                "content": ["INTERNAL_CONTENT"],
            }
        },
    )
    notice(
        "item/started",
        {
            "item": {
                "id": "observe",
                "type": "mcpToolCall",
                "server": "fixture",
                "tool": "observe",
                "status": "inProgress",
            }
        },
    )
    notice(
        "item/completed",
        {
            "item": {
                "id": "observe",
                "type": "mcpToolCall",
                "server": "fixture",
                "tool": "observe",
                "status": "completed",
                "result": {"content": [{"type": "text", "text": "観測完了"}]},
            }
        },
    )
    send(
        {
            "id": "dialogue-question",
            "method": "item/tool/requestUserInput",
            "params": {
                "threadId": thread_id,
                "turnId": turn_id,
                "itemId": "questions",
                "isBlocking": True,
                "questions": [
                    {
                        "id": "route",
                        "header": "経路",
                        "question": "どの経路ですか?",
                        "isOther": False,
                        "isSecret": False,
                        "options": [
                            {"label": "North", "description": "北側"},
                            {"label": "South", "description": "南側"},
                        ],
                    },
                    {
                        "id": "note",
                        "header": "補足",
                        "question": "回答後の指示は?",
                        "isOther": True,
                        "isSecret": False,
                        "options": [],
                    },
                ],
            },
        }
    )
    steering = json.loads(sys.stdin.buffer.readline())
    assert steering["method"] == "turn/steer"
    assert steering["params"] == {
        "threadId": thread_id,
        "expectedTurnId": turn_id,
        "input": [{"type": "text", "text": "質問は保留し、門の前で止まる"}],
    }
    send({"id": steering["id"], "result": {"turnId": turn_id}})
    answer = json.loads(sys.stdin.buffer.readline())
    answer_matches = answer == {
        "id": "dialogue-question",
        "result": {
            "answers": {
                "route": {"answers": ["South"]},
                "note": {"answers": ["補給後に現在地を確認する"]},
            }
        },
    }
    # Keep failure diagnostics boolean-only instead of exposing RPC payloads.
    assert answer_matches
    del answer
    notice("serverRequest/resolved", {"requestId": "dialogue-question"})
    command = {
        "id": "command",
        "type": "commandExecution",
        "command": "Write-Output fixture",
        "cwd": str(Path.cwd()),
        "status": "inProgress",
    }
    notice("item/started", {"item": command})
    send(
        {
            "id": "dialogue-approval",
            "method": "item/commandExecution/requestApproval",
            "params": {
                "threadId": thread_id,
                "turnId": turn_id,
                "itemId": "command",
                "startedAtMs": 1,
                "command": "Write-Output fixture",
                "cwd": str(Path.cwd()),
                "availableDecisions": ["accept", "decline", "cancel"],
            },
        }
    )
    decision = json.loads(sys.stdin.buffer.readline())
    assert decision == {"id": "dialogue-approval", "result": {"decision": "decline"}}
    notice("serverRequest/resolved", {"requestId": "dialogue-approval"})
    notice("item/completed", {"item": {**command, "status": "declined"}})
    notice("turn/plan/updated", {"plan": [{"step": "経路確認", "status": "completed"}]})
    notice(
        "item/completed",
        {
            "item": {
                "id": "final",
                "type": "agentMessage",
                "text": "GIGAS_DIALOGUE_OK",
            }
        },
    )
    notice("turn/completed", {"turn": {"id": turn_id, "status": "completed"}})
    for raw in sys.stdin.buffer:
        request = json.loads(raw)
        assert request["method"] == "thread/backgroundTerminals/list"
        send({"id": request["id"], "result": {"data": [], "nextCursor": None}})


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "multiplex"
    if mode == "multiplex":
        multiplex()
    elif mode == "eof":
        eof()
    elif mode == "late":
        late_response()
    elif mode == "rpc-error":
        rpc_error()
    elif mode == "sized":
        sized_frames()
    elif mode == "lifetime":
        lifetime()
    elif mode == "inherited-pipe":
        inherited_pipe()
    elif mode == "inherited-pipe-child":
        inherited_pipe_child()
    elif mode == "dialogue":
        dialogue()
    else:
        invalid(mode)
