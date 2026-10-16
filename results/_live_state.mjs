/**
 * One-off: what is the live demo's chat page actually showing?
 *
 * `_live_cards.mjs` failed with "no chat textarea", which means the chat input is not in the
 * DOM at all. Two plausible causes: the demo's per-day question budget is exhausted (in which
 * case the app renders a limit notice instead of the input), or the nav click landed somewhere
 * else. This prints the page text and every testid so the two can be told apart.
 *
 *   CHROME_PATH="..." node results/_live_state.mjs
 *
 * Submits nothing.
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const CHROME = process.env.CHROME_PATH;
const PORT = Number(process.env.CDP_PORT || 9371);
const URL = process.env.TARGET_URL || 'https://permission-aware-kb.app.workbuddy.host';
const ACCOUNT = process.env.ACCOUNT || 'Jamie Doe';

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-'));
const chrome = spawn(CHROME, [
  `--remote-debugging-port=${PORT}`, '--remote-allow-origins=*',
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

const CLICKABLES = `(() => {
  const out = [];
  for (const el of document.querySelectorAll('button, label, [role="radio"], [data-testid="stRadio"] label')) {
    const t = (el.innerText || '').trim().replace(/\\s+/g, ' ');
    if (t) out.push(el.tagName.toLowerCase() + (el.getAttribute('data-testid') ? '[' + el.getAttribute('data-testid') + ']' : '') + ' "' + t.slice(0, 46) + '"');
  }
  return out.join('\\n');
})()`;

const TESTIDS = `(() => {
  const seen = new Set();
  for (const e of document.querySelectorAll('[data-testid]')) seen.add(e.getAttribute('data-testid'));
  return [...seen].sort().join(', ');
})()`;

const INPUTS = `(() => {
  const t = document.querySelectorAll('textarea');
  const i = document.querySelectorAll('input');
  return 'textarea=' + t.length + '  input=' + i.length
    + '\\n  chatInput=' + !!document.querySelector('[data-testid="stChatInput"]')
    + '  chatInputTextArea=' + !!document.querySelector('[data-testid="stChatInputTextArea"]');
})()`;

try {
  let pg;
  for (let i = 0; i < 200 && !pg; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/list`);
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

  /* Streamlit shows its own "Stop" button in the header while the script is still running. On a
     freshly deployed instance the first render has to seed the audit history and warm the vector
     index, so a fixed sleep either fires too early — which is how the earlier probe managed to
     see a half-painted page with no chat input in it — or wastes minutes. Wait for the button
     to go away instead. */
  /* Note the shape: the wait is for the input to *appear*, not for the "Stop" button to go away.
     An earlier version of this waited on "Stop" and printed "waited 0s" — it polled once, before
     the header had rendered, saw no button and concluded the script had already settled. The
     page then still showed a running script with no input in it. */
  const hasInput = () => ev(
    `!!document.querySelector('textarea[data-testid="stChatInputTextArea"]')`);
  let waited = 0;
  while (waited < 180000 && !(await hasInput())) { await sleep(2000); waited += 2000; }
  console.log('waited for chat input: ' + (waited / 1000) + 's');
  await sleep(2000);

  console.log('\n=== PAGE TEXT (after sign-in) ===');
  console.log((await body()).slice(0, 2000));
  console.log('\n=== INPUTS ===');
  console.log(await ev(INPUTS));
  console.log('\n=== CLICKABLES ===');
  console.log(await ev(CLICKABLES));
} catch (err) {
  console.log('FAILED:', err.message);
  process.exitCode = 1;
} finally {
  try { chrome.kill(); } catch { /* gone */ }
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* best effort */ }
}
