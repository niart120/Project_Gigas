"""Exercise active-run protection with actual independently spawned processes."""

import multiprocessing
from pathlib import Path

from gigas.storage.diagnostics import _lock, clean_old_runs
from tests.integration.peers.diagnostic_writer import hold_session


def test_concurrent_processes_keep_separate_files_and_protect_active_runs(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    processes = []
    channels = []
    paths = []
    try:
        for _ in range(2):
            parent, child = context.Pipe()
            process = context.Process(target=hold_session, args=(str(tmp_path), child))
            process.start()
            child.close()
            processes.append(process)
            channels.append(parent)
            assert parent.poll(10), "diagnostic process did not start"
            paths.append(Path(parent.recv()))
        assert paths[0] != paths[1]
        root = tmp_path / "logs"
        old_runs = []
        for number in range(12):
            run = root / f"20990101T0000{number:02d}Z_123"
            run.mkdir()
            (run / ".lock").touch()
            (run / "gigas.jsonl").write_text("{}\n")
            old_runs.append(run)
        with _lock(root / ".cleanup.lock"):
            clean_old_runs(root)
            assert all(run.exists() for run in old_runs)
        clean_old_runs(root)
        assert all(path.exists() for path in paths)
        assert sum(run.exists() for run in old_runs) == 10
        channels[0].send("stop")
        processes[0].join(10)
        assert processes[0].exitcode == 0
        assert not paths[0].parent.exists()
        assert paths[1].exists()
        # An abnormal exit releases the OS lock even though .lock remains on disk.
        processes[1].terminate()
        processes[1].join(10)
        clean_old_runs(root)
        assert not paths[1].parent.exists()
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(10)
            process.close()
        for channel in channels:
            channel.close()
