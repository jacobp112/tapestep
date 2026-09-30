use crate::{Explorer, Quote, State};

pub const HELP: &[&str] = &[
    "next (n): move forward one event",
    "previous (p): restore the preceding state",
    "goto SEQ (g SEQ, go to SEQ): select sequence 0..last",
    "show (s): display the current state",
    "list [RADIUS] (l): nearby events; default 2, maximum 100",
    "help (h, ?): show these commands",
    "quit (q): exit; EOF and Ctrl-C also exit",
];

pub fn quote_text(quote: Option<Quote>) -> String {
    quote.map_or_else(
        || "--".into(),
        |q| format!("{} ticks x {}", q.price_ticks, q.size),
    )
}

pub fn event_text(state: &State) -> String {
    let Some(event) = &state.event else {
        return "before first event".into();
    };
    let mut text = event.kind.name().to_string();
    if let Some(side) = event.side {
        text.push_str(&format!(" {}", side.name()));
    }
    if let Some(quote) = event.quote {
        text.push_str(&format!(" {}", quote_text(Some(quote))));
    }
    text
}

fn optional<T: ToString>(value: Option<T>) -> String {
    value.map_or_else(|| "--".into(), |v| v.to_string())
}

pub fn render_state(explorer: &Explorer) -> Vec<String> {
    let state = explorer.current();
    let mut lines = vec![
        format!(
            "TapeStep | sequence {}/{}",
            state.seq(),
            explorer.last_seq()
        ),
        format!("Event: {}", event_text(state)),
        format!(
            "Time ns: {}",
            optional(state.event.as_ref().map(|e| e.timestamp_ns))
        ),
        format!("Bid: {}", quote_text(state.bid)),
        format!("Ask: {}", quote_text(state.ask)),
        format!("Spread ticks: {}", optional(state.spread_ticks())),
        format!(
            "Condition: {}",
            optional(state.quote_condition().map(str::to_uppercase))
        ),
        format!("Last trade: {}", quote_text(state.last_trade)),
        format!(
            "Traded size: {} | Trades: {}",
            state.cumulative_traded_size, state.trade_count
        ),
        "Changes (from preceding event):".into(),
    ];
    if explorer.cursor() == 0 {
        lines.push("  Initial state; no event applied.".into());
        return lines;
    }
    let before = &explorer.states()[explorer.cursor() - 1];
    let mut changes = Vec::new();
    for (label, old, new) in [
        ("Bid", before.bid, state.bid),
        ("Ask", before.ask, state.ask),
        ("Last trade", before.last_trade, state.last_trade),
    ] {
        if old != new {
            changes.push(format!(
                "  {label}: {} -> {}",
                quote_text(old),
                quote_text(new)
            ));
        }
    }
    for (label, old, new) in [
        (
            "Traded size",
            Some(before.cumulative_traded_size),
            Some(state.cumulative_traded_size),
        ),
        ("Trades", Some(before.trade_count), Some(state.trade_count)),
        ("Spread ticks", before.spread_ticks(), state.spread_ticks()),
    ] {
        if old != new {
            changes.push(format!("  {label}: {} -> {}", optional(old), optional(new)));
        }
    }
    if before.quote_condition() != state.quote_condition() {
        changes.push(format!(
            "  Condition: {} -> {}",
            optional(before.quote_condition()),
            optional(state.quote_condition())
        ));
    }
    if changes.is_empty() {
        changes.push("  Quotes and trade statistics unchanged.".into());
    }
    lines.extend(changes);
    lines
}

pub fn wrapped(lines: &[String], width: usize) -> String {
    let options = textwrap::Options::new(width)
        .word_splitter(textwrap::WordSplitter::NoHyphenation)
        .wrap_algorithm(textwrap::WrapAlgorithm::FirstFit);
    lines
        .iter()
        .map(|line| textwrap::fill(line, &options))
        .collect::<Vec<_>>()
        .join("\n")
}

fn decimal(text: &str) -> Option<usize> {
    if text.is_empty() || !text.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    text.parse().ok()
}

pub fn command(explorer: &mut Explorer, text: &str) -> (Vec<String>, bool) {
    let lower = text.to_lowercase();
    let words: Vec<&str> = lower.split_whitespace().collect();
    let Some((&name, mut args)) = words.split_first() else {
        return (vec![], false);
    };
    if matches!(name, "quit" | "q") && args.is_empty() {
        return (vec!["Goodbye.".into()], true);
    }
    if matches!(name, "help" | "h" | "?") && args.is_empty() {
        return (HELP.iter().map(|line| (*line).into()).collect(), false);
    }
    if matches!(name, "show" | "s") && args.is_empty() {
        return (render_state(explorer), false);
    }
    if matches!(name, "next" | "n" | "previous" | "p") && args.is_empty() {
        let forward = matches!(name, "next" | "n");
        let boundary = explorer.cursor() == if forward { explorer.last_seq() } else { 0 };
        if forward {
            explorer.forward();
        } else {
            explorer.previous();
        }
        let mut lines = Vec::new();
        if boundary {
            lines.push(
                if forward {
                    "At end of stream."
                } else {
                    "Before first event."
                }
                .into(),
            );
        }
        lines.extend(render_state(explorer));
        return (lines, false);
    }
    if name == "go" && args.first() == Some(&"to") {
        args = &args[1..];
    }
    if matches!(name, "goto" | "g" | "go") && args.len() == 1 {
        if !args[0].bytes().all(|byte| byte.is_ascii_digit()) {
            return (
                vec!["Sequence must be a nonnegative integer.".into()],
                false,
            );
        }
        let result = decimal(args[0])
            .ok_or_else(|| format!("sequence must be from 0 to {}", explorer.last_seq()))
            .and_then(|seq| explorer.goto(seq).map(|_| ()));
        return match result {
            Ok(()) => (render_state(explorer), false),
            Err(error) => (vec![error], false),
        };
    }
    if matches!(name, "list" | "l") && args.len() <= 1 {
        let radius = if args.is_empty() {
            Some(2)
        } else if args[0].len() <= 3 {
            decimal(args[0]).filter(|&r| r <= 100)
        } else {
            None
        };
        let Some(radius) = radius else {
            return (
                vec!["Radius must be an integer from 0 to 100.".into()],
                false,
            );
        };
        let start = explorer.cursor().saturating_sub(radius).max(1);
        let stop = explorer
            .cursor()
            .saturating_add(radius)
            .min(explorer.last_seq());
        let mut lines = vec!["Nearby events (> = last applied):".into()];
        for seq in start..=stop {
            let state = &explorer.states()[seq];
            lines.push(format!(
                "{} {seq} @ {}: {}",
                if seq == explorer.cursor() { ">" } else { " " },
                state
                    .event
                    .as_ref()
                    .expect("nonzero prefix has event")
                    .timestamp_ns,
                event_text(state)
            ));
        }
        if stop < start {
            lines.push("  No events in this range.".into());
        }
        return (lines, false);
    }
    (
        vec!["Unknown command or arguments. Type help for commands.".into()],
        false,
    )
}
