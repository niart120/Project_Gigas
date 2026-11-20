import time
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from gigas.runtime.codex.management import ThreadInfo, ThreadPage
from gigas.runtime.codex.models import ConnectionOptions
from gigas.services.conversations import ArchiveResult, NewConversationTarget
from tests.integration.peers.archives import ArchivePeer
from tests.integration.test_codex_service import _project_responses
from tests.integration.test_conversation_selection import _RequestGate, _saved
from tests.integration.test_conversation_switching import _session
from tests.integration.test_project_management import ProjectPeer
from tests.unit.test_codex_connection import FakePeer


@pytest.mark.parametrize("create", [False, True])
def test_pending_project_picker_does_not_replace_selection_during_archive(
    qtbot: QtBot, tmp_path: Path, *, create: bool
) -> None:
    peer = FakePeer()
    path = tmp_path / "project"
    if not create:
        path.mkdir()
    _project_responses(peer, path)
    _saved(peer, path, "saved")
    peer.responses["thread/archive"] = {}
    picker = _RequestGate()
    archive = _RequestGate()
    peer.gates["project/create" if create else "project/list"] = picker
    peer.gates["thread/archive"] = archive
    with _session(qtbot, tmp_path, peer) as session:
        projects = []
        service = session.service
        service.project_completed.connect(projects.append)
        selection = service.selected_target
        try:
            (service.create_project if create else service.open_project)(path)
            qtbot.waitUntil(picker.entered.is_set)
            service.archive_threads("project-1", ("saved",))
            qtbot.waitUntil(archive.entered.is_set)
            service._enqueue(picker.set)
            qtbot.waitUntil(lambda: bool(projects))
            assert projects[0].error
            assert service.selected_target is selection
        finally:
            service._enqueue(picker.set)
            service._enqueue(archive.set)
        qtbot.waitUntil(lambda: any(r.operation == "archive" for r in session.management))
        assert not next(r for r in session.management if r.operation == "archive").error


def test_partial_bulk_archiving_reconciles_lost_reply_and_clears_only_archived_selection(
    qtbot: QtBot, tmp_path: Path
) -> None:
    peer = ArchivePeer(tmp_path)
    peer.reject_archive = "thread-2"
    peer.lose_archive_response = "thread-1"
    with _session(qtbot, tmp_path, peer) as session:
        assert not session.manage(
            "select", lambda: session.service.select_conversation("thread-1")
        ).error
        result = session.manage(
            "archive",
            lambda: session.service.archive_threads("project-1", tuple(peer.members)),
        )
        assert result.error
        assert isinstance(result.value, ArchiveResult)
        assert [row.id for row in result.value.threads] == ["thread-0", "thread-1"]
        assert (
            result.value.new_target
            == session.service.selected_target
            == NewConversationTarget("project-1")
        )
        assert peer.archived == {"thread-0", "thread-1"}
        page = session.manage("archived", session.service.list_archived_threads).value
        assert isinstance(page, ThreadPage)
        assert {row.id for row in page.threads} == peer.archived
        restored = session.manage("restore", lambda: session.service.restore_thread("thread-1"))
        assert not restored.error
        assert isinstance(restored.value, ThreadInfo)
        assert restored.value.id == "thread-1"
        assert session.service.selected_target == NewConversationTarget("project-1")
        peer.reject_archive = None
        retained = session.manage("auto_archive", lambda: session.service.auto_archive_threads(1))
        assert not retained.error
        assert "thread-1" not in peer.archived
        assert not any(m.startswith("turn/") or m == "thread/resume" for m, _ in peer.calls)


@pytest.mark.parametrize("rejected", ["thread/unarchive", "thread/resume"])
def test_restore_keeps_connection_only_for_definite_initial_rejection(
    qtbot: QtBot, tmp_path: Path, rejected: str
) -> None:
    peer = ProjectPeer(tmp_path)
    peer.archived.add("thread-1")
    peer.members["thread-1"]["cwd"] = str(tmp_path / "old")
    peer.rpc_errors[rejected] = -32600
    with _session(qtbot, tmp_path, peer) as session:
        result = session.manage("restore", lambda: session.service.restore_thread("thread-1"))
        assert result.error
        if rejected == "thread/unarchive":
            assert not peer.closed
            assert "thread-1" in peer.archived
            peer.rpc_errors.clear()
            assert not session.manage(
                "restore", lambda: session.service.restore_thread("thread-1")
            ).error
        else:
            assert peer.closed
            assert "thread-1" not in peer.archived


def test_connection_time_scan_uses_read_status_and_rechecks_targets_before_writing(
    qtbot: QtBot, tmp_path: Path
) -> None:
    peer = ArchivePeer(tmp_path)
    peer.members["thread-1"]["projectId"] = None
    peer.read["thread-0"] = None
    peer.read["thread-2"] = {"firstUnread": {"turnId": "turn"}, "revision": "unread"}
    peer.members["thread-3"]["status"] = {"type": "active"}
    peer.recent_on_read = "thread-4"
    peer.members["thread-6"]["updatedAt"] = int(time.time())
    with _session(qtbot, tmp_path, peer) as session:
        assert not session.manage(
            "select", lambda: session.service.select_conversation("thread-5")
        ).error
        selection = session.service.selected_target
        peer.calls.clear()
        assert not session.manage(
            "auto_archive", lambda: session.service.auto_archive_threads(0)
        ).error
        assert peer.calls == []
        result = session.manage("auto_archive", lambda: session.service.auto_archive_threads(1))
        assert not result.error
        assert isinstance(result.value, ArchiveResult)
        assert [(row.id, row.project_id) for row in result.value.threads] == [("thread-1", None)]
        assert result.value.new_target is None
        assert session.service.selected_target is selection
        assert peer.archived == {"thread-1"}
        assert not any(
            m in {"thread/items/list", "thread/goal/get", "thread/resume"} for m, _ in peer.calls
        )
        assert all("projectId" not in (p or {}) for m, p in peer.calls if m == "thread/list")


def test_viewing_unread_history_acknowledges_only_the_observed_revision(
    qtbot: QtBot, tmp_path: Path
) -> None:
    peer = ArchivePeer(tmp_path)
    peer.read["thread-4"] = {"firstUnread": {"turnId": "turn"}, "revision": "viewed-revision"}
    with _session(qtbot, tmp_path, peer) as session:
        assert not session.manage(
            "select", lambda: session.service.select_conversation("thread-4")
        ).error
        qtbot.waitUntil(lambda: any(m == "thread/readState/update" for m, _ in peer.calls))
        assert [p for m, p in peer.calls if m == "thread/readState/update"] == [
            {
                "threadId": "thread-4",
                "expectedRevision": "viewed-revision",
                "operation": {"type": "read"},
            }
        ]


def test_reconnect_reconciles_unknown_selected_archive_without_replaying_it(
    qtbot: QtBot, tmp_path: Path
) -> None:
    peer = ArchivePeer(tmp_path)
    peer.lose_archive_response = "thread-1"
    peer.fail_reconciliation = True
    with _session(qtbot, tmp_path, peer) as session:
        assert not session.manage(
            "select", lambda: session.service.select_conversation("thread-1")
        ).error
        lost = session.manage(
            "archive", lambda: session.service.archive_threads("project-1", ("thread-1",))
        )
        assert lost.error
        assert peer.closed
        assert peer.archived == {"thread-1"}
        fresh_peer = ArchivePeer(tmp_path)
        fresh_peer.archived.update(peer.archived)
        session.peer = fresh_peer
        assert not session.command(
            "connect",
            lambda: session.service.connect_runtime(ConnectionOptions(tmp_path / "runtime")),
        ).error
        repair = next(item for item in session.management if item.operation == "archive_reconciled")
        assert isinstance(repair.value, ArchiveResult)
        assert (
            repair.value.new_target
            == session.service.selected_target
            == NewConversationTarget("project-1")
        )
        assert not any(m == "thread/archive" for m, _p in fresh_peer.calls)
