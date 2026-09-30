"""Compare real CLI bytes against goldens and each other; never rewrite goldens."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from generate import generate

ROOT = Path(__file__).resolve().parent
PYTHON = [sys.executable, "-m", "price_event_explorer"]


def invoke(command, path, flags=(), data=None):
    return subprocess.run([*command, str(path), *flags], cwd=ROOT / "python", input=data, capture_output=True)


def compare(path, rust, golden=None):
    py = invoke(PYTHON, path, ["--all", "--json"])
    rs = invoke(rust, path, ["--all", "--json"])
    for label, result in (("Python", py), ("Rust", rs)):
        if result.returncode != 0 or result.stderr:
            raise AssertionError(f"{label} failed for {path.name}: {result.stderr.decode(errors='replace')}")
    if py.stdout != rs.stdout:
        raise AssertionError(f"Python/Rust snapshot bytes differ: {path.name}")
    if golden is not None and py.stdout != golden:
        raise AssertionError(f"snapshot bytes differ from golden: {path.name}")
    return len(py.stdout.splitlines())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rust-bin", type=Path, help="use this built executable; otherwise build with Cargo")
    args = parser.parse_args()
    if args.rust_bin:
        binary = args.rust_bin.resolve()
    else:
        if not shutil.which("cargo"):
            print("Cannot run Rust or cross-language checks: cargo is unavailable.", file=sys.stderr)
            return 1
        subprocess.run(["cargo", "build", "--locked", "--manifest-path", str(ROOT / "rust/Cargo.toml"),
                        "--target-dir", str(ROOT / "rust/target")], check=True)
        binary = ROOT / "rust/target/debug" / ("tapestep.exe" if os.name == "nt" else "tapestep")
    rust = [str(binary)]
    total = 0
    for path in sorted((ROOT / "examples").glob("*.jsonl")):
        count = compare(path, rust, (ROOT / "golden" / path.name).read_bytes())
        total += count
        print(f"PASS golden {path.name}: {count} identical snapshots")
    with tempfile.TemporaryDirectory(prefix="tapestep-verify-") as directory:
        path = Path(directory) / "events.jsonl"
        for seed in (0, 1, 42, 2026, 9223372036854775807):
            data = "".join(json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n" for e in generate(seed, 1000))
            path.write_text(data, encoding="utf-8", newline="\n")
            count = compare(path, rust)
            total += count
            print(f"PASS generated seed={seed}: {count} identical snapshots")
        valid_edges = [
            ("empty", b""),
            ("signed timestamps and ties", b'{"seq":1,"timestamp_ns":-9223372036854775808,"type":"reset"}\n'
             b'{"seq":2,"timestamp_ns":-1,"type":"reset"}\n'
             b'{"seq":3,"timestamp_ns":-1,"type":"reset"}\n'
             b'{"seq":4,"timestamp_ns":9223372036854775807,"type":"reset"}\n'),
            ("CRLF", (ROOT / "examples/normal.jsonl").read_bytes().replace(b"\n", b"\r\n")),
            ("no final newline", (ROOT / "examples/reset_edges.jsonl").read_bytes().rstrip(b"\n")),
            ("extreme spreads", b'{"seq":1,"timestamp_ns":0,"type":"quote_set","side":"bid","price_ticks":1,"size":1}\n'
             b'{"seq":2,"timestamp_ns":0,"type":"quote_set","side":"ask","price_ticks":9223372036854775807,"size":1}\n'
             b'{"seq":3,"timestamp_ns":0,"type":"quote_set","side":"bid","price_ticks":9223372036854775807,"size":1}\n'
             b'{"seq":4,"timestamp_ns":0,"type":"quote_set","side":"ask","price_ticks":1,"size":1}\n'),
            ("reset prevents overflow", b'{"seq":1,"timestamp_ns":0,"type":"trade","price_ticks":1,"size":9223372036854775807}\n'
             b'{"seq":2,"timestamp_ns":0,"type":"reset"}\n'
             b'{"seq":3,"timestamp_ns":0,"type":"trade","price_ticks":1,"size":1}\n'),
        ]
        for label, data in valid_edges:
            path.write_bytes(data)
            total += compare(path, rust)
            print(f"PASS valid edge: {label}")
        cases = json.loads((ROOT / "tests/invalid_cases.json").read_text(encoding="utf-8"))
        invalid = [(case["name"], case["input"].encode(), case["line"], case["contains"]) for case in cases]
        invalid.append(("invalid UTF-8", b'{"seq":1,"timestamp_ns":0,"type":"reset"}\n\xff\n', 2, "line 2"))
        for label, data, line, contains in invalid:
            path.write_bytes(data)
            for implementation, cmd in (("Python", PYTHON), ("Rust", rust)):
                for flags in ([], ["--all", "--json"]):
                    result = invoke(cmd, path, [*flags, "--width", "200"], data=b"next\nquit\n")
                    error = result.stderr.decode("utf-8", errors="replace")
                    if result.returncode != 1 or result.stdout or f"line {line}" not in error or contains not in error:
                        raise AssertionError(f"{implementation} invalid case {label}: code={result.returncode}, stdout={result.stdout!r}, error={error}")
        print(f"PASS {len(invalid)} invalid cases: both CLIs reject before interactive/batch output")
    for cmd in (PYTHON, rust):
        result = invoke(cmd, ROOT / "examples/reset_edges.jsonl", ["--width", "20"],
                        data=b"goto 8\nprevious\nshow\nlist\nhelp\ngoto 0\nquit\n")
        text = result.stdout.decode("utf-8")
        if result.returncode or result.stderr or any(len(line) > 20 for line in text.splitlines()):
            raise AssertionError(f"narrow terminal transcript failed: {cmd[0]}")
        if "before first event" not in " ".join(text.split()) or "9007199254740993" not in text:
            raise AssertionError("narrow transcript lost state information")
    print("PASS 20-column scripted navigation in both CLIs")
    print(f"PASS cross-language comparison: {total} snapshots; exact UTF-8 bytes with LF")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, subprocess.CalledProcessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
