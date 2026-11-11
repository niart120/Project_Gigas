"""Immutable observations encoded from capture frames without geometric changes."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Self
from uuid import uuid4

import cv2
import numpy as np

if TYPE_CHECKING:
    from gigas.game_io.capture.models import Frame

MAX_PNG_BYTES = 32 * 1024 * 1024


class ObservationError(ValueError):
    """An observation failed with a stable reason code and a displayable message."""

    def __init__(
        self,
        code: Literal["DEVICE_UNAVAILABLE", "BUSY", "OBSERVATION_ENCODING_ERROR"],
        message: str,
    ) -> None:
        """Attach the reason code without translating it into UI or MCP types."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Observation:
    """One immutable image and its persistent identifier.

    Attributes:
        observation_id: UUID4 identifier prefixed with obs-.
        frame: Original source frame, including its host acquisition time.
        png_bytes: Original-size PNG; creation alone does not imply persistence.
    """

    observation_id: str
    frame: "Frame"
    png_bytes: bytes

    @property
    def image_path(self) -> str:
        """Return the project-relative PNG path using forward slashes."""
        return f"observations/{self.observation_id}.png"

    @classmethod
    def from_frame(cls, frame: "Frame") -> Self:
        """Encode a frame once without resizing, cropping, or changing its time.

        Args:
            frame: BGR image with width 16..3840 and height 16..2160.

        Returns:
            A new observation ID and at most 32 MiB of PNG bytes, not yet saved.

        Raises:
            ObservationError: Dimensions, encoding, or encoded size are invalid.
        """
        if not (16 <= frame.width <= 3840 and 16 <= frame.height <= 2160):
            message = "観測画像の寸法が対応範囲外です"
            raise ObservationError("OBSERVATION_ENCODING_ERROR", message)
        pixels = np.frombuffer(frame.bgr, dtype=np.uint8).reshape(frame.height, frame.width, 3)
        try:
            success, encoded = cv2.imencode(".png", pixels)
        except cv2.error as error:
            message = f"観測画像のPNG符号化に失敗しました: {error}"
            raise ObservationError("OBSERVATION_ENCODING_ERROR", message) from error
        if not success or encoded is None or not 0 < encoded.nbytes <= MAX_PNG_BYTES:
            message = "観測画像を32 MiB以内のPNGに符号化できません"
            raise ObservationError("OBSERVATION_ENCODING_ERROR", message)
        return cls(f"obs-{uuid4()}", frame, encoded.tobytes())
