# TapeStep: Price and Event Explorer

A terminal explorer for one instrument's quote and trade events. Step forward,
restore an earlier state, and inspect what each event changed. One active bid
and one active ask are retained. All prices use integer ticks.

Python was implemented and tested first as the reference. Rust consumes the
same files and emits the same snapshots. The shared contract is in
[SPEC.md](SPEC.md); expected results live in [golden/](golden/).

## Setup and run

Requirements: Python 3.10+ and Rust 1.85+ with Cargo. Python uses only its
standard library. Cargo downloads its locked dependencies on the first build.
The commands below are run from `price_event_explorer/`, inside this checkout.
On macOS/Linux, replace `py` with `python3`.

```powershell
# From the repository root:
cd price_event_explorer

# Run Python directly; no installation or virtual environment required.
py explore.py examples/normal.jsonl

# Build once, then run Rust.
cargo build --locked --manifest-path rust/Cargo.toml
cargo run --quiet --locked --manifest-path rust/Cargo.toml -- examples/normal.jsonl
```

The Python package can also run from its source directory:

```powershell
cd python
py -m price_event_explorer ../examples/normal.jsonl
cd ..
```

For an installed `tapestep` Python command, optionally create a virtual
environment and install `python/` with `python -m pip install -e ./python`.
Both CLIs support `--help`, `--all`, `--json`, and `--width`.

## A 60–90 second demo

After setup, this runs a **68-second scripted tour** with actual Python CLI
output, pausing four seconds on each of 17 screens:

```powershell
py demo.py
```

Run the same tour with the built Rust executable:

```powershell
py demo.py --impl rust
```

Use `--fast` to skip pauses for a smoke check. Ctrl-C stops the tour.
The tour opens three files, each starting before the first event:

| File | What the screens demonstrate |
| --- | --- |
| `normal.jsonl` | An empty initial state, bid then ask, a trade that preserves quotes, bid replacement, backward restoration, and a jump to the last state |
| `locked_crossed.jsonl` | A zero spread marked `LOCKED`, a negative spread marked `CROSSED`, stepping back to the locked state, and a positive spread again |
| `reset_edges.jsonl` | Replacement of size at the same price, clearing state with reset, restoring the pre-reset state, and an exact integer price above 2^53 |

For hands-on review, open `examples/normal.jsonl` and enter:

```text
help
next
next
next
goto 4
previous
show
list 2
go to 0
quit
```

## Reading the terminal

An example screen after event 4 in normal flow:

```text
TapeStep | sequence 4/7
Event: quote_set bid 101 ticks x 8
Time ns: 1002
Bid: 101 ticks x 8
Ask: 103 ticks x 12
Spread ticks: 2
Condition: --
Last trade: 102 ticks x 3
Traded size: 3 | Trades: 1
Changes (from preceding event):
  Bid: 100 ticks x 10 -> 101 ticks x 8
  Spread ticks: 3 -> 2
```

The header is the cursor: sequence 0 is before the stream, sequence N has
applied events 1 through N. The event and time identify the last event applied.
Bid, ask, and last trade show `price ticks x size`; `--` means absent. Spread is
ask minus bid and is absent unless both exist. Volume and count are measured
since the last reset. `LOCKED` and `CROSSED` call out quote conditions.

**Changes** always compares this event with its preceding event, including when
you move backward or jump. It shows both old and new values. Clearing an empty
side, repeating an identical quote, or resetting an already empty state reports
unchanged quote and trade values while still advancing the event and time.
`list` shows nearby events and marks the last applied event with `>`.

Output is plain ASCII with no color requirement or screen erasure. All text
wraps to terminal width, with a minimum of 20 columns. Override detection with
`--width 40`. JSON output ignores text width.

| Command | Aliases / behavior |
| --- | --- |
| `next` | `n`; move one event forward |
| `previous` | `p`; restore one event earlier |
| `goto SEQ` | `g SEQ`, `go SEQ`, `go to SEQ`; 0 through final sequence |
| `show` | `s`; redraw state and its transition |
| `list [RADIUS]` | `l`; default radius 2, range 0–100 |
| `help` | `h`, `?` |
| `quit` | `q`; EOF and Ctrl-C also exit |

## Generate and export

The seeded generator emits all four event types and permits locked and crossed
quotes, empty clears, and timestamp ties. The same seed and count produce the
same bytes. Generation is bounded to 1,000,000 events; default seed 42/count 100.

```powershell
py generate.py --seed 42 --count 100 --output generated.jsonl
py explore.py generated.jsonl
py explore.py examples/normal.jsonl --all --json
cargo run --quiet --locked --manifest-path rust/Cargo.toml -- examples/normal.jsonl --all --json
```

`--all --json` emits one compact snapshot per event and no banners or prompts.
It validates the entire file first, so even a late error produces zero snapshot
bytes. Keys are sorted, numbers are integers, and line endings are LF on every
platform. `--all` alone prints readable state screens. `--json` requires `--all`.
PowerShell redirection may re-encode output depending on its version; use
`verify.py`, which captures raw subprocess bytes, for equality checks.

## Tests and cross-language verification

Run these commands from `price_event_explorer/`:

```powershell
py -m unittest discover -s python/tests -v
cargo test --locked --manifest-path rust/Cargo.toml
py verify.py
cargo fmt --manifest-path rust/Cargo.toml -- --check
cargo clippy --locked --manifest-path rust/Cargo.toml --all-targets -- -D warnings
py demo.py --fast
py demo.py --impl rust --fast
```

Python discovery also works from the `python/` directory:

```powershell
cd python
py -m unittest discover -s tests -v
cd ..
```

Both suites cover replacement (including same-price size changes), clearing
an empty side, trades preserving quotes, reset, backward navigation across all
prefixes, locked/crossed conditions, large integers, narrow terminal rendering,
and shared invalid cases. The shared cases cover invalid price/size values,
missing fields, sequence gaps, timestamp decreases, malformed JSON, duplicate
and unknown fields, and overflow; invalid UTF-8 is also tested.

`verify.py` builds Rust, compares **exact bytes** for all 23 shared golden
snapshots, checks 5,000 generated events across five seeds and 28 extra valid
edge snapshots, and checks 31 invalid files in interactive and batch modes in
both CLIs. It also runs a 20-column navigation transcript in each CLI. Goldens
are hand-authored expected states and are never updated by tests or verification.
`--rust-bin PATH` can select a previously built executable instead of building.

Missing toolchains or failed builds cause verification to fail explicitly;
no unavailable check is treated as passing.

Validated on Windows on 2026-09-30 with Python 3.13.1, Cargo 1.98.1, and
rustc 1.98.1. Commands below were run from this product directory:

| Exact command | Result |
| --- | --- |
| `py -m unittest discover -s python/tests -v` | 15 tests passed |
| `cargo test --locked --manifest-path rust/Cargo.toml` | 15 tests passed |
| `py verify.py` | 5,051 byte-identical snapshots; 31 invalid cases rejected in both modes by both CLIs; both 20-column transcripts passed |
| `cargo fmt --manifest-path rust/Cargo.toml -- --check` | Passed |
| `cargo clippy --locked --manifest-path rust/Cargo.toml --all-targets -- -D warnings` | Passed, no warnings |
| `py demo.py --fast` | Passed; all 17 screens displayed |
| `py demo.py --impl rust --fast` | Passed; all 17 screens displayed |

## Project layout and limits

```text
explore.py                 Source-checkout Python launcher
python/price_event_explorer/ Reference engine and terminal CLI
python/tests/              Python unittest suite
rust/src/                 Rust engine and terminal CLI
rust/tests/               Rust integration and CLI tests
rust/Cargo.lock            Reproducible dependency resolution
generate.py               Seeded stream generator
examples/                 Three event streams
golden/                   Shared expected canonical snapshots
tests/invalid_cases.json  Shared malformed-input cases
SPEC.md                   Event and snapshot contract
verify.py                 Cross-language acceptance check
demo.py                   Scripted reviewer tour
```

Both engines keep the state after each prefix, including sequence 0. Navigation
selects the corresponding saved state, so backward movement never guesses an
inverse transition. Loading costs O(events) time and memory; navigation is O(1).
Files are finite and preloaded. This is a single-instrument event explorer with
one quote per side; depth, matching, and live feeds are outside its scope.
