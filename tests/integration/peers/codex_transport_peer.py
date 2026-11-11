"""Small line-oriented peer for transport integration tests."""

import json
import sys


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


if __name__ == "__main__":
    multiplex()
