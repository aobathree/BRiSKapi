"""Account-free demonstration of a strategy consuming BRiSK on the v2 bus."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import math
from pathlib import Path
import time

from nautilus_trader.common import LoggerConfig, LogLevel
from nautilus_trader.live import LiveNode, LiveNodeConfig
from nautilus_trader.model import TraderId, StrategyId
from nautilus_trader.trading import Strategy, StrategyConfig

from tools.brisk_mock.nautilus_v2 import (
    AUCTION_TOPIC, FRAME_TOPIC, STATUS_TOPIC, BriskMockActor, BriskMockConfig,
)


class AuctionProbeStrategy(Strategy):
    """Receives data only; never submits orders."""

    def __init__(self, codes: tuple[str, ...] = ()) -> None:
        super().__init__(StrategyConfig(strategy_id=StrategyId("BRISK-PROBE")))
        self.codes = codes
        self.updates = 0
        self.issues: set[int] = set()
        self.frames = 0
        self.last_seq = -1
        self.sequence_errors = 0
        self.statuses = []
        self._samples: list[float] = []
        self._local_samples: list[float] = []
        self.toyota_preopen = None

    def on_start(self) -> None:
        for topic in self._topics():
            self.subscribe_topic(topic, self.on_auction)
        self.subscribe_topic(FRAME_TOPIC, self.on_frame)
        self.subscribe_topic(STATUS_TOPIC, self.on_status)

    def _topics(self) -> list[str]:
        return ([f"{AUCTION_TOPIC}.{code}.*" for code in self.codes]
                if self.codes else [f"{AUCTION_TOPIC}.*"])

    def on_stop(self) -> None:
        for topic in self._topics():
            self.unsubscribe_topic(topic, self.on_auction)
        self.unsubscribe_topic(FRAME_TOPIC, self.on_frame)
        self.unsubscribe_topic(STATUS_TOPIC, self.on_status)

    def on_auction(self, event) -> None:
        now = time.time_ns()
        self.updates += 1
        self.issues.add(event.issue_id)
        if not event.is_snapshot:
            self._samples.append((now - event.ts_init) / 1_000_000)
            if event.received_unix_ms is not None:
                self._local_samples.append(now / 1_000_000 - event.received_unix_ms)
        if event.code == "7203" and event.is_snapshot:
            self.toyota_preopen = {
                "source_time_us": event.source_time_us, "ts_event": event.ts_event,
                "indicative_price10": event.fields["indicative_price10"],
                "indicative_volume": event.fields["indicative_volume"],
            }

    def on_frame(self, event) -> None:
        if event.seq != self.last_seq + 1:
            self.sequence_errors += 1
        self.last_seq = event.seq
        self.frames += 1

    def on_status(self, event) -> None:
        self.statuses.append(asdict(event))

    @staticmethod
    def distribution(samples: list[float]) -> dict:
        values = sorted(samples)
        return {"samples": len(values), **{
            f"p{p}_ms": values[math.ceil(len(values) * p / 100) - 1] if values else None
            for p in (50, 95, 99)}, "max_ms": max(values, default=None)}

    def summary(self) -> dict:
        return {
            "updates": self.updates, "issues": len(self.issues), "frames": self.frames,
            "sequence_errors": self.sequence_errors, "statuses": self.statuses,
            "toyota_preopen": self.toyota_preopen,
            "rust_state_to_strategy": self.distribution(self._samples),
            "local_node_receipt_to_strategy": self.distribution(self._local_samples),
        }


async def run(config: BriskMockConfig, *, strategy_codes: tuple[str, ...] = ()) -> dict:
    node = LiveNode.build("BRISK-MOCK", LiveNodeConfig(
        trader_id=TraderId("BRISK-MOCK-001"), timeout_reconciliation_secs=0,
        delay_post_stop_secs=0, logging=LoggerConfig(stdout_level=LogLevel.ERROR)))
    actor = BriskMockActor(config)
    strategy = AuctionProbeStrategy(strategy_codes)
    node.add_actor(actor)
    node.add_strategy(strategy)
    handle = node.handle()

    async def stop_on_completion() -> None:
        while actor._task is None:
            await asyncio.sleep(0.01)
        await actor.wait_closed()
        handle.stop()

    monitor = asyncio.create_task(stop_on_completion())
    try:
        await node.run_async()
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
        await actor.wait_closed()
        node.dispose()
    result = {"source": "historical_mock", "strategy": strategy.summary(),
              "state": actor.state_status(), "failure": actor.failure}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--cache", type=Path)
    source.add_argument("--web", action="store_true", help="Connect to the public historical demo")
    parser.add_argument("--codes", default="", help="Upstream extraction; empty means all")
    parser.add_argument("--strategy-codes", default="", help="Bus subscriptions; empty means all")
    parser.add_argument("--speed", type=float, default=1)
    parser.add_argument("--limit-frames", type=int)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    codes = tuple(filter(None, args.codes.split(",")))
    strategy_codes = tuple(filter(None, args.strategy_codes.split(",")))
    result = asyncio.run(run(BriskMockConfig(
        cache=args.cache, web=args.web, codes=codes, speed=args.speed, limit_frames=args.limit_frames),
        strategy_codes=strategy_codes))
    report = json.dumps(result, indent=2)
    if args.report:
        args.report.write_text(report + "\n")
    print(report)
    if result["failure"] or result["strategy"]["sequence_errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
