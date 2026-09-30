"""Plain ASCII presentation that wraps safely on narrow terminals."""

from __future__ import annotations

import argparse
import shutil
import sys
import textwrap

from .engine import Explorer, InputError, Quote, State, load_states, snapshot_json

HELP = [
    "next (n): move forward one event",
    "previous (p): restore the preceding state",
    "goto SEQ (g SEQ, go to SEQ): select sequence 0..last",
    "show (s): display the current state",
    "list [RADIUS] (l): nearby events; default 2, maximum 100",
    "help (h, ?): show these commands",
    "quit (q): exit; EOF and Ctrl-C also exit",
]


def quote_text(quote: Quote | None) -> str:
    return f"{quote.price_ticks} ticks x {quote.size}" if quote else "--"


def event_text(state: State) -> str:
    event = state.event
    if event is None:
        return "before first event"
    parts = [event.type]
    if event.side:
        parts.append(event.side)
    if event.price_ticks is not None:
        parts.append(f"{event.price_ticks} ticks x {event.size}")
    return " ".join(parts)


def render_state(explorer: Explorer) -> list[str]:
    state = explorer.current
    lines = [
        f"TapeStep | sequence {state.seq}/{len(explorer.states) - 1}",
        f"Event: {event_text(state)}",
        f"Time ns: {state.event.timestamp_ns if state.event else '--'}",
        f"Bid: {quote_text(state.bid)}",
        f"Ask: {quote_text(state.ask)}",
        f"Spread ticks: {state.spread_ticks if state.spread_ticks is not None else '--'}",
        f"Condition: {state.quote_condition.upper() if state.quote_condition else '--'}",
        f"Last trade: {quote_text(state.last_trade)}",
        f"Traded size: {state.cumulative_traded_size} | Trades: {state.trade_count}",
        "Changes (from preceding event):",
    ]
    if state.seq == 0:
        return lines + ["  Initial state; no event applied."]
    before = explorer.states[state.seq - 1]
    changes = []
    for label, field in (("Bid", "bid"), ("Ask", "ask"), ("Last trade", "last_trade")):
        old, new = getattr(before, field), getattr(state, field)
        if old != new:
            changes.append(f"  {label}: {quote_text(old)} -> {quote_text(new)}")
    for label, field in (("Traded size", "cumulative_traded_size"), ("Trades", "trade_count"),
                         ("Spread ticks", "spread_ticks"), ("Condition", "quote_condition")):
        old, new = getattr(before, field), getattr(state, field)
        if old != new:
            changes.append(f"  {label}: {old if old is not None else '--'} -> {new if new is not None else '--'}")
    return lines + (changes or ["  Quotes and trade statistics unchanged."])


def wrapped(lines: list[str], width: int) -> str:
    return "\n".join(
        chunk for line in lines
        for chunk in (textwrap.wrap(line, width=width, break_long_words=True, break_on_hyphens=False) or [""])
    )


def command(explorer: Explorer, text: str) -> tuple[list[str], bool]:
    words = text.lower().split()
    if not words:
        return [], False
    name, args = words[0], words[1:]
    if name in ("quit", "q") and not args:
        return ["Goodbye."], True
    if name in ("help", "h", "?") and not args:
        return HELP, False
    if name in ("show", "s") and not args:
        return render_state(explorer), False
    if name in ("next", "n", "previous", "p") and not args:
        forward = name in ("next", "n")
        boundary = explorer.cursor == (len(explorer.states) - 1 if forward else 0)
        explorer.next() if forward else explorer.previous()
        notice = ["At end of stream." if forward else "Before first event."] if boundary else []
        return notice + render_state(explorer), False
    if name == "go" and args and args[0] == "to":
        args = args[1:]
    if name in ("goto", "g", "go") and len(args) == 1:
        if not args[0].isascii() or not args[0].isdigit():
            return ["Sequence must be a nonnegative integer."], False
        try:
            explorer.goto(int(args[0]))
        except ValueError as error:
            return [str(error)], False
        return render_state(explorer), False
    if name in ("list", "l") and len(args) <= 1:
        if args and (not args[0].isascii() or not args[0].isdigit() or len(args[0]) > 3 or int(args[0]) > 100):
            return ["Radius must be an integer from 0 to 100."], False
        radius = int(args[0]) if args else 2
        start, stop = max(1, explorer.cursor - radius), min(len(explorer.states) - 1, explorer.cursor + radius)
        lines = ["Nearby events (> = last applied):"]
        for seq in range(start, stop + 1):
            state = explorer.states[seq]
            lines.append(f"{'>' if seq == explorer.cursor else ' '} {seq} @ {state.event.timestamp_ns}: {event_text(state)}")
        return lines + ([] if stop >= start else ["  No events in this range."]), False
    return ["Unknown command or arguments. Type help for commands."], False


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser(description="TapeStep: explore quote and trade events in integer ticks.")
    parser.add_argument("file", help="UTF-8 JSONL event file")
    parser.add_argument("--all", action="store_true", help="print every event state and exit")
    parser.add_argument("--json", action="store_true", help="canonical JSON snapshots (requires --all)")
    parser.add_argument("--width", type=int, default=None, help="text width, at least 20 columns")
    args = parser.parse_args(argv)
    if args.json and not args.all:
        parser.error("--json requires --all")
    if args.width is not None and args.width < 20:
        parser.error("--width must be at least 20")
    width = max(20, args.width or shutil.get_terminal_size((80, 24)).columns)
    try:
        states = load_states(args.file)
    except InputError as error:
        print(wrapped([f"Error: {error}"], width), file=sys.stderr)
        return 1
    explorer = Explorer(states)
    if args.all:
        for seq in range(1, len(states)):
            explorer.goto(seq)
            if args.json:
                print(snapshot_json(explorer.current))
            else:
                print(wrapped(render_state(explorer), width) + "\n")
        return 0
    print(wrapped(render_state(explorer) + ["Type help for commands."], width))
    while True:
        try:
            if sys.stdin.isatty():
                print("step> ", end="", flush=True)
            line = sys.stdin.readline()
            if not line:
                print("Goodbye.")
                return 0
            lines, done = command(explorer, line)
            if lines:
                print(wrapped(lines, width))
            if done:
                return 0
        except KeyboardInterrupt:
            print("\nGoodbye.")
            return 0
