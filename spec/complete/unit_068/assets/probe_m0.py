"""M0 protocol probe: isolated home, no login, no model or game execution.

Run with uv run python -X utf8 <this-file> --output <new-directory>.
The directory must not exist. Keep it for inspection; nothing is deleted.
"""

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from codex_cli_bin import bundled_codex_path


class McpFixture:
    def __init__(self, marker):
        self.token = uuid.uuid4().hex
        self.calls = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_error(405)

            def do_POST(self):
                if self.headers.get("Authorization") != "Bearer " + fixture.token:
                    self.send_error(401)
                    return
                value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fixture.calls.append(value["method"])
                if "id" not in value:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                result = {
                    "initialize": {
                        "protocolVersion": value.get("params", {}).get(
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
                }.get(value["method"], {})
                data = json.dumps({"jsonrpc": "2.0", "id": value["id"], "result": result}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/mcp"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def config(self):
        return {
            "url": self.url,
            "http_headers": {"Authorization": "Bearer " + self.token},
            "required": True,
            "startup_timeout_sec": 10,
        }

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Client:
    def __init__(self, root, memory=True):
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
        self.process = subprocess.Popen(
            command,
            cwd=root,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr,
            text=True,
            encoding="utf-8",
        )
        self.queue = queue.Queue()
        self.counter = 0
        self.notifications = []
        threading.Thread(target=self.read, daemon=True).start()
        self.call(
            "initialize",
            {
                "clientInfo": {"name": "gigas_m0_probe", "version": "0.1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        self.send({"method": "initialized"})

    def read(self):
        for line in self.process.stdout:
            self.queue.put(json.loads(line))

    def send(self, value):
        self.process.stdin.write(json.dumps(value) + "\n")
        self.process.stdin.flush()

    def call(self, method, params):
        self.counter += 1
        self.send({"id": self.counter, "method": method, "params": params})
        while True:
            value = self.queue.get(timeout=30)
            if value.get("id") == self.counter:
                if "error" in value:
                    raise RuntimeError((method, value["error"]))
                return value["result"]
            self.notifications.append(value)

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.stderr.close()


def run(root, first_mcp, second_mcp):
    root.mkdir(parents=True, exist_ok=False)
    (root / "codex_home").mkdir()
    project = root / "project"
    workspace = root / "unassigned" / uuid.uuid4().hex
    project.mkdir()
    workspace.mkdir(parents=True)
    skill = workspace / ".agents" / "skills" / "m0-local-marker"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: m0-local-marker\ndescription: M0 isolated discovery probe.\n---\nOnly a discovery marker.\n",
        encoding="utf-8",
    )
    (workspace / "AGENTS.md").write_text("M0 isolated instruction marker.\n", encoding="utf-8")
    version_env = os.environ.copy()
    version_env["CODEX_HOME"] = str(root / "codex_home")
    result = {
        "runtime": subprocess.check_output(
            [str(bundled_codex_path()), "--version"], env=version_env, text=True
        ).strip()
    }
    c = Client(root)
    try:
        p = c.call(
            "project/create",
            {
                "name": "M0 probe",
                "roots": [{"path": str(project)}],
                "idempotencyKey": uuid.uuid4().hex,
            },
        )["project"]
        assert p["roots"] == [{"path": str(project)}]
        result["project_one_root"] = True
        config = c.call("config/read", {"cwd": str(workspace), "includeLayers": False})["config"]
        result["config"] = {
            key: config.get(key) for key in ("web_search", "memories", "project_doc_max_bytes")
        }
        skills = c.call("skills/list", {"cwds": [str(workspace)], "forceReload": True})
        assert "m0-local-marker" in json.dumps(skills)
        result["local_skill_discovered"] = True
        params = {
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
        thread = response["thread"]
        tid = thread["id"]
        assert thread["cwd"] == str(workspace) and thread.get("projectId") is None
        assert response["approvalsReviewer"] == "auto_review"
        result["start"] = {
            key: response.get(key)
            for key in ("cwd", "approvalPolicy", "approvalsReviewer", "sandbox")
        }
        result["session_source"] = thread.get("source")
        result["instruction_source_kinds"] = response.get("instructionSources")
        c.call("thread/unsubscribe", {"threadId": tid})
        bootstrap = {
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
            raise AssertionError("Expected pinned-runtime empty-thread limitation changed")
        # The failed cold resume removed the empty in-memory thread; start afresh.
        response = c.call("thread/start", params)
        tid = response["thread"]["id"]
        inventory = c.call("mcpServerStatus/list", {"threadId": tid})
        assert "m0_marker_first" in json.dumps(inventory)
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
            c.call("turn/interrupt", {"threadId": tid, "turnId": turn["turn"]["id"]})
        except RuntimeError as error:
            if "no active turn to interrupt" not in str(error):
                raise
        result["persistence_turn"] = (
            "Unavailable local provider; interrupted or already terminated."
        )
        read = c.call("thread/read", {"threadId": tid})["thread"]
        assert read["cwd"] == str(workspace)
        unassigned = c.call("thread/list", {"projectId": None, "modelProviders": []})["data"]
        assert tid in [t["id"] for t in unassigned], {"thread": read, "listed": unassigned}
        result["unassigned_list_and_read"] = True
        result["unsubscribe"] = c.call("thread/unsubscribe", {"threadId": tid})["status"]
        resume = {
            **params,
            "threadId": tid,
            "excludeTurns": True,
            "approvalsReviewer": "user",
            "developerInstructions": "M0 changed instruction marker.",
        }
        for key in ("projectId", "ephemeral", "historyMode"):
            resume.pop(key, None)
        resume["config"] = {**params["config"], "mcp_servers": {"m0_fixture": second_mcp.config()}}
        response = c.call("thread/resume", resume)
        assert response["approvalsReviewer"] == "user", response
        result["idle_unsubscribe_resume_reviewer_changed"] = True
        inventory = c.call("mcpServerStatus/list", {"threadId": tid})
        assert "m0_marker_second" in json.dumps(inventory) and "m0_marker_first" not in json.dumps(
            inventory
        )
        result["idle_resume_mcp_url_and_auth_replaced"] = True
        pages = c.call("thread/turns/list", {"threadId": tid, "limit": 10})
        result["history_page_read"] = bool(pages.get("data"))
        result["goal_read"] = c.call("thread/goal/get", {"threadId": tid})
    finally:
        c.close()
    c = Client(root, memory=False)
    try:
        read = c.call("thread/read", {"threadId": tid})["thread"]
        assert read["cwd"] == str(workspace)
        assert c.call("project/read", {"projectId": p["id"]})["project"]["id"] == p["id"]
        result["restart_resolves_workspace_and_project"] = True
        response = c.call("thread/resume", resume)
        assert response["thread"]["id"] == tid
        config = c.call("config/read", {"includeLayers": False})["config"]
        result["memory_off_config"] = config.get("memories")
        result["restart_resume"] = True
    finally:
        c.close()
    (root / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    first = McpFixture("m0_marker_first")
    second = McpFixture("m0_marker_second")
    try:
        run(args.output.resolve(), first, second)
    finally:
        first.close()
        second.close()
