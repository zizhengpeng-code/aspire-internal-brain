/**
 * One-off: is the *live* site running the latest bundle? The page itself is only a 6KB shell —
 * Streamlit ships its CSS over the WebSocket — so `curl` cannot answer this. This signs in to
 * the public instance and reads what the browser actually computed for the assistant tile:
 * the `translateY(-3px)` and the mark's 19px box are the fingerprints of the alignment fix.
 *
 *   CHROME_PATH="..." node results/_live_check.mjs
 *
 * No question is submitted, so no demo slot is spent and no audit record is written.
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const CHROME = process.env.CHROME_PATH;
const CDP_PORT = Number(process.env.CDP_PORT || 9351);
const URL = process.env.URL || 'https://permission-aware-kb.app.workbuddy.host';
const ACCOUNT = process.env.ACCOUNT || 'Jamie Doe';

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-'));
const chrome = spawn(CHROME, [
  `--remote-debugging-port=${CDP_PORT}`, '--remote-allow-origins=*',
  '--no-sandbox', '--disable-dev-shm-usage', '--user-data-dir=' + profile,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars',
  '--window-size=1440,1100', 'about:blank',
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

const PROBE = `(() => {
  const t = document.querySelector('[data-testid="stChatMessageAvatarAssistant"]');
  const u = document.querySelector('[data-testid="stChatMessageAvatarUser"]');
  const read = (el) => {
    if (!el) return null;
    const cs = getComputedStyle(el);
    const af = getComputedStyle(el, '::after');
    return {
      display: cs.display, alignItems: cs.alignItems, justify: cs.justifyContent,
      transform: cs.transform, radius: cs.borderRadius,
      childDisplay: el.children.length ? getComputedStyle(el.children[0]).display : '(no child)',
      afterW: af.width, afterH: af.height, afterBg: String(af.backgroundImage).slice(0, 46),
    };
  };
  return { url: location.href, assistant: read(t), user: read(u) };
})()`;

try {
  let pg;
  for (let i = 0; i < 200 && !pg; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
      if (res.ok) pg = (await res.json()).find((t) => t.type === 'page');
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
             { width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: URL });

  const body = () => ev('document.body ? document.body.innerText : ""');
  const waitText = async (sub, ms = 120000) => {
    const t0 = Date.now();
    for (;;) {
      if ((await body()).includes(sub)) return;
      if (Date.now() - t0 > ms) throw new Error('timeout waiting for ' + sub);
      await sleep(500);
    }
  };
  const clickText = async (sub) => {
    for (let i = 0; i < 80; i++) {
      const ok = await ev(`(() => {
        const el = [...document.querySelectorAll('button, label')]
          .find(e => (e.innerText || '').trim().includes(${JSON.stringify(sub)}));
        if (!el) return false; el.click(); return true; })()`);
      if (ok) return;
      await sleep(500);
    }
    throw new Error('no clickable ' + sub);
  };

  await waitText('Sign in to Internal Brain');
  await sleep(1200);
  await clickText(ACCOUNT);
  await sleep(800);
  await clickText(`Continue as ${ACCOUNT}`);
  await waitText('Internal Brain', 90000);
  await sleep(3000);
  await clickText('Chat');
  await sleep(1500);

  const out = await ev(PROBE);
  console.log('live url :', out.url);
  for (const who of ['assistant', 'user']) {
    const v = out[who];
    console.log(`\n[${who}]`);
    if (!v) { console.log('  (element absent)'); continue; }
    for (const [k, val] of Object.entries(v)) console.log(`  ${k.padEnd(13)} ${val}`);
  }
  const fixed = out.assistant && out.assistant.transform.includes('-3')
             && out.assistant.childDisplay === 'none'
             && out.assistant.afterW === '19px';
  console.log('\nlive site is running the alignment fix:', fixed ? 'YES' : 'NO');
} catch (err) {
  console.log('FAILED:', err.message);
  process.exitCode = 1;
} finally {
  try { chrome.kill(); } catch { /* gone */ }
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* best effort */ }
}
