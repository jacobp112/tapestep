pub mod ui;

use serde::de::{self, MapAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::fmt;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::Path;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Kind {
    QuoteSet,
    QuoteClear,
    Trade,
    Reset,
}

impl Kind {
    pub fn name(&self) -> &'static str {
        match self {
            Self::QuoteSet => "quote_set",
            Self::QuoteClear => "quote_clear",
            Self::Trade => "trade",
            Self::Reset => "reset",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    Bid,
    Ask,
}

impl Side {
    pub fn name(self) -> &'static str {
        match self {
            Self::Bid => "bid",
            Self::Ask => "ask",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Event {
    pub seq: i64,
    pub timestamp_ns: i64,
    pub kind: Kind,
    pub side: Option<Side>,
    pub quote: Option<Quote>,
}

impl Event {
    pub fn as_json(&self) -> Value {
        let mut obj =
            json!({"seq": self.seq, "timestamp_ns": self.timestamp_ns, "type": self.kind.name()});
        if let Some(side) = self.side {
            obj["side"] = json!(side.name());
        }
        if let Some(quote) = self.quote {
            obj["price_ticks"] = json!(quote.price_ticks);
            obj["size"] = json!(quote.size);
        }
        obj
    }
}

// Preserve JSON integer types, reject duplicate top-level members before validation.
struct RawEvent(BTreeMap<String, Value>);

impl<'de> Deserialize<'de> for RawEvent {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct ObjectVisitor;
        impl<'de> Visitor<'de> for ObjectVisitor {
            type Value = RawEvent;
            fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
                formatter.write_str("event must be a JSON object")
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<RawEvent, A::Error> {
                let mut fields = BTreeMap::new();
                while let Some((key, value)) = map.next_entry::<String, Value>()? {
                    if fields.insert(key.clone(), value).is_some() {
                        return Err(de::Error::custom(format!("duplicate field '{key}'")));
                    }
                }
                Ok(RawEvent(fields))
            }
        }
        deserializer.deserialize_any(ObjectVisitor)
    }
}

fn integer(fields: &BTreeMap<String, Value>, field: &str, minimum: i64) -> Result<i64, String> {
    fields[field]
        .as_i64()
        .filter(|&value| value >= minimum)
        .ok_or_else(|| {
            format!(
                "'{field}' must be an integer from {minimum} to {}",
                i64::MAX
            )
        })
}

pub fn parse_event(
    line: &str,
    expected_seq: i64,
    previous_timestamp: Option<i64>,
) -> Result<Event, String> {
    let RawEvent(fields) = serde_json::from_str(line).map_err(|e| e.to_string())?;
    for field in ["seq", "timestamp_ns", "type"] {
        if !fields.contains_key(field) {
            return Err(format!("missing field '{field}'"));
        }
    }
    let kind = match fields["type"].as_str() {
        Some("quote_set") => Kind::QuoteSet,
        Some("quote_clear") => Kind::QuoteClear,
        Some("trade") => Kind::Trade,
        Some("reset") => Kind::Reset,
        _ => return Err("'type' must be quote_set, quote_clear, trade, or reset".into()),
    };
    let mut required = vec!["seq", "timestamp_ns", "type"];
    if matches!(kind, Kind::QuoteSet | Kind::QuoteClear) {
        required.push("side");
    }
    if matches!(kind, Kind::QuoteSet | Kind::Trade) {
        required.extend(["price_ticks", "size"]);
    }
    required.sort_unstable();
    for field in &required {
        if !fields.contains_key(*field) {
            return Err(format!("missing field '{field}'"));
        }
    }
    for field in fields.keys() {
        if !required.contains(&field.as_str()) {
            return Err(format!("unknown field '{field}'"));
        }
    }
    let seq = integer(&fields, "seq", 1)?;
    let timestamp_ns = integer(&fields, "timestamp_ns", i64::MIN)?;
    if seq != expected_seq {
        return Err(format!("expected sequence {expected_seq}, got {seq}"));
    }
    if let Some(previous_timestamp) = previous_timestamp.filter(|&previous| timestamp_ns < previous)
    {
        return Err(format!(
            "timestamp_ns decreased: {timestamp_ns} < {previous_timestamp}"
        ));
    }
    let side = if required.contains(&"side") {
        Some(match fields["side"].as_str() {
            Some("bid") => Side::Bid,
            Some("ask") => Side::Ask,
            _ => return Err("'side' must be 'bid' or 'ask'".into()),
        })
    } else {
        None
    };
    let quote = if required.contains(&"price_ticks") {
        Some(Quote {
            price_ticks: integer(&fields, "price_ticks", 1)?,
            size: integer(&fields, "size", 1)?,
        })
    } else {
        None
    };
    Ok(Event {
        seq,
        timestamp_ns,
        kind,
        side,
        quote,
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Quote {
    pub price_ticks: i64,
    pub size: i64,
}

impl Quote {
    pub fn as_json(self) -> Value {
        json!({"price_ticks": self.price_ticks, "size": self.size})
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct State {
    pub event: Option<Event>,
    pub bid: Option<Quote>,
    pub ask: Option<Quote>,
    pub last_trade: Option<Quote>,
    pub cumulative_traded_size: i64,
    pub trade_count: i64,
}

impl State {
    pub fn seq(&self) -> i64 {
        self.event.as_ref().map_or(0, |e| e.seq)
    }

    pub fn spread_ticks(&self) -> Option<i64> {
        self.ask
            .zip(self.bid)
            .map(|(ask, bid)| ask.price_ticks - bid.price_ticks)
    }

    pub fn quote_condition(&self) -> Option<&'static str> {
        match self.spread_ticks() {
            Some(0) => Some("locked"),
            Some(spread) if spread < 0 => Some("crossed"),
            _ => None,
        }
    }

    pub fn as_json(&self) -> Value {
        json!({
            "seq": self.seq(),
            "timestamp_ns": self.event.as_ref().map(|e| e.timestamp_ns),
            "event": self.event.as_ref().map(Event::as_json),
            "bid": self.bid.map(Quote::as_json),
            "ask": self.ask.map(Quote::as_json),
            "spread_ticks": self.spread_ticks(),
            "last_trade": self.last_trade.map(Quote::as_json),
            "cumulative_traded_size": self.cumulative_traded_size,
            "trade_count": self.trade_count,
            "quote_condition": self.quote_condition(),
        })
    }

    pub fn snapshot_json(&self) -> String {
        // serde_json's default map uses BTreeMap: keys sort recursively.
        self.as_json().to_string()
    }
}

pub fn apply_event(state: &State, event: Event) -> Result<State, String> {
    let mut next = if event.kind == Kind::Reset {
        State::default()
    } else {
        state.clone()
    };
    match event.kind {
        Kind::QuoteSet | Kind::QuoteClear => {
            let quote = if event.kind == Kind::QuoteSet {
                event.quote
            } else {
                None
            };
            match event.side {
                Some(Side::Bid) => next.bid = quote,
                Some(Side::Ask) => next.ask = quote,
                None => return Err("quote event requires a side".into()),
            }
        }
        Kind::Trade => {
            let quote = event.quote.ok_or("trade requires price_ticks and size")?;
            next.cumulative_traded_size = next
                .cumulative_traded_size
                .checked_add(quote.size)
                .ok_or("cumulative_traded_size exceeds signed 64-bit range")?;
            next.trade_count = next
                .trade_count
                .checked_add(1)
                .ok_or("trade_count exceeds signed 64-bit range")?;
            next.last_trade = Some(quote);
        }
        Kind::Reset => {}
    }
    next.event = Some(event);
    Ok(next)
}

pub fn read_states<R: BufRead>(mut reader: R, label: &str) -> Result<Vec<State>, String> {
    let mut states = vec![State::default()];
    let mut bytes = Vec::new();
    let mut line_number = 0;
    loop {
        bytes.clear();
        let count = reader
            .read_until(b'\n', &mut bytes)
            .map_err(|e| format!("{label}: line {}: cannot read file: {e}", line_number + 1))?;
        if count == 0 {
            break;
        }
        line_number += 1;
        let result = (|| {
            let line = std::str::from_utf8(&bytes).map_err(|e| format!("invalid UTF-8: {e}"))?;
            let before = states.last().expect("initial state always exists");
            let previous_timestamp = before.event.as_ref().map(|e| e.timestamp_ns);
            let event = parse_event(line, line_number, previous_timestamp)?;
            apply_event(before, event)
        })();
        states.push(result.map_err(|e| format!("{label}: line {line_number}: {e}"))?);
    }
    Ok(states)
}

pub fn load_states(path: &Path) -> Result<Vec<State>, String> {
    let label = path.display().to_string();
    let file = File::open(path).map_err(|e| format!("{label}: cannot read file: {e}"))?;
    read_states(BufReader::new(file), &label)
}

pub struct Explorer {
    states: Vec<State>,
    cursor: usize,
}

impl Explorer {
    pub fn new(states: Vec<State>) -> Self {
        assert!(!states.is_empty(), "explorer requires an initial state");
        Self { states, cursor: 0 }
    }
    pub fn current(&self) -> &State {
        &self.states[self.cursor]
    }
    pub fn states(&self) -> &[State] {
        &self.states
    }
    pub fn cursor(&self) -> usize {
        self.cursor
    }
    pub fn last_seq(&self) -> usize {
        self.states.len() - 1
    }
    pub fn goto(&mut self, seq: usize) -> Result<&State, String> {
        if seq >= self.states.len() {
            return Err(format!("sequence must be from 0 to {}", self.last_seq()));
        }
        self.cursor = seq;
        Ok(self.current())
    }
    pub fn forward(&mut self) -> &State {
        self.cursor = self.cursor.saturating_add(1).min(self.last_seq());
        self.current()
    }
    pub fn previous(&mut self) -> &State {
        self.cursor = self.cursor.saturating_sub(1);
        self.current()
    }
}
