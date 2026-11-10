"""Application composition and lifetime management."""

import sys


def main() -> int:
    """Run the desktop application and return its process exit code.

    Qt is imported only when this entry point is called.

    Returns:
        Zero after normal shutdown, or one after a handled startup failure.
    """
    try:
        from gigas.app.bootstrap import run  # noqa: PLC0415 -- keep imports free of Qt initialization

        return run()
    except Exception as error:  # noqa: BLE001 -- process entry boundary
        sys.stderr.write(f"GIGAS: {type(error).__name__}: {error}\n")
        return 1
