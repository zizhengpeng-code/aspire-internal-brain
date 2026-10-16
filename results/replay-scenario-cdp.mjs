/**
 * Live replay of demo scenario ② ("ask it again"), the one that produced the double refusal.
 *
 *   1. sign in as Wei Lin
 *   2. Admin → "Push runbook update (13:00)"        (E06 becomes rev 8, which links E16)
 *   3. Chat → "Clear this chat"
 *   4. ask the runbook question                      → answer cites E06 rev 8 + E16
 *   5. ask the *same* question again                 → the defect was a refusal here
 *   6. Admin → "Reset demo state"                    (leave the shared demo as we found it)
 *
 *   CHROME_PATH="..." TARGET_URL=https://permission-aware-kb.app.workbuddy.host \
 *   OUT_PREFIX=results/live/scenario2 node results/replay-scenario-cdp.mjs
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const CHROME = process.env.CHROME_PATH;
const PORT = Number(process.env.CDP_PORT || 9335);
const URL = process.env.TARGET_URL || 'http://127.0.0.1:8501';
const OUT = process.env.OUT_PREFIX || 'scenario2';
const ACCOUNT = process.env.ACCOUNT || 'Wei Lin';
const QUESTION =
  process.env.QUESTION || 'Show me the latest runbook for the payment service';
const REFUSAL = "I couldn't find information you have access to";
const RESET_AT_END = process.env.RESET_AT_END !== '0';

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
  async function waitGone(sub, ms = 30000) {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) {
      if (!(await bodyText()).includes(sub)) return;
      await sleep(400);
    }
    throw new Error(`still showing ${JSON.stringify(sub)}`);
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
  const turns = async () => cdp.eval(`(() => {
    return [...document.querySelectorAll('div[data-testid="stChatMessage"]')].map(m => ({
      role: (m.querySelector('[data-testid="stChatMessageAvatarUser"]') ? 'user' : 'assistant'),
      text: m.innerText.trim(),
    }));
  })()`);
  const assistantCount = async () => (await turns()).filter((t) => t.role === 'assistant').length;

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
    const t0 = Date.now();
    let stable = 0;
    let prev = '';
    let cur = await bodyText();
    while (Date.now() - t0 < 150000) {
      await sleep(900);
      cur = await bodyText();
      if ((await assistantCount()) > before && cur === prev) {
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

  console.log('1. sign in as', ACCOUNT);
  await waitText('Sign in to Internal Brain', 120000);
  await clickText(ACCOUNT);
  await sleep(800);
  await clickText(`Continue as ${ACCOUNT}`);
  await waitText('Chat —', 90000);
  await sleep(1500);

  console.log('2. Admin → push the 13:00 runbook update');
  await clickText('Admin');
  await waitText('Push runbook update (13:00)', 60000);
  await clickText('Push runbook update (13:00)');
  await waitText('Push runbook update (E06', 60000);       // render_last_action
  await sleep(1500);
  await cdp.shot(`${OUT}-1-admin-pushed.png`);

  console.log('3. Chat → clear this chat');
  await clickText('Chat');
  await waitText('Chat —', 60000);
  await sleep(1000);
  await clickText('Clear this chat');
  await sleep(2500);

  console.log('4. first ask:', JSON.stringify(QUESTION));
  await ask(QUESTION, '2-answer-1');
  const first = (await turns()).filter((t) => t.role === 'assistant');
  const t1 = first.at(-1)?.text || '';
  console.log('   cites [E16]:', t1.includes('[E16]'), '| says rev 8:', t1.includes('rev 8'));

  // Repeat the *same* question. On the pre-fix code a repeat whose answer brackets a document
  // that only an earlier turn returned is replaced by the refusal, so a repeat that brackets
  // [E16] and is still answered is the signal that matters — a repeat that merely mentions
  // E16 in prose proves nothing, because the guard only reads bracketed ids.
  const REPEATS = Number(process.env.REPEATS || 4);
  let anyRefused = false;
  let bracketedAndAnswered = 0;
  for (let i = 1; i <= REPEATS; i++) {
    console.log(`5.${i} same question again (repeat ${i}/${REPEATS})`);
    await ask(QUESTION, `3-answer-${i + 1}`);
    const answered = (await turns()).filter((t) => t.role === 'assistant');
    const text = answered.at(-1)?.text || '';
    const refused = text.includes(REFUSAL);
    const bracketed = text.includes('[E16]');
    anyRefused = anyRefused || refused;
    if (bracketed && !refused) bracketedAndAnswered += 1;
    console.log(`   bracketed [E16]: ${bracketed} | refused: ${refused}`);
    console.log('   answer tail: ' +
                JSON.stringify(text.replace(/\s+/g, ' ').slice(-260)));
  }
  console.log(`   repeats: ${REPEATS} · any refused: ${anyRefused} · ` +
              `bracketed [E16] and still answered: ${bracketedAndAnswered}`);
  const secondRefused = anyRefused;

  // A directed probe. The defect only fires when a turn writes the document id *in square
  // brackets* while this turn's search did not return it; the plain repeats above leave that
  // choice to the model, which has been answering them with a bare "E16". Asking for the linked
  // document by id nudges the model into the bracketed form — which is what rule 1 reads.
  const PROBE = process.env.PROBE ||
    'Which Jira ticket does that runbook link to? Give its document id in square brackets.';
  console.log('6. directed probe:', JSON.stringify(PROBE));
  await ask(PROBE, '4-probe');
  const probeTurns = (await turns()).filter((t) => t.role === 'assistant');
  const probeText = probeTurns.at(-1)?.text || '';
  const probeRefused = probeText.includes(REFUSAL);
  const probeBracketed = /\[E\d+\]/.test(probeText);
  console.log(`   probe bracketed an id: ${probeBracketed} | refused: ${probeRefused}`);
  console.log(`   probe tail: ${JSON.stringify(probeText.replace(/\s+/g, ' ').slice(-300))}`);
  if (probeBracketed && !probeRefused) bracketedAndAnswered += 1;

  if (RESET_AT_END) {
    console.log('6. Admin → reset demo state');
    await clickText('Admin');
    await waitText('Reset demo state', 60000);
    await clickText('Reset demo state');
    await sleep(3000);
    await cdp.shot(`${OUT}-4-admin-reset.png`);
    console.log('   reset done');
  }

  console.log('');
  console.log(`VERDICT: any repeat refused=${secondRefused} · ` +
              `bracketed-[E16]-and-answered=${bracketedAndAnswered}`);
} catch (err) {
  console.log('FAILED:', err.message);
  process.exitCode = 1;
} finally {
  try { chrome.kill(); } catch { /* already gone */ }
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* best effort */ }
}
