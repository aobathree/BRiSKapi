# BRiSK API, live feed and shared archive

A pybrisk-style Python API and `brisk` command line for BRiSK auction data:

- **Consume a feed live.** `brisk.connect()` keeps auction state current as frames
  arrive. Use callbacks or iterators, and query any security at any moment.
- **Query recordings.** Load local or archived recordings and ask for state at any
  JST time, full update history, market-wide snapshots and imbalances.
- **Share data automatically.** Complete sessions are contributed to a public
  archive in AWS Tokyo (`ap-northeast-1`) after you agree once. See [PRIVACY.md](PRIVACY.md).

Underneath, Node hosts BRiSK's own hash-pinned WASM decoder without a browser or
Chrome CDP, and a Rust engine maintains the validated auction state.

**Current input is the historical BRiSK Next demo dated 2021-09-27**, replayed at
its recorded pace. It has one pre-open snapshot at 08:59:59.999955 JST and the
opening transition. It is not live market data, a pre-open time series or a
full-depth book. Live market data still requires a Tachibana account/session and
current protocol validation; the feed API is source independent so such a
connector can plug in. Source clocks are preserved; exchange provenance and
exchange delay are unverified.

## Install

Node 22+, Rust 1.92+ and Python 3.12+:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .            # `import brisk` and the `brisk` command
.venv/bin/python -m pip install -e '.[pandas]'  # optional: Table.to_pandas()
cargo build --locked --release --manifest-path rust/brisk_quote_ingest/Cargo.toml
```

Install editable from this checkout: the API uses the repository's decoder, Rust
recorder, archive configuration and reference fingerprints in place. The vendor
decoder/data assets are fetched separately at runtime; none are bundled here.
`brisk ...` is equivalent to `.venv/bin/python brisk_archive.py ...`.

## Live API

```python
import brisk

feed = brisk.connect(web=True, codes=["7203", "6758"])  # returns once the bootstrap is in
toyota = brisk.Ticker("7203")
toyota.quote()             # current auction quote, updated as frames arrive
toyota.auction()           # indicative price/volume and market-order imbalance

feed.on_quote(lambda q: print(q["code"], q["indicative_price"]), codes="6758")
for q in feed.quotes("7203"):        # blocks for each update; ends with the session
    if q["last_price"]:
        print("opened at", q["last_price"], q["time"])
        break

brisk.Market().imbalances(top=10).to_pandas()
feed.wait()                # or feed.close(); `with brisk.connect(...) as feed:` also works
```

`speed=1` replays in real time, `speed=0` as fast as possible; `cache=DIR` uses
downloaded assets instead of `web=True`. Callbacks and iterators first receive
each selected security's current quote, then every update in order. A slow
consumer applies backpressure instead of dropping updates. A live feed holds
current state; pass `history=True` to retain updates for `Ticker.history()`.
`brisk.stream(...)` yields the raw decoder batches.

From any language, read one JSON object per quote update:

```sh
brisk live --web --codes 7203,6758       # --raw for vendor fields; --speed 0 to run unpaced
```

## Recordings and the archive

```python
brisk.recordings(source="historical_mock")   # published recordings; no AWS account needed
brisk.pull("archive/20210927/SHA256")       # download, verify, decode and cache; now the default
brisk.load("recordings/my-session")         # or a local recording (events.jsonl[.gz] or folder)

brisk.Ticker("7203").quote(at="08:59:59.99")               # state at any JST time
brisk.Ticker("7203").history(start="09:00", end="09:01")   # every update in a window
brisk.Market().snapshot(at="09:00:00").to_pandas()
```

| Call | Returns |
| --- | --- |
| `brisk.connect(...)` | Live `Feed`; becomes the default source |
| `Ticker(code).info()` | Name, lot size, tick type, base price and daily limits |
| `Ticker(code).quote(at=None)` | Bid/ask, indicative price/volume, market-order and closing quantities, last trade |
| `Ticker(code).auction(at=None)` | Indicative auction state with `market_order_imbalance` |
| `Ticker(code).history(start, end)` | Every update in order |
| `Market().stocks()` | Master for every security |
| `Market().snapshot(at=None)` | Every security's quote |
| `Market().imbalances(at=None, top=None)` | Securities ranked by absolute market-order imbalance |
| `Market().summary()` | Source, date, coverage and clock range |
| `Feed.quotes(codes)` / `Feed.on_quote(fn, codes)` | Live updates as they arrive |
| `brisk.recordings()` / `brisk.pull()` / `brisk.load()` | Archive listing, verified download, local file |
| `brisk.record(output, web=True, ...)` | A recording made with the Rust recorder, contributed per consent |
| `brisk.consent(...)` | Your contribution choice |

Prices are yen floats, with `None` for the vendor's zero "unavailable" sentinel.
Times are JST `datetime`s on the trading date. Quantities are shares, and side,
flag and status enums are raw vendor values. `raw=True` returns vendor fields
(`*_price10` in tenths of a yen, `*_us` microseconds since JST midnight). Tabular
results are lists of dicts with `.to_pandas()`. Errors are `brisk.BriskError` and
`brisk.NotFoundError`. A whole-market query on a recording parses it once (about
six seconds for the complete 420 MB demo replay); per-security history skips
unrelated batches.

## Automatic contribution

The first time you record or open a live session, the tool shows what would be
shared and asks once; pressing Enter accepts. After that, every clean and complete
session (`brisk record`, `brisk live`, `brisk.connect()`, `brisk.record()`) is
uploaded and published without further prompts. Library calls never prompt: until
you decide, sessions stay local and a warning explains how to decide.

```sh
brisk record --web --output recordings/my-session    # asks once, then contributes automatically
brisk consent                                         # show the saved choice
brisk consent --accept --contributor ALIAS --license CC-BY-4.0
brisk consent --revoke                                # or BRISK_CONTRIBUTE=0, or --no-upload per run
```

A contribution contains the decoded market data, local timing measurements
(including your computer's receipt clock) and your public alias and license.
Your IP address is used only for rate limiting. Published recordings are public
and permanent. [PRIVACY.md](PRIVACY.md) has the details. The default alias is a
random `anon-…` value.

Accepting declares that you may redistribute the recordings under the selected
data license (CC0-1.0 or CC-BY-4.0). Open-source code licensing does not grant
redistribution rights to vendor or exchange data, and the demo is not
automatically licensed for redistribution by this project. The service checks
that declaration and the recording; it cannot establish legal permissions from
the payload. Revoke contribution if you cannot make the declaration.

Partial replays (`--limit-frames`) stay local. Without a decision (or after
revoking) `brisk record` saves `OUTPUT/events.jsonl` locally. To retry a prepared
package, run `brisk upload recordings/my-session`. The explicit per-run
declaration `--contributor ALIAS --license L --redistribution-permitted` still
works for `record` and `package`. Software is MIT licensed.

## Find and pull recordings from the command line

Public downloads do not use AWS credentials:

```sh
brisk list --date 20210927 --source historical_mock
brisk pull archive/20210927/SHA256_FROM_LIST --output recordings/downloaded
```

`pull` checks compressed size, SHA-256, the complete stream, its manifest and the
reference replay, then atomically makes the downloaded directory available. It
contains `manifest.json`, `events.jsonl.gz` and decoded `events.jsonl`. Existing
output folders are never overwritten. To validate/reconstruct a historical
recording with the same Rust state used by the recorder:

```sh
rust/brisk_quote_ingest/target/release/brisk_recording \
  --input recordings/downloaded/events.jsonl --latest /tmp/restored-state.json
```

This reconstructs final state for offline analysis; it does not retimestamp data
or simulate original exchange delivery. The archive also contains a small,
self-authored `synthetic_test` fixture used to verify automatic publication.
Filter by source when selecting market data.

## Archive design and integrity

The public configuration is [archive.json](archive.json). The shared bucket is
`brisk-recordings-honvl-tokyo`, located in Japan. Public access covers published
`archive/` objects and listing that prefix. `incoming/` is private. HTTPS is required.

The client is open source, so the service trusts nothing it sends. Only genuine
replays can be stored, whatever a modified client uploads:

- **Reference replay.** Market content must equal the pinned demo replay.
  [references/historical_mock.json](references/historical_mock.json) holds a
  truncated SHA-256 chain per security (master entry plus every update and the
  batch it arrived in) and a hash of the batch clock timeline; it contains no
  market data. Any subset of securities is accepted, but only complete replays.
- **Canonical bytes.** Each line must be the one canonical JSON encoding of its
  value: sorted keys, issues in ID order, compact separators, no escapes. Extra
  whitespace, duplicate keys, alternative number spellings and other encoding
  variants are rejected. Packaging converts recorder output to this form and
  rounds local timings to microseconds.
- **Exact fields.** Batches, master entries and quotes must have exactly the
  documented fields. Account, session or unknown fields are rejected. Local
  timing values must fall in plausible ranges (decode ≤ 10 s, receipt clock
  monotonic within 24 h, millisecond values with at most microsecond precision,
  consistent paced/unpaced replay), and end-of-stream counts must match.
- **Service-made objects.** The service publishes its own deterministic gzip of
  the validated lines and never the uploaded bytes, so gzip header fields, extra
  members, padding and deflate choices cannot carry data. `synthetic_test`
  accepts only the fixed one-security probe in `archive_schema.py`.
- **Nothing lingers in staging.** The validated upload is deleted immediately
  after validation, whether accepted or rejected. A ticket admits only its first
  object version, and later POSTs with the same upload form are deleted on
  arrival.

What a client still chooses: its alias (64 characters), license and bounded timing
measurements. Those cannot be verified, so some per-recording capacity remains;
it is small and rate limited.

Other properties:

- Tickets last 15 minutes and bind the object key, exact size and encryption.
- Uploads are bound to their first S3 object version. Retries of that event are
  safe; overwriting an upload cannot replace a published dataset.
- Recordings are content addressed: `archive/YYYYMMDD/SHA256/`, where the
  SHA-256 is that of the published gzip. Publication writes the data first and
  the immutable manifest last as the catalog commit marker.
- Limits: 64 MiB compressed, 1 GiB expanded, 16 MiB per JSONL line; four tickets
  per IP/hour (keyed hash, never the address), 64 globally/hour and 5 GiB of
  authorized uploads per UTC day. A complete replay is about 34 MB compressed.
- Ticket metadata in staging expires after two days. Published data is retained.

The supported schema currently accepts `historical_mock` and `synthetic_test`.
Live/raw-wire submissions require an explicit schema/transport extension, including
bootstrap and timestamp provenance, plus a new trust model, because there is no
reference replay to compare live data against. They are not silently treated as
demo data.

## Record locally with the Rust collector

```sh
rust/brisk_quote_ingest/target/release/brisk_quote_ingest \
  --web --latest /tmp/brisk-latest.json --events /tmp/brisk-events.jsonl
```

Select a basket with `--codes 7203,6758,8306`. Omit it to record all 4,131 demo
securities. `--speed 0` replays without pacing. For cache/offline setup, transport,
field definitions and timing, see [recorder documentation](tools/brisk_mock/README.md).
For in-process Rust state and Nautilus v2 subscriptions, see
[the v2 integration guide](tools/brisk_mock/NAUTILUS_V2.md).

## Development and deployment

```sh
.venv/bin/python -m pip install -r requirements-dev.txt -e .
.venv/bin/python -m pytest tests \
  --cov=archive_schema --cov=archive_service --cov=brisk_archive --cov=brisk --cov-fail-under=85
BRISK_MOCK_CACHE=/tmp/brisk-mock-cache .venv/bin/python -m pytest tools/brisk_mock/test_reference.py
BRISK_MOCK_CACHE=/tmp/brisk-mock-cache node --test tools/brisk_mock/*.test.cjs
BRISK_MOCK_CACHE=/tmp/brisk-mock-cache cargo test --locked \
  --manifest-path rust/brisk_quote_ingest/Cargo.toml
```

`tools/brisk_mock/build_reference.py --cache DIR` regenerates the reference; do so
only after re-auditing a changed asset pin. See [contributing](CONTRIBUTING.md) and
[infrastructure](infra/README.md). CI tests the API, archive, reference replay,
official-decoder replay, Rust state and actual Nautilus v2 bus. Archive changes
take effect for contributors only after `infra/deploy.py` runs. `cloud_smoke.py`
is an opt-in real cloud test: it publishes only the synthetic fixture and checks
that tampered content is rejected.

See [third-party attribution](THIRD_PARTY.md) for decoder and data ownership.
