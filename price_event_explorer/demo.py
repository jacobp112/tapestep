"""A 68-second scripted tour using actual CLI output. Build Rust before its tour."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
SCENES = [
    ("normal", [
        ("next", "A bid appears; ask and spread are empty."),
        ("next", "The ask establishes a three-tick spread."),
        ("next", "A trade updates statistics and leaves both quotes unchanged."),
        ("goto 4", "A replacement updates the bid price and size."),
        ("previous", "Going backward restores the preceding quote and statistics."),
        ("goto 7", "Jump to the final state; compare changes with event six."),
    ]),
    ("locked_crossed", [
        ("goto 2", "Equal prices lock the quotes; spread is zero."),
        ("goto 4", "Bid above ask crosses the quotes; spread is negative."),
        ("previous", "Restore the locked state and earlier bid."),
        ("goto 5", "A replacement ask restores a positive spread."),
    ]),
    ("reset_edges", [
        ("goto 4", "Replacing at the same price still changes quote size."),
        ("next", "Reset clears quotes and every trade statistic."),
        ("previous", "Moving backward across reset restores all cleared values."),
        ("goto 7", "Large integer prices remain exact, even above 2^53."),
    ]),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--impl", choices=("python", "rust"), default="python")
    parser.add_argument("--fast", action="store_true", help="skip delays for a smoke check")
    args = parser.parse_args()
    cmd = [sys.executable, "-u", "-m", "price_event_explorer"] if args.impl == "python" else [
        str(ROOT / "rust/target/debug" / ("tapestep.exe" if os.name == "nt" else "tapestep"))]
    for name, steps in SCENES:
        result = subprocess.run([*cmd, str(ROOT / "examples" / f"{name}.jsonl"), "--width", "60"],
                                cwd=ROOT / "python", input=("\n".join(command for command, _ in steps) + "\nquit\n").encode(),
                                capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace"))
        screens = []
        for line in result.stdout.decode().splitlines():
            if line.startswith("TapeStep |"):
                screens.append([])
            if screens:
                screens[-1].append(line)
        annotations = [("start", "Loaded before the first event; no future state is visible."), *steps]
        if len(screens) != len(annotations):
            raise RuntimeError("unexpected CLI screen count")
        for (command, note), lines in zip(annotations, screens):
            print(f"\n=== {name} | {command} ===\n{note}\n", flush=True)
            print("\n".join(lines), flush=True)
            if not args.fast:
                time.sleep(4)
    print("\nTour complete. Use next, previous, goto, show, list, help, quit in either CLI.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nTour stopped.")
    except (OSError, RuntimeError) as error:
        print(f"Demo failed: {error}", file=sys.stderr)
        raise SystemExit(1)
