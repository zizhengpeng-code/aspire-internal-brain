/**
 * Inspect the running app's real DOM and capture per-page screenshots.
 *
 * Sign in as ACCOUNT, walk the pages, and for each page screenshot it and report which of a
 * fixed list of Streamlit selectors actually match — plus the computed style of the first
 * match, so a stylesheet can be written against the real DOM rather than a guess.
 *
 *   CHROME_PATH="..." TARGET_URL=http://127.0.0.1:8502 ACCOUNT="Grace Lim" \
 *   OUT_PREFIX=results/ui/before node results/ui-probe.mjs
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const CHROME = process.env.CHROME_PATH;
const PORT = Number(process.env.CDP_PORT || 9336);
const URL = process.env.TARGET_URL || 'http://127.0.0.1:8502';
const OUT = process.env.OUT_PREFIX || 'ui';
const ACCOUNT = process.env.ACCOUNT || 'Grace Lim';
const PAGES = (process.env.PAGES ?? 'Chat,Admin,Compliance,ROI').split(',').filter(Boolean);

fs.mkdirSync(path.dirname(OUT), { recursive: true });

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-'));
const chrome = spawn(CHROME, [
  `--remote-debugging-port=${PORT}`,
  '--remote-allow-origins=*',
  '--no-sandbox', '--disable-dev-shm-usage',
  '--user-data-dir=' + profile,
  '--no-first-run', '--no-default-browser-check',
  '--disable-gpu', '--hide-scrollbars',
  '--window-size=1440,1100',
  'about:blank',
], { stdio: ['ignore', 'pipe', 'pipe'] });
let browserLog = '';
chrome.stderr.on('data', (d) => { browserLog += String(d); });

class CDP {
  constructor() { this.id = 0; this.pending = new Map(); }
  connect(url) {
    this.ws = new WebSocket(url);
    this.ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(JSON.stringify(msg.error))); else resolve(msg.result);
      }
    });
    return new Promise((resolve, reject) => {
      this.ws.addEventListener('open', () => resolve());
      this.ws.addEventListener('error', () => reject(new Error('ws error')));
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP timeout: ${method}`));
      }, 30000);
      this.pending.set(id, {
        resolve: (v) => { clearTimeout(timer); resolve(v); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  async eval(expression) {
    const r = await this.send('Runtime.evaluate',
                              { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('eval failed: ' + JSON.stringify(r.exceptionDetails));
    return r.result.value;
  }
  async shot(file) {
    // A loaded machine occasionally blows CDP's 30s screenshot budget; one retry is enough and
    // much cheaper than re-running the whole walk.
    for (let attempt = 1; ; attempt++) {
      try {
        const r = await this.send('Page.captureScreenshot', { format: 'png' });
        fs.writeFileSync(file, Buffer.from(r.data, 'base64'));
        console.log('  saved', file);
        return;
      } catch (err) {
        if (attempt >= 3) throw err;
        console.log(`  (screenshot retry ${attempt}: ${err.message})`);
        await sleep(1500);
      }
    }
  }
}

async function findPage() {
  for (let i = 0; i < 120; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      if (r.ok) {
        const page = (await r.json()).find((t) => t.type === 'page');
        if (page?.webSocketDebuggerUrl) return page;
      }
    } catch { /* not up yet */ }
    await sleep(250);
  }
  throw new Error('devtools never came up\n' + browserLog);
}

const SELECTORS = [
  '.stApp',
  '[data-testid="stMain"]',
  '[data-testid="stSidebar"]',
  '[data-testid="stSidebar"] h1',
  '[data-testid="stSidebar"] [data-baseweb="select"] > div',
  '[data-testid="stSidebar"] .stButton > button',
  '[data-testid="stSidebar"] [role="radiogroup"] label',
  '[data-testid="stAlert"]',
  '[data-testid="stAlert"] p',
  '[data-testid="stChatMessage"]',
  '[data-testid="stChatMessageContent"]',
  '[data-testid="stChatMessageAvatarUser"]',
  '[data-testid="stChatMessageAvatarAssistant"]',
  '[data-testid="stChatInput"]',
  '[data-testid="stChatInputContainer"]',
  '[data-testid="stChatInput"] textarea',
  '[data-testid="stChatInput"] button',
  '[data-testid="stBottom"]',
  '[data-testid="stDataFrameResizable"]',
  '[data-testid="stMarkdownContainer"] a',
  '[data-testid="stAlertContainer"]',
  '[data-testid="stExpander"]',
  '[data-testid="stExpander"] summary',
  '[data-testid="stTabs"] [data-baseweb="tab"]',
  '[data-baseweb="tab-highlight"]',
  '[data-testid="stMetric"]',
  '[data-testid="stMetricValue"]',
  '[data-testid="stMetricLabel"]',
  '[data-testid="stDataFrame"]',
  '[data-baseweb="input"]',
  '[data-baseweb="input"] > div',
  '[data-baseweb="tab-highlight"]',
  '[data-baseweb="tab-border"]',
  '[data-testid="stTabs"] [aria-selected="true"]',
  '[data-testid="stTabs"] [data-baseweb="tab"]',
  '[data-testid="stMarkdownContainer"] code',
  '[data-testid="stVerticalBlockBorderWrapper"]',
  'button[kind="secondary"]',
  'button[kind="primary"]',
  '[data-testid="stCaptionContainer"]',
  '[data-testid="stHeading"]',
  '[data-testid="stDivider"]',
];

// The dialog is an overlay of its own, so none of the page selectors above reach it — while it
// is open they all still resolve to whatever is behind it. It gets its own probe.
const DIALOG_SELECTORS = [
  '[data-testid="stModal"]',
  '[data-testid="stDialog"]',
  'div[role="dialog"]',
  '[data-testid="stDialog"] h2',
  '[data-testid="stDialog"] p',
  '[data-testid="stDialog"] [data-testid="stMarkdownContainer"]',
  '[data-testid="stDialog"] [data-testid="stCaptionContainer"]',
  '[data-testid="stDialog"] button',
  '[data-testid="stDialog"] button[kind="primary"]',
  '[data-testid="stDialog"] [data-testid="stVerticalBlock"]',
  '[data-testid="stDialog"] svg',
];

const probeJs = (selectors) => `(() => {
  const out = {};
  for (const sel of ${JSON.stringify(selectors)}) {
    let els = [];
    try { els = [...document.querySelectorAll(sel)]; } catch (e) { out[sel] = 'BAD SELECTOR'; continue; }
    if (!els.length) { out[sel] = null; continue; }
    const cs = getComputedStyle(els[0]);
    out[sel] = {
      n: els.length,
      bg: cs.backgroundColor,
      color: cs.color,
      radius: cs.borderRadius,
      border: cs.borderColor + ' ' + cs.borderWidth,
      weight: cs.fontWeight,
      font: cs.fontFamily.split(',')[0],
      html: els[0].outerHTML.replace(/\\s+/g, ' ').slice(0, 220),
    };
  }
  return out;
})()`;

const PROBE_JS = probeJs(SELECTORS);
const DIALOG_PROBE_JS = probeJs(DIALOG_SELECTORS);

function dumpProbe(label, probe) {
  console.log(`   ${label} (n / bg / color / radius / weight)`);
  for (const [sel, info] of Object.entries(probe)) {
    if (info === null) continue;
    if (info === 'BAD SELECTOR') { console.log(`     BAD  ${sel}`); continue; }
    console.log(`     ${String(info.n).padStart(3)}  ${sel}`);
    console.log(`          bg=${info.bg} color=${info.color} r=${info.radius} ` +
                `border=${info.border} w=${info.weight} font=${info.font}`);
  }
}

const cdp = new CDP();

try {
  const page = await findPage();
  await cdp.connect(page.webSocketDebuggerUrl);
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Emulation.setDeviceMetricsOverride',
                 { width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false });
  await cdp.send('Page.navigate', { url: URL });

  const bodyText = () => cdp.eval('document.body ? document.body.innerText : ""');
  async function waitText(sub, ms = 90000) {
    const t0 = Date.now();
    let last = '';
    while (Date.now() - t0 < ms) {
      last = await bodyText();
      if (last.includes(sub)) return last;
      await sleep(400);
    }
    throw new Error(`timed out waiting for ${JSON.stringify(sub)}; body:\n${last.slice(0, 800)}`);
  }
  async function clickText(sub, ms = 30000) {
    const t0 = Date.now();
    for (;;) {
      const ok = await cdp.eval(`(() => {
        const el = [...document.querySelectorAll('button, label')]
          .find(e => (e.innerText || '').trim().includes(${JSON.stringify(sub)}));
        if (!el) return false;
        el.scrollIntoView({ block: 'center' });
        el.click();
        return true;
      })()`);
      if (ok) return;
      if (Date.now() - t0 > ms) throw new Error('no button/label matching ' + sub);
      await sleep(400);
    }
  }

  console.log('1. sign-in screen');
  await waitText('Sign in to Internal Brain');
  await sleep(1200);
  await cdp.shot(`${OUT}-0-signin.png`);

  // A first-time visitor meets the shared-instance warning here, on the sign-in screen, so this
  // is where the dialog is worth capturing. `SHOT_DIALOG` keeps it out of the other walks.
  if (process.env.SHOT_DIALOG) {
    console.log('1b. shared-demo dialog (open → acknowledge)');
    await clickText('Shared demo');
    await sleep(1000);
    await cdp.shot(`${OUT}-0-dialog.png`);
    const dprobe = await cdp.eval(DIALOG_PROBE_JS);
    fs.writeFileSync(`${OUT}-probe-dialog.json`, JSON.stringify(dprobe, null, 1));
    dumpProbe('dialog', dprobe);
    await clickText('Got it');
    await sleep(1000);
    await cdp.shot(`${OUT}-0-dialog-closed.png`);
    const gone = await cdp.eval(
      `document.querySelectorAll('[data-testid="stDialog"], div[role="dialog"]').length`);
    console.log(`   dialogs in the DOM after "Got it": ${gone}`);
  }

  console.log('2. sign in as', ACCOUNT);
  await clickText(ACCOUNT);
  await sleep(700);
  // The row's selected state only exists between picking an account and pressing Continue, so it
  // needs its own shot or it is never seen.
  if (process.env.SHOT_SELECTED) {
    await cdp.shot(`${OUT}-0-sel.png`);
  }
  await clickText(`Continue as ${ACCOUNT}`);
  await waitText('Internal Brain', 60000);
  await sleep(2500);

  const ASK = process.env.ASK || '';
  if (ASK) {
    console.log('2b. ask:', JSON.stringify(ASK));
    await clickText('Chat');
    await sleep(800);
    const focusInput = async (ms = 60000) => {
      const t0 = Date.now();
      for (;;) {
        const ok = await cdp.eval(`(() => {
          const t = document.querySelector('textarea[data-testid="stChatInputTextArea"]')
                 || document.querySelector('section[data-testid="stChatInput"] textarea')
                 || document.querySelector('textarea');
          if (!t) return false;
          t.focus();
          return true;
        })()`);
        if (ok) return;
        if (Date.now() - t0 > ms) throw new Error('no chat input');
        await sleep(500);
      }
    };
    const assistantCount = async () => cdp.eval(
      `[...document.querySelectorAll('div[data-testid="stChatMessage"]')]`
      + `.filter(m => !m.querySelector('[data-testid="stChatMessageAvatarUser"]')).length`);
    const before = await assistantCount();
    await focusInput();
    await cdp.send('Input.insertText', { text: ASK });
    await sleep(600);
    // The send arrow is grey while the box is empty and green once there is something to send,
    // so the typed state has to be captured before Enter is pressed.
    await cdp.shot(`${OUT}-0a-typed.png`);
    for (const type of ['keyDown', 'keyUp']) {
      await cdp.send('Input.dispatchKeyEvent',
                     { type, key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13,
                       nativeVirtualKeyCode: 13, text: '\r' });
    }
    const t0 = Date.now();
    let stable = 0;
    let prev = '';
    while (Date.now() - t0 < 120000) {
      await sleep(900);
      const cur = await bodyText();
      if ((await assistantCount()) > before && cur === prev) {
        stable += 1;
        if (stable >= 3) break;
      } else {
        stable = 0;
      }
      prev = cur;
    }
    await cdp.shot(`${OUT}-0b-chat-answer.png`);
  }

  for (const pageName of PAGES) {
    console.log(`3. page ${pageName}`);
    await clickText(pageName);
    await sleep(2500);
    await cdp.shot(`${OUT}-${pageName.toLowerCase()}.png`);
    const probe = await cdp.eval(PROBE_JS);
    fs.writeFileSync(`${OUT}-probe-${pageName.toLowerCase()}.json`,
                     JSON.stringify(probe, null, 1));
    dumpProbe('selectors', probe);
  }
} catch (err) {
  console.log('FAILED:', err.message);
  process.exitCode = 1;
} finally {
  try { chrome.kill(); } catch { /* already gone */ }
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* best effort */ }
}
