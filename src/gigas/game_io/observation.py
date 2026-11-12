"""Immutable agent images encoded from the latest capture frame."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Self
from uuid import uuid4

import cv2
import numpy as np

if TYPE_CHECKING:
    from datetime import datetime

    from gigas.game_io.capture.models import Frame

MAX_PNG_BYTES = 32 * 1024 * 1024

type ObservationResolution = Literal["original", "full_hd", "hd"]


class ObservationError(ValueError):
    """An observation failed with a stable reason code and a displayable message."""

    def __init__(
        self,
        code: Literal[
            "DEVICE_UNAVAILABLE", "BUSY", "OBSERVATION_ENCODING_ERROR", "OBSERVATION_TIMEOUT"
        ],
        message: str,
    ) -> None:
        """Attach the reason code without translating it into UI or MCP types."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class FrameSource:
    """Source identifiers and dimensions without retaining captured pixels.

    Attributes:
        generation: Capture generation within the capture service lifetime.
        sequence: Source frame sequence within that generation.
        width: Source width in pixels, before observation resizing.
        height: Source height in pixels, before observation resizing.
    """

    generation: int
    sequence: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class Observation:
    """One encoded agent image and its source metadata, not yet necessarily saved.

    Attributes:
        observation_id: UUID4 identifier prefixed with obs-.
        captured_at: Original host acquisition time, not encoding or saving time.
        source_frame: Source identifiers and dimensions without source pixels.
        png_bytes: Final PNG bytes shared by storage and MCP image encoding.
        width: Encoded image width in pixels.
        height: Encoded image height in pixels.
    """

    observation_id: str
    captured_at: "datetime"
    source_frame: FrameSource
    png_bytes: bytes
    width: int
    height: int

    @property
    def mime_type(self) -> Literal["image/png"]:
        """Return the format of the final image bytes."""
        return "image/png"

    @property
    def image_path(self) -> str:
        """Return the project-relative PNG path using forward slashes."""
        return f"observations/{self.observation_id}.png"

    @classmethod
    def from_frame(cls, frame: "Frame", *, resolution: ObservationResolution = "full_hd") -> Self:
        """Encode the whole frame once, optionally shrinking to the selected bounds.

        Args:
            frame: BGR image with width 16..3840 and height 16..2160.
            resolution: Original size, FullHD (1920x1080), or HD (1280x720).
                FullHD is the default. Aspect ratio is preserved within integer
                rounding; smaller frames are never enlarged.

        Returns:
            An observation with at most 32 MiB of PNG bytes and source metadata.
            The source pixels are not retained and no file is written.

        Raises:
            ObservationError: Dimensions, resolution, resizing, or encoding are invalid.
        """
        if not (16 <= frame.width <= 3840 and 16 <= frame.height <= 2160):
            message = "観測画像の寸法が対応範囲外です"
            raise ObservationError("OBSERVATION_ENCODING_ERROR", message)
        width, height = frame.width, frame.height
        if resolution == "original":
            bounds = (width, height)
        elif resolution == "full_hd":
            bounds = (1920, 1080)
        elif resolution == "hd":
            bounds = (1280, 720)
        else:
            message = "観測解像度が対応範囲外です"
            raise ObservationError("OBSERVATION_ENCODING_ERROR", message)
        if width > bounds[0] or height > bounds[1]:
            if width * bounds[1] >= height * bounds[0]:
                height = max(1, height * bounds[0] // width)
                width = bounds[0]
            else:
                width = max(1, width * bounds[1] // height)
                height = bounds[1]
        pixels = np.frombuffer(frame.bgr, dtype=np.uint8).reshape(frame.height, frame.width, 3)
        try:
            if (width, height) != (frame.width, frame.height):
                pixels = cv2.resize(pixels, (width, height), interpolation=cv2.INTER_AREA)
            success, encoded = cv2.imencode(".png", pixels)
        except cv2.error as error:
            message = f"観測画像の加工・PNG符号化に失敗しました: {error}"
            raise ObservationError("OBSERVATION_ENCODING_ERROR", message) from error
        if not success or encoded is None or not 0 < encoded.nbytes <= MAX_PNG_BYTES:
            message = "観測画像を32 MiB以内のPNGに符号化できません"
            raise ObservationError("OBSERVATION_ENCODING_ERROR", message)
        return cls(
            f"obs-{uuid4()}",
            frame.captured_at,
            FrameSource(frame.generation, frame.sequence, frame.width, frame.height),
            encoded.tobytes(),
            width,
            height,
        )
