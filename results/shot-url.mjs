/**
 * Screenshot any URL full-page. Good for checking a generated report actually renders.
 *
 *   node results/shot-url.mjs <url> <out.png> [width]
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const [url, out, widthArg] = process.argv.slice(2);
if (!url || !out) { console.error('usage: shot-url.mjs <url> <out.png> [width]'); process.exit(2); }
const WIDTH = Number(widthArg || 1280);
const PORT = Number(process.env.CDP_PORT || 9341);

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-'));
const chrome = spawn(process.env.CHROME_PATH, [
  `--remote-debugging-port=${PORT}`, '--remote-allow-origins=*',
  '--no-sandbox', '--disable-dev-shm-usage', '--user-data-dir=' + profile,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars',
  `--window-size=${WIDTH},1200`, 'about:blank',
], { stdio: ['ignore', 'pipe', 'pipe'] });

let id = 0; const pending = new Map(); let ws;
const send = (method, params = {}) => new Promise((res, rej) => {
  const i = ++id;
  const t = setTimeout(() => { pending.delete(i); rej(new Error('timeout ' + method)); }, 60000);
  pending.set(i, { resolve: (v) => { clearTimeout(t); res(v); }, reject: (e) => { clearTimeout(t); rej(e); } });
  ws.send(JSON.stringify({ id: i, method, params }));
});
const ev = async (e) => {
  const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return r.result.value;
};

try {
  let pg;
  for (let i = 0; i < 120 && !pg; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      if (r.ok) pg = (await r.json()).find((t) => t.type === 'page');
    } catch { /* not up */ }
    if (!pg) await sleep(250);
  }
  ws = new WebSocket(pg.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.addEventListener('open', () => res());
    ws.addEventListener('error', () => rej(new Error('ws error')));
  });
  ws.addEventListener('message', (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) {
      const { resolve, reject } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
    }
  });

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride',
             { width: WIDTH, height: 1200, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url });
  // Streamlit renders over a websocket, so a fixed sleep captures an empty shell on a slow
  // start. WAIT_TEXT makes the shot wait for the page to actually say something.
  const waitFor = process.env.WAIT_TEXT;
  if (waitFor) {
    const bodyText = () => ev('document.body ? document.body.innerText : ""');
    const t0 = Date.now();
    let last = '';
    for (;;) {
      last = await bodyText();
      if (last.includes(waitFor)) break;
      if (Date.now() - t0 > 60000) {
        throw new Error(`timed out waiting for ${JSON.stringify(waitFor)}; body: ${last.slice(0, 400)}`);
      }
      await sleep(400);
    }
  }
  // Let images decode: every figure is a data URI, and they are the whole point of the page.
  await sleep(2500);

  // SEL= crops to the first element matching it plus its following siblings up to the next
  // heading — for a long generated sheet (review.html is 13000px) that is the difference between
  // "look at the section I just wrote" and a 13000px scroll hunt. It reports the node it found, so
  // a selector that matched nothing says so instead of silently shooting the whole page.
  const sel = process.env.SEL;
  if (sel) {
    const box = await ev(`(() => {
      const el = document.querySelector(${JSON.stringify(sel)});
      if (!el) return null;
      let bottom = el.getBoundingClientRect().bottom;
      for (let n = el.nextElementSibling; n; n = n.nextElementSibling) {
        if (/^H[12]$/.test(n.tagName)) break;
        const b = n.getBoundingClientRect().bottom;
        if (b > bottom) bottom = b;
      }
      const top = el.getBoundingClientRect().top + window.scrollY;
      return { top: Math.round(top), height: Math.round(bottom - el.getBoundingClientRect().top + window.scrollY),
               text: (el.innerText || '').slice(0, 60) };
    })()`);
    if (!box) throw new Error(`SEL matched nothing: ${sel}`);
    console.log(`SEL ${sel} → "${box.text}" at y=${box.top} h=${box.height}`);
    const clipShot = await send('Page.captureScreenshot',
      { format: 'png', captureBeyondViewport: true,
        clip: { x: 0, y: box.top, width: WIDTH, height: box.height, scale: 1 } });
    fs.writeFileSync(out, Buffer.from(clipShot.data, 'base64'));
    console.log('saved', out);
  } else {
    const height = await ev('Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)');
    const imgs = await ev(`(() => { const a = [...document.images];
      return { total: a.length, broken: a.filter(i => !i.complete || i.naturalWidth === 0).length }; })()`);
    console.log(`page height ${height}px · images ${imgs.total} · broken ${imgs.broken}`);

    await send('Emulation.setDeviceMetricsOverride',
               { width: WIDTH, height: Math.min(height, 30000), deviceScaleFactor: 1, mobile: false });
    await sleep(600);
    const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
    fs.writeFileSync(out, Buffer.from(r.data, 'base64'));
    console.log('saved', out);
  }
} catch (err) {
  console.log('FAILED:', err.message);
  process.exitCode = 1;
} finally {
  try { chrome.kill(); } catch { /* gone */ }
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* best effort */ }
}
