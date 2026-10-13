// Black-box bisection of the snapshot step with briskapi's own Decoder: which subsets of today's
// snapshot records load, and whether SBI's March 2026 decoder (pybrisk's pinned copy) loads today's snapshot.
// Usage: node checks/t12_snapshot_bisect_070.cjs <site-packages/briskapi> <today-dir> <march-dir>
const fs = require('node:fs'), path = require('node:path');
const [pkg, today, march] = process.argv.slice(2);
const { checkAbi, checkMaster } = require(path.resolve(pkg, 'decoder/sbi.cjs'));
const { Decoder } = require(path.resolve(pkg, 'decoder/decoder.cjs'));
const read = (dir, n) => fs.readFileSync(path.join(dir, n));
const snap = read(today, 'snapshot.dat');
const recs = []; for (let o = 0; o < snap.length;) { const l = snap.readUInt32LE(o); recs.push(snap.subarray(o, o + l)); o += l; }
const lens = recs.map(r => r.length).sort((a, b) => a - b);
console.log(`records ${recs.length}; length min ${lens[0]} median ${lens[lens.length >> 1]} max ${lens[lens.length - 1]}`);
const attempt = async (label, dec, part) => {
  const assets = { 'fita.js': read(dec, 'fita.js'), 'fita.wasm': read(dec, 'fita.wasm'), 'master.dat': read(today, 'master.dat'), 'snapshot.dat': Buffer.concat(part) };
  try {
    const d = await Decoder.create(assets, { protocolVersion: 18000, check: checkAbi, checkMaster: m => checkMaster(m, 18000) });
    const q = d.quote(0);
    console.log(`${label}: OK (issue 0 frame ${q.frame}/${q.max_frame}, source_time_us ${q.source_time_us})`);
  } catch (e) { console.log(`${label}: FAILED ${String(e && e.message || e).slice(0, 120)}`); }
};
(async () => {
  console.log('== today\'s decoder');
  await attempt('record 0 only', today, recs.slice(0, 1));
  await attempt('record 1 only', today, recs.slice(1, 2));
  await attempt('records 0-9', today, recs.slice(0, 10));
  await attempt('records 0-99', today, recs.slice(0, 100));
  await attempt('records 0-999', today, recs.slice(0, 1000));
  await attempt('last record only', today, recs.slice(-1));
  await attempt('shortest record only', today, [recs.find(r => r.length === lens[0])]);
  console.log('== SBI decoder of March 2026 (pybrisk pin) with today\'s master and snapshot');
  await attempt('master only', march, []);
  await attempt('record 0 only', march, recs.slice(0, 1));
  await attempt('full snapshot', march, recs);
})();
