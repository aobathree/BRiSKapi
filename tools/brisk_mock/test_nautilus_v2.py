"""Real Rust extension and v2 bus tests; no mocked message bus."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import threading
import subprocess
import sys

import pytest
import brisk_state_native
from nautilus_trader.common import LoggerConfig, LogLevel
from nautilus_trader.live import LiveNode, LiveNodeConfig
from nautilus_trader.model import StrategyId, TraderId
from nautilus_trader.trading import Strategy, StrategyConfig

from tools.brisk_mock.nautilus_v2 import (
    AUCTION_TOPIC, FRAME_TOPIC, STATUS_TOPIC, SNAPSHOT_REQUEST_TOPIC,
    BriskMockActor, BriskMockConfig, SnapshotRequest,
)
from tools.brisk_mock.run_nautilus_v2 import run


def bootstrap() -> dict:
    quotes = [{"issue_id": i, "code": code, "frame": 10, "source_time_us": 100,
               "indicative_price10": 101500, "market_buy_quantity": 2**60 + i,
               "flag": True, "optional": None}
              for i, code in enumerate(["9434", "94345", "9434"])]
    return {"type": "bootstrap", "seq": 0, "source": "historical_mock",
            "source_time_us": 100, "trading_date": "20210927", "market_issue_count": 3,
            "master": [{"issue_id": q["issue_id"], "code": q["code"]} for q in quotes],
            "quotes": quotes}


def update(seq: int = 1) -> dict:
    return {"type": "quotes", "seq": seq, "source_time_us": 101,
            "quotes": [{"issue_id": 0, "code": "9434", "frame": 12,
                        "source_time_us": 101, "indicative_price10": 101600}],
            "decode_ns": 2_500_000}


def test_native_atomic_validation_types_and_snapshot_lifecycle():
    state = brisk_state_native.AuctionState()
    with pytest.raises(ValueError, match="No valid"):
        state.snapshot()
    batch = state.apply_json(json.dumps(bootstrap()))
    assert batch["quotes"][1]["market_buy_quantity"] == 2**60 + 1
    assert batch["quotes"][0]["flag"] is True
    assert batch["quotes"][0]["optional"] is None
    assert len(state.snapshot(["9434"])) == 2
    assert state.snapshot([]) == []
    state.apply_json(json.dumps(update()))
    assert state.snapshot(["9434"])[0]["frame"] == 12
    bad = update(2)
    bad["quotes"].append({"issue_id": 99, "code": "BAD", "frame": 12,
                          "source_time_us": 101})
    with pytest.raises(ValueError, match="Unknown issue"):
        state.apply_json(json.dumps(bad))
    assert state.status()["quote_updates"] == 4
    assert state.status()["latency"]["exchange_delay_ms"] is None
    with pytest.raises(ValueError, match="No valid"):
        state.snapshot()
    with pytest.raises(ValueError):
        state.apply_json("not json")


def test_native_nested_fields_and_transport_provenance():
    state = brisk_state_native.AuctionState()
    record = bootstrap()
    record["input_transport"] = {"kind": "https_recorded_assets", "asset_fetch_ms": 12.5}
    record["quotes"][0]["nested"] = {"values": [-4, 1.25, "auction"]}
    batch = state.apply_json(json.dumps(record))
    assert batch["quotes"][0]["nested"] == {"values": [-4, 1.25, "auction"]}
    assert state.status()["input_transport"]["kind"] == "https_recorded_assets"
    state.invalidate("lost connection")
    assert state.status()["failure"] == "lost connection"


@pytest.mark.parametrize("kwargs", [{}, {"cache": Path("/tmp"), "web": True},
    {"web": True, "speed": -1}, {"web": True, "speed": float("nan")},
    {"web": True, "limit_frames": 0}, {"web": True, "idle_timeout_seconds": 0}])
def test_configuration_rejects_ambiguous_source_and_invalid_pacing(kwargs):
    with pytest.raises(ValueError):
        BriskMockConfig(**kwargs)


class Consumer(Strategy):
    def __new__(cls, name: str, pattern: str):
        return super().__new__(cls)

    def __init__(self, name: str, pattern: str):
        super().__init__(StrategyConfig(strategy_id=StrategyId(name)))
        self.pattern = pattern
        self.events = []
        self.frames = []
        self.statuses = []
        self.snapshots = []
        self.thread_ids = set()
        self.reply_topic = f"brisk.snapshot.reply.{name}"
        self.actor_to_stop = None

    def on_start(self):
        self.subscribe_topic(self.pattern, self.on_auction)
        self.subscribe_topic(FRAME_TOPIC, self.on_frame)
        self.subscribe_topic(STATUS_TOPIC, self.statuses.append)
        self.subscribe_topic(self.reply_topic, self.snapshots.append)
        self.publish_message(SNAPSHOT_REQUEST_TOPIC, SnapshotRequest(self.reply_topic))

    def on_auction(self, event):
        self.thread_ids.add(threading.get_ident())
        self.events.append(event)
        if self.actor_to_stop is not None:
            self.actor_to_stop.stop()

    def on_frame(self, frame):
        self.frames.append(frame)
        if frame.kind in ("quotes", "end"):
            self.publish_message(SNAPSHOT_REQUEST_TOPIC,
                                 SnapshotRequest(self.reply_topic, ("9434", "94345")))

    def on_stop(self):
        for topic, callback in [
            (self.pattern, self.on_auction), (FRAME_TOPIC, self.on_frame),
            (STATUS_TOPIC, self.statuses.append), (self.reply_topic, self.snapshots.append),
        ]:
            self.unsubscribe_topic(topic, callback)


async def exercise(config, *, stop_after=None, stop_in_callback=False):
    node = LiveNode.build("BRISK-TEST", LiveNodeConfig(
        trader_id=TraderId("BRISK-TEST-001"), timeout_reconciliation_secs=0,
        delay_post_stop_secs=0, logging=LoggerConfig(stdout_level=LogLevel.ERROR)))
    actor = BriskMockActor(config)
    all_names = Consumer("ALL-001", f"{AUCTION_TOPIC}.*")
    selected = Consumer("SELECT-002", f"{AUCTION_TOPIC}.94345.*")
    if stop_in_callback:
        all_names.actor_to_stop = actor
    node.add_actor(actor)
    node.add_strategy(all_names)
    node.add_strategy(selected)
    handle = node.handle()

    async def finish():
        while actor._task is None:
            await asyncio.sleep(0.001)
        if stop_after is not None:
            await asyncio.sleep(stop_after)
        else:
            await actor.wait_closed()
        handle.stop()

    task = asyncio.create_task(finish())
    try:
        await asyncio.wait_for(node.run_async(), 10)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await actor.wait_closed()
        node.dispose()
    return actor, all_names, selected


def fake_decoder(tmp_path, records, *, exit_code=0, hang=False):
    path = tmp_path / "decoder.cjs"
    script = "\n".join(f"console.log({json.dumps(json.dumps(r))});" for r in records)
    script += ("\nsetInterval(() => {}, 1000);" if hang
               else f"\nprocess.exitCode = {exit_code};")
    path.write_text(script)
    return BriskMockConfig(cache=tmp_path, decoder=path, speed=0,
                           idle_timeout_seconds=0.2, startup_timeout_seconds=1)


def test_real_v2_bus_fanout_filters_identity_and_late_snapshots(tmp_path):
    config = fake_decoder(tmp_path, [bootstrap(), update(),
                          {"type": "end", "seq": 2, "source_time_us": 101}])
    actor, all_names, selected = asyncio.run(exercise(config))
    assert actor.failure is None and actor.phase == "completed"
    assert len(all_names.events) == 4 and len(selected.events) == 1
    assert {e.issue_id for e in all_names.events} == {0, 1, 2}
    assert {e.topic for e in all_names.events} == {
        "brisk.auction.9434.0", "brisk.auction.94345.1", "brisk.auction.9434.2"}
    assert [f.seq for f in all_names.frames] == [0, 1, 2]
    assert all_names.thread_ids == {threading.get_ident()}
    assert not all_names.snapshots[0].valid  # before bootstrap
    snapshot = all_names.snapshots[1]
    assert snapshot.valid and snapshot.seq == 1 and len(snapshot.quotes) == 3
    assert snapshot.quotes[0].frame == 12
    assert all(q.received_unix_ms is None for q in snapshot.quotes)
    assert all(e.stream_id == actor.stream_id for e in snapshot.quotes)
    assert all_names.events[0].ts_event == 1632668400000100000
    with pytest.raises(TypeError):
        all_names.events[0].fields["indicative_price10"] = 0
    assert all_names.statuses[-1].phase == "completed"
    assert not all_names.snapshots[-1].valid  # end-frame callback must get a reply
    assert not actor.snapshot().valid


@pytest.mark.parametrize("failure", ["gap", "malformed", "eof", "exit", "stall", "after_end"])
def test_feed_failure_invalidates_and_prevents_unvalidated_delivery(tmp_path, failure):
    records = [bootstrap()]
    if failure == "gap":
        records.append(update(2))
    elif failure == "malformed":
        records.append({"bad": True})
    elif failure in ("exit", "after_end"):
        records.append({"type": "end", "seq": 1, "source_time_us": 100})
        if failure == "after_end":
            records.append(update(2))
    config = fake_decoder(tmp_path, records, exit_code=1 if failure == "exit" else 0,
                          hang=failure == "stall")
    actor, consumer, _ = asyncio.run(exercise(config))
    assert actor.failure is not None and actor.phase == "failed"
    assert len(consumer.events) == 3
    assert consumer.statuses[-1].phase == "failed" and not consumer.statuses[-1].valid
    assert not actor.snapshot().valid
    assert actor._process.returncode is not None


def test_stop_cancels_and_reaps_decoder(tmp_path):
    config = fake_decoder(tmp_path, [bootstrap()], hang=True)
    actor, consumer, _ = asyncio.run(exercise(config, stop_after=0.1))
    assert actor.phase == "stopped"
    assert not actor.snapshot().valid
    assert actor._process.returncode is not None
    assert consumer.statuses[-1].phase == "stopped"


def test_stop_from_strategy_callback_stops_remaining_batch_delivery(tmp_path):
    config = fake_decoder(tmp_path, [bootstrap()], hang=True)
    actor, consumer, selected = asyncio.run(exercise(config, stop_in_callback=True))
    assert actor.phase == "stopped" and not actor.snapshot().valid
    assert len(consumer.events) == 1 and not selected.events
    assert actor._process.returncode is not None


def test_public_mock_reaches_strategy_and_keeps_exchange_delay_unavailable():
    cache = os.environ.get("BRISK_MOCK_CACHE")
    if not cache:
        pytest.skip("Set BRISK_MOCK_CACHE for public fixture integration")
    result = asyncio.run(run(BriskMockConfig(
        cache=Path(cache), codes=("7203", "5659"), speed=0, limit_frames=100),
        strategy_codes=("7203",)))
    assert result["failure"] is None
    assert result["state"]["issues"] == 2 and result["strategy"]["issues"] == 1
    assert result["strategy"]["frames"] == 101
    assert result["strategy"]["sequence_errors"] == 0
    assert result["strategy"]["toyota_preopen"]["indicative_price10"] == 101500
    assert result["strategy"]["toyota_preopen"]["indicative_volume"] == 304700
    assert result["state"]["latency"]["exchange_delay_ms"] is None
    assert result["state"]["input_transport"]["kind"] == "local_cache"


def test_example_cli_delivers_public_mock_to_strategy(tmp_path):
    cache = os.environ.get("BRISK_MOCK_CACHE")
    if not cache:
        pytest.skip("Set BRISK_MOCK_CACHE for public fixture integration")
    report = tmp_path / "strategy.json"
    result = subprocess.run([sys.executable, "-m", "tools.brisk_mock.run_nautilus_v2",
        "--cache", cache, "--speed", "0", "--limit-frames", "100",
        "--codes", "7203,5659", "--strategy-codes", "7203", "--report", str(report)],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    data = json.loads(report.read_text())
    assert data["strategy"]["issues"] == 1 and data["strategy"]["frames"] == 101
