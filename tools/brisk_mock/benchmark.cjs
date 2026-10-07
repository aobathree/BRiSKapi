'use strict';
const { replay } = require('./decoder.cjs');
const cache = process.argv[2];
if (!cache) throw Error('Usage: node benchmark.cjs CACHE');
const timings = [];
let bootstrap;
replay({ cache, speed: 0, emit: batch => {
  if (batch.type === 'bootstrap') bootstrap = batch;
  if (batch.type === 'quotes') timings.push(batch.decode_ns);
} }).then(summary => {
  timings.sort((a, b) => a - b);
  console.log(JSON.stringify({
    source: 'historical_mock', trading_date: bootstrap.trading_date,
    market_issue_count: bootstrap.market_issue_count,
    preopen_snapshot_time_us: bootstrap.source_time_us,
    issues_with_indicative_price: bootstrap.quotes.filter(q => q.indicative_price10 > 0).length,
    summary, decode_and_extract_us: {
      p50: timings[Math.floor(timings.length * 0.50)] / 1000,
      p95: timings[Math.floor(timings.length * 0.95)] / 1000,
      p99: timings[Math.floor(timings.length * 0.99)] / 1000,
      max: timings.at(-1) / 1000,
    },
    note: 'Local unpaced WASM decode and auction field extraction only; excludes JSON transport, Rust publication, network and exchange latency.',
  }, null, 2));
}).catch(error => { console.error(error.message); process.exitCode = 1; });
