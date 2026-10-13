// Is the ws_url session the same across boot calls (per login) or new each time? Prints hashes only.
const fs = require('node:fs'), path = require('node:path'), os = require('node:os'), crypto = require('node:crypto');
const [pkg, siteId = 'e-shiten'] = process.argv.slice(2);
const { Session, site } = require(path.resolve(pkg, 'decoder/sbi.cjs'));
(async () => {
  const where = site(siteId);
  const cookies = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.config/brisk', `${siteId}-cookies.json`), 'utf8'));
  const session = new Session(cookies, fetch, where);
  const h = s => crypto.createHash('sha256').update(String(s)).digest('hex').slice(0, 12);
  for (let i = 0; i < 2; i++) {
    const frontend = await session.get('/api/frontend/boot'); session.token = frontend.api_token;
    const app = await session.get('/api/app/boot');
    console.log(`${siteId} boot ${i + 1}: api_token hash ${h(frontend.api_token)}, ws_url hash ${h(app.ws_url)}, session_expires ${app.session_expires}, time ${app.time}`);
    await new Promise(r => setTimeout(r, 1500));
  }
})().catch(e => console.error('fatal', e.message));
