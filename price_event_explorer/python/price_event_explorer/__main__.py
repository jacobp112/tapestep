import sys

from .cli import main

# Canonical JSONL is byte-identical across platforms, including Windows.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")

try:
    raise SystemExit(main())
except BrokenPipeError:
    raise SystemExit(0)
