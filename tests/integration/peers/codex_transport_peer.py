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
    else:
        invalid(mode)
