"""Deterministic seeded JSONL generator; only the Python standard library."""

import argparse
import json
from pathlib import Path
import random
import sys


def generate(seed: int, count: int):
    # Use getrandbits rather than choice/randrange for a stable, specified draw.
    rng = random.Random(seed)
    timestamp = 1_000_000_000
    for seq in range(1, count + 1):
        bits = rng.getrandbits(64)
        timestamp += (bits & 1023) * 1000
        kind = (bits >> 10) % 10
        event = {"seq": seq, "timestamp_ns": timestamp}
        if kind < 5:
            event.update(type="quote_set", side="bid" if (bits >> 14) & 1 else "ask",
                         price_ticks=10000 + (bits >> 16) % 21 - 10, size=1 + (bits >> 24) % 50)
        elif kind < 7:
            event.update(type="quote_clear", side="bid" if (bits >> 14) & 1 else "ask")
        elif kind < 9:
            event.update(type="trade", price_ticks=10000 + (bits >> 16) % 21 - 10,
                         size=1 + (bits >> 24) % 50)
        else:
            event.update(type="reset")
        yield event


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--output", type=Path, help="write here instead of stdout")
    args = parser.parse_args()
    if not 0 <= args.count <= 1_000_000:
        parser.error("--count must be from 0 to 1000000")
    stream = args.output.open("w", encoding="utf-8", newline="\n") if args.output else sys.stdout
    if not args.output and hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", newline="\n")
    try:
        for event in generate(args.seed, args.count):
            stream.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
    finally:
        if args.output:
            stream.close()


if __name__ == "__main__":
    main()
