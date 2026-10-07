"""BRiSK historical mock -> in-process Rust state -> Nautilus v2 topics.

Run the node with run_async(): the actor's decoder task and topic publication
stay on the node's Python/event-loop thread. No Redis, file polling or orders.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4

import nautilus_trader

if not nautilus_trader.__version__.startswith("2."):
    raise ImportError("The BRiSK actor requires NautilusTrader v2")

from nautilus_trader.common import DataActor, DataActorConfig
from nautilus_trader.model import ActorId
import brisk_state_native

AUCTION_TOPIC = "brisk.auction"
FRAME_TOPIC = "brisk.frame"
STATUS_TOPIC = "brisk.status"
SNAPSHOT_REQUEST_TOPIC = "brisk.snapshot.request"
SOURCE_ORIGIN = "brisk_decoder_unverified"


@dataclass(frozen=True, slots=True)
class AuctionQuote:
    stream_id: str
    seq: int
    issue_id: int
    code: str
    frame: int
    trading_date: str
    source_time_us: int
    ts_event: int  # source clock, not verified as exchange-origin
    ts_init: int  # Rust state-ready Unix nanoseconds, before Python conversion
    received_unix_ms: int | None
    is_snapshot: bool
    fields: Mapping[str, object]
    source: str = "historical_mock"
    source_timestamp_origin: str = SOURCE_ORIGIN

    @property
    def topic(self) -> str:
        # Preserve issue identity even when more than one security shares a label.
        return f"{AUCTION_TOPIC}.{self.code}.{self.issue_id}"


@dataclass(frozen=True, slots=True)
class FeedFrame:
    stream_id: str
    seq: int
    source_time_us: int
    ts_init: int
    updates: int
    kind: str


@dataclass(frozen=True, slots=True)
class FeedStatus:
    stream_id: str
    phase: str
    valid: bool
    last_seq: int
    reason: str | None
    ts_init: int
    source: str = "historical_mock"
    exchange_delay_ms: None = None


@dataclass(frozen=True, slots=True)
class SnapshotRequest:
    reply_topic: str
    codes: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class AuctionSnapshot:
    stream_id: str
    seq: int
    valid: bool
    quotes: tuple[AuctionQuote, ...]
    missing_codes: tuple[str, ...]
    reason: str | None


@dataclass(frozen=True, slots=True)
class BriskMockConfig:
    cache: Path | None = None
    web: bool = False
    codes: tuple[str, ...] = ()  # empty means whole-market upstream extraction
    speed: float = 1.0
    limit_frames: int | None = None
    idle_timeout_seconds: float = 5.0
    startup_timeout_seconds: float = 30.0
    node: str = "node"
    decoder: Path = Path(__file__).resolve().parents[2] / "briskapi/decoder/decoder.cjs"
    actor_id: str = "BRISK-MOCK"

    def __post_init__(self) -> None:
        import math
        if (self.cache is not None) == self.web:
            raise ValueError("Select exactly one of cache or web")
        if not math.isfinite(self.speed) or self.speed < 0:
            raise ValueError("speed must be finite and nonnegative")
        if self.limit_frames is not None and self.limit_frames < 1:
            raise ValueError("limit_frames must be positive")
        for timeout in (self.idle_timeout_seconds, self.startup_timeout_seconds):
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("timeouts must be finite and positive")


class BriskMockActor(DataActor):
    """One mock feed shared by any number of strategy topic subscriptions."""

    def __init__(self, config: BriskMockConfig) -> None:
        super().__init__(DataActorConfig(actor_id=ActorId(config.actor_id)))
        self.brisk_config = config
        self._state = brisk_state_native.AuctionState()
        self.stream_id = ""
        self.phase = "stopped"
        self.failure: str | None = None
        self._task: asyncio.Task | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._date = ""
        self._midnight_ns = 0
        self._last_batch: dict | None = None
        self.stderr_tail = ""

    def on_start(self) -> None:
        loop = asyncio.get_running_loop()  # requires LiveNode.run_async()
        if self._task is not None and not self._task.done():
            raise RuntimeError("Previous BRiSK feed must finish before restarting")
        self._state = brisk_state_native.AuctionState()
        self.stream_id = uuid4().hex
        self.failure = None
        self._last_batch = None
        self._date = ""
        self.stderr_tail = ""
        self.subscribe_topic(SNAPSHOT_REQUEST_TOPIC, self._on_snapshot_request)
        self._set_phase("starting")
        self._task = loop.create_task(self._run_decoder(), name="brisk-mock-decoder")

    def on_stop(self) -> None:
        self.unsubscribe_topic(SNAPSHOT_REQUEST_TOPIC, self._on_snapshot_request)
        if self.phase not in ("completed", "failed"):
            self._state.invalidate("Collector stopped")
            self._set_phase("stopped", "Collector stopped")
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def wait_closed(self) -> None:
        """Await this after stopping the node so its decoder child is reaped."""
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def state_status(self) -> dict:
        return self._state.status()

    def _set_phase(self, phase: str, reason: str | None = None) -> None:
        self.phase = phase
        seq = self._state.status()["next_seq"] - 1
        self.publish_message(STATUS_TOPIC, FeedStatus(
            self.stream_id, phase, phase == "running", seq, reason, time.time_ns()))

    def _quote(self, quote: dict, batch: dict, *, snapshot: bool) -> AuctionQuote:
        fields = {k: v for k, v in quote.items()
                  if k not in ("issue_id", "code", "frame", "source_time_us")}
        return AuctionQuote(
            stream_id=self.stream_id, seq=batch["seq"], issue_id=quote["issue_id"],
            code=quote["code"], frame=quote["frame"], trading_date=self._date,
            source_time_us=quote["source_time_us"],
            ts_event=self._midnight_ns + quote["source_time_us"] * 1000,
            ts_init=batch["state_ready_ns"], received_unix_ms=batch["received_unix_ms"],
            is_snapshot=snapshot, fields=MappingProxyType(fields))

    def snapshot(self, codes: tuple[str, ...] | None = None) -> AuctionSnapshot:
        """Consistent current state; must be called on the node's event-loop thread."""
        status = self._state.status()
        seq = status["next_seq"] - 1
        if self.phase != "running" or not status["replay_running"] or self._last_batch is None:
            return AuctionSnapshot(self.stream_id, seq, False, (), codes or (),
                                   self.failure or f"Feed is {self.phase}")
        quotes = self._state.snapshot(None if codes is None else list(codes))
        present = {q["code"] for q in quotes}
        missing = tuple(sorted(set(codes or ()) - present))
        # This is a new read of retained state. We do not retain each quote's
        # original receipt time, so never substitute the latest frame's receipt.
        snapshot_batch = dict(self._last_batch, state_ready_ns=time.time_ns(), received_unix_ms=None)
        events = tuple(self._quote(q, snapshot_batch, snapshot=True) for q in quotes)
        return AuctionSnapshot(self.stream_id, seq, not missing, events, missing,
                               "Requested codes unavailable" if missing else None)

    def _on_snapshot_request(self, request: SnapshotRequest) -> None:
        if not isinstance(request, SnapshotRequest):
            return
        if not request.reply_topic.startswith("brisk.snapshot.reply."):
            return  # reply cannot target a feed/control topic and recurse
        self.publish_message(request.reply_topic, self.snapshot(request.codes))

    async def _stderr(self, stream: asyncio.StreamReader) -> None:
        while chunk := await stream.read(4096):
            self.stderr_tail = (self.stderr_tail + chunk.decode(errors="replace"))[-65536:]

    async def _run_decoder(self) -> None:
        config = self.brisk_config
        command = [config.node, str(config.decoder), "--speed", str(config.speed)]
        command += ["--web"] if config.web else ["--cache", str(config.cache)]
        if config.codes:
            command += ["--codes", ",".join(config.codes)]
        if config.limit_frames is not None:
            command += ["--limit-frames", str(config.limit_frames)]
        stderr_task = None
        ended = False
        try:
            self._process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=16 * 1024 * 1024)
            stderr_task = asyncio.create_task(self._stderr(self._process.stderr))
            while True:
                timeout = (config.startup_timeout_seconds if self.phase == "starting"
                           else config.idle_timeout_seconds)
                line = await asyncio.wait_for(self._process.stdout.readline(), timeout)
                if not line:
                    break
                if ended:
                    raise ValueError("Decoder emitted data after end")
                batch = self._state.apply_json(line.decode("utf-8"))
                self._last_batch = batch
                if batch["type"] == "bootstrap":
                    self._date = batch["trading_date"]
                    midnight = datetime.strptime(self._date, "%Y%m%d").replace(
                        tzinfo=timezone(timedelta(hours=9)))
                    self._midnight_ns = int(midnight.timestamp()) * 1_000_000_000
                    self._set_phase("running")
                if batch["type"] == "end":
                    ended = True
                    self._set_phase("finishing")
                for quote in batch["quotes"]:
                    if self.phase != "running":
                        if batch["type"] == "end":
                            raise ValueError("End marker must not contain quote updates")
                        raise asyncio.CancelledError
                    event = self._quote(quote, batch, snapshot=batch["type"] == "bootstrap")
                    self.publish_message(event.topic, event)
                self.publish_message(FRAME_TOPIC, FeedFrame(
                    self.stream_id, batch["seq"], batch["source_time_us"],
                    batch["state_ready_ns"], len(batch["quotes"]), batch["type"]))
                # A buffered unpaced replay must still let the node/other actors run.
                await asyncio.sleep(0)
            code = await asyncio.wait_for(self._process.wait(), config.idle_timeout_seconds)
            await stderr_task
            if code != 0 or not ended:
                raise RuntimeError(f"Decoder exited {code}; end={ended}; {self.stderr_tail[-2000:]}")
            self._set_phase("completed")
        except asyncio.CancelledError:
            if self.phase not in ("stopped", "completed", "failed"):
                self._state.invalidate("Decoder task cancelled")
                self._set_phase("stopped", "Decoder task cancelled")
            raise
        except Exception as error:
            self.failure = f"{type(error).__name__}: {error}"
            self._state.invalidate(self.failure)
            self._set_phase("failed", self.failure)
            self.log.error(self.failure)
        finally:
            if self._process is not None and self._process.returncode is None:
                try:
                    self._process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(self._process.wait(), 2)
                except asyncio.TimeoutError:
                    self._process.kill()
                    await self._process.wait()
            if stderr_task is not None:
                await stderr_task
