"""A scripted resume peer with separate request and notification timing."""

from gigas.runtime.codex.transport import JsonObject, JsonValue, RpcNotification
from tests.unit.test_codex_connection import FakePeer


def execution_resume_result(params: JsonObject, thread: JsonObject) -> JsonObject:
    """Confirm execution settings for the resumed thread."""
    config = params["config"]
    assert isinstance(config, dict)
    return {
        "thread": thread,
        "model": params["model"],
        "reasoningEffort": config["model_reasoning_effort"],
        "cwd": params["cwd"],
        "runtimeWorkspaceRoots": params["runtimeWorkspaceRoots"],
        "approvalPolicy": params["approvalPolicy"],
        "approvalsReviewer": params["approvalsReviewer"],
        "sandbox": {
            "type": "workspaceWrite",
            "writableRoots": [],
            "networkAccess": False,
            "excludeTmpdirEnvVar": True,
            "excludeSlashTmp": True,
        },
    }


class ResumePeer(FakePeer):
    """Return current execution settings and issue public Goal/turn notifications."""

    def __init__(self, project_id: str | None = None) -> None:
        """Keep the saved assignment and independently configurable notification behavior."""
        super().__init__()
        self.project_id = project_id
        self.auto_turn_started = False
        self.auto_goal = True
        self.auto_interrupt = True

    async def request(self, method: str, params: JsonObject | None) -> JsonValue:
        """Use the shared gates and failures before returning the requested public reply."""
        result = await super().request(method, params)
        if method == "thread/resume" and method not in self.responses:
            assert params is not None
            return execution_resume_result(
                params, {"id": params["threadId"], "projectId": self.project_id}
            )
        if method == "turn/start":
            assert params is not None
            self.events.put_nowait(
                RpcNotification(
                    "turn/started",
                    {
                        "threadId": params["threadId"],
                        "turn": {"id": "turn-1", "status": "inProgress"},
                    },
                )
            )
        if method == "turn/interrupt" and self.auto_interrupt:
            assert params is not None
            self.events.put_nowait(
                RpcNotification(
                    "turn/completed",
                    {
                        "threadId": params["threadId"],
                        "turn": {"id": params["turnId"], "status": "interrupted"},
                    },
                )
            )
        if method == "thread/goal/set" and self.auto_goal:
            assert params is not None
            self.events.put_nowait(
                RpcNotification(
                    "thread/goal/updated",
                    {
                        "threadId": params["threadId"],
                        "goal": self.goal,
                    },
                )
            )
        if method == "thread/goal/clear" and self.auto_goal:
            assert params is not None
            self.events.put_nowait(
                RpcNotification(
                    "thread/goal/cleared",
                    {
                        "threadId": params["threadId"],
                    },
                )
            )
        return result
