"""Backward-compatible Web command and Flask imports."""

from server.app import __all__, __getattr__, main


if __name__ == "__main__":
    raise SystemExit(main())
