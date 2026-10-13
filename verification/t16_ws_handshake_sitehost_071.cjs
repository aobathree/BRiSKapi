// Does the stream live on the site's own host (wss://<site>/realtime/0, as pybrisk's March capture shows)
// rather than on api_endpoint (api.brisk.jp)? Upgrade probe; status codes only, session never printed.
const fs = require('node:fs'), path = require('node:path'), os = require('node:os'), https = require('node:https'), crypto = require('node:crypto');
const [pkg, siteId = 'e-shiten'] = process.argv.slice(2);
const { Session, site } = require(path.resolve(pkg, 'decoder/sbi.cjs'));
const upgrade = (url, headers, label) => new Promise(resolve => {
  const req = https.request(url, { method: 'GET', headers: { ...headers, connection: 'Upgrade', upgrade: 'websocket',
    'sec-websocket-version': '13', 'sec-websocket-key': crypto.randomBytes(16).toString('base64') } });
  const done = (status, h, note) => { console.log(`${label}: HTTP ${status} ${note} via=${h.via || '-'} upgrade=${h.upgrade || '-'} accept=${h['sec-websocket-accept'] ? 'yes' : '-'}`); resolve(); };
  req.on('upgrade', (res, socket) => { done(res.statusCode, res.headers, '(UPGRADED)'); socket.destroy(); });
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
  const onSite = new URL(app.ws_url, where.origin + '/');   // relative ws_url against the site itself
  console.log(`${siteId}: ws_url is ${app.ws_url.startsWith('/') ? 'relative' : 'absolute'}; site host ${onSite.host}; path ${onSite.pathname}`);
  await upgrade(onSite, { origin: where.origin, cookie: session.cookie }, 'K. site host, Origin + site cookie');
  await upgrade(onSite, { origin: where.origin }, 'L. site host, Origin only');
  const api = new URL(app.ws_url, frontend.api_endpoint + '/');
  await upgrade(api, { origin: where.origin }, 'M. api_endpoint host, Origin only (as 0.7.1 does)');
})().catch(e => console.error('fatal', e.message));
