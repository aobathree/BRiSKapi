'use strict';
// Experimental live host for broker-hosted BRiSK (SBI BRiSK, Tachibana e-shiten
// BRiSK Next): the session's own WASM decoder under Node, fed from the
// authenticated WebSocket. No browser or Chrome DevTools. Emits the same JSON
// batches as decoder.cjs (source=sbi_live or eshiten_live).
// Cookies come from BRISK_SBI_COOKIES (JSON object), never from the command line;
// BRISK_SBI_SITE selects the site (default sbi).
const crypto = require('node:crypto');
const { once } = require('node:events');
const { Decoder } = require('./decoder.cjs');

// Broker sites serving the same app. Both use the SBI endpoint sequence; the
// e-shiten site was mapped in a browser session (2026-10-08).
const SITES = {
  'sbi': { origin: 'https://sbi.brisk.jp', source: 'sbi_live' },
  'e-shiten': { origin: 'https://next.e-shiten.brisk.jp', source: 'eshiten_live' },
};
const ORIGIN = SITES.sbi.origin;
const PROTOCOL_VERSION = 18000;
const MAX_BYTES = 64 * 1024 * 1024;
// SBI's decoder must provide everything the host calls (the demo ABI minus portfolio).
const REQUIRED = ['_initialize', '_push', '_applyBasePriceQueue', '_stockCount', '_getStockMaster', '_unserialize',
  '_getDate', '_getTime', '_pushWs', '_getFrameNumbers', '_apiRecieved', '_addTraceUpdate', '_getTrace',
  '_clearTrace', '_getStockView', '_fitItaViewRowPrice10', '_getItaRows', '_malloc', 'addFunction'];

function site(name = 'sbi') {
  if (!Object.hasOwn(SITES, name)) throw new Error(`Unknown BRiSK site ${JSON.stringify(name)}; use ${Object.keys(SITES).join(' or ')}`);
  return { name, ...SITES[name] };
}

// Hosts that may receive the session token or cookies: the site itself and BRiSK's own API.
function trustedHost(url, origin) {
  const host = new URL(url).hostname;
  return url.startsWith('wss://') && (host === new URL(origin).hostname || host === 'brisk.jp' || host.endsWith('.brisk.jp'));
}

class Session {
  constructor(cookies, fetchImpl = fetch, origin = ORIGIN) {
    if (!cookies || typeof cookies !== 'object' || !Object.keys(cookies).length) {
      throw new Error('Set BRISK_SBI_COOKIES to your BRiSK session cookies (JSON object)');
    }
    this.cookie = Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join('; ');
    this.fetch = fetchImpl;
    this.origin = origin;
    this.token = null;
  }

  async get(path, kind = 'json') {
    const headers = { cookie: this.cookie };
    if (this.token) headers.authorization = `Bearer ${this.token}`;
    // Redirects mean a login page; following them would forward credentials elsewhere.
    const response = await this.fetch(new URL(path, this.origin), { headers, redirect: 'manual', signal: AbortSignal.timeout(30000) });
    if ([301, 302, 303, 307, 308, 401, 403].includes(response.status) || response.type === 'opaqueredirect') {
      throw new Error('BRiSK session expired or invalid; log in again');
    }
    if (!response.ok) throw new Error(`BRiSK ${path}: HTTP ${response.status}`);
    const data = Buffer.from(await response.arrayBuffer());
    if (data.length > MAX_BYTES) throw new Error(`BRiSK ${path}: response too large`);
    return kind === 'json' ? JSON.parse(data) : kind === 'text' ? data.toString('utf8') : data;
  }
}

// The decoder is served to logged-in sessions only and changes with releases,
// so it is located through the app's own bundles rather than pinned. The page
// may reference its bundles by absolute or relative path; the bundle names the
// loader script as a literal or (e-shiten) as a template with an exchange
// variant, `./assets/wasm${r}/fita.<hash>.js`, and derives the .wasm name from
// it (Emscripten convention). Only the TSE variant (no suffix) is used here.
async function decoderAssets(session) {
  const page = await session.get('/', 'text');
  const scripts = [...page.matchAll(/<script[^>]+src="(\.?\/?[\w./-]+\.js)"/g)].map(m => '/' + m[1].replace(/^\.?\//, ''));
  for (const script of scripts) {
    const source = await session.get(script, 'text');
    const js = source.match(/["'`]\.?\/?assets\/wasm(?:\$\{\w+\})?\/(fita[\w.-]*)\.js["'`]/);
    if (!js) continue;
    const paths = [`/assets/wasm/${js[1]}.js`, `/assets/wasm/${js[1]}.wasm`];
    const assets = { 'fita.js': await session.get(paths[0], 'bytes'), 'fita.wasm': await session.get(paths[1], 'bytes') };
    return { assets, paths };
  }
  throw new Error('BRiSK decoder not found in the app bundles; the site layout may have changed');
}

function checkAbi(wasm) {
  const missing = REQUIRED.filter(name => typeof wasm[name] !== 'function');
  if (missing.length) throw new Error(`BRiSK decoder changed; missing ${missing.join(', ')}`);
}

async function live({ cookies, codes = [], emit, fetchImpl = fetch, WebSocketImpl = WebSocket, startTimeoutMs = 60000,
  protocolVersion = PROTOCOL_VERSION, site: siteName = 'sbi' }) {
  const { name, origin, source } = site(siteName);
  const session = new Session(cookies, fetchImpl, origin);
  const began = performance.now();
  const frontend = await session.get('/api/frontend/boot');
  session.token = frontend.api_token;
  const app = await session.get('/api/app/boot');
  const { assets, paths } = await decoderAssets(session);
  assets['master.dat'] = await session.get(`/api/master/${encodeURIComponent(app.master)}`, 'bytes');
  assets['snapshot.dat'] = await session.get(`/api/snapshot/${encodeURIComponent(app.snapshot)}`, 'bytes');
  const decoder = await Decoder.create(assets, { protocolVersion, check: checkAbi });
  const selected = new Set(codes);
  const master = decoder.master.filter(m => !selected.size || selected.has(m.code));
  const missing = codes.filter(code => !master.some(m => m.code === code));
  if (missing.length) throw new Error(`Codes not in the ${name} master: ${missing.join(',')}`);
  const ids = new Set(master.map(m => m.issue_id));
  // The browser resolves the relative ws_url against the API endpoint named by the
  // frontend boot (api.brisk.jp for e-shiten), not the site that served the page.
  const base = typeof frontend.api_endpoint === 'string' && frontend.api_endpoint.startsWith('https://') ? frontend.api_endpoint : origin;
  const wsUrl = new URL(app.ws_url, base.replace('https:', 'wss:'));
  if (!trustedHost(wsUrl.href, origin)) throw new Error(`Refusing WebSocket to ${wsUrl.host}: not a BRiSK host`);
  const input_transport = { kind: `${name}_websocket`, origin: origin + '/', ws_host: wsUrl.host, series: app.series,
    decoder: paths, decoder_sha256: crypto.createHash('sha256').update(assets['fita.wasm']).digest('hex'),
    setup_ms: performance.now() - began };

  // The session token in ws_url authenticates the stream; cookies are for the site
  // that issued them only. Origin is what a browser on the site would send.
  const headers = { origin };
  if (wsUrl.hostname === new URL(origin).hostname) headers.cookie = session.cookie;
  const socket = new WebSocketImpl(wsUrl, { headers });
  socket.binaryType = 'arraybuffer';
  let seq = 0, frames = 0, updates = 0, started = false, work = Promise.resolve(), failure = null;
  const start = performance.now();
  const timer = setTimeout(() => fail(new Error('BRiSK stream did not initialize; a snapshot catch-up may be required')), startTimeoutMs);
  const fail = error => { failure = failure || error; socket.close(); };
  const handle = async data => {
    const received_unix_ms = Date.now();
    const frame = Buffer.from(data);
    frames++;
    if (!started) {
      // Until the decoder reports initial frame numbers there is no consistent state to publish.
      if (!decoder.start(frame)) return;
      started = true; clearTimeout(timer);
      const now = decoder.time();
      const quotes = master.map(m => decoder.quote(m.issue_id));
      // The stock view layout is unverified for broker builds: refuse implausible values.
      if (quotes.some(q => q.frame > q.max_frame || q.source_time_us > now)) {
        throw new Error(`BRiSK stock view layout not recognised (ohlcLength=${decoder.ohlc})`);
      }
      return emit({ type: 'bootstrap', seq: seq++, source, trading_date: String(app.date).replaceAll('-', ''),
        input_transport, source_timestamp_origin: 'brisk_decoder_unverified', exchange_delay_ms: null,
        source_time_us: now, market_issue_count: decoder.master.length, master, quotes });
    }
    const t0 = process.hrtime.bigint();
    decoder.feed(frame);
    const quotes = decoder.changed().filter(id => ids.has(id)).map(id => decoder.quote(id));
    updates += quotes.length;
    await emit({ type: 'quotes', seq: seq++, source_time_us: decoder.time(), received_unix_ms,
      decode_ns: Number(process.hrtime.bigint() - t0), replay_lateness_ms: null, quotes });
  };
  socket.addEventListener('message', event => {
    if (typeof event.data === 'string') return;  // Text control messages carry no market data.
    work = work.then(() => failure || handle(event.data)).catch(fail);
  });
  socket.addEventListener('error', event => fail(new Error(`BRiSK WebSocket error: ${event.message || 'connection failed'}`)));
  const [closed] = await once(socket, 'close');
  clearTimeout(timer);
  await work;
  if (failure) throw failure;
  if (closed.code !== 1000 || !started) throw new Error(`BRiSK stream closed (${closed.code} ${closed.reason || ''})`.trim());
  const summary = { type: 'end', seq, source_time_us: decoder.time(), frames, quote_updates: updates,
    replay_wall_ms: performance.now() - start };
  await emit(summary);
  return summary;
}

async function main(argv, env = process.env) {
  const codesAt = argv.indexOf('--codes');
  if (argv.length && (codesAt !== 0 || argv.length !== 2)) throw new Error('Usage: BRISK_SBI_COOKIES=... [BRISK_SBI_SITE=e-shiten] sbi.cjs [--codes 7203,6758]');
  const version = env.BRISK_SBI_PROTOCOL_VERSION;
  if (version !== undefined && !/^\d{1,6}$/.test(version)) throw new Error('BRISK_SBI_PROTOCOL_VERSION must be an integer');
  await live({ cookies: JSON.parse(env.BRISK_SBI_COOKIES || 'null'), codes: codesAt === 0 ? argv[1].split(',') : [],
    site: env.BRISK_SBI_SITE || 'sbi', protocolVersion: version === undefined ? PROTOCOL_VERSION : Number(version),
    emit: async record => {
      if (!process.stdout.write(JSON.stringify(record) + '\n')) await once(process.stdout, 'drain');
    } });
}

module.exports = { Session, decoderAssets, checkAbi, live, main, site, SITES, PROTOCOL_VERSION, REQUIRED };
if (require.main === module) main(process.argv.slice(2)).catch(error => {
  console.error(error.message); process.exitCode = 1;
});
