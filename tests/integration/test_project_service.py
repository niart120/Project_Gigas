import json
from dataclasses import replace
from pathlib import Path

from pytestqt.qtbot import QtBot

from gigas.runtime.codex.models import ConnectionOptions
from gigas.services.conversations import StoredConversationTarget
from gigas.services.projects import ProjectDeleted, ProjectDetails
from tests.integration.test_conversation_selection import _RequestGate
from tests.integration.test_conversation_switching import _session
from tests.integration.test_project_management import ProjectPeer


def test_project_service_updates_selected_identity_without_replacing_history(
    qtbot: QtBot, tmp_path: Path
) -> None:
    peer = ProjectPeer(tmp_path)
    root = tmp_path / "new"
    root.mkdir()
    with _session(qtbot, tmp_path, peer) as session:
        assert not session.manage(
            "select", lambda: session.service.select_conversation("thread-0")
        ).error
        result = session.manage(
            "project_update",
            lambda: session.service.update_project(peer.project, name="Renamed", root=root),
        )
        assert not result.error
        assert isinstance(result.value, ProjectDetails)
        assert session.service.selected_target == StoredConversationTarget(
            "thread-0", root, "project-1"
        )
        assert not any(method.startswith("turn/") for method, _ in peer.calls)


def test_project_service_deletes_selected_assignment_but_keeps_thread(
    qtbot: QtBot, tmp_path: Path
) -> None:
    peer = ProjectPeer(tmp_path / "missing")
    with _session(qtbot, tmp_path, peer) as session:
        assert not session.manage(
            "select", lambda: session.service.select_conversation("thread-0")
        ).error
        result = session.manage(
            "project_delete", lambda: session.service.delete_project(peer.project)
        )
        assert not result.error
        assert isinstance(result.value, ProjectDeleted)
        assert session.service.selected_target == StoredConversationTarget(
            "thread-0", peer.project.root, None
        )
        assert not any(method == "thread/resume" for method, _ in peer.calls)


def test_delete_refreshes_saved_workspace_after_an_external_member_update(
    qtbot: QtBot, tmp_path: Path
) -> None:
    peer = ProjectPeer(tmp_path)
    root = tmp_path / "moved"
    root.mkdir()
    with _session(qtbot, tmp_path, peer) as session:
        assert not session.manage(
            "select", lambda: session.service.select_conversation("thread-0")
        ).error
        peer.members["thread-0"]["cwd"] = str(root)
        result = session.manage(
            "project_delete", lambda: session.service.delete_project(peer.project)
        )
        assert not result.error
        assert session.service.selected_target == StoredConversationTarget("thread-0", root, None)


def test_send_after_project_move_resumes_same_thread_and_records_at_new_root(
    qtbot: QtBot, tmp_path: Path
) -> None:
    peer = ProjectPeer(tmp_path)
    root = tmp_path / "moved"
    root.mkdir()
    with _session(qtbot, tmp_path, peer) as session:
        assert not session.manage(
            "select", lambda: session.service.select_conversation("thread-0")
        ).error
        peer.project = replace(peer.project, root=root)
        for member in peer.members.values():
            member["cwd"] = str(root)
        result = session.command("send", lambda: session.service.send("Continue"))
        assert not result.error
        assert result.message is not None
        assert result.message.outcome == "accepted"
        assert not (tmp_path / "records").exists()
        resumed = [params for method, params in peer.calls if method == "thread/resume"]
        assert len(resumed) == 1
        params = resumed[0]
        assert params is not None
        assert (params["threadId"], params["cwd"], params["runtimeWorkspaceRoots"]) == (
            "thread-0",
            str(root),
            [str(root)],
        )
        turns = [p for method, p in peer.calls if method == "turn/start"]
        assert len(turns) == 1
        assert turns[0] is not None
        assert turns[0]["threadId"] == "thread-0"
    log = root / "records" / "thread-0" / "events.jsonl"
    rows = [json.loads(line) for line in log.read_bytes().splitlines()]
    assert [row["payload"]["text"] for row in rows if row["type"] == "dialogue.submitted"] == [
        "Continue"
    ]


def test_unfinished_project_cannot_start_a_new_thread_after_reconnect(
    qtbot: QtBot, tmp_path: Path
) -> None:
    root = tmp_path / "new"
    root.mkdir()
    peer = ProjectPeer(root)
    peer.members["thread-0"]["cwd"] = str(tmp_path)
    with _session(qtbot, tmp_path, peer) as session:
        assert not session.manage(
            "new", lambda: session.service.new_conversation("project-1")
        ).error
        result = session.command("send", lambda: session.service.send("Keep playing"))
        assert result.error
        assert not any(method in {"thread/start", "turn/start"} for method, _ in peer.calls)


def test_late_mutation_failure_cannot_close_a_new_connection(qtbot: QtBot, tmp_path: Path) -> None:
    peer = ProjectPeer(tmp_path)
    root = tmp_path / "new"
    root.mkdir()
    gate = _RequestGate()
    peer.gates["thread/resume"] = gate
    with _session(qtbot, tmp_path, peer) as session:
        session.service.update_project(peer.project, name="Renamed", root=root)
        qtbot.waitUntil(gate.entered.is_set)
        assert not session.command("disconnect", session.service.disconnect_runtime).error
        next_peer = ProjectPeer(root)
        session.peer = next_peer
        assert not session.command(
            "connect",
            lambda: session.service.connect_runtime(ConnectionOptions(tmp_path / "runtime")),
        ).error
        session.service._enqueue(gate.set)
        qtbot.waitUntil(lambda: any(r.operation == "project_update" for r in session.management))
        assert not next_peer.closed
        assert session.service.codex._connection is not None


def test_stop_cancels_project_update_and_closes_uncertain_runtime(
    qtbot: QtBot, tmp_path: Path
) -> None:
    peer = ProjectPeer(tmp_path)
    root = tmp_path / "new"
    root.mkdir()
    gate = _RequestGate()
    peer.gates["thread/resume"] = gate
    with _session(qtbot, tmp_path, peer) as session:
        session.service.update_project(peer.project, name="Renamed", root=root)
        qtbot.waitUntil(gate.entered.is_set)
        assert not session.command("stop", session.service.interrupt).error
        qtbot.waitUntil(lambda: any(r.operation == "project_update" for r in session.management))
        assert next(r for r in session.management if r.operation == "project_update").error
        assert peer.closed
        assert peer.project.root == root
        assert any(member["cwd"] != str(root) for member in peer.members.values())
        assert not any(method.startswith("turn/") for method, _ in peer.calls)
