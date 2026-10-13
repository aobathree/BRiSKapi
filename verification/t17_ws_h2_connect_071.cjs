// Does api.brisk.jp accept the stream as WebSocket over HTTP/2 (RFC 8441 extended CONNECT)?
// Chrome uses this when the server advertises SETTINGS_ENABLE_CONNECT_PROTOCOL. Status only; session never printed.
const fs = require('node:fs'), path = require('node:path'), os = require('node:os'), http2 = require('node:http2'), crypto = require('node:crypto');
const [pkg, siteId = 'e-shiten'] = process.argv.slice(2);
const { Session, site } = require(path.resolve(pkg, 'decoder/sbi.cjs'));
(async () => {
  const where = site(siteId);
  const cookies = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.config/brisk', `${siteId}-cookies.json`), 'utf8'));
  const session = new Session(cookies, fetch, where);
  const frontend = await session.get('/api/frontend/boot'); session.token = frontend.api_token;
  const app = await session.get('/api/app/boot');
  const url = new URL(app.ws_url, frontend.api_endpoint + '/');
  const client = http2.connect(`https://${url.host}`);
  await new Promise((res, rej) => { client.once('remoteSettings', res); client.once('error', rej); setTimeout(() => rej(new Error('no settings')), 10000); });
  console.log(`${siteId}: h2 to ${url.host}; alpn ${client.alpnProtocol}; enableConnectProtocol=${client.remoteSettings.enableConnectProtocol}`);
  const req = client.request({ ':method': 'CONNECT', ':protocol': 'websocket', ':scheme': 'https', ':authority': url.host,
    ':path': url.pathname + url.search, origin: where.origin, 'sec-websocket-version': '13' });
  const result = await new Promise(resolve => {
    req.on('response', h => { resolve(`HTTP ${h[':status']} via=${h.via || '-'} ct=${h['content-type'] || '-'}`); });
    req.on('error', e => resolve(`error ${e.code || e.message}`));
    req.on('close', () => resolve(`closed rstCode=${req.rstCode}`));
    setTimeout(() => resolve('timeout'), 10000);
  });
  console.log(`N. HTTP/2 extended CONNECT :protocol=websocket: ${result}`);
  if (result.startsWith('HTTP 200')) {
    let n = 0; req.on('data', d => { if (n++ < 3) console.log(`   ← ${d.length} bytes ${d.subarray(0, 12).toString('hex')}`); });
    await new Promise(r => setTimeout(r, 6000));
  }
  req.close(); client.close();
})().catch(e => { console.error('fatal', e.message); process.exit(1); });
