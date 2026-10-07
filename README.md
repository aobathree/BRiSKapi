# BRiSK recorder

A Node/WASM recorder and Rust auction-state engine, with an **automatic shared
recording archive in AWS Tokyo (`ap-northeast-1`)**. The recorder hosts BRiSK's own
hash-pinned decoder without a browser or Chrome CDP. It preserves frame batches,
security identities, auction quotes and local timing measurements.

**Current input is the historical BRiSK Next demo dated 2021-09-27.** It has one
pre-open snapshot at 08:59:59.999955 JST and the opening transition. It is not a
live connector, pre-open time series or full-depth book exporter. A Tachibana
account/session and current protocol validation are still required for live data.
Source clocks are preserved; exchange provenance and exchange delay are unverified.

## Install

Node 22+, Rust 1.92+ and Python 3.12+:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cargo build --locked --release --manifest-path rust/brisk_quote_ingest/Cargo.toml
```

The vendor decoder/data assets are fetched separately; none are bundled here.

## Record locally

```sh
rust/brisk_quote_ingest/target/release/brisk_quote_ingest \
  --web --latest /tmp/brisk-latest.json --events /tmp/brisk-events.jsonl
```

Select a basket with `--codes 7203,6758,8306`. Omit it to record all 4,131 demo
securities. `--speed 0` replays without pacing. For cache/offline setup, transport,
field definitions and timing, see [recorder documentation](tools/brisk_mock/README.md).
For in-process Rust state and Nautilus v2 subscriptions, see
[the v2 integration guide](tools/brisk_mock/NAUTILUS_V2.md).

## Automatically contribute

No AWS account, credentials, upload-link request or maintainer approval is needed.
The API issues an expiring upload ticket, then S3 triggers automatic validation
and publication. The CLI waits for the result:

```sh
.venv/bin/python brisk_archive.py package \
  --events /tmp/brisk-events.jsonl --output recordings/my-session \
  --contributor YOUR_PUBLIC_ALIAS --license CC-BY-4.0 \
  --redistribution-permitted --upload
```

Use `--redistribution-permitted` only for data you may redistribute under the
selected data license. Open-source code licensing does not grant redistribution
rights to vendor or exchange data. The service checks that declaration and the
recording's structure; it cannot establish legal permissions from the payload.
The demo is not automatically licensed for redistribution by this project.

For data you have permission to share, the combined record/package/upload command is:

```sh
.venv/bin/python brisk_archive.py record --web \
  --output recordings/my-session --contributor YOUR_PUBLIC_ALIAS \
  --license CC-BY-4.0 --redistribution-permitted --upload
```

`--cache DIR` is an alternative to `--web`. Upload happens after the recorder
finishes a clean segment. Interrupted/incomplete streams are rejected. To retry
a prepared package, run `brisk_archive.py upload recordings/my-session`.
Data licenses supported: CC0-1.0 and CC-BY-4.0. Attribution uses the public alias
in the immutable manifest. Software is MIT licensed.

## Find and pull recordings

Public downloads do not use AWS credentials:

```sh
.venv/bin/python brisk_archive.py list --date 20210927 --source historical_mock
.venv/bin/python brisk_archive.py pull archive/20210927/SHA256_FROM_LIST \
  --output recordings/downloaded
```

`pull` checks compressed size, SHA-256, the complete stream and its manifest,
then atomically makes the downloaded directory available. It contains
`manifest.json`, `events.jsonl.gz` and decoded `events.jsonl`. Existing output
folders are never overwritten. To validate/reconstruct a historical recording
with the same Rust state used by the recorder:

```sh
rust/brisk_quote_ingest/target/release/brisk_recording \
  --input recordings/downloaded/events.jsonl --latest /tmp/restored-state.json
```

This reconstructs final state for offline analysis; it does not retimestamp data
or simulate original exchange delivery. The archive also contains a small,
self-authored `synthetic_test` fixture used to verify automatic publication.
Filter by source when selecting market data.

## Archive design

The public configuration is [archive.json](archive.json). The shared bucket is
`brisk-recordings-honvl-tokyo`, located in Japan. Public access covers published
`archive/` objects and listing that prefix. `incoming/` is private. HTTPS is required.

- Tickets last 15 minutes and bind the object key, exact size and encryption.
- Automatic validation checks bootstrap/master coverage, security identity,
  sequence continuity, monotonic frame/source clocks, clean completion, schema,
  SHA-256, and manifest agreement. Unknown/account fields are rejected.
- Uploads are bound to their first S3 object version. Retries of that event are
  safe; overwriting an upload cannot replace a published dataset.
- Recordings are content addressed: `archive/YYYYMMDD/SHA256/`. Publication writes
  the data first and the immutable manifest last as the catalog commit marker.
- Limits: 64 MiB compressed, 1 GiB expanded, 16 MiB per JSONL line; four tickets
  per IP/hour, 64 globally/hour and 5 GiB of authorized uploads per UTC day.
- Private staging expires after two days; old staging versions after one day.
  Published data is retained. Rejected submissions do not enter the catalog.

The supported schema currently accepts `historical_mock` and `synthetic_test`.
Live/raw-wire submissions require an explicit schema/transport extension, including
bootstrap and timestamp provenance; they are not silently treated as demo data.

## Development and deployment

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/test_archive.py \
  --cov=archive_schema --cov=archive_service --cov=brisk_archive --cov-fail-under=85
BRISK_MOCK_CACHE=/tmp/brisk-mock-cache node --test tools/brisk_mock/*.test.cjs
BRISK_MOCK_CACHE=/tmp/brisk-mock-cache cargo test --locked \
  --manifest-path rust/brisk_quote_ingest/Cargo.toml
```

See [contributing](CONTRIBUTING.md) and [infrastructure](infra/README.md).
CI tests the archive, official-decoder replay, Rust state and actual Nautilus v2 bus.
`cloud_smoke.py` is an opt-in real cloud test; it publishes only a synthetic fixture.

See [third-party attribution](THIRD_PARTY.md) for decoder and data ownership.
