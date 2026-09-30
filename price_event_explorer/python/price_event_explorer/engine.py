"""Strict event validation and immutable prefix states. See ../../SPEC.md."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

MAX_INT = (1 << 63) - 1
MIN_INT = -(1 << 63)
COMMON = {"seq", "timestamp_ns", "type"}
FIELDS = {
    "quote_set": COMMON | {"side", "price_ticks", "size"},
    "quote_clear": COMMON | {"side"},
    "trade": COMMON | {"price_ticks", "size"},
    "reset": COMMON,
}


class InputError(ValueError):
    """Input cannot be replayed under the event contract."""


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate field '{key}'")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant '{value}'")


def _integer(event: dict[str, Any], field: str, minimum: int) -> int:
    value = event[field]
    if type(value) is not int or not minimum <= value <= MAX_INT:
        raise ValueError(f"'{field}' must be an integer from {minimum} to {MAX_INT}")
    return value


@dataclass(frozen=True)
class Event:
    seq: int
    timestamp_ns: int
    type: str
    side: str | None = None
    price_ticks: int | None = None
    size: int | None = None

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "seq": self.seq, "timestamp_ns": self.timestamp_ns, "type": self.type
        }
        if self.side is not None:
            value["side"] = self.side
        if self.price_ticks is not None:
            value["price_ticks"] = self.price_ticks
            value["size"] = self.size
        return value


def parse_event(line: str, expected_seq: int, previous_timestamp: int | None) -> Event:
    obj = json.loads(line, object_pairs_hook=_object, parse_constant=_constant)
    if not isinstance(obj, dict):
        raise ValueError("event must be a JSON object")
    for key in sorted(COMMON):
        if key not in obj:
            raise ValueError(f"missing field '{key}'")
    kind = obj["type"]
    if not isinstance(kind, str) or kind not in FIELDS:
        raise ValueError("'type' must be quote_set, quote_clear, trade, or reset")
    required = FIELDS[kind]
    for key in sorted(required - obj.keys()):
        raise ValueError(f"missing field '{key}'")
    for key in sorted(obj.keys() - required):
        raise ValueError(f"unknown field '{key}'")
    seq = _integer(obj, "seq", 1)
    timestamp = _integer(obj, "timestamp_ns", MIN_INT)
    if seq != expected_seq:
        raise ValueError(f"expected sequence {expected_seq}, got {seq}")
    if previous_timestamp is not None and timestamp < previous_timestamp:
        raise ValueError(f"timestamp_ns decreased: {timestamp} < {previous_timestamp}")
    side = obj.get("side")
    if "side" in required and side not in ("bid", "ask"):
        raise ValueError("'side' must be 'bid' or 'ask'")
    price = _integer(obj, "price_ticks", 1) if "price_ticks" in required else None
    size = _integer(obj, "size", 1) if "size" in required else None
    return Event(seq, timestamp, kind, side, price, size)


@dataclass(frozen=True)
class Quote:
    price_ticks: int
    size: int

    def as_dict(self) -> dict[str, int]:
        return {"price_ticks": self.price_ticks, "size": self.size}


@dataclass(frozen=True)
class State:
    event: Event | None = None
    bid: Quote | None = None
    ask: Quote | None = None
    last_trade: Quote | None = None
    cumulative_traded_size: int = 0
    trade_count: int = 0

    @property
    def seq(self) -> int:
        return self.event.seq if self.event else 0

    @property
    def spread_ticks(self) -> int | None:
        return self.ask.price_ticks - self.bid.price_ticks if self.ask and self.bid else None

    @property
    def quote_condition(self) -> str | None:
        spread = self.spread_ticks
        return "locked" if spread == 0 else "crossed" if spread is not None and spread < 0 else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "timestamp_ns": self.event.timestamp_ns if self.event else None,
            "event": self.event.as_dict() if self.event else None,
            "bid": self.bid.as_dict() if self.bid else None,
            "ask": self.ask.as_dict() if self.ask else None,
            "spread_ticks": self.spread_ticks,
            "last_trade": self.last_trade.as_dict() if self.last_trade else None,
            "cumulative_traded_size": self.cumulative_traded_size,
            "trade_count": self.trade_count,
            "quote_condition": self.quote_condition,
        }


def apply_event(state: State, event: Event) -> State:
    if event.type == "reset":
        return State(event=event)
    bid, ask, last = state.bid, state.ask, state.last_trade
    volume, count = state.cumulative_traded_size, state.trade_count
    if event.type == "quote_set":
        quote = Quote(event.price_ticks, event.size)  # validated before replay
        if event.side == "bid":
            bid = quote
        else:
            ask = quote
    elif event.type == "quote_clear":
        if event.side == "bid":
            bid = None
        else:
            ask = None
    else:
        volume += event.size
        if volume > MAX_INT:
            raise ValueError("cumulative_traded_size exceeds signed 64-bit range")
        count += 1
        last = Quote(event.price_ticks, event.size)
    return State(event, bid, ask, last, volume, count)


def load_states(path: str | Path) -> tuple[State, ...]:
    path = Path(path)
    states = [State()]
    try:
        with path.open("rb") as stream:
            for line_number, raw in enumerate(stream, 1):
                try:
                    line = raw.decode("utf-8")
                    event = parse_event(line, line_number, states[-1].event.timestamp_ns if states[-1].event else None)
                    states.append(apply_event(states[-1], event))
                except (ValueError, RecursionError) as error:
                    raise InputError(f"{path}: line {line_number}: {error}") from error
    except OSError as error:
        raise InputError(f"{path}: cannot read file: {error}") from error
    return tuple(states)


def snapshot_json(state: State) -> str:
    return json.dumps(state.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Explorer:
    def __init__(self, states: tuple[State, ...]):
        self.states = states
        self.cursor = 0

    @property
    def current(self) -> State:
        return self.states[self.cursor]

    def goto(self, seq: int) -> State:
        if not 0 <= seq < len(self.states):
            raise ValueError(f"sequence must be from 0 to {len(self.states) - 1}")
        self.cursor = seq
        return self.current

    def next(self) -> State:
        return self.goto(min(self.cursor + 1, len(self.states) - 1))

    def previous(self) -> State:
        return self.goto(max(0, self.cursor - 1))
