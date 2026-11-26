"""Host Web process entry and compatibility exports for Flask integrations."""

if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.launch_host import main

__all__ = ["app", "run_server", "parse_arguments", "initialize_system", "main", "DEFAULT_PORT"]


def __getattr__(name):
    if name in __all__ and name != "main":
        from server import app_legacy

        value = getattr(app_legacy, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    raise SystemExit(main())
