"""M0 protocol probe: isolated home, no login, no model or game execution.

Run with uv run python -X utf8 <this-file> --output <new-directory>.
The directory must not exist. Keep it for inspection; nothing is deleted.
"""

import argparse
import json
import os
import queue
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from codex_cli_bin import bundled_codex_path

from gigas.runtime.codex.transport import JsonObject, JsonValue


def _object(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        message = "Expected a JSON object in the probe response"
        raise TypeError(message)
    return value


def _text(value: JsonValue) -> str:
    if not isinstance(value, str):
        message = "Expected text in the probe response"
        raise TypeError(message)
    return value


def _array(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        message = "Expected an array in the probe response"
        raise TypeError(message)
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class McpFixture:
    """Host a local discovery-only MCP endpoint with one unique marker tool."""

    def __init__(self, marker: str) -> None:
        """Start a local endpoint without connecting to any game device."""
        self.token = uuid.uuid4().hex
        self.calls: list[str] = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(
                self,
                format: str,  # noqa: A002 -- Preserve the standard handler's keyword signature.
                *args: object,
            ) -> None:
                pass

            def do_GET(self) -> None:
                self.send_error(405)

            def do_POST(self) -> None:
                if self.headers.get("Authorization") != "Bearer " + fixture.token:
                    self.send_error(401)
                    return
                value = _object(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                method = _text(value["method"])
                fixture.calls.append(method)
                if "id" not in value:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                result = {
                    "initialize": {
                        "protocolVersion": _object(value.get("params", {})).get(
                            "protocolVersion", "2025-06-18"
                        ),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": marker, "version": "1"},
                    },
                    "tools/list": {
                        "tools": [
                            {
                                "name": marker,
                                "description": "Discovery only; no game I/O.",
                                "inputSchema": {"type": "object", "properties": {}},
                            }
                        ]
                    },
                    "resources/list": {"resources": []},
                    "resources/templates/list": {"resourceTemplates": []},
                    "prompts/list": {"prompts": []},
                }.get(method, {})
                data = json.dumps({"jsonrpc": "2.0", "id": value["id"], "result": result}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/mcp"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def config(self) -> JsonObject:
        """Return only the endpoint configuration needed by app-server discovery."""
        return {
            "url": self.url,
            "http_headers": {"Authorization": "Bearer " + self.token},
            "required": True,
            "startup_timeout_sec": 10,
        }

    def close(self) -> None:
        """Stop and release the fixture HTTP server."""
        self.server.shutdown()
        self.server.server_close()


class Client:
    """Drive the pinned stdio protocol in one isolated, unauthenticated home."""

    def __init__(self, root: Path, memory: bool = True) -> None:
        """Start the runtime with an unavailable local inference provider."""
        env = os.environ.copy()
        env["CODEX_HOME"] = str(root / "codex_home")
        for key in ("CODEX_WINDOWS_REGISTERED_CORE", "CODEX_WINDOWS_SANDBOX_PACKAGE_FAMILY"):
            env.pop(key, None)
        self.stderr = (root / f"probe-stderr-{uuid.uuid4().hex}.txt").open("w", encoding="utf-8")
        command = [str(bundled_codex_path())]
        overrides = [
            'model_provider="m0_probe"',
            'model_providers.m0_probe={name="Unavailable local probe",base_url="http://127.0.0.1:9/v1",wire_api="responses",requires_openai_auth=false,request_max_retries=0,stream_max_retries=0}',
            f"features.memories={str(memory).lower()}",
            f"memories.generate_memories={str(memory).lower()}",
            f"memories.use_memories={str(memory).lower()}",
            "memories.disable_on_external_context=false",
            "features.apps=false",
            "features.plugins=false",
            "features.hooks=false",
            "features.codex_hooks=false",
            "features.plugin_hooks=false",
            "notify=[]",
            'web_search="live"',
        ]
        for override in overrides:
            command.extend(["-c", override])
        command.append("app-server")
        self.process = subprocess.Popen(  # noqa: S603 -- Bundled executable and fixed probe arguments.
            command,
            cwd=root,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr,
            text=True,
            encoding="utf-8",
        )
        stdin, stdout = self.process.stdin, self.process.stdout
        if stdin is None or stdout is None:
            message = "Probe subprocess pipes were not created"
            raise RuntimeError(message)
        self.stdin = stdin
        self.stdout = stdout
        self.queue: queue.Queue[JsonObject] = queue.Queue()
        self.counter = 0
        self.notifications: list[JsonObject] = []
        threading.Thread(target=self.read, daemon=True).start()
        self.call(
            "initialize",
            {
                "clientInfo": {"name": "gigas_m0_probe", "version": "0.1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        self.send({"method": "initialized"})

    def read(self) -> None:
        """Receive protocol objects on the sole stdout reader thread."""
        for line in self.stdout:
            self.queue.put(_object(json.loads(line)))

    def send(self, value: JsonObject) -> None:
        """Write one JSON request or notification to the local subprocess."""
        self.stdin.write(json.dumps(value) + "\n")
        self.stdin.flush()

    def call(self, method: str, params: JsonObject) -> JsonObject:
        """Await one RPC response while retaining intervening notifications."""
        self.counter += 1
        self.send({"id": self.counter, "method": method, "params": params})
        while True:
            value = self.queue.get(timeout=30)
            if value.get("id") == self.counter:
                if "error" in value:
                    raise RuntimeError((method, value["error"]))
                return _object(value["result"])
            self.notifications.append(value)

    def close(self) -> None:
        """Close stdin and wait for the owned runtime process to exit."""
        self.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.stderr.close()


def run(root: Path, first_mcp: McpFixture, second_mcp: McpFixture) -> JsonObject:
    """Check public management/configuration boundaries without inference.

    Args:
        root: New absolute directory that must not exist before the probe.
        first_mcp: Initial discovery-only MCP endpoint.
        second_mcp: Replacement endpoint for the idle resume check.

    Returns:
        Evidence also saved under root/result.json; no credential values are included.
    """
    root.mkdir(parents=True, exist_ok=False)
    (root / "codex_home").mkdir()
    project = root / "project"
    workspace = root / "unassigned" / uuid.uuid4().hex
    project.mkdir()
    workspace.mkdir(parents=True)
    skill = workspace / ".agents" / "skills" / "m0-local-marker"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: m0-local-marker\ndescription: M0 isolated discovery probe.\n"
        "---\nOnly a discovery marker.\n",
        encoding="utf-8",
    )
    (workspace / "AGENTS.md").write_text("M0 isolated instruction marker.\n", encoding="utf-8")
    version_env = os.environ.copy()
    version_env["CODEX_HOME"] = str(root / "codex_home")
    result: JsonObject = {
        "runtime": subprocess.check_output(  # noqa: S603 -- Bundled executable with --version.
            [str(bundled_codex_path()), "--version"], env=version_env, text=True
        ).strip()
    }
    c = Client(root)
    try:
        p = _object(
            c.call(
                "project/create",
                {
                    "name": "M0 probe",
                    "roots": [{"path": str(project)}],
                    "idempotencyKey": uuid.uuid4().hex,
                },
            )["project"]
        )
        _require(p["roots"] == [{"path": str(project)}], "Project root mismatch")
        result["project_one_root"] = True
        config = _object(
            c.call("config/read", {"cwd": str(workspace), "includeLayers": False})["config"]
        )
        result["config"] = {
            key: config.get(key) for key in ("web_search", "memories", "project_doc_max_bytes")
        }
        skills = c.call("skills/list", {"cwds": [str(workspace)], "forceReload": True})
        _require("m0-local-marker" in json.dumps(skills), "Local skill was not discovered")
        result["local_skill_discovered"] = True
        params: JsonObject = {
            "cwd": str(workspace),
            "runtimeWorkspaceRoots": [str(workspace)],
            "projectId": None,
            "ephemeral": False,
            "approvalPolicy": "on-request",
            "approvalsReviewer": "auto_review",
            "sandbox": "workspace-write",
            "developerInstructions": "M0 first instruction marker.",
            "config": {
                "mcp_servers": {"m0_fixture": first_mcp.config()},
                "sandbox_workspace_write": {
                    "network_access": False,
                    "writable_roots": [],
                    "exclude_tmpdir_env_var": True,
                    "exclude_slash_tmp": True,
                },
            },
        }
        response = c.call("thread/start", params)
        thread = _object(response["thread"])
        tid = _text(thread["id"])
        _require(thread["cwd"] == str(workspace), "Thread cwd mismatch")
        _require(thread.get("projectId") is None, "Thread should be unassigned")
        _require(response["approvalsReviewer"] == "auto_review", "Initial reviewer mismatch")
        result["start"] = {
            key: response.get(key)
            for key in ("cwd", "approvalPolicy", "approvalsReviewer", "sandbox")
        }
        result["session_source"] = thread.get("source")
        result["instruction_source_kinds"] = response.get("instructionSources")
        c.call("thread/unsubscribe", {"threadId": tid})
        bootstrap: JsonObject = {
            **params,
            "threadId": tid,
            "excludeTurns": True,
            "developerInstructions": "Records: " + str(workspace / "records" / tid),
        }
        for key in ("projectId", "ephemeral", "historyMode"):
            bootstrap.pop(key, None)
        try:
            c.call("thread/resume", bootstrap)
        except RuntimeError as error:
            if "no rollout found" not in str(error):
                raise
            result["empty_thread_resume_with_record_path"] = "Rejected: no rollout found"
        else:
            message = "Expected pinned-runtime empty-thread limitation changed"
            raise AssertionError(message)
        # The failed cold resume removed the empty in-memory thread; start afresh.
        response = c.call("thread/start", params)
        tid = _text(_object(response["thread"])["id"])
        inventory = c.call("mcpServerStatus/list", {"threadId": tid})
        _require("m0_marker_first" in json.dumps(inventory), "Initial MCP tool was not discovered")
        # Persist a user item without a usable inference endpoint; interrupt explicitly.
        turn = c.call(
            "turn/start",
            {
                "threadId": tid,
                "input": [{"type": "text", "text": "M0 persistence probe; no model endpoint."}],
            },
        )
        time.sleep(1)
        try:
            c.call("turn/interrupt", {"threadId": tid, "turnId": _object(turn["turn"])["id"]})
        except RuntimeError as error:
            if "no active turn to interrupt" not in str(error):
                raise
        result["persistence_turn"] = (
            "Unavailable local provider; interrupted or already terminated."
        )
        read = _object(c.call("thread/read", {"threadId": tid})["thread"])
        _require(read["cwd"] == str(workspace), "Stored cwd mismatch")
        unassigned = _array(
            c.call("thread/list", {"projectId": None, "modelProviders": []})["data"]
        )
        _require(
            tid in [_object(t)["id"] for t in unassigned], "Thread absent from unassigned list"
        )
        result["unassigned_list_and_read"] = True
        result["unsubscribe"] = c.call("thread/unsubscribe", {"threadId": tid})["status"]
        resume: JsonObject = {
            **params,
            "threadId": tid,
            "excludeTurns": True,
            "approvalsReviewer": "user",
            "developerInstructions": "M0 changed instruction marker.",
        }
        for key in ("projectId", "ephemeral", "historyMode"):
            resume.pop(key, None)
        resume["config"] = {
            **_object(params["config"]),
            "mcp_servers": {"m0_fixture": second_mcp.config()},
        }
        response = c.call("thread/resume", resume)
        _require(response["approvalsReviewer"] == "user", "Resumed reviewer mismatch")
        result["idle_unsubscribe_resume_reviewer_changed"] = True
        inventory = c.call("mcpServerStatus/list", {"threadId": tid})
        _require("m0_marker_second" in json.dumps(inventory), "Replacement MCP missing")
        _require("m0_marker_first" not in json.dumps(inventory), "Old MCP remains after resume")
        result["idle_resume_mcp_url_and_auth_replaced"] = True
        pages = c.call("thread/turns/list", {"threadId": tid, "limit": 10})
        result["history_page_read"] = bool(pages.get("data"))
        result["goal_read"] = c.call("thread/goal/get", {"threadId": tid})
    finally:
        c.close()
    c = Client(root, memory=False)
    try:
        read = _object(c.call("thread/read", {"threadId": tid})["thread"])
        _require(read["cwd"] == str(workspace), "Stored cwd mismatch")
        _require(
            _object(c.call("project/read", {"projectId": p["id"]})["project"])["id"] == p["id"],
            "Project identity changed after restart",
        )
        result["restart_resolves_workspace_and_project"] = True
        response = c.call("thread/resume", resume)
        _require(_object(response["thread"])["id"] == tid, "Thread identity changed after restart")
        config = _object(c.call("config/read", {"includeLayers": False})["config"])
        result["memory_off_config"] = config.get("memories")
        result["restart_resume"] = True
    finally:
        c.close()
    (root / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> None:
    """Run the probe and print its evidence from the CLI boundary."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    first = McpFixture("m0_marker_first")
    second = McpFixture("m0_marker_second")
    try:
        result = run(args.output.resolve(), first, second)
        print(json.dumps(result, ensure_ascii=False, indent=2))  # noqa: T201 -- CLI evidence.
    finally:
        first.close()
        second.close()


if __name__ == "__main__":
    main()
