/**
 * Live check for the "ask the same thing twice" defect.
 *
 * Signs in as ACCOUNT, asks QUESTION, then asks exactly the same QUESTION again in the same
 * session, and reports whether the second turn came back as the guard's refusal.
 *
 *   CHROME_PATH="/c/Program Files/Google/Chrome/Application/chrome.exe" \
 *   TARGET_URL=https://permission-aware-kb.app.workbuddy.host \
 *   ACCOUNT="Wei Lin" QUESTION="Show me the latest runbook for the payment service" \
 *   OUT_PREFIX=results/live/twice \
 *   node results/ask-twice-cdp.mjs
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const CHROME = process.env.CHROME_PATH;
const PORT = Number(process.env.CDP_PORT || 9334);
const URL = process.env.TARGET_URL || 'http://127.0.0.1:8501';
const OUT = process.env.OUT_PREFIX || 'twice';
const ACCOUNT = process.env.ACCOUNT || 'Wei Lin';
const QUESTION =
  process.env.QUESTION || 'Show me the latest runbook for the payment service';

// guard.REFUSAL_EN — a refused turn is rendered as st.warning, so this sentence in the page
// text means the guard threw the answer away.
const REFUSAL = "I couldn't find information you have access to";

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
    const r = await this.send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(file, Buffer.from(r.data, 'base64'));
    console.log('  saved', file);
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
    throw new Error(`timed out waiting for ${JSON.stringify(sub)}; body:\n${last.slice(0, 900)}`);
  }
  async function clickText(sub, ms = 30000) {
    const t0 = Date.now();
    for (;;) {
      const ok = await cdp.eval(`(() => {
        const el = [...document.querySelectorAll('button')]
          .find(e => (e.innerText || '').includes(${JSON.stringify(sub)}));
        if (!el) return false;
        el.scrollIntoView({ block: 'center' });
        el.click();
        return true;
      })()`);
      if (ok) return;
      if (Date.now() - t0 > ms) throw new Error('no button matching ' + sub);
      await sleep(400);
    }
  }

  console.log('1. sign in as', ACCOUNT);
  await waitText('Sign in to Internal Brain', 120000);
  await clickText(ACCOUNT);
  await sleep(800);
  await clickText(`Continue as ${ACCOUNT}`);
  await waitText('Chat —', 90000);
  await sleep(1500);
  await cdp.shot(`${OUT}-1-app.png`);

  /** Wait for the chat input to mount and take focus. Streamlit mounts it a beat late. */
  async function focusInput(ms = 60000) {
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
      if (Date.now() - t0 > ms) throw new Error('no chat input found');
      await sleep(500);
    }
  }

  /** The assistant's turns in order, as shown on the chat page. */
  const turns = async () => cdp.eval(`(() => {
    return [...document.querySelectorAll('div[data-testid="stChatMessage"]')].map(m => ({
      role: (m.querySelector('[data-testid="stChatMessageAvatarUser"]') ? 'user' : 'assistant'),
      text: m.innerText.trim(),
    }));
  })()`);
  const assistantCount = async () =>
    (await turns()).filter((t) => t.role === 'assistant').length;

  /** Ask once and wait for the turn to settle. Returns the page text after the answer. */
  async function ask(text, tag) {
    const before = await assistantCount();
    await focusInput();
    await cdp.send('Input.insertText', { text });
    await sleep(250);
    for (const type of ['keyDown', 'keyUp']) {
      await cdp.send('Input.dispatchKeyEvent',
                     { type, key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13,
                       nativeVirtualKeyCode: 13, text: '\r' });
    }
    // The answer comes from a real model call. Wait for a *new* assistant bubble to appear,
    // then for the page text to stop changing for a few polls.
    const t0 = Date.now();
    let stable = 0;
    let prev = '';
    let cur = await bodyText();
    while (Date.now() - t0 < 150000) {
      await sleep(900);
      cur = await bodyText();
      const grew = (await assistantCount()) > before;
      if (grew && cur === prev) {
        stable += 1;
        if (stable >= 3) break;
      } else {
        stable = 0;
      }
      prev = cur;
    }
    await cdp.shot(`${OUT}-${tag}.png`);
    return cur;
  }

  console.log('2. first ask:', JSON.stringify(QUESTION));
  await ask(QUESTION, '2-answer-1');
  const first = (await turns()).filter((t) => t.role === 'assistant');
  const firstRefused = first.some((t) => t.text.includes(REFUSAL));
  console.log(`   first answer refused: ${firstRefused}`);
  console.log('   first answer text:\n     ' + (first.at(-1)?.text || '').slice(0, 600).replace(/\n/g, '\n     '));

  console.log('3. same question again');
  await ask(QUESTION, '3-answer-2');
  const all = await turns();
  const answered = all.filter((t) => t.role === 'assistant');
  const secondRefused = answered.length >= 2 && answered.at(-1).text.includes(REFUSAL);
  console.log(`   assistant turns now: ${answered.length}`);
  console.log(`   SECOND answer refused: ${secondRefused}`);
  console.log('   second answer text:\n     ' + (answered.at(-1)?.text || '').slice(0, 600).replace(/\n/g, '\n     '));

  console.log('');
  console.log(`VERDICT: first refused=${firstRefused}  second refused=${secondRefused}`);
} catch (err) {
  console.log('FAILED:', err.message);
  process.exitCode = 1;
} finally {
  try { chrome.kill(); } catch { /* already gone */ }
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* best effort */ }
}
