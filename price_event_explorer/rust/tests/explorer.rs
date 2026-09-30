use serde_json::Value;
use std::io::{Cursor, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use tapestep::{load_states, read_states, ui, Explorer, Quote, State};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf()
}
fn fixture(name: &str) -> Vec<State> {
    load_states(&root().join("examples").join(format!("{name}.jsonl"))).unwrap()
}

struct TempInput(PathBuf);
impl TempInput {
    fn new(bytes: &[u8]) -> Self {
        static NEXT: AtomicUsize = AtomicUsize::new(0);
        let path = std::env::temp_dir().join(format!(
            "tapestep-test-{}-{}.jsonl",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::write(&path, bytes).unwrap();
        Self(path)
    }
}
impl Drop for TempInput {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

#[test]
fn shared_goldens() {
    for name in ["normal", "locked_crossed", "reset_edges"] {
        let states = fixture(name);
        let actual: String = states[1..]
            .iter()
            .map(|s| s.snapshot_json() + "\n")
            .collect();
        let expected = std::fs::read(root().join("golden").join(format!("{name}.jsonl"))).unwrap();
        assert_eq!(actual.as_bytes(), expected, "{name}");
    }
}

#[test]
fn replacement_price_and_size() {
    let states = fixture("normal");
    assert_eq!(
        states[4].bid,
        Some(Quote {
            price_ticks: 101,
            size: 8
        })
    );
    assert_eq!(states[4].ask, states[3].ask);
    assert_eq!(
        fixture("reset_edges")[4].ask,
        Some(Quote {
            price_ticks: 1,
            size: 9
        })
    );
}

#[test]
fn empty_clear_advances_only_event() {
    let states = fixture("reset_edges");
    assert_eq!(states[1].bid, None);
    assert_eq!(states[1].cumulative_traded_size, 0);
    assert_eq!(states[1].seq(), 1);
    let mut explorer = Explorer::new(states);
    assert!(ui::command(&mut explorer, "goto 1")
        .0
        .join(" ")
        .contains("unchanged"));
}

#[test]
fn trades_preserve_quotes() {
    let states = fixture("normal");
    for seq in [3, 5] {
        assert_eq!(
            (states[seq].bid, states[seq].ask),
            (states[seq - 1].bid, states[seq - 1].ask)
        );
    }
    assert_eq!(states[5].cumulative_traded_size, 7);
    assert_eq!(states[5].trade_count, 2);
    assert_eq!(
        states[5].last_trade,
        Some(Quote {
            price_ticks: 103,
            size: 4
        })
    );
}

#[test]
fn locked_crossed_and_missing_spread() {
    let states = fixture("locked_crossed");
    assert_eq!(
        (states[2].spread_ticks(), states[2].quote_condition()),
        (Some(0), Some("locked"))
    );
    assert_eq!(
        (states[4].spread_ticks(), states[4].quote_condition()),
        (Some(-2), Some("crossed"))
    );
    assert_eq!(states[6].spread_ticks(), None);
    assert_eq!(states[6].quote_condition(), None);
}

#[test]
fn reset_and_large_integers() {
    let states = fixture("reset_edges");
    for seq in [5, 9, 10] {
        let s = &states[seq];
        assert_eq!(
            (
                s.bid,
                s.ask,
                s.last_trade,
                s.cumulative_traded_size,
                s.trade_count
            ),
            (None, None, None, 0, 0)
        );
    }
    assert_eq!(states[7].last_trade.unwrap().price_ticks, 9007199254740993);
    assert_eq!(states[8].bid.unwrap().price_ticks, i64::MAX);
}

#[test]
fn backward_navigation_all_prefixes() {
    for name in ["normal", "locked_crossed", "reset_edges"] {
        let states = fixture(name);
        let mut explorer = Explorer::new(states.clone());
        explorer.goto(states.len() - 1).unwrap();
        for seq in (0..states.len() - 1).rev() {
            assert_eq!(explorer.previous(), &states[seq]);
        }
        assert_eq!(explorer.previous(), &State::default());
        explorer.goto(states.len() - 1).unwrap();
        assert_eq!(explorer.forward(), states.last().unwrap());
        assert!(explorer.goto(states.len()).is_err());
        assert_eq!(explorer.current(), states.last().unwrap());
    }
}

#[test]
fn invalid_shared_cases() {
    let cases: Value =
        serde_json::from_str(include_str!("../../tests/invalid_cases.json")).unwrap();
    for case in cases.as_array().unwrap() {
        let result = read_states(
            Cursor::new(case["input"].as_str().unwrap().as_bytes()),
            "case.jsonl",
        );
        let error = result.unwrap_err();
        assert!(
            error.contains(&format!("line {}", case["line"])),
            "{}: {error}",
            case["name"]
        );
        assert!(
            error.contains(case["contains"].as_str().unwrap()),
            "{}: {error}",
            case["name"]
        );
    }
}

#[test]
fn invalid_utf8_has_physical_line() {
    let error = read_states(
        Cursor::new(b"{\"seq\":1,\"timestamp_ns\":0,\"type\":\"reset\"}\n\xff\n"),
        "bad",
    )
    .unwrap_err();
    assert!(error.contains("line 2"));
    assert!(error.contains("UTF-8"));
}

#[test]
fn empty_crlf_and_no_final_newline() {
    assert_eq!(
        read_states(Cursor::new(b""), "empty").unwrap(),
        vec![State::default()]
    );
    let data = std::fs::read_to_string(root().join("examples/normal.jsonl")).unwrap();
    assert_eq!(
        read_states(Cursor::new(data.replace('\n', "\r\n").as_bytes()), "crlf").unwrap(),
        fixture("normal")
    );
    assert_eq!(
        read_states(Cursor::new(data.trim_end_matches('\n').as_bytes()), "tail").unwrap(),
        fixture("normal")
    );
}

#[test]
fn commands_and_narrow_display() {
    let mut explorer = Explorer::new(fixture("normal"));
    for (text, cursor) in [
        ("next", 1),
        ("n", 2),
        ("go to 4", 4),
        ("p", 3),
        ("goto 0", 0),
        ("g 7", 7),
    ] {
        let (lines, done) = ui::command(&mut explorer, text);
        assert!(!done);
        assert_eq!(explorer.cursor(), cursor);
        assert!(lines.join(" ").contains("Changes"));
        assert!(ui::wrapped(&lines, 20)
            .lines()
            .all(|line| line.chars().count() <= 20));
    }
    for bad in [
        "goto -1",
        "goto 999",
        "goto cheese",
        "list 101",
        "list -1",
        "quit extra",
        "wat",
    ] {
        let (lines, done) = ui::command(&mut explorer, bad);
        assert!(!lines.is_empty());
        assert!(!done);
        assert_eq!(explorer.cursor(), 7);
    }
    assert!(
        !ui::command(&mut explorer, &format!("goto {}", "9".repeat(5000)))
            .0
            .is_empty()
    );
    assert_eq!(explorer.cursor(), 7);
    assert!(ui::command(&mut explorer, "list")
        .0
        .join("\n")
        .contains("> 7"));
    assert!(ui::command(&mut explorer, "help")
        .0
        .join(" ")
        .contains("restore"));
    assert!(ui::command(&mut explorer, "quit").1);
}

#[test]
fn signed_timestamps_and_ties() {
    let timestamps = [i64::MIN, -1, -1, i64::MAX];
    let data: String = timestamps
        .iter()
        .enumerate()
        .map(|(index, timestamp)| {
            format!(
                "{{\"seq\":{},\"timestamp_ns\":{timestamp},\"type\":\"reset\"}}\n",
                index + 1
            )
        })
        .collect();
    let states = read_states(Cursor::new(data.as_bytes()), "signed").unwrap();
    let actual: Vec<i64> = states[1..]
        .iter()
        .map(|s| s.event.as_ref().unwrap().timestamp_ns)
        .collect();
    assert_eq!(actual, timestamps);
}

#[test]
fn cli_json_bytes_and_arguments() {
    let output = Command::new(env!("CARGO_BIN_EXE_tapestep"))
        .arg(root().join("examples/normal.jsonl"))
        .args(["--all", "--json"])
        .output()
        .unwrap();
    assert!(output.status.success(), "{:?}", output.stderr);
    assert_eq!(
        output.stdout,
        std::fs::read(root().join("golden/normal.jsonl")).unwrap()
    );
    for flags in [vec!["--json"], vec!["--width", "19"]] {
        let bad = Command::new(env!("CARGO_BIN_EXE_tapestep"))
            .arg("missing")
            .args(flags)
            .output()
            .unwrap();
        assert_eq!(bad.status.code(), Some(2));
    }
}

#[test]
fn cli_validates_before_interactive_and_batch_output() {
    let temp = TempInput::new(b"{\"seq\":1,\"timestamp_ns\":0,\"type\":\"reset\"}\n{}\n");
    for flags in [vec![], vec!["--all", "--json"]] {
        let result = Command::new(env!("CARGO_BIN_EXE_tapestep"))
            .arg(&temp.0)
            .args(flags)
            .args(["--width", "200"])
            .output()
            .unwrap();
        assert_eq!(result.status.code(), Some(1));
        assert!(result.stdout.is_empty());
        assert!(String::from_utf8(result.stderr).unwrap().contains("line 2"));
    }
}

#[test]
fn cli_script_and_eof() {
    let mut child = Command::new(env!("CARGO_BIN_EXE_tapestep"))
        .arg(root().join("examples/reset_edges.jsonl"))
        .args(["--width", "20"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(b"goto 8\nprevious\nshow\nlist\nhelp\ngoto 0\n")
        .unwrap();
    let output = child.wait_with_output().unwrap();
    assert!(output.status.success());
    assert!(output.stderr.is_empty());
    let text = String::from_utf8(output.stdout).unwrap();
    assert!(text.lines().all(|line| line.chars().count() <= 20));
    assert!(text.ends_with("Goodbye.\n"));
    assert!(text.contains("9007199254740993"));
    assert!(text
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .contains("before first event"));
}
