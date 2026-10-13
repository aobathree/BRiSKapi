// Fetch today's e-shiten decoder, master and snapshot with the saved cookies (values never printed),
// keep them in a Git-ignored folder, then create briskapi's Decoder with and without the snapshot
// to see which step fails. Usage: node checks/t11_fetch_and_bisect_070.cjs <site-packages/briskapi> <outdir> [site]
const fs = require('node:fs'), path = require('node:path'), os = require('node:os'), crypto = require('node:crypto');
const [pkg, out, siteId = 'e-shiten'] = process.argv.slice(2);
const { Session, site, decoderAssets, checkAbi, checkMaster } = require(path.resolve(pkg, 'decoder/sbi.cjs'));
const { Decoder } = require(path.resolve(pkg, 'decoder/decoder.cjs'));
const sha = b => crypto.createHash('sha256').update(b).digest('hex').slice(0, 16) + '…';
(async () => {
  const cookies = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.config/brisk', `${siteId}-cookies.json`), 'utf8'));
  const session = new Session(cookies, fetch, site(siteId));
  const frontend = await session.get('/api/frontend/boot'); session.token = frontend.api_token;
  const app = await session.get('/api/app/boot');
  console.log('boot:', { date: app.date, series: app.series, flex_version: app.flex_version, ws_url: app.ws_url === undefined ? 'absent' : 'present',
    session_status: app.session_status, next_date: app.next_date, base_prices: (app.base_prices || []).length });
  const { assets, paths } = await decoderAssets(session);
  console.log('decoder paths:', paths);
  assets['master.dat'] = await session.get(`/api/master/${encodeURIComponent(app.master)}`, 'bytes');
  assets['snapshot.dat'] = await session.get(`/api/snapshot/${encodeURIComponent(app.snapshot)}`, 'bytes');
  for (const [name, buf] of Object.entries(assets)) { fs.writeFileSync(path.join(out, name), buf); console.log(`${name}: ${buf.length.toLocaleString()} bytes sha256 ${sha(buf)}`); }
  fs.writeFileSync(path.join(out, 'boot-redacted.json'), JSON.stringify({ ...app, ws_url: undefined, identity: undefined }, null, 1));
  const attempt = async (label, a, boot) => {
    try {
      const d = await Decoder.create(a, { protocolVersion: app.flex_version, check: checkAbi, checkMaster: m => checkMaster(m, app.flex_version), boot });
      console.log(`${label}: OK master ${d.master.length} issues, ${d.layout.size}-byte records, date ${d.date}, ohlc ${d.ohlc}`);
      return d;
    } catch (e) { console.log(`${label}: FAILED ${e && e.message || e}`); }
  };
  await attempt('A. master only, no snapshot, no boot', { ...assets, 'snapshot.dat': Buffer.alloc(0) });
  await attempt('B. master + boot (0 base prices), no snapshot', { ...assets, 'snapshot.dat': Buffer.alloc(0) }, { exceptional_sq: app.exceptional_sq, base_prices: app.base_prices });
  const head = assets['snapshot.dat'].readUInt32LE(0);
  console.log('snapshot first record length:', head, 'records approx:', (() => { let n = 0; for (let o = 0; o < assets['snapshot.dat'].length;) { const l = assets['snapshot.dat'].readUInt32LE(o); if (l < 4) return `invalid at ${o}`; n++; o += l; } return n; })());
  await attempt('C. master + boot + full snapshot', assets, { exceptional_sq: app.exceptional_sq, base_prices: app.base_prices });
})().catch(e => { console.error('fatal:', e.message); process.exitCode = 1; });
