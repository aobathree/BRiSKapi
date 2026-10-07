'use strict';
// SBI live host against a fake sbi.brisk.jp and WebSocket, driven by the demo's
// real decoder and frames (no SBI account or network needed).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { Session, checkAbi, live, main } = require('../../briskapi/decoder/sbi.cjs');
const { frames } = require('../../briskapi/decoder/decoder.cjs');
const cache = process.env.BRISK_MOCK_CACHE;

function server(files, overrides = {}) {
  const requests = [];
  const routes = {
    '/api/frontend/boot': () => JSON.stringify({ api_token: 'token' }),
    '/api/app/boot': () => JSON.stringify({ date: '2021-09-27', series: 0, master: 'm1', snapshot: 's1',
      ws_url: '/realtime/0?session=abc' }),
    '/': () => '<html><script src="/main.abc.js"></script></html>',
    '/main.abc.js': () => 'load("assets/wasm/fita.test.js");load("assets/wasm/fita.test.wasm")',
    '/assets/wasm/fita.test.js': () => files['fita.js'],
    '/assets/wasm/fita.test.wasm': () => files['fita.wasm'],
    '/api/master/m1': () => files['master.dat'],
    '/api/snapshot/s1': () => files['snapshot.dat'],
    ...overrides,
  };
  const fetchImpl = async (url, init) => {
    requests.push({ path: url.pathname, headers: init.headers, redirect: init.redirect });
    const route = routes[url.pathname];
    if (!route) return new Response('missing', { status: 404 });
    const body = route();
    return body instanceof Response ? body : new Response(body, { status: 200 });
  };
  return { fetchImpl, requests };
}

function socketFrom(chunks, code = 1000) {
  return class extends EventTarget {
    constructor(url, init) {
      super();
      this.url = url; this.init = init;
      setImmediate(() => {
        this.dispatchEvent(new MessageEvent('message', { data: 'hello' }));
        for (const chunk of chunks) {
          const data = chunk.buffer.slice(chunk.byteOffset, chunk.byteOffset + chunk.length);
          this.dispatchEvent(new MessageEvent('message', { data }));
        }
        setImmediate(() => this.close(code));
      });
      socketFrom.last = this;
    }
    close(closeCode = 1000) {
      if (this.closed) return;
      this.closed = true;
      this.dispatchEvent(Object.assign(new Event('close'), { code: closeCode, reason: '' }));
    }
  };
}

function demo() {
  const files = Object.fromEntries(['fita.js', 'fita.wasm', 'master.dat', 'snapshot.dat', 'ws.dat']
    .map(name => [name, fs.readFileSync(path.join(cache, name))]));
  const chunks = [...frames(files['ws.dat'])].slice(0, 6).map(f => f.data);
  return { files, chunks };
}

test('session sends cookies, refuses redirects and reports expiry', async () => {
  assert.throws(() => new Session(null), /BRISK_SBI_COOKIES/);
  const { fetchImpl, requests } = server({}, { '/api/frontend/boot': () => new Response('', { status: 401 }) });
  const session = new Session({ session_x: 'v1', other: 'v2' }, fetchImpl);
  await assert.rejects(session.get('/api/frontend/boot'), /expired/);
  assert.equal(requests[0].headers.cookie, 'session_x=v1; other=v2');
  assert.equal(requests[0].redirect, 'manual');
  await assert.rejects(session.get('/nope'), /HTTP 404/);
  assert.throws(() => checkAbi({ _initialize() {} }), /missing _push/);
});

test('SBI host bootstraps from boot, master and snapshot, then streams frames', { skip: !cache }, async () => {
  const { files, chunks } = demo();
  const { fetchImpl, requests } = server(files);
  const batches = [];
  const summary = await live({ cookies: { session_x: 'v' }, codes: ['7203'], emit: async b => batches.push(b),
    fetchImpl, WebSocketImpl: socketFrom(chunks), protocolVersion: 16000 });
  assert.equal(String(socketFrom.last.url), 'wss://sbi.brisk.jp/realtime/0?session=abc');
  assert.equal(socketFrom.last.init.headers.cookie, 'session_x=v');
  assert.ok(requests.filter(r => r.path.startsWith('/api/app')).every(r => r.headers.authorization === 'Bearer token'));
  const [bootstrap, ...rest] = batches;
  assert.equal(bootstrap.source, 'sbi_live');
  assert.equal(bootstrap.trading_date, '20210927');
  assert.deepEqual(bootstrap.master.map(m => m.code), ['7203']);
  assert.equal(bootstrap.quotes[0].indicative_price10, 101500);
  assert.equal(bootstrap.input_transport.kind, 'sbi_websocket');
  assert.match(bootstrap.input_transport.decoder_sha256, /^[0-9a-f]{64}$/);
  assert.deepEqual(rest.map(b => b.seq), [1, 2, 3, 4, 5, 6]);
  assert.equal(rest.at(-1).type, 'end');
  assert.equal(summary.frames, 6);
});

test('SBI host fails loudly on changed sites, unknown codes and abnormal closes', { skip: !cache }, async () => {
  const { files, chunks } = demo();
  const run = (overrides, options = {}) => live({ cookies: { s: 'v' }, emit: async () => {}, protocolVersion: 16000,
    fetchImpl: server(files, overrides).fetchImpl, WebSocketImpl: socketFrom(chunks), ...options });
  await assert.rejects(run({ '/main.abc.js': () => 'nothing' }), /decoder not found/);
  await assert.rejects(run({}, { codes: ['0000'] }), /not in the SBI master/);
  await assert.rejects(run({}, { WebSocketImpl: socketFrom(chunks, 1006) }), /closed \(1006/);
  await assert.rejects(run({}, { WebSocketImpl: socketFrom([]) }), /closed/);
  await assert.rejects(main(['--bad']), /Usage/);
  await assert.rejects(main([], {}), /BRISK_SBI_COOKIES/);
});
