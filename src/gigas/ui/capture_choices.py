"""Consistent display names for capture identities in settings and menus."""

from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gigas.game_io.capture.models import DeviceInfo


def capture_device_choices(devices: "tuple[DeviceInfo, ...]") -> tuple[tuple[str, str], ...]:
    """Return display-name/path pairs, numbering only duplicate device names.

    Args:
        devices: Current catalog in enumeration order; entries without a path are omitted.

    Returns:
        Plain labels and unchanged device paths, without selecting or saving a device.
    """
    valid = tuple(device for device in devices if device.path)
    counts = Counter(device.name for device in valid)
    seen: Counter[str] = Counter()
    choices: list[tuple[str, str]] = []
    for device in valid:
        seen[device.name] += 1
        label = f"{device.name} ({seen[device.name]})" if counts[device.name] > 1 else device.name
        choices.append((label, device.path))
    return tuple(choices)
