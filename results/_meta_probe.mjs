/**
 * One-off. `submission/question-checklist.md` still marks the meta question
 * "what can i do by using this agent" as a known failure ("fix in progress"), but `app.py`
 * now routes it through `is_about_assistant`, a short classifier call. This asks the live app
 * that exact string and prints the answer it got back, so the checklist can be corrected with
 * a measurement instead of a memory.
 *
 *   CHROME_PATH="..." results/with-app.sh node results/_meta_probe.mjs
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const CHROME = process.env.CHROME_PATH;
const CDP_PORT = Number(process.env.CDP_PORT || 9347);
const URL = process.env.TARGET_URL || 'http://127.0.0.1:8503';
const ACCOUNT = process.env.ACCOUNT || 'Jamie Doe';
const ASK = process.env.ASK || 'what can i do by using this agent';

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
  const t = setTimeout(() => { pending.delete(i); rej(new Error('timeout ' + method)); }, 45000);
  pending.set(i, { resolve: (v) => { clearTimeout(t); res(v); }, reject: (e) => { clearTimeout(t); rej(e); } });
  ws.send(JSON.stringify({ id: i, method, params }));
});
const ev = async (e) => {
  const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return r.result.value;
};

const READ = `(() => {
  const ms = [...document.querySelectorAll('[data-testid="stChatMessage"]')];
  const last = ms[ms.length - 1];
  if (!last) return '(no chat message)';
  const kind = last.querySelector('[data-testid="stChatMessageAvatarUser"]') ? 'USER' : 'ASSISTANT';
  const body = last.querySelector('[data-testid="stChatMessageContent"]');
  return kind + ': ' + (body ? body.innerText : '').replace(/\\s+/g, ' ').slice(0, 300);
})()`;

try {
  let pg;
  for (let i = 0; i < 120 && !pg; i++) {
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
  const waitText = async (sub, ms = 90000) => {
    const t0 = Date.now();
    for (;;) {
      if ((await body()).includes(sub)) return;
      if (Date.now() - t0 > ms) throw new Error('timeout waiting for ' + sub);
      await sleep(400);
    }
  };
  const clickText = async (sub) => {
    for (let i = 0; i < 60; i++) {
      const ok = await ev(`(() => {
        const el = [...document.querySelectorAll('button, label')]
          .find(e => (e.innerText || '').trim().includes(${JSON.stringify(sub)}));
        if (!el) return false; el.click(); return true; })()`);
      if (ok) return;
      await sleep(400);
    }
    throw new Error('no clickable ' + sub);
  };

  await waitText('Sign in to Internal Brain');
  await sleep(1000);
  await clickText(ACCOUNT);
  await sleep(600);
  await clickText(`Continue as ${ACCOUNT}`);
  await waitText('Internal Brain', 60000);
  await sleep(2500);
  await clickText('Chat');
  await sleep(700);
  const ok = await ev(`(() => {
    const t = document.querySelector('textarea[data-testid="stChatInputTextArea"]')
           || document.querySelector('textarea');
    if (!t) return false; t.focus(); return true; })()`);
  if (!ok) throw new Error('no chat input');
  await send('Input.insertText', { text: ASK });
  await sleep(400);
  for (const type of ['keyDown', 'keyUp']) {
    await send('Input.dispatchKeyEvent',
               { type, key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13,
                 nativeVirtualKeyCode: 13, text: '\r' });
  }
  await sleep(15000);
  console.log('ask:', ASK);
  console.log('reply:', await ev(READ));
} catch (err) {
  console.log('FAILED:', err.message);
  process.exitCode = 1;
} finally {
  try { chrome.kill(); } catch { /* gone */ }
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* best effort */ }
}
