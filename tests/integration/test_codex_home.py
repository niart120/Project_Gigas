import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pytestqt.qtbot import QtBot

from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.runtime.codex import launch
from gigas.runtime.codex.models import ConnectionOptions
from gigas.services.codex import CodexServiceThread
from tests.unit.test_codex_connection import FakePeer

if TYPE_CHECKING:
    from gigas.services.codex import ServiceResult


def test_default_service_uses_app_home_without_touching_development_home(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    development_home = tmp_path / "development"
    development_home.mkdir()
    credential = development_home / "auth.json"
    credential.write_text("development-only", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(development_home))
    data_root = tmp_path / "GIGAS"
    launches: list[dict[str, str]] = []
    peers: list[FakePeer] = []

    def transport(
        _command: list[str], cwd: Path, env: dict[str, str], *, unset_env: tuple[str, ...] = ()
    ) -> FakePeer:
        assert cwd == data_root
        assert "CODEX_WINDOWS_REGISTERED_CORE" in unset_env
        assert Path(env["CODEX_HOME"]).is_dir()
        launches.append(env)
        peer = FakePeer()
        peers.append(peer)
        return peer

    monkeypatch.setattr(launch, "StdioTransport", transport)
    service = CodexServiceThread(data_root, CaptureReader(CaptureSnapshot))
    results: list[ServiceResult] = []
    service.completed.connect(results.append)
    service.start()
    try:
        assert not launches
        assert not data_root.exists()
        for index in range(2):
            service.connect_runtime(ConnectionOptions(data_root))
            qtbot.waitUntil(
                lambda count=index + 1: (
                    sum(result.operation == "connect" for result in results) == count
                )
            )
            assert launches[-1]["CODEX_HOME"] == str(data_root / "codex_home")
            service.disconnect_runtime()
            qtbot.waitUntil(
                lambda count=index + 1: (
                    sum(result.operation == "disconnect" for result in results) == count
                )
            )
        assert all(not result.error for result in results)
        assert all(peer.closed for peer in peers)
        assert os.environ["CODEX_HOME"] == str(development_home)
        assert credential.read_text(encoding="utf-8") == "development-only"
        assert not (data_root / "codex_home" / "auth.json").exists()
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))
