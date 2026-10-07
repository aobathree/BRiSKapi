use serde_json::{Value, json};
use std::{
    fs,
    path::PathBuf,
    process::Command,
    sync::atomic::{AtomicU64, Ordering},
    time::{SystemTime, UNIX_EPOCH},
};

static NEXT_TEMP_ID: AtomicU64 = AtomicU64::new(0);

fn run(records: &[Value], exit: i32, extra: &[&str]) -> (bool, Value, String) {
    run_mode(records, exit, extra, false)
}

fn run_mode(records: &[Value], exit: i32, extra: &[&str], web: bool) -> (bool, Value, String) {
    let dir = std::env::temp_dir().join(format!(
        "brisk-cli-{}-{}",
        std::process::id(),
        NEXT_TEMP_ID.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&dir).unwrap();
    let decoder = dir.join("fake.cjs");
    let mut script = String::new();
    if web {
        script += "if (!process.argv.includes(\"--web\") || process.argv.includes(\"--cache\")) throw Error(\"wrong transport\");\n";
    }
    for record in records {
        script += &format!("console.log(JSON.stringify({record}));\n");
    }
    script += &format!("process.exitCode = {exit};\n");
    fs::write(&decoder, script).unwrap();
    let latest = dir.join("latest.json");
    let mut command = Command::new(env!("CARGO_BIN_EXE_brisk_quote_ingest"));
    if web {
        command.arg("--web");
    } else {
        command.args(["--cache", "/unused"]);
    }
    let output = command
        .args(["--speed", "0", "--latest"])
        .arg(&latest)
        .arg("--decoder")
        .arg(&decoder)
        .arg("--events")
        .arg(dir.join("events.jsonl"))
        .args(extra)
        .output()
        .unwrap();
    let state = serde_json::from_slice(&fs::read(&latest).unwrap()).unwrap();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    fs::remove_dir_all(dir).unwrap();
    (output.status.success(), state, stderr)
}

fn bootstrap() -> Value {
    json!({"type":"bootstrap","seq":0,"source":"historical_mock","trading_date":"20210927",
        "source_time_us":100,"market_issue_count":1,"master":[{"issue_id":0,"code":"7203"}],
        "quotes":[{"issue_id":0,"code":"7203","frame":1,"source_time_us":100,"indicative_price10":101500}]})
}

#[test]
fn completion_records_latest_and_marks_replay_stopped() {
    let (ok, state, stderr) = run(
        &[
            bootstrap(),
            json!({"type":"end","seq":1,"source_time_us":100}),
        ],
        0,
        &[],
    );
    assert!(ok, "{stderr}");
    assert_eq!(state["quotes"]["0"]["indicative_price10"], 101500);
    assert_eq!(state["replay_running"], false);
    assert_eq!(state["failure"], Value::Null);
    assert_eq!(state["latency"]["exchange_delay_ms"], Value::Null);
    assert!(stderr.contains("exchange=N/A"));
}

#[test]
fn records_local_timing_without_treating_source_time_as_exchange_delay() {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_millis();
    let update = json!({"type":"quotes","seq":1,"source_time_us":101,
        "quotes":[],"decode_ns":2_500_000,"received_unix_ms":now,"replay_lateness_ms":7.5});
    let (ok, state, stderr) = run(
        &[
            bootstrap(),
            update,
            json!({"type":"end","seq":2,"source_time_us":101}),
        ],
        0,
        &["--status-ms", "0"],
    );
    assert!(ok, "{stderr}");
    assert!(!stderr.contains("latency ["));
    assert_eq!(state["latency"]["exchange_delay_ms"], Value::Null);
    assert_eq!(state["latency"]["decode"]["p99_ms"], 2.5);
    assert_eq!(
        state["latency"]["local_receive_to_state"]["session_samples"],
        1
    );
    assert_eq!(state["latency"]["replay_schedule_lateness"]["p99_ms"], 7.5);
}

#[test]
fn eof_failure_malformed_records_and_gaps_invalidate() {
    for (records, exit) in [
        (vec![bootstrap()], 0),
        (vec![bootstrap()], 1),
        (
            vec![
                bootstrap(),
                json!({"type":"quotes","seq":2,"source_time_us":100}),
            ],
            0,
        ),
        (vec![json!({"bad":true})], 0),
    ] {
        let (ok, state, _) = run(&records, exit, &[]);
        assert!(!ok);
        assert_eq!(state["replay_running"], false);
        assert!(state["failure"].is_string());
    }
}

#[test]
fn public_mock_runs_through_rust_state() {
    let Some(cache) = std::env::var_os("BRISK_MOCK_CACHE") else {
        return;
    };
    let dir: PathBuf = std::env::temp_dir().join(format!("brisk-real-{}", std::process::id()));
    fs::create_dir_all(&dir).unwrap();
    let latest = dir.join("latest.json");
    let output = Command::new(env!("CARGO_BIN_EXE_brisk_quote_ingest"))
        .arg("--cache")
        .arg(cache)
        .args([
            "--codes",
            "7203,5659",
            "--speed",
            "0",
            "--limit-frames",
            "100",
            "--latest",
        ])
        .arg(&latest)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let state: Value = serde_json::from_slice(&fs::read(&latest).unwrap()).unwrap();
    assert_eq!(state["quotes"].as_object().unwrap().len(), 2);
    assert_eq!(state["batches"], 101);
    assert_eq!(state["failure"], Value::Null);
    assert_eq!(state["latency"]["exchange_delay_ms"], Value::Null);
    assert_eq!(state["latency"]["decode"]["session_samples"], 99);
    assert_eq!(
        state["latency"]["local_receive_to_state"]["session_samples"],
        99
    );
    assert_eq!(
        state["latency"]["replay_schedule_lateness"]["p99_ms"],
        Value::Null
    );
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn web_mode_forwards_transport_and_preserves_historical_provenance() {
    let mut first = bootstrap();
    first["input_transport"] = json!({"kind":"https_recorded_assets", "origin":"https://next-demo.brisk.jp/", "asset_fetch_ms":25.0});
    let (ok, state, stderr) = run_mode(
        &[first, json!({"type":"end","seq":1,"source_time_us":100})],
        0,
        &[],
        true,
    );
    assert!(ok, "{stderr}");
    assert_eq!(state["source"], "historical_mock");
    assert_eq!(state["input_transport"]["kind"], "https_recorded_assets");
    assert_eq!(state["input_transport"]["asset_fetch_ms"], 25.0);
    assert_eq!(state["latency"]["exchange_delay_ms"], Value::Null);
}

#[test]
fn rejects_missing_or_conflicting_transport_before_startup() {
    for args in [
        vec!["--latest", "/unused"],
        vec!["--web", "--cache", "/unused", "--latest", "/unused"],
    ] {
        let output = Command::new(env!("CARGO_BIN_EXE_brisk_quote_ingest"))
            .args(args)
            .output()
            .unwrap();
        assert!(!output.status.success());
        assert!(String::from_utf8_lossy(&output.stderr).contains("error:"));
    }
}

#[test]
fn web_download_failure_never_publishes_a_running_book() {
    let (ok, state, _) = run_mode(&[], 1, &[], true);
    assert!(!ok);
    assert_eq!(state["replay_running"], false);
    assert!(state["failure"].is_string());
    assert!(state["quotes"].as_object().unwrap().is_empty());
}

#[test]
fn saved_recording_reconstructs_state_and_rejects_incomplete_or_trailing_data() {
    let dir = std::env::temp_dir().join(format!("brisk-saved-{}", std::process::id()));
    fs::create_dir_all(&dir).unwrap();
    let input = dir.join("events.jsonl");
    let latest = dir.join("latest.json");
    let complete = format!(
        "{}\n{}\n",
        bootstrap(),
        json!({"type":"end","seq":1,"source_time_us":100})
    );
    fs::write(&input, &complete).unwrap();
    let result = Command::new(env!("CARGO_BIN_EXE_brisk_recording"))
        .arg("--input")
        .arg(&input)
        .arg("--latest")
        .arg(&latest)
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    let state: Value = serde_json::from_slice(&fs::read(&latest).unwrap()).unwrap();
    assert_eq!(state["quotes"]["0"]["indicative_price10"], 101500);
    for broken in [
        format!("{}\n", bootstrap()),
        format!("{complete}{}\n", bootstrap()),
        "invalid\n".into(),
    ] {
        fs::write(&input, broken).unwrap();
        assert!(
            !Command::new(env!("CARGO_BIN_EXE_brisk_recording"))
                .arg("--input")
                .arg(&input)
                .output()
                .unwrap()
                .status
                .success()
        );
    }
    fs::remove_dir_all(dir).unwrap();
}
