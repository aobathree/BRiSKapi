//! Validate/reconstruct a saved stream using the collector's Rust state rules.
use anyhow::{Context, Result, ensure};
use brisk_quote_ingest::{Batch, State};
use clap::Parser;
use serde_json::json;
use std::{
    fs::File,
    io::{BufRead, BufReader},
    path::PathBuf,
};

#[derive(Parser)]
struct Args {
    #[arg(long)]
    input: PathBuf,
    #[arg(long)]
    latest: Option<PathBuf>,
}
fn main() -> Result<()> {
    let args = Args::parse();
    let mut state = State::default();
    let mut ended = false;
    for line in BufReader::new(File::open(args.input)?).lines() {
        ensure!(!ended, "Data after end batch");
        let batch: Batch = serde_json::from_str(&line?).context("Invalid recording batch")?;
        ended = batch.r#type == "end";
        state.apply(batch)?;
    }
    ensure!(ended, "Recording must end cleanly");
    if let Some(path) = args.latest {
        serde_json::to_writer(File::create(path)?, &state)?;
    }
    println!(
        "{}",
        json!({"source":state.source,"trading_date":state.trading_date,
        "batches":state.batches,"quote_updates":state.quote_updates,"issues":state.quotes.len()})
    );
    Ok(())
}
