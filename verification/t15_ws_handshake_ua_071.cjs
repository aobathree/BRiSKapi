// Same as t13 but with browser-like headers, to see whether the 502 depends on them. Session never printed.
const fs = require('node:fs'), path = require('node:path'), os = require('node:os'), https = require('node:https'), crypto = require('node:crypto');
const [pkg, siteId = 'e-shiten'] = process.argv.slice(2);
const { Session, site, streamUrl } = require(path.resolve(pkg, 'decoder/sbi.cjs'));
const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36';
const upgrade = (url, headers, label) => new Promise(resolve => {
  const req = https.request(url, { method: 'GET', headers: { ...headers, connection: 'Upgrade', upgrade: 'websocket',
    'sec-websocket-version': '13', 'sec-websocket-key': crypto.randomBytes(16).toString('base64') } });
  const done = (status, h, note) => { const hh = { ...h }; delete hh['set-cookie']; console.log(`${label}: HTTP ${status} ${note || ''} via=${h.via || '-'} server=${h.server || '-'}`); resolve(); };
  req.on('upgrade', (res, socket) => { done(res.statusCode, res.headers, '(upgraded!)'); socket.destroy(); });
  req.on('response', res => { res.resume(); res.on('end', () => done(res.statusCode, res.headers, '')); });
  req.on('error', e => { console.log(`${label}: error ${e.message}`); resolve(); });
  req.end();
});
(async () => {
  const where = site(siteId);
  const cookies = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.config/brisk', `${siteId}-cookies.json`), 'utf8'));
  const session = new Session(cookies, fetch, where);
  const frontend = await session.get('/api/frontend/boot'); session.token = frontend.api_token;
  const app = await session.get('/api/app/boot');
  const url = new URL(streamUrl(app.ws_url, frontend.api_endpoint, where.origin)); url.protocol = 'https:';
  const browser = { origin: where.origin, 'user-agent': UA, 'accept-encoding': 'gzip, deflate, br', 'accept-language': 'ja,en;q=0.9',
    'sec-websocket-extensions': 'permessage-deflate; client_max_window_bits', pragma: 'no-cache', 'cache-control': 'no-cache' };
  await upgrade(url, browser, 'G. browser-like headers (UA, extensions), same session');
  await upgrade(url, { ...browser, 'sec-websocket-protocol': 'binary' }, 'H. G + Sec-WebSocket-Protocol: binary');
  await new Promise(r => setTimeout(r, 3000));
  await upgrade(url, { origin: where.origin }, 'I. plain again after 3 s (same session)');
  const app2 = await session.get('/api/app/boot');
  const url2 = new URL(streamUrl(app2.ws_url, frontend.api_endpoint, where.origin)); url2.protocol = 'https:';
  await upgrade(url2, browser, 'J. browser-like headers, fresh session used immediately');
})().catch(e => console.error('fatal', e.message));
