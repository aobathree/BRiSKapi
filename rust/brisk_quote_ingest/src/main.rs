use anyhow::{Context, Result, ensure};
use brisk_quote_ingest::{Batch, State};
use clap::Parser;
use std::{
    fs,
    io::{BufWriter, Write},
    path::{Path, PathBuf},
    process::Stdio,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::{
    io::{AsyncBufReadExt, BufReader},
    process::Command,
};

#[derive(Parser)]
#[command(about = "Account-free BRiSK historical auction quote replay (never live)")]
struct Args {
    #[arg(long, required_unless_present = "web", conflicts_with = "web")]
    cache: Option<PathBuf>,
    /// Connect directly to the public demo's HTTPS assets (historical data).
    #[arg(long, conflicts_with = "cache")]
    web: bool,
    #[arg(long, default_value = "node")]
    node: String,
    #[arg(long, default_value = concat!(env!("CARGO_MANIFEST_DIR"), "/../../tools/brisk_mock/decoder.cjs"))]
    decoder: PathBuf,
    #[arg(long)]
    codes: Option<String>,
    #[arg(long, default_value_t = 1.0)]
    speed: f64,
    #[arg(long)]
    limit_frames: Option<u64>,
    /// Atomically publish latest state; state ingestion itself is event driven.
    #[arg(long)]
    latest: PathBuf,
    #[arg(long, default_value_t = 20)]
    publish_ms: u64,
    /// Print local latency indicators to stderr; zero disables the display.
    #[arg(long, default_value_t = 1000)]
    status_ms: u64,
    /// Optional lossless frame-batch recorder. The pipe applies backpressure.
    #[arg(long)]
    events: Option<PathBuf>,
}

fn publish(path: &Path, state: &State) -> Result<()> {
    #[derive(serde::Serialize)]
    struct Published<'a> {
        #[serde(flatten)]
        state: &'a State,
        latency: brisk_quote_ingest::latency::LatencySummary,
        published_unix_ms: u128,
    }
    let temp = path.with_extension("json.tmp");
    let mut file = BufWriter::with_capacity(512 * 1024, fs::File::create(&temp)?);
    serde_json::to_writer(
        &mut file,
        &Published {
            state,
            latency: state.latency.summary(),
            published_unix_ms: SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis(),
        },
    )?;
    file.flush()?;
    fs::rename(temp, path)?;
    Ok(())
}

fn display_latency(state: &State) {
    let s = state.latency.summary();
    let ms = |value: Option<f64>| {
        value
            .map(|v| format!("{v:.2} ms"))
            .unwrap_or_else(|| "N/A".into())
    };
    eprintln!(
        "latency [historical mock]: decode p99={}; local receipt-to-state p99={}; replay scheduling p99={}; exchange=N/A (timestamp origin unverified)",
        ms(s.decode.p99_ms),
        ms(s.local_receive_to_state.p99_ms),
        ms(s.replay_schedule_lateness.p99_ms)
    );
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<()> {
    let args = Args::parse();
    ensure!(
        args.speed.is_finite() && args.speed >= 0.0,
        "speed must be >= 0"
    );
    ensure!(args.publish_ms > 0, "publish-ms must be > 0");
    ensure!(
        args.events.as_ref() != Some(&args.latest),
        "events and latest must differ"
    );
    if let Some(parent) = args.latest.parent().filter(|p| !p.as_os_str().is_empty()) {
        fs::create_dir_all(parent)?;
    }
    let mut state = State::default();
    state.invalidate("Starting; no snapshot loaded".into());
    publish(&args.latest, &state)?;
    state = State::default();
    let mut command = Command::new(&args.node);
    command
        .arg(&args.decoder)
        .arg("--speed")
        .arg(args.speed.to_string())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .kill_on_drop(true);
    if args.web {
        command.arg("--web");
    } else if let Some(cache) = args.cache {
        command.arg("--cache").arg(cache);
    }
    if let Some(codes) = args.codes {
        command.arg("--codes").arg(codes);
    }
    if let Some(limit) = args.limit_frames {
        command.arg("--limit-frames").arg(limit.to_string());
    }
    let mut events = args
        .events
        .map(fs::File::create)
        .transpose()
        .context("Create event recorder")?;
    let mut child = command.spawn().context("Start Node/WASM decoder")?;
    let mut lines = BufReader::new(child.stdout.take().unwrap()).lines();
    let mut timer = tokio::time::interval(Duration::from_millis(args.publish_ms));
    timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut status_timer = tokio::time::interval(Duration::from_millis(args.status_ms.max(1)));
    status_timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut ended = false;
    let mut dirty = false;
    let outcome: Result<()> = async {
        loop {
            tokio::select! {
                line = lines.next_line() => {
                    let Some(line) = line? else { break; };
                    let batch: Batch = serde_json::from_str(&line).context("Malformed decoder batch")?;
                    ended = batch.r#type == "end";
                    let first = batch.r#type == "bootstrap";
                    let timing = (batch.decode_ns, batch.received_unix_ms, batch.replay_lateness_ms);
                    let update = batch.r#type == "quotes";
                    state.apply(batch)?;
                    if update {
                        let ready_ms = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis().try_into()?;
                        state.latency.record(timing.0, timing.1, ready_ms, timing.2);
                    }
                    if let Some(file) = &mut events { writeln!(file, "{line}")?; }
                    dirty = true;
                    if first { publish(&args.latest, &state)?; dirty = false; }
                }
                _ = timer.tick(), if dirty => { publish(&args.latest, &state)?; dirty = false; }
                _ = status_timer.tick(), if args.status_ms > 0 && state.batches > 0 => { display_latency(&state); }
                _ = tokio::signal::ctrl_c() => { anyhow::bail!("Replay interrupted"); }
            }
        }
        let status = child.wait().await?;
        ensure!(status.success(), "Decoder exited with {status}");
        ensure!(ended, "Decoder closed before replay completion");
        Ok(())
    }.await;
    if let Err(error) = &outcome {
        state.invalidate(error.to_string());
        let _ = child.kill().await;
    }
    publish(&args.latest, &state)?;
    if args.status_ms > 0 {
        display_latency(&state);
    }
    eprintln!(
        "{} issues; {} batches; {} quotes; source=historical_mock; replay_running={}",
        state.quotes.len(),
        state.batches,
        state.quote_updates,
        state.replay_running
    );
    outcome
}
