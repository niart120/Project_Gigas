"""New Nintendo 3DS XL device access inside the capture worker."""

from contextlib import contextmanager
from typing import TYPE_CHECKING

from gigas.game_io.capture.models import CaptureError

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from ponkan import CaptureReader


def detect_ponkan() -> tuple[bool, str]:
    """Return whether one board is selectable and any descriptor lookup diagnostic."""
    try:
        import ponkan  # noqa: PLC0415 -- native discovery runs only in the device worker

        discovery = ponkan.list_capture_devices(profile="n3dsxl", backend="auto")
    except (ImportError, OSError, SystemExit):
        # PyD3XX 1.2 exits during import if its native DLL cannot be loaded.
        return False, "3DS 映像の列挙に失敗しました。D3XX の導入状態を確認してください"
    except Exception as exc:  # noqa: BLE001 -- isolate USB errors from the camera catalog
        return False, f"3DS 映像の列挙に失敗しました: {exc}"
    if discovery.backend_status != "available":
        return (
            False,
            f"3DS ドライバーを利用できません。D3XX を確認してください ({discovery.reason})",
        )
    if len(discovery.devices) > 1:
        return False, "対応する 3DS 基板を複数検出しました。対象の1台だけを接続してください"
    return bool(discovery.devices), ""


@contextmanager
def open_ponkan(
    stopped: "Callable[[], bool]",
    released: "Callable[[], None] | None",
    detected_board: "Callable[[bool], None] | None" = None,
) -> "Generator[CaptureReader | None, None, None]":
    """Check the number of supported boards and own the selected reader until exit."""
    detected, warning = detect_ponkan()
    if detected_board is not None:
        detected_board(detected)
    if not detected:
        message = warning or "対応する 3DS 基板が見つかりません。USB 接続と D3XX を確認してください"
        raise CaptureError(message)
    if stopped():
        yield None
        return

    import ponkan  # noqa: PLC0415 -- load only for an explicit connection
    from ponkan.errors import CaptureError as PonkanError  # noqa: PLC0415

    try:
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
