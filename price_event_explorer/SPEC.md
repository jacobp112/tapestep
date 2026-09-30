# Event and snapshot specification (version 1)

This is a state explorer for **one instrument with one active quote per side**.
Prices are integer ticks; no price conversion or floating point is used. A quote
is informational: trades never consume it. There is no matching engine, depth,
order ID, venue, or instrument field.

## Input

UTF-8 JSON Lines, one object per physical line. An empty file is valid. Blank
lines, a BOM, duplicate object keys, unknown fields, invalid UTF-8, non-JSON
constants, and non-object lines are errors. CRLF and a missing final newline are
accepted. All numeric fields must be JSON integers (booleans, decimals, and
exponents are rejected) in the signed 64-bit range. Fields may appear in any
order. The entire file is validated before any state or prompt is printed.
Errors identify the file, the 1-based physical line, and the reason.

Every event has exactly these common fields:

| Field | Rule |
| --- | --- |
| `seq` | Contiguous positive sequence: 1, 2, 3, ... |
| `timestamp_ns` | Signed integer nanoseconds, nondecreasing; negative values and ties allowed |
| `type` | One of the four types below |

Additional fields and transitions:

| Type | Additional required fields | Transition |
| --- | --- | --- |
| `quote_set` | `side`: `bid` or `ask`; positive `price_ticks`, positive `size` | Replace the entire active quote on that side, even if price is unchanged |
| `quote_clear` | `side`: `bid` or `ask` | Clear that side; clearing an empty side is a no-op |
| `trade` | positive `price_ticks`, positive `size` | Replace last trade; add size to cumulative size; increment count; preserve both quotes |
| `reset` | None | Clear both quotes, last trade, cumulative size, and trade count |

Cumulative traded size must stay within signed 64-bit range. A trade that would
overflow it is an input error on that trade's line. Reset starts its accumulation
again. Sequence and timestamp ordering continue through reset.

## State snapshots

The initial cursor is sequence 0, **before the first event**. Its `event` and
`timestamp_ns` are null, its quotes and last trade are null, and both counters
are zero. A snapshot after an event has exactly these fields:

| Field | Meaning |
| --- | --- |
| `seq`, `timestamp_ns` | Last applied event's sequence and timestamp |
| `event` | Complete last applied input event object |
| `bid`, `ask` | null or `{"price_ticks": integer, "size": integer}` |
| `spread_ticks` | ask price minus bid price, or null if either side is empty |
| `last_trade` | null or `{"price_ticks": integer, "size": integer}` |
| `cumulative_traded_size`, `trade_count` | Since start or most recent reset |
| `quote_condition` | `locked` for spread 0, `crossed` for negative spread, otherwise null |

The event and timestamp advance even when an event has no effect on quotes or
statistics. A missing quote has no spread or condition. Negative spread is
valid, and is displayed as a crossed condition. Last trade need not lie between
the quotes. Numeric values remain integers at every step.

`--all --json` emits exactly one snapshot per event (no sequence-0 snapshot), in
sequence order: compact JSON, object keys sorted lexicographically at every
level, no spaces, LF separators, and a trailing LF. For an empty file it emits
zero bytes. This byte-level contract is shared by Python and Rust.

## Navigation and presentation

`next` / `n` applies one event; `previous` / `p` restores the preceding state;
`goto SEQ` / `g SEQ` selects any state from 0 through the final sequence.
`show` / `s` redraws state; `list [RADIUS]` / `l [RADIUS]` lists events around
the cursor (default radius 2, maximum 100); `help` / `h` / `?` explains commands;
`quit` / `q` exits. `go SEQ` and `go to SEQ` are also accepted. Navigation past
either boundary keeps the cursor and reports the boundary. Invalid commands
keep the cursor. EOF and Ctrl-C exit cleanly. Exit status: 0 success, 1 input
or I/O error, 2 argument misuse.

Both implementations retain immutable states for every prefix, including 0.
Navigation selects a saved prefix state, without reversing events or carrying
future values backward. This costs O(number of events) memory and gives O(1)
state selection. This product targets reviewable generated streams, not live
or unbounded feeds.

Interactive screens show cursor position, the last event, quotes, spread,
condition, last trade, volume and count, and a **Changes** section comparing the
displayed state to the state before that event. This section has the same
meaning when navigating in either direction. It reports unchanged values for
no-op events. The initial state has no transition. Nearby event listings mark
the last applied event with `>`; sequence 0 has no marked event.

Plain ASCII, no ANSI cursor movement, and no dependency on color make captured
output readable. Lines wrap to terminal width (`--width` overrides it; minimum
20, default 80 when detection is unavailable). This includes errors, help,
commands, and listings. Wrapping never modifies JSON output.

## Shared acceptance fixtures

`examples/normal.jsonl`, `examples/locked_crossed.jsonl`, and
`examples/reset_edges.jsonl` have hand-authored expected snapshots in
`golden/`. Both test suites compare the same expected bytes, and the
cross-language check compares actual CLI bytes against both each other and the
goldens. `tests/invalid_cases.json` specifies shared invalid-input cases.
Golden files are specification artifacts; the verification script never
regenerates or overwrites them.
