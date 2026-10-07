# briskapi

[English](README.md) | [日本語](README.ja.md)

An unofficial, pybrisk-style Python API and `brisk` command line for BRiSK auction
data. Consume a live feed, query recordings at any point in time, and pull shared
recordings from a public archive. This is an independent project, not affiliated
with or endorsed by BRiSK, Tachibana, SBI, TSE or JPX.

> **Data available today:** the public BRiSK Next demo of 27 September 2021 (one
> pre-open snapshot and the first three minutes after the open), replayed at its
> recorded pace. It is not live market data. Live data needs a Tachibana account
> and is not supported yet.

## Install

Python 3.12+, Node 22+ and Rust 1.92+:

```sh
git clone https://github.com/honvl/briskapi && cd briskapi
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[pandas]'   # `import brisk` and the `brisk` command
cargo build --locked --release --manifest-path rust/brisk_quote_ingest/Cargo.toml   # only for `brisk record`
```

Install from a checkout as shown (editable): the API uses the repository's decoder
and archive settings in place. BRiSK's decoder and demo data are downloaded at
runtime and are not included in the repository.

## Live feed

```python
import brisk

feed = brisk.connect(web=True, codes=["7203", "6758"])   # returns once initial state is in
toyota = brisk.Ticker("7203")
toyota.quote()        # current quote, updated as each frame arrives
toyota.auction()      # indicative price/volume and market-order imbalance

feed.on_quote(lambda q: print(q["code"], q["indicative_price"]), codes="6758")
for q in feed.quotes("7203"):       # one item per update; ends with the session
    if q["last_price"]:
        print("opened at", q["last_price"], q["time"])
        break

brisk.Market().imbalances(top=10).to_pandas()
feed.wait()           # or feed.close(); `with brisk.connect(...) as feed:` also works
```

Options: `web=True` (fetch from the demo site) or `cache=DIR` (assets downloaded
with `python tools/brisk_mock/download_mock.py --cache DIR`), `codes`, `speed`
(`1` real time, `0` as fast as possible) and `history=True` (keep updates for
`Ticker.history()`). Callbacks and iterators first receive each security's
current quote, then every update in order. A slow consumer slows the feed instead
of losing updates.

## Recordings and the archive

```python
brisk.recordings(source="historical_mock")   # published recordings; no AWS account needed
brisk.pull("archive/20210927/SHA256")       # download, verify, decode and cache; becomes the default
brisk.load("recordings/my-session")         # or a local recording (events.jsonl[.gz] or folder)

brisk.Ticker("7203").quote(at="08:59:59.99")               # state at any JST time
brisk.Ticker("7203").history(start="09:00", end="09:01")   # every update in a window
brisk.Market().snapshot(at="09:00:00").to_pandas()
```

## API reference

| Call | Returns |
| --- | --- |
| `brisk.connect(...)` | Live `Feed`; becomes the default source |
| `Ticker(code).info()` | Name, lot size, tick type, base price and daily limits |
| `Ticker(code).quote(at=None)` | Bid/ask, indicative price/volume, market-order and closing quantities, last trade |
| `Ticker(code).auction(at=None)` | Indicative auction state with `market_order_imbalance` (market buy minus sell) |
| `Ticker(code).history(start, end)` | Every update in order |
| `Market().stocks()` | Master for every security |
| `Market().snapshot(at=None)` | Every security's quote |
| `Market().imbalances(at=None, top=None)` | Securities ranked by absolute market-order imbalance |
| `Market().summary()` | Source, date, coverage and clock range |
| `Feed.quotes(codes)` / `Feed.on_quote(fn, codes)` | Live updates as they arrive |
| `brisk.recordings()` / `brisk.pull()` / `brisk.load()` | Archive listing, verified download, local file |
| `brisk.record(output, web=True, ...)` | A recording made with the Rust recorder, shared per your choice |
| `brisk.consent(...)` | Your sharing choice |

Prices are yen floats, with `None` for the vendor's zero "unavailable" value.
Times are JST `datetime`s on the trading date. Quantities are shares; side, flag
and status codes are raw vendor values. `raw=True` returns vendor fields
(`*_price10` in tenths of a yen, `*_us` in microseconds since JST midnight).
Tabular results are lists of dicts with `.to_pandas()`. Errors are
`brisk.BriskError` and `brisk.NotFoundError`. A whole-market query reads a
recording once (about six seconds for the complete 420 MB demo).

## Command line

```sh
brisk live --web --codes 7203,6758          # one JSON object per quote update (--raw for vendor fields)
brisk record --web --output recordings/s1   # record a replay (shared if you agreed)
brisk list --date 20210927 --source historical_mock
brisk pull archive/20210927/SHA256 --output recordings/downloaded
brisk consent [--accept | --revoke]         # show or change sharing
brisk upload recordings/s1                  # retry sharing a recording
```

Each command has `--help`. `pull` verifies everything before writing and never
overwrites an existing folder.

## Sharing recordings

The first time you record or start a live session from the command line, the
tool shows what would be shared and asks once; Enter accepts. After that, every
complete session is uploaded and published automatically. The Python API never
asks: until you decide, sessions stay on your computer.

- **What is shared:** the market data you recorded, local timing measurements
  (including your computer's clock, which shows when you recorded), and a public
  alias (random `anon-…` by default) and license. Your IP address is used only to
  rate limit uploads.
- **Visibility:** published recordings are public and permanent, and you cannot
  delete them yourself. See [PRIVACY.md](PRIVACY.md).
- **Opting out:** `brisk consent --revoke`, `BRISK_CONTRIBUTE=0`, or `--no-upload`
  for one run.
- **License:** accepting declares that you may redistribute the recordings under
  the chosen data license (CC0-1.0 or CC-BY-4.0). This project's open-source
  license gives no rights to vendor or exchange data. If you can't make that
  declaration, turn sharing off.
- Partial replays (`--limit-frames`) and sessions closed early are never shared.

## More documentation

- [ARCHITECTURE.md](ARCHITECTURE.md): how it works, data format, archive integrity and limits
- [PRIVACY.md](PRIVACY.md): privacy policy
- [CONTRIBUTING.md](CONTRIBUTING.md): development and tests
- [tools/brisk_mock/README.md](tools/brisk_mock/README.md): Rust collector, field definitions, timing and latency
- [tools/brisk_mock/NAUTILUS_V2.md](tools/brisk_mock/NAUTILUS_V2.md): NautilusTrader v2 integration
- [infra/README.md](infra/README.md): deploying your own archive
- [THIRD_PARTY.md](THIRD_PARTY.md): decoder and data ownership

Software is MIT licensed.
