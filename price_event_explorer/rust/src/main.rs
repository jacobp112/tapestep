use clap::Parser;
use std::io::{self, BufRead, IsTerminal, Write};
use std::path::PathBuf;
use tapestep::{load_states, ui, Explorer};

#[derive(Parser)]
#[command(
    version,
    about = "TapeStep: explore quote and trade events in integer ticks."
)]
struct Args {
    /// UTF-8 JSONL event file
    file: PathBuf,
    /// Print every event state and exit
    #[arg(long)]
    all: bool,
    /// Canonical JSON snapshots (requires --all)
    #[arg(long, requires = "all")]
    json: bool,
    /// Text width, at least 20 columns
    #[arg(long, value_parser = clap::value_parser!(u16).range(20..))]
    width: Option<u16>,
}

fn run(args: Args) -> Result<(), (String, usize)> {
    let width = args.width.map(usize::from).unwrap_or_else(|| {
        terminal_size::terminal_size()
            .map_or(80, |(w, _)| usize::from(w.0))
            .max(20)
    });
    let states = load_states(&args.file).map_err(|error| (error, width))?;
    let mut explorer = Explorer::new(states);
    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout);
    let write_error = |error: io::Error| (format!("output error: {error}"), width);
    if args.all {
        for seq in 1..=explorer.last_seq() {
            explorer.goto(seq).expect("sequence is in range");
            if args.json {
                writeln!(out, "{}", explorer.current().snapshot_json()).map_err(write_error)?;
            } else {
                writeln!(
                    out,
                    "{}\n",
                    ui::wrapped(&ui::render_state(&explorer), width)
                )
                .map_err(write_error)?;
            }
        }
        return out.flush().map_err(write_error);
    }
    // Ctrl-C exits without a panic or platform-specific exception message.
    ctrlc::set_handler(|| {
        let _ = writeln!(io::stdout(), "\nGoodbye.");
        std::process::exit(0);
    })
    .map_err(|error| (format!("cannot install Ctrl-C handler: {error}"), width))?;
    let mut lines = ui::render_state(&explorer);
    lines.push("Type help for commands.".into());
    writeln!(out, "{}", ui::wrapped(&lines, width)).map_err(write_error)?;
    let stdin = io::stdin();
    let terminal = stdin.is_terminal();
    let mut reader = stdin.lock();
    let mut line = String::new();
    loop {
        if terminal {
            write!(out, "step> ").map_err(write_error)?;
        }
        out.flush().map_err(write_error)?;
        line.clear();
        if reader
            .read_line(&mut line)
            .map_err(|error| (format!("cannot read command: {error}"), width))?
            == 0
        {
            writeln!(out, "Goodbye.").map_err(write_error)?;
            return out.flush().map_err(write_error);
        }
        let (lines, done) = ui::command(&mut explorer, &line);
        if !lines.is_empty() {
            writeln!(out, "{}", ui::wrapped(&lines, width)).map_err(write_error)?;
        }
        if done {
            return out.flush().map_err(write_error);
        }
    }
}

fn main() {
    if let Err((error, width)) = run(Args::parse()) {
        eprintln!("{}", ui::wrapped(&[format!("Error: {error}")], width));
        std::process::exit(1);
    }
}
