/**
 * One-off: dump the citation-card subtree the way the browser lays it out, plus every testid in
 * the build. `results/layout-probe.mjs` measures; this only looks, so the measuring selector can
 * be written against the real DOM instead of a remembered one.
 *
 *   CHROME_PATH="..." results/with-app.sh node results/_cards_dump.mjs
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const CHROME = process.env.CHROME_PATH;
const PORT = Number(process.env.CDP_PORT || 9347);
const URL = process.env.TARGET_URL || 'http://127.0.0.1:8503';
const ACCOUNT = process.env.ACCOUNT || 'Jamie Doe';
const ASK = process.env.ASK ||
  "What's the status of the database migration project and were there any blockers raised in Slack last week?";

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
  const t = setTimeout(() => { pending.delete(i); rej(new Error('timeout ' + method)); }, 45000);
  pending.set(i, { resolve: (v) => { clearTimeout(t); res(v); }, reject: (e) => { clearTimeout(t); rej(e); } });
  ws.send(JSON.stringify({ id: i, method, params }));
});
const ev = async (e) => {
  const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return r.result.value;
};

const TREE = `(() => {
  const cols = [...document.querySelectorAll('[data-testid="stColumn"]')];
  if (!cols.length) return '(no stColumn)';
  const lines = [];
  const walk = (el, d) => {
    const b = el.getBoundingClientRect();
    const tid = el.getAttribute('data-testid');
    const cls = (typeof el.className === 'string' && el.className)
      ? '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.') : '';
    const cs = getComputedStyle(el);
    const box = cs.display === 'flex' || cs.display === 'grid' || cs.borderTopWidth !== '0px'
      ? ' disp=' + cs.display + ' alignItems=' + cs.alignItems + ' border=' + cs.borderTopWidth
        + ' pad=' + cs.paddingTop + '/' + cs.paddingBottom + ' gap=' + cs.rowGap : '';
    const txt = el.children.length === 0 ? ' "' + (el.innerText || '').replace(/\\s+/g, ' ').slice(0, 40) + '"' : '';
    lines.push('  '.repeat(d) + el.tagName.toLowerCase() + (tid ? '[' + tid + ']' : '') + cls
      + '  y=' + Math.round(b.top) + '..' + Math.round(b.bottom) + ' h=' + Math.round(b.height)
      + ' w=' + Math.round(b.width) + box + txt);
    for (const c of el.children) walk(c, d + 1);
  };
  walk(cols[0], 0);
  lines.push('');
  lines.push('--- all testids ---');
  const seen = new Set();
  for (const e of document.querySelectorAll('[data-testid]')) seen.add(e.getAttribute('data-testid'));
  lines.push([...seen].sort().join(', '));
  return lines.join('\\n');
})()`;

/* The radius ramp is claimed in a comment; this prints what each rung of it actually paints.
   `details` and `summary` are read separately because they are different elements and only one of
   them was ever styled — a rule that lands on the one that paints nothing looks applied in the
   source and invisible on the screen, which is the failure mode this whole round kept hitting. */
const RADII = `(() => {
  const read = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return sel + '  → (absent)';
    const cs = getComputedStyle(el);
    return sel.padEnd(46)
      + ' radius ' + cs.borderTopLeftRadius.padEnd(6)
      + ' border ' + cs.borderTopWidth.padEnd(8)
      + ' bg ' + cs.backgroundColor.padEnd(22)
      + ' shadow ' + cs.boxShadow;
  };
  return [
    read('[data-testid="stChatMessage"]'),
    read('[data-testid="stExpander"]'),
    read('[data-testid="stExpander"] details'),
    read('[data-testid="stExpander"] summary'),
    read('[data-testid="stChatMessageContent"] [data-testid="stVerticalBlock"]'),
  ].join('\\n');
})()`;

try {
  let pg;
  for (let i = 0; i < 120 && !pg; i++) {
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
  if (ok) {
    await send('Input.insertText', { text: ASK });
    await sleep(400);
    for (const type of ['keyDown', 'keyUp']) {
      await send('Input.dispatchKeyEvent',
                 { type, key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13,
                   nativeVirtualKeyCode: 13, text: '\r' });
    }
    await sleep(14000);
  }
  console.log(await ev(TREE));
  console.log('\n--- what each rung of the radius ramp actually paints ---');
  console.log(await ev(RADII));
  /* Then the same again with the steps open: `details` is transparent when collapsed, and if it
     ever grows an edge it does it here. */
  await ev(`(() => { const s = document.querySelector('[data-testid="stExpander"] summary');
                     if (s) s.click(); return true; })()`);
  await sleep(1200);
  console.log('\n--- same, after opening the expander ---');
  console.log(await ev(RADII));
} catch (err) {
  console.log('FAILED:', err.message);
  process.exitCode = 1;
} finally {
  try { chrome.kill(); } catch { /* gone */ }
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* best effort */ }
}
