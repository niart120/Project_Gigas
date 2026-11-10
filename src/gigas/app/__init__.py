"""Application composition and lifetime management."""


def main() -> int:
    """Run the desktop application and return its process exit code.

    Qt is imported only when this entry point is called.

    Returns:
        Zero after normal application shutdown.
    """
    from gigas.app.bootstrap import run

    return run()
