"""New Nintendo 3DS XL device access inside the capture worker."""

from contextlib import contextmanager
from typing import TYPE_CHECKING

from gigas.game_io.capture.models import CaptureError

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from ponkan import CaptureReader


@contextmanager
def open_ponkan(
    stopped: "Callable[[], bool]", released: "Callable[[], None] | None"
) -> "Generator[CaptureReader | None, None, None]":
    """Check the number of supported boards and own the selected reader until exit."""
    import ponkan  # noqa: PLC0415 -- load only for an explicit connection
    from ponkan.errors import CaptureError as PonkanError  # noqa: PLC0415

    try:
        try:
            discovery = ponkan.list_capture_devices(profile="n3dsxl", backend="auto")
        except (OSError, SystemExit) as exc:
            # PyD3XX 1.2 exits during import if its native DLL cannot be loaded.
            message = "3DS 映像の列挙に失敗しました。D3XX の導入状態を確認してください"
            raise CaptureError(message) from exc
        if discovery.backend_status != "available":
            message = (
                f"3DS ドライバーを利用できません。D3XX を確認してください ({discovery.reason})"
            )
            raise CaptureError(message)
        if not discovery.devices:
            message = "対応する 3DS 基板が見つかりません。USB 接続と D3XX を確認してください"
            raise CaptureError(message)
        if len(discovery.devices) != 1:
            message = "対応する 3DS 基板を複数検出しました。対象の1台だけを接続してください"
            raise CaptureError(message)
        if stopped():
            yield None
            return
        reader = ponkan.open_capture(
            backend="auto",
            output=ponkan.CaptureOutput.BOTH_VERTICAL,
            colorspace="BGR",
            read_timeout=0.1,
        )
        try:
            yield reader
        finally:
            reader.close()
            if released is not None:
                released()
    except PonkanError as exc:
        message = (
            f"3DS 映像取得に失敗しました ({exc.code}): {exc}。接続を確認して再試行してください"
        )
        raise CaptureError(message) from exc
