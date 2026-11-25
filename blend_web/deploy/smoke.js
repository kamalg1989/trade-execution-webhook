// Smoke test: run the page script against the real data.json with a stub DOM, for all three views.
const fs = require('fs');
const html = fs.readFileSync('/root/web-app/dist/blend/index.html', 'utf8');
const js = html.split('<script>')[1].split('</script>')[0];
const data = JSON.parse(fs.readFileSync('/root/blend_web/data/data.json', 'utf8'));
const els = {};
const el = () => ({ innerHTML: '', hidden: false, textContent: '', value: '', dataset: {}, onclick: null,
  addEventListener() {}, querySelectorAll() { return []; } });
global.document = { getElementById: id => (els[id] = els[id] || el()) };
const store = { trading_api_key: 'x' };
global.localStorage = { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };
global.fetch = async () => ({ status: 200, ok: true, json: async () => data });
global.setInterval = () => 0;
(async () => {
  eval(js + `
  ;globalThis.__go = async v => { view = v; await load(); return document.getElementById('app').innerHTML; };`);
  for (const v of ['Combined', 'A5', 'OHM Pro']) {
    const out = await globalThis.__go(v);
    const bad = /undefined|NaN|null/.exec(out.replace(/stroke-dasharray|nullable/g, ''));
    console.log(v, 'html', out.length, 'chars', bad ? 'SUSPECT: ' + out.slice(Math.max(0, bad.index - 120), bad.index + 40) : 'clean');
  }
})().catch(e => { console.error('ERROR', e); process.exit(1); });
