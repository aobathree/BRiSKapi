'use strict';
// The public Next demo serves recorded assets over HTTPS, not a live WebSocket.
const crypto = require('node:crypto');
const manifest = require('./assets.json');
const MAX_ASSET_BYTES = 32 * 1024 * 1024;

function verifyAsset(name, data) {
  if (crypto.createHash('sha256').update(data).digest('hex') !== manifest.assets[name].sha256) {
    throw new Error(`Asset hash mismatch: ${name}`);
  }
  return data;
}

async function fetchAsset(name, { fetchImpl = fetch, timeoutMs = 30000 } = {}) {
  const response = await fetchImpl(manifest.assets[name].url, {
    signal: AbortSignal.timeout(timeoutMs), redirect: 'error',
  });
  if (!response.ok) throw new Error(`Demo asset ${name}: HTTP ${response.status}`);
  if (!response.body) throw new Error(`Demo asset ${name}: empty response body`);
  const chunks = [];
  let size = 0;
  for await (const chunk of response.body) {
    size += chunk.length;
    if (size > MAX_ASSET_BYTES) throw new Error(`Demo asset ${name}: exceeds size limit`);
    chunks.push(Buffer.from(chunk));
  }
  return verifyAsset(name, Buffer.concat(chunks, size));
}

async function loadWebAssets(options) {
  const began = performance.now();
  // Wait for every request to settle, including on failure, before reporting it.
  const names = Object.keys(manifest.assets);
  const results = await Promise.allSettled(names.map(name => fetchAsset(name, options)));
  const failed = results.find(result => result.status === 'rejected');
  if (failed) throw failed.reason;
  return {
    assets: Object.fromEntries(names.map((name, i) => [name, results[i].value])),
    input_transport: { kind: 'https_recorded_assets', origin: manifest.source,
      asset_fetch_ms: performance.now() - began },
  };
}

module.exports = { verifyAsset, fetchAsset, loadWebAssets, MAX_ASSET_BYTES };
