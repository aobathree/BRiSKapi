# BRiSK auction updates on the Nautilus v2 bus

The mock can now run with **Node decoder → Rust state in the Nautilus process →
v2 bus → Python strategies**. `BriskMockActor` reads the decoder's stdout as each
frame arrives and applies it through a PyO3 extension built from the existing
Rust collector. It publishes only after the entire batch passes Rust validation.
It does not read the latest-state JSON or run a second Rust process. There is no
20 ms publication timer on this strategy path.

This is a custom-topic data actor, not yet a registered native `DataClientFactory`
or a standard `subscribe_quotes`/`subscribe_data` adapter. The auction fields need
their own event type. The supported entry point is **`LiveNode.run_async()`**:
decoder reads, Rust state and bus publication use that node's event-loop thread.
One actor supplies any number of strategies in that node. There is no Redis
transport or cross-process bus connection in this implementation.

## Build and run

Python 3.12+, Node and Cargo are required. The integration has been verified with
the installed custom `2.0.0rc6` wheel and the public
`2.0.0rc6.dev20261004` wheel. Use an existing v2 environment, or create a dedicated
one and install `requirements-v2.txt` with the public wheel index:

```sh
uv venv --python 3.12 .venv-brisk-v2
uv pip install --python .venv-brisk-v2/bin/python --extra-index-url https://packages.nautechsystems.io/simple --index-strategy unsafe-best-match -r tools/brisk_mock/requirements-v2.txt
```

From the repository root, with your v2 Python (below, `.venv-v2/bin/python`):

```sh
.venv-v2/bin/python tools/brisk_mock/download_mock.py --cache /tmp/brisk-mock-cache
.venv-v2/bin/python tools/brisk_mock/build_native.py --output /tmp/brisk-native
PYTHONPATH=/tmp/brisk-native .venv-v2/bin/python -m tools.brisk_mock.run_nautilus_v2 --cache /tmp/brisk-mock-cache --speed 1 --limit-frames 2000 --strategy-codes 7203,5659 --report /tmp/brisk-v2.json
# Or connect directly to the pinned public demo assets:
PYTHONPATH=/tmp/brisk-native .venv-v2/bin/python -m tools.brisk_mock.run_nautilus_v2 --web --speed 0 --limit-frames 100 --strategy-codes 7203
```

The example strategy receives data and never submits orders. With no `--codes`,
the collector extracts the entire mock master; `--strategy-codes` changes only
the strategy's bus subscriptions. `--codes` limits upstream extraction when that
is preferable for a known trading universe. `--speed 0` runs an unpaced replay.
The native library is built locally and remains outside the repository. Its
`AuctionState` owns the same validated Rust state used by the standalone CLI.

## Topics and subscription

| Topic | Payload | Purpose |
| --- | --- | --- |
| `brisk.auction.<code>.<issue_id>` | `AuctionQuote` | Initial state and changed auction quotes |
| `brisk.frame` | `FeedFrame` | Sequence and stream health, including empty frames |
| `brisk.status` | `FeedStatus` | Starting, running, finishing, completed, failed or stopped |
| `brisk.snapshot.request` | `SnapshotRequest` | Read current Rust state on the node thread |
| `brisk.snapshot.reply.<consumer>` | `AuctionSnapshot` | Requested state, validity and missing codes |

Within a v2 strategy:

```python
from tools.brisk_mock.nautilus_v2 import (
    STATUS_TOPIC, FRAME_TOPIC, SNAPSHOT_REQUEST_TOPIC, SnapshotRequest,
)

def on_start(self):
    self.subscribe_topic("brisk.auction.7203.*", self.on_auction)
    # Use "brisk.auction.*" for the whole market.
    self.subscribe_topic(STATUS_TOPIC, self.on_brisk_status)
    self.subscribe_topic(FRAME_TOPIC, self.on_brisk_frame)
    self.subscribe_topic("brisk.snapshot.reply.my_strategy", self.on_brisk_snapshot)
    self.publish_message(SNAPSHOT_REQUEST_TOPIC, SnapshotRequest(
        "brisk.snapshot.reply.my_strategy", ("7203",)))

def on_auction(self, event):
    price10 = event.fields["indicative_price10"]
    matched_quantity = event.fields["indicative_volume"]
    # Preserve stream_id, seq, issue_id and status when maintaining strategy state.
```

Add `BriskMockActor(BriskMockConfig(cache=Path("/tmp/brisk-mock-cache")))` through
`node.add_actor(...)`, then add your strategy through `node.add_strategy(...)`.
Set `PYTHONPATH` to the native build output before importing the actor. After
`run_async()` returns, await `actor.wait_closed()` to finish child-process cleanup.
The runnable example demonstrates node creation and graceful stop via its handle.

A subscription does not automatically return retained data. A strategy present
at startup receives the bootstrap quotes (`is_snapshot=True`). A late subscriber
requests a snapshot after subscribing to updates and its reply topic. Before
bootstrap, after completion or after failure, snapshots are explicitly invalid.
Missing requested codes are reported rather than silently treated as covered.
Snapshot reads and updates are serialized on the node thread; a snapshot's `seq`
identifies its state boundary. Its per-security source timestamps and frames are
retained. Strategies should use `stream_id` and sequence to avoid double applying
an update already represented in a snapshot.

## Timing, validity and load

`AuctionQuote.ts_event` converts the source clock and trading date to Unix
nanoseconds. It is **not verified as an exchange-origin timestamp**. Individual
quotes can predate the frame's market clock: Toyota's bootstrap quote time is
08:59:59.993551 JST while the market snapshot clock is 08:59:59.999955. `ts_init`
is Rust state-ready time for streaming events. For a requested snapshot it is the
snapshot read time, and original receipt time is unavailable. `received_unix_ms`
is Node's mock-frame receipt time for incremental events. The example measures
receipt-to-strategy and Rust-state-to-strategy separately. Exchange delay stays
unavailable throughout historical replay.

Callbacks run synchronously on the node thread. Keep strategy callbacks short;
slow consumers delay subsequent updates. Async stdout reads and Node's pipe
backpressure preserve frame order without an unbounded application queue. Each
unpaced batch yields to the event loop. A frame line larger than 16 MiB fails the
feed rather than growing memory indefinitely. Decoder stderr is bounded to its
last 64 KiB.

Malformed input, sequence gaps, child failure, EOF without an end marker, data
after end, and inactivity invalidate the state and publish `failed`. Default
startup and frame-idle deadlines are 30 and 5 seconds, respectively; they are
configurable. Stop cancels the decoder task and reaps its child. Snapshots cannot
return stale valid state. Subscribe to `brisk.status` and monitor `brisk.frame`
freshness independently of any one security's update time. A new actor start
requires a fresh bootstrap and assigns a new `stream_id`.

## Verification

```sh
PYTHONPATH=/tmp/brisk-native BRISK_MOCK_CACHE=/tmp/brisk-mock-cache .venv-v2/bin/python -m pytest -q tools/brisk_mock/test_nautilus_v2.py
```

These tests exercise the real Rust extension, real v2 node and real strategy
subscriptions. They cover multi-strategy fanout, identity/filtering, atomic
validation, snapshots, failures and process cleanup. CI installs the pinned
public v2 wheel and runs the same checks. Measurements and full-market delivery
baseline counts are described in the recorder README.

The actor remains restricted to the historical public mock. Live authentication,
current decoder compatibility, final-second exchange delivery and full-depth
export require separate validation. No paper runner or dashboard is deployed or
restarted by this change.
