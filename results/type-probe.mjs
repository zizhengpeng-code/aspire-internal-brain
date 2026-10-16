/**
 * Measure the app's rendered typography, with the numbers the rules are written against.
 *
 * Signs in, walks to a page with prose and a page with data, and reads computed font-size,
 * line-height, measure and character counts. Prints ratios (line-height / font-size) because
 * that is the form the rules are stated in.
 *
 *   CHROME_PATH="..." results/with-app.sh node results/type-probe.mjs
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const CHROME = process.env.CHROME_PATH;
const PORT = Number(process.env.CDP_PORT || 9344);
const URL = process.env.TARGET_URL || 'http://127.0.0.1:8502';
const ACCOUNT = process.env.ACCOUNT || 'Grace Lim';

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

// Ratios and character counts have to be computed in the page, where the real metrics live.
const MEASURE = `(() => {
  const px = (v) => Math.round(parseFloat(v) * 100) / 100;
  const info = (el, label) => {
    if (!el) return { label, missing: true };
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const fs = parseFloat(cs.fontSize);
    const lh = cs.lineHeight === 'normal' ? null : parseFloat(cs.lineHeight);
    // Average advance of this exact face at this exact size: render a probe string off-screen.
    const probe = document.createElement('span');
    probe.style.cssText = 'position:absolute;visibility:hidden;white-space:pre';
    probe.style.font = cs.font;
    probe.textContent = 'abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789';
    document.body.appendChild(probe);
    const avgChar = probe.getBoundingClientRect().width / 64;
    probe.remove();
    const text = (el.innerText || '').trim();
    return {
      label,
      font: cs.fontFamily.split(',')[0].replace(/"/g, ''),
      size: px(cs.fontSize),
      lineHeight: lh === null ? 'normal' : px(cs.lineHeight),
      lhRatio: lh === null ? null : Math.round((lh / fs) * 1000) / 1000,
      boxWidth: Math.round(r.width),
      charsPerLine: r.width > 0 ? Math.round(r.width / avgChar) : null,
      avgChar: Math.round(avgChar * 100) / 100,
      weight: cs.fontWeight,
      text: text.slice(0, 40),
    };
  };
  const q = (s) => document.querySelector(s);
  const rows = [];
  rows.push(info(document.documentElement, 'html (base)'));
  rows.push(info(q('[data-testid="stMain"] p'), 'main body paragraph'));
  rows.push(info(q('[data-testid="stChatMessageContent"] p'), 'chat message paragraph'));
  rows.push(info(q('[data-testid="stChatMessageContent"]'), 'chat message container (measure)'));
  rows.push(info(q('[data-testid="stCaptionContainer"] p'), 'caption'));
  rows.push(info(q('[data-testid="stHeading"] h1') || q('h1'), 'h1'));
  rows.push(info(q('[data-testid="stHeading"] h2') || q('h2'), 'h2'));
  rows.push(info(q('[data-testid="stHeading"] h3') || q('h3'), 'h3'));
  rows.push(info(q('[data-testid="stHeading"] h4') || q('h4'), 'h4'));
  rows.push(info(q('[data-testid="stMetricValue"]'), 'metric value'));
  rows.push(info(q('[data-testid="stMetricLabel"]'), 'metric label'));
  rows.push(info(q('.stButton > button'), 'button label'));
  rows.push(info(q('table td'), 'table cell'));
  rows.push(info(q('[data-testid="stMarkdownContainer"] code'), 'inline code'));
  return rows;
})()`;

const HOVER = `(() => {
  // Does a button ease its colours, or snap? Read the transition the browser actually resolved.
  const b = document.querySelector('.stButton > button') || document.querySelector('button');
  if (!b) return null;
  const cs = getComputedStyle(b);
  return { transitionProperty: cs.transitionProperty, transitionDuration: cs.transitionDuration,
           transitionTimingFunction: cs.transitionTimingFunction, cursor: cs.cursor };
})()`;

const FOCUS = `(async () => {
  const b = document.querySelector('.stButton > button') || document.querySelector('button');
  if (!b) return null;
  b.focus();
  // Read *after* the transition settles. Now that the sheets give buttons a 180ms transition on
  // box-shadow, reading the computed style synchronously after focus returns the starting value
  // (transparent, 0px) and makes a perfectly visible focus ring look like it was removed.
  await new Promise((r) => setTimeout(r, 400));
  const cs = getComputedStyle(b);
  return { outlineStyle: cs.outlineStyle, outlineWidth: cs.outlineWidth,
           outlineColor: cs.outlineColor, outlineOffset: cs.outlineOffset,
           boxShadow: cs.boxShadow.slice(0, 80) };
})()`;

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
  // Put a transcript on screen: an answer is what the measure rule is about.
  const ASK = process.env.ASK || 'What rate limit applies to the payment gateway API?';
  await clickText('Chat');
  await sleep(700);
  const typed = await ev(`(() => {
    const t = document.querySelector('textarea[data-testid="stChatInputTextArea"]')
           || document.querySelector('textarea');
    if (!t) return false; t.focus(); return true; })()`);
  if (typed) {
    await send('Input.insertText', { text: ASK });
    await sleep(400);
    for (const type of ['keyDown', 'keyUp']) {
      await send('Input.dispatchKeyEvent',
                 { type, key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13,
                   nativeVirtualKeyCode: 13, text: '\r' });
    }
    await sleep(9000);
  }

  console.log('--- typography, as rendered ---');
  for (const row of await ev(MEASURE)) {
    if (row.missing) { console.log(`  ${row.label.padEnd(30)} (absent)`); continue; }
    console.log(`  ${row.label.padEnd(30)} ${String(row.font).padEnd(14)} size=${String(row.size).padStart(5)}` +
                ` lh=${String(row.lineHeight).padStart(6)} ratio=${String(row.lhRatio).padStart(5)}` +
                ` box=${String(row.boxWidth).padStart(4)}px avgChar=${String(row.avgChar).padStart(5)}` +
                ` chars/line=${String(row.charsPerLine).padStart(3)} w=${row.weight}`);
  }
  console.log('--- interaction ---');
  console.log('  transition:', JSON.stringify(await ev(HOVER)));
  console.log('  focus:     ', JSON.stringify(await ev(FOCUS)));
} catch (err) {
  console.log('FAILED:', err.message);
  process.exitCode = 1;
} finally {
  try { chrome.kill(); } catch { /* gone */ }
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* best effort */ }
}
