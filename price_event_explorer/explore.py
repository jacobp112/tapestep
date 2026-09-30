"""Run the Python CLI directly from the source checkout, without installation."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "python"))

from price_event_explorer.cli import main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
