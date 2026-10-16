/**
 * One-off: walk the *inside* of the shared-demo dialog and print the real box tree, so the
 * dialog can be styled against measured elements rather than guesses about Streamlit's DOM.
 *
 *   CHROME_PATH="..." TARGET_URL=http://127.0.0.1:8502 node results/_dlg_struct.mjs
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const CHROME = process.env.CHROME_PATH;
const PORT = Number(process.env.CDP_PORT || 9338);
const URL = process.env.TARGET_URL || 'http://127.0.0.1:8502';

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-'));
const chrome = spawn(CHROME, [
  `--remote-debugging-port=${PORT}`, '--remote-allow-origins=*',
  '--no-sandbox', '--disable-dev-shm-usage', '--user-data-dir=' + profile,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars',
  '--window-size=1440,1100', 'about:blank',
], { stdio: ['ignore', 'pipe', 'pipe'] });

class CDP {
  constructor() { this.id = 0; this.pending = new Map(); }
  connect(url) {
    this.ws = new WebSocket(url);
    this.ws.addEventListener('message', (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) {
        const { resolve, reject } = this.pending.get(m.id);
        this.pending.delete(m.id);
        if (m.error) reject(new Error(JSON.stringify(m.error))); else resolve(m.result);
      }
    });
    return new Promise((res, rej) => {
      this.ws.addEventListener('open', () => res());
      this.ws.addEventListener('error', () => rej(new Error('ws error')));
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      const t = setTimeout(() => { this.pending.delete(id); rej(new Error('timeout ' + method)); }, 30000);
      this.pending.set(id, { resolve: (v) => { clearTimeout(t); res(v); },
                             reject: (e) => { clearTimeout(t); rej(e); } });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  async eval(e) {
    const r = await this.send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('eval: ' + JSON.stringify(r.exceptionDetails));
    return r.result.value;
  }
}

const cdp = new CDP();
try {
  let page;
  for (let i = 0; i < 120 && !page; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      if (r.ok) page = (await r.json()).find((t) => t.type === 'page');
    } catch { /* not up */ }
    if (!page) await sleep(250);
  }
  await cdp.connect(page.webSocketDebuggerUrl);
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Emulation.setDeviceMetricsOverride',
                 { width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false });
  await cdp.send('Page.navigate', { url: URL });

  const body = () => cdp.eval('document.body ? document.body.innerText : ""');
  const waitText = async (sub, ms = 90000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) {
      if ((await body()).includes(sub)) return;
      await sleep(400);
    }
    throw new Error('timeout waiting for ' + sub);
  };
  const clickText = async (sub) => {
    for (let i = 0; i < 60; i++) {
      const ok = await cdp.eval(`(() => {
        const el = [...document.querySelectorAll('button, label')]
          .find(e => (e.innerText || '').trim().includes(${JSON.stringify(sub)}));
        if (!el) return false; el.click(); return true; })()`);
      if (ok) return;
      await sleep(400);
    }
    throw new Error('no clickable ' + sub);
  };

  await waitText('Sign in to Internal Brain');
  await sleep(1200);
  await clickText('Shared demo');
  await sleep(1200);

  const tree = await cdp.eval(`(() => {
    const root = document.querySelector('[data-testid="stDialog"]');
    if (!root) return 'NO stDialog';
    const lines = [];
    const describe = (el, depth) => {
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      const bits = [
        '  '.repeat(depth) + el.tagName.toLowerCase(),
        el.getAttribute('data-testid') ? 'tid=' + el.getAttribute('data-testid') : '',
        el.getAttribute('role') ? 'role=' + el.getAttribute('role') : '',
        el.id ? 'id=' + el.id.slice(0, 18) : '',
        'box=' + [r.width, r.height].map(v => Math.round(v)).join('x') + '@' + [r.x, r.y].map(v => Math.round(v)).join(','),
        'bg=' + cs.backgroundColor,
        'r=' + cs.borderRadius,
        'sh=' + (cs.boxShadow === 'none' ? '-' : cs.boxShadow.slice(0, 42)),
        'bw=' + cs.borderTopWidth + ' ' + cs.borderTopColor,
        'font=' + cs.fontFamily.split(',')[0],
      ].filter(Boolean);
      lines.push(bits.join(' '));
      if (depth < 4) [...el.children].forEach((c) => describe(c, depth + 1));
    };
    describe(root, 0);
    return lines.join('\\n');
  })()`);
  console.log(tree);

  const backdrops = await cdp.eval(`(() => {
    const out = [];
    for (const el of document.querySelectorAll('body *')) {
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      if (r.width > 1200 && r.height > 700 && cs.backgroundColor !== 'rgba(0, 0, 0, 0)') {
        out.push(el.tagName + ' tid=' + (el.getAttribute('data-testid') || '-') +
                 ' bg=' + cs.backgroundColor + ' z=' + cs.zIndex + ' pos=' + cs.position +
                 ' box=' + Math.round(r.width) + 'x' + Math.round(r.height));
      }
    }
    return out;
  })()`);
  console.log('\n--- full-viewport painted layers (the scrim candidates) ---');
  console.log(backdrops.join('\n') || '(none)');
} catch (err) {
  console.log('FAILED:', err.message);
  process.exitCode = 1;
} finally {
  try { chrome.kill(); } catch { /* gone */ }
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* best effort */ }
}
