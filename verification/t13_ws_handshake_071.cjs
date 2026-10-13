// Diagnose the WebSocket upgrade the live host attempts: repeat the handshake with a raw HTTPS request
// and print only the status code and response headers (the ws_url session token is never printed).
// Usage: node checks/t13_ws_handshake_071.cjs <site-packages/briskapi> [site]
const fs = require('node:fs'), path = require('node:path'), os = require('node:os'), https = require('node:https'), crypto = require('node:crypto');
const [pkg, siteId = 'e-shiten'] = process.argv.slice(2);
const { Session, site, streamUrl } = require(path.resolve(pkg, 'decoder/sbi.cjs'));
const upgrade = (url, headers, label) => new Promise(resolve => {
  const req = https.request(url, { method: 'GET', headers: { ...headers, connection: 'Upgrade', upgrade: 'websocket',
    'sec-websocket-version': '13', 'sec-websocket-key': crypto.randomBytes(16).toString('base64') } });
  const done = (status, h, note) => { const hh = { ...h }; delete hh['set-cookie']; console.log(`${label}: HTTP ${status}${note ? ' ' + note : ''}`, JSON.stringify(hh)); resolve(); };
  req.on('upgrade', (res, socket) => { done(res.statusCode, res.headers, '(upgraded)'); socket.destroy(); });
  req.on('response', res => { let body = ''; res.on('data', d => body += d); res.on('end', () => done(res.statusCode, res.headers, body ? `body ${body.slice(0, 200).replace(/session=[^&"\s]+/g, 'session=<redacted>')}` : '')); });
  req.on('error', e => { console.log(`${label}: error ${e.message}`); resolve(); });
  req.end();
});
(async () => {
  const where = site(siteId);
  const cookies = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.config/brisk', `${siteId}-cookies.json`), 'utf8'));
  const session = new Session(cookies, fetch, where);
  const frontend = await session.get('/api/frontend/boot'); session.token = frontend.api_token;
  const app = await session.get('/api/app/boot');
  const wsUrl = streamUrl(app.ws_url, frontend.api_endpoint, where.origin);
  const httpsUrl = new URL(wsUrl); httpsUrl.protocol = 'https:';
  console.log('site', siteId, 'api_endpoint', frontend.api_endpoint, 'ws host', wsUrl.host, 'path', wsUrl.pathname, 'query keys', [...wsUrl.searchParams.keys()]);
  await upgrade(httpsUrl, { origin: where.origin }, 'A. as the host (Origin, no cookie)');
  await upgrade(httpsUrl, {}, 'B. no Origin, no cookie');
  await upgrade(httpsUrl, { origin: where.origin, cookie: session.cookie }, 'C. Origin + site cookie');
  await upgrade(httpsUrl, { origin: where.origin, authorization: `Bearer ${session.token}` }, 'D. Origin + Bearer api_token');
  const eio = new URL(httpsUrl); eio.searchParams.set('EIO', '4'); eio.searchParams.set('transport', 'websocket');
  await upgrade(eio, { origin: where.origin }, 'E. Origin + EIO=4&transport=websocket');
  const plain = new URL(httpsUrl); plain.search = '';
  await upgrade(plain, { origin: where.origin }, 'F. Origin, no query at all');
})().catch(e => console.error('fatal', e.message));
