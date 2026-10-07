'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { Decoder, QuoteHistory, frames, u64, loadAssets, replay, main } = require('../../briskapi/decoder/decoder.cjs');
const cache = process.env.BRISK_MOCK_CACHE;

function frame(time, bytes = Buffer.from([1])) {
  const data = Buffer.alloc(8 + bytes.length);
  data.writeUInt32LE(time, 0); data.writeUInt32LE(bytes.length, 4); bytes.copy(data, 8);
  return data;
}

test('record container validates lengths, truncation and time order', () => {
  assert.equal([...frames(Buffer.concat([frame(9), frame(10)]))].length, 2);
  for (const bad of [Buffer.alloc(7), frame(9).subarray(0, 8), Buffer.alloc(8),
    Buffer.concat([frame(9), frame(8)])]) assert.throws(() => [...frames(bad)]);
  assert.equal(u64([0xffffffff, 1], 0), 8589934591);
  assert.throws(() => u64([0xffffffff, 0xffffffff], 0), /Unsafe/);
});

test('QR rollback and clone keep independent last prices', () => {
  const q = new QuoteHistory(); q.initialize(1);
  assert.equal(q.lastPrice10(1), 0);
  q.emplace(1, 0, 99, 700, 10, 100, 1); q.clone(2, 1);
  q.emplace(2, 0, 100, 710, 10, 100, 2);
  assert.equal(q.lastPrice10(1), 700); assert.equal(q.lastPrice10(2), 710);
  q.popBack(2); assert.equal(q.size(2), 1); q.deinitialize(2);
  assert.equal(q.qrs.has(2), false);
});

test('rejects unpinned cache and invalid CLI settings', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'brisk-hash-'));
  try { fs.writeFileSync(path.join(dir, 'fita.js'), 'bad'); assert.throws(() => loadAssets(dir), /hash mismatch/); }
  finally { fs.rmSync(dir, { recursive: true }); }
  await assert.rejects(main([]), /cache/);
  await assert.rejects(main(['--unknown', 'x']), /Usage/);
  await assert.rejects(main(['--cache']), /Usage/);
  await assert.rejects(replay({ speed: -1 }), /Speed/);
  await assert.rejects(replay({ speed: Infinity }), /Speed/);
  await assert.rejects(replay({ speed: 0, limitFrames: 0 }), /limit/);
});

test('decoder rejects malformed snapshots, gaps and overflows', () => {
  const d = Object.create(Decoder.prototype);
  d.w = { HEAPU8: new Uint8Array(4 * 1024 * 1024), _push() {}, _unserialize() {},
    _pushWs() {}, _applyBasePriceQueue() {}, _getTrace: () => -1, _clearTrace() {},
    _getStockView: () => 0 };
  d.buf = 0; d.aux = 8; d.master = [{ code: '7203' }];
  d.push(Buffer.alloc(4 * 1024 * 1024 + 1));
  for (const bad of [Buffer.alloc(3), Buffer.alloc(4), Buffer.from([9, 0, 0, 0])]) {
    assert.throws(() => d.unserialize(bad), /snapshot/);
  }
  assert.throws(() => d.feed(Buffer.alloc(4 * 1024 * 1024 + 1)), /buffer/);
  d.authError = true; assert.throws(() => d.feed(Buffer.alloc(1)), /session/); d.authError = false;
  assert.equal(d.start(Buffer.alloc(1)), false);  // waits for frame numbers
  d.initialFrames = [1, 2];
  assert.throws(() => d.start(Buffer.alloc(1)), /bootstrap/);
  d.initialFrames = [1]; d.w._getFrameNumbers = () => {};
  assert.throws(() => d.start(Buffer.alloc(1)), /catch-up/);
  assert.throws(() => d.changed(), /overflow/);
  d.w._getTrace = () => 1; d.w.HEAPU8[0] = 2;
  assert.throws(() => d.changed(), /issue ID/);
  assert.throws(() => d.quote(1), /Unknown/);
  assert.throws(() => d.quote(0), /unavailable/);
});

test('public mock pre-open snapshot and complete opening replay', { skip: !cache }, async () => {
  let bootstrap, previousSeq = -1, previousTime = 0, count = 0, sawOpeningTrade = false;
  const stats = await replay({ cache, speed: 0, emit: batch => {
    assert.equal(batch.seq, previousSeq + 1); previousSeq = batch.seq;
    assert.ok(batch.source_time_us >= previousTime); previousTime = batch.source_time_us;
    if (batch.type === 'bootstrap') {
      bootstrap = batch;
      assert.equal(batch.market_issue_count, 4131); assert.equal(batch.quotes.length, 4131);
      assert.equal(batch.source_time_us, 32399999955);
      assert.equal(batch.source_timestamp_origin, 'brisk_decoder_unverified');
      assert.equal(batch.exchange_delay_ms, null);
      const q = batch.quotes.find(q => q.code === '7203');
      assert.equal(q.indicative_price10, 101500); assert.equal(q.indicative_volume, 304700);
      assert.equal(q.market_buy_quantity, 201300); assert.equal(q.market_sell_quantity, 105700);
      assert.equal(q.volume, 0); assert.equal(q.last_price10, 0);
      assert.equal(batch.quotes.filter(q => q.indicative_price10 > 0).length, 3891);
    }
    if (batch.type === 'quotes') {
      assert.equal(batch.replay_lateness_ms, null);
      count += batch.quotes.length;
      if (batch.quotes.some(q => q.code === '7203' && q.open_price10 > 0 && q.volume > 0)) sawOpeningTrade = true;
    }
  } });
  assert.equal(bootstrap.trading_date, '20210927');
  assert.equal(stats.frames, 18001); assert.equal(count, 731251);
  assert.equal(stats.source_time_us, 32579999896); assert.ok(sawOpeningTrade);
});

test('filtering, replay pacing and partial-run termination', { skip: !cache }, async () => {
  const batches = [];
  await replay({ cache, codes: ['7203', '5659'], speed: 10, limitFrames: 10, emit: b => batches.push(b) });
  assert.equal(batches[0].quotes.length, 2);
  assert.ok(batches.every(b => !b.quotes || b.quotes.every(q => ['7203', '5659'].includes(q.code))));
  assert.equal(batches.at(-1).frames, 10);
  assert.ok(batches.filter(b => b.type === 'quotes').every(b => b.replay_lateness_ms >= 0));
  await assert.rejects(replay({ cache, codes: ['130A'], speed: 0, emit() {} }), /missing from 2021/);
});

test('CLI emits JSON only and reports failures', { skip: !cache }, () => {
  const command = path.join(__dirname, '../../briskapi/decoder/decoder.cjs');
  const run = spawnSync(process.execPath, [command, '--cache', cache, '--codes', '7203', '--speed', '0', '--limit-frames', '2'], { encoding: 'utf8' });
  assert.equal(run.status, 0, run.stderr);
  const records = run.stdout.trim().split('\n').map(JSON.parse);
  assert.equal(records.length, 3); assert.equal(records.at(-1).type, 'end');
  const bad = spawnSync(process.execPath, [command], { encoding: 'utf8' });
  assert.equal(bad.status, 1); assert.match(bad.stderr, /cache/);
});
