'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { fetchAsset, loadWebAssets, MAX_ASSET_BYTES } = require('./web.cjs');
const { main, replay } = require('./decoder.cjs');
const manifest = require('./assets.json');
const cache = process.env.BRISK_MOCK_CACHE;

test('web transport rejects HTTP failures, empty bodies and changed assets', async () => {
  await assert.rejects(fetchAsset('fita.js', { fetchImpl: async () => new Response('', { status: 503 }) }), /HTTP 503/);
  await assert.rejects(fetchAsset('fita.js', { fetchImpl: async () => new Response(null) }), /empty response/);
  await assert.rejects(fetchAsset('fita.js', { fetchImpl: async () => new Response('changed') }), /hash mismatch/);
  await assert.rejects(fetchAsset('fita.js', { fetchImpl: async () => { throw Error('network down'); } }), /network down/);
});

test('bounds streamed response size and enforces a request timeout', async () => {
  const body = new ReadableStream({ start(controller) {
    controller.enqueue(new Uint8Array(MAX_ASSET_BYTES));
    controller.enqueue(new Uint8Array(1)); controller.close();
  } });
  await assert.rejects(fetchAsset('fita.js', { fetchImpl: async () => new Response(body) }), /size limit/);
  const fetchImpl = (_, { signal }) => new Promise((resolve, reject) => {
    const guard = setTimeout(() => reject(Error('timeout did not fire')), 1000);
    signal.addEventListener('abort', () => { clearTimeout(guard); reject(signal.reason); }, { once: true });
  });
  await assert.rejects(fetchAsset('fita.js', { fetchImpl, timeoutMs: 5 }), { name: 'TimeoutError' });
});

test('CLI selects exactly one transport and rejects duplicate options', async () => {
  await assert.rejects(main(['--cache', '/unused', '--web']), /exactly one/);
  await assert.rejects(main(['--web', '--web']), /Duplicate/);
  await assert.rejects(main(['--cache', '--web']), /Usage/);
  await assert.rejects(main(['--cache', 'a', '--cache', 'b']), /Duplicate/);
  await assert.rejects(replay({ cache: '/unused', web: true }), /exactly one/);
});

test('web startup settles every request and reports an asset failure', async () => {
  let completed = 0;
  const result = loadWebAssets({ fetchImpl: async () => {
    await new Promise(resolve => setTimeout(resolve, 2)); completed++;
    return new Response('', { status: 404 });
  } });
  await assert.rejects(result, /HTTP 404/);
  assert.equal(completed, Object.keys(manifest.assets).length);
});

test('downloads all pinned assets and records HTTPS historical provenance', { skip: !cache }, async () => {
  const requested = [];
  const fetchImpl = async (url, options) => {
    requested.push(url);
    assert.equal(options.redirect, 'error'); assert.ok(options.signal instanceof AbortSignal);
    const name = Object.keys(manifest.assets).find(name => manifest.assets[name].url === url);
    return new Response(fs.readFileSync(path.join(cache, name)));
  };
  const result = await loadWebAssets({ fetchImpl });
  assert.equal(requested.length, 5);
  assert.equal(result.input_transport.kind, 'https_recorded_assets');
  assert.equal(result.input_transport.origin, 'https://next-demo.brisk.jp/');
  assert.ok(result.input_transport.asset_fetch_ms >= 0);
  for (const name of Object.keys(manifest.assets)) {
    assert.deepEqual(result.assets[name], fs.readFileSync(path.join(cache, name)));
  }
});
