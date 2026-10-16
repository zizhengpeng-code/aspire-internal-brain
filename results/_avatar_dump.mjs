/**
 * One-off: the chat avatar is a 32px tile whose mark is drawn by `::after`. The mark came back
 * sitting in the tile's bottom-right corner and clipped by its edge, which a centred background
 * cannot do — so something about the box, or about who else is inside it, is not what the
 * stylesheet assumes. This prints the box, every direct child, and both pseudo-elements, then
 * photographs the tile at 4x so the two accounts can be compared.
 *
 *   CHROME_PATH="..." results/with-app.sh node results/_avatar_dump.mjs
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
const SHOT = process.env.SHOT || 'results/ui/_avatar-now.png';
const SCALE = Number(process.env.SCALE || 4);

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

/* `getComputedStyle(el, '::after')` is the only way to ask the pseudo-element itself what it is;
   reading the host and guessing is what produced the wrong answer the last three times. */
const DUMP = `(() => {
  const out = [];
  const read = (sel) => {
    const els = [...document.querySelectorAll(sel)];
    out.push('=== ' + sel + '  (' + els.length + ' on the page) ===');
    els.forEach((el, i) => {
      const cs = getComputedStyle(el);
      const b = el.getBoundingClientRect();
      out.push('  [' + i + '] rect  x=' + Math.round(b.left) + ' y=' + Math.round(b.top)
        + '  ' + Math.round(b.width) + 'x' + Math.round(b.height));
      out.push('      host  display=' + cs.display + ' dir=' + cs.flexDirection
        + ' align=' + cs.alignItems + ' justify=' + cs.justifyContent
        + ' gap=' + cs.gap + ' pad=' + cs.padding + ' box=' + cs.boxSizing
        + ' overflow=' + cs.overflow + ' pos=' + cs.position
        + ' border=' + cs.borderWidth + ' radius=' + cs.borderRadius
        + ' transform=' + cs.transform);
      const kids = [...el.children].map((c) => {
        const cb = c.getBoundingClientRect();
        const ccs = getComputedStyle(c);
        return c.tagName.toLowerCase() + '[' + (c.getAttribute('data-testid') || '-') + '] '
          + Math.round(cb.width) + 'x' + Math.round(cb.height)
          + ' at ' + Math.round(cb.left - b.left) + ',' + Math.round(cb.top - b.top)
          + ' disp=' + ccs.display + ' flex=' + ccs.flex + ' pos=' + ccs.position;
      });
      out.push('      kids  ' + (kids.length ? kids.join('\\n              ') : '(none)'));
      for (const pe of ['::before', '::after']) {
        const ps = getComputedStyle(el, pe);
        out.push('      ' + pe.padEnd(8) + ' content=' + ps.content
          + ' disp=' + ps.display + ' ' + ps.width + 'x' + ps.height
          + ' pos=' + ps.position + ' inset=' + ps.top + '/' + ps.right + '/' + ps.bottom + '/' + ps.left
          + ' margin=' + ps.margin);
        out.push('      ' + ' '.repeat(8) + ' bgSize=' + ps.backgroundSize
          + ' bgPos=' + ps.backgroundPosition + ' bgRepeat=' + ps.backgroundRepeat
          + ' bg=' + String(ps.backgroundImage).slice(0, 64));
      }
      out.push('      html  ' + el.outerHTML.replace(/\\s+/g, ' ').slice(0, 500));
    });
  };
  read('[data-testid="stChatMessageAvatarAssistant"]');
  read('[data-testid="stChatMessageAvatarUser"]');
  return out.join('\\n');
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

  console.log(await ev(DUMP));

  /* Photograph both tiles on their own: 6px of air, then the 32px square, at 4x. Nothing else in
     the frame, so the mark's position inside the tile is the only thing in the picture. Both,
     because the same rule paints them and only one of them was reported broken.
     Two traps here, both hit once. `captureBeyondViewport` clips in *document* coordinates while
     `getBoundingClientRect` is in viewport ones, so the crop lands above the page and renders as
     an empty white square — which looks exactly like a missing avatar. And `window.scrollY` stays
     0 because Streamlit scrolls an inner container, not the document, so the offset cannot be
     recovered by adding it; the tile has to be scrolled into view and clipped in viewport
     coordinates with the beyond-viewport mode off. */
  await ev(`(() => {
    const el = document.querySelector('[data-testid="stChatMessageAvatarAssistant"]');
    if (el) el.scrollIntoView({ block: 'center' });
    return true; })()`);
  await sleep(700);
  const tiles = await ev(`(() => {
    const out = {};
    for (const [k, sel] of [
      ['assistant', '[data-testid="stChatMessageAvatarAssistant"]'],
      ['user', '[data-testid="stChatMessageAvatarUser"]']]) {
      const el = document.querySelector(sel);
      if (!el) continue;
      const b = el.getBoundingClientRect();
      out[k] = { x: Math.round(b.left), y: Math.round(b.top),
                 w: Math.round(b.width), h: Math.round(b.height) };
    }
    return out; })()`);
  for (const [who, box] of Object.entries(tiles)) {
    console.log('\ntile box ' + who, JSON.stringify(box));
    const pad = 6;
    const shot = await send('Page.captureScreenshot', {
      format: 'png', captureBeyondViewport: false,
      clip: { x: box.x - pad, y: box.y - pad,
              width: box.w + pad * 2, height: box.h + pad * 2, scale: SCALE },
    });
    const file = SHOT.replace(/\.png$/, '-' + who + '.png');
    fs.writeFileSync(file, Buffer.from(shot.data, 'base64'));
    console.log('saved', file, `(${(box.w + pad * 2) * SCALE}x${(box.h + pad * 2) * SCALE})`);
  }
} catch (err) {
  console.log('FAILED:', err.message);
  process.exitCode = 1;
} finally {
  try { chrome.kill(); } catch { /* gone */ }
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* best effort */ }
}
