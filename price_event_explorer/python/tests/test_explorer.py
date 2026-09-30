import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / "python"
sys.path.insert(0, str(PYTHON))

from price_event_explorer.cli import command, wrapped
from price_event_explorer.engine import Explorer, InputError, Quote, State, load_states, snapshot_json


class ExplorerTests(unittest.TestCase):
    def fixture(self, name):
        return load_states(ROOT / "examples" / f"{name}.jsonl")

    def temporary_states(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_bytes(data)
            return load_states(path)

    def test_shared_goldens(self):
        for path in sorted((ROOT / "examples").glob("*.jsonl")):
            with self.subTest(path=path.name):
                actual = "".join(snapshot_json(s) + "\n" for s in load_states(path)[1:]).encode()
                self.assertEqual(actual, (ROOT / "golden" / path.name).read_bytes())

    def test_replacement_price_and_size(self):
        states = self.fixture("normal")
        self.assertEqual(states[4].bid, Quote(101, 8))
        self.assertEqual(states[4].ask, states[3].ask)
        edges = self.fixture("reset_edges")
        self.assertEqual(edges[4].ask, Quote(1, 9))

    def test_empty_clear_advances_only_event(self):
        states = self.fixture("reset_edges")
        self.assertEqual(states[1].bid, None)
        self.assertEqual(states[1].cumulative_traded_size, 0)
        self.assertEqual(states[1].seq, 1)
        self.assertIn("unchanged", " ".join(command(Explorer(states), "goto 1")[0]))

    def test_trades_preserve_quotes(self):
        states = self.fixture("normal")
        for seq in (3, 5):
            self.assertEqual((states[seq].bid, states[seq].ask), (states[seq - 1].bid, states[seq - 1].ask))
        self.assertEqual(states[5].cumulative_traded_size, 7)
        self.assertEqual(states[5].trade_count, 2)
        self.assertEqual(states[5].last_trade, Quote(103, 4))

    def test_locked_crossed_and_missing_spread(self):
        states = self.fixture("locked_crossed")
        self.assertEqual((states[2].spread_ticks, states[2].quote_condition), (0, "locked"))
        self.assertEqual((states[4].spread_ticks, states[4].quote_condition), (-2, "crossed"))
        self.assertIsNone(states[6].spread_ticks)
        self.assertIsNone(states[6].quote_condition)

    def test_reset_and_large_integers(self):
        states = self.fixture("reset_edges")
        for seq in (5, 9, 10):
            state = states[seq]
            self.assertEqual((state.bid, state.ask, state.last_trade, state.cumulative_traded_size, state.trade_count),
                             (None, None, None, 0, 0))
        self.assertEqual(states[7].last_trade.price_ticks, 9007199254740993)
        self.assertEqual(states[8].bid.price_ticks, (1 << 63) - 1)

    def test_backward_navigation_all_prefixes(self):
        for name in ("normal", "locked_crossed", "reset_edges"):
            states = self.fixture(name)
            explorer = Explorer(states)
            explorer.goto(len(states) - 1)
            for seq in reversed(range(len(states) - 1)):
                self.assertEqual(explorer.previous(), states[seq])
            self.assertEqual(explorer.previous(), State())
            explorer.goto(len(states) - 1)
            self.assertEqual(explorer.next(), states[-1])
            with self.assertRaises(ValueError):
                explorer.goto(len(states))
            self.assertEqual(explorer.current, states[-1])

    def test_invalid_shared_cases(self):
        cases = json.loads((ROOT / "tests" / "invalid_cases.json").read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case=case["name"]):
                with self.assertRaises(InputError) as caught:
                    self.temporary_states(case["input"].encode("utf-8"))
                self.assertIn(f"line {case['line']}", str(caught.exception))
                self.assertIn(case["contains"], str(caught.exception))

    def test_invalid_utf8_has_physical_line(self):
        with self.assertRaisesRegex(InputError, "line 2"):
            self.temporary_states(b'{"seq":1,"timestamp_ns":0,"type":"reset"}\n\xff\n')

    def test_empty_crlf_and_no_final_newline(self):
        self.assertEqual(self.temporary_states(b""), (State(),))
        data = (ROOT / "examples" / "normal.jsonl").read_bytes()
        self.assertEqual(self.temporary_states(data.replace(b"\n", b"\r\n")), self.fixture("normal"))
        self.assertEqual(self.temporary_states(data.rstrip(b"\n")), self.fixture("normal"))

    def test_signed_timestamps_and_ties(self):
        timestamps = [-(1 << 63), -1, -1, (1 << 63) - 1]
        data = "".join(json.dumps({"seq": i, "timestamp_ns": timestamp, "type": "reset"}) + "\n"
                       for i, timestamp in enumerate(timestamps, 1)).encode()
        self.assertEqual([state.event.timestamp_ns for state in self.temporary_states(data)[1:]], timestamps)

    def test_commands_and_narrow_display(self):
        explorer = Explorer(self.fixture("normal"))
        for text, cursor in (("next", 1), ("n", 2), ("go to 4", 4), ("p", 3), ("goto 0", 0), ("g 7", 7)):
            lines, done = command(explorer, text)
            self.assertFalse(done)
            self.assertEqual(explorer.cursor, cursor)
            self.assertIn("Changes", " ".join(lines))
            self.assertTrue(all(len(line) <= 20 for line in wrapped(lines, 20).splitlines()))
        for bad in ("goto -1", "goto 999", "goto cheese", "goto " + "9" * 5000, "list 101", "list -1", "quit extra", "wat"):
            lines, done = command(explorer, bad)
            self.assertTrue(lines)
            self.assertFalse(done)
            self.assertEqual(explorer.cursor, 7)
        self.assertIn("> 7", "\n".join(command(explorer, "list")[0]))
        self.assertIn("restore", " ".join(command(explorer, "help")[0]))
        self.assertTrue(command(explorer, "quit")[1])

    def test_cli_json_bytes_and_arguments(self):
        for entry in ([sys.executable, "-m", "price_event_explorer"], [sys.executable, str(ROOT / "explore.py")]):
            run = subprocess.run([*entry, str(ROOT / "examples" / "normal.jsonl"), "--all", "--json"],
                                 cwd=PYTHON, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout, (ROOT / "golden" / "normal.jsonl").read_bytes())
        bad = subprocess.run([sys.executable, "-m", "price_event_explorer", "missing", "--json"],
                             cwd=PYTHON, capture_output=True)
        self.assertEqual(bad.returncode, 2)

    def test_cli_validates_before_interactive_and_batch_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            path.write_text('{"seq":1,"timestamp_ns":0,"type":"reset"}\n{}\n')
            for flags in ([], ["--all", "--json"]):
                result = subprocess.run([sys.executable, "-m", "price_event_explorer", str(path), *flags],
                                        cwd=PYTHON, input=b"next\nquit\n", capture_output=True)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b"")
                self.assertIn(b"line 2", result.stderr)

    def test_generator_determinism_and_validity(self):
        spec = importlib.util.spec_from_file_location("generate", ROOT / "generate.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        events = list(module.generate(42, 500))
        self.assertEqual(events, list(module.generate(42, 500)))
        self.assertNotEqual(events, list(module.generate(43, 500)))
        self.assertEqual(events[0], {"seq": 1, "timestamp_ns": 1000413000, "type": "trade", "price_ticks": 9996, "size": 26})
        data = "".join(json.dumps(e) + "\n" for e in events).encode()
        self.assertEqual(len(self.temporary_states(data)), 501)
        self.assertEqual(set(e["type"] for e in events), {"quote_set", "quote_clear", "trade", "reset"})


if __name__ == "__main__":
    unittest.main()
