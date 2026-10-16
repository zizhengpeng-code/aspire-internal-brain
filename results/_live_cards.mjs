/**
 * One-off: verify the citation-card grid on the *live* public demo.
 *
 * `results/_cards_dump.mjs` waits a fixed 14s and then dumps `stColumn`; pointed at the hosted
 * instance that printed "(no stColumn)", which says nothing about *why* — the answer may still
 * have been generating, the question may never have been submitted, or the demo's per-day
 * question budget may have been exhausted. So this polls for the cards, and on timeout prints
 * the page text instead of a bare "absent", which tells those three cases apart.
 *
 *   CHROME_PATH="..." node results/_live_cards.mjs
 *
 * It does spend one question from the demo budget (MAX_QUESTIONS_PER_SESSION=20,
 * MAX_QUESTIONS_PER_DAY=300). Run Admin -> Reset demo state afterwards to restore it.
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const CHROME = process.env.CHROME_PATH;
const PORT = Number(process.env.CDP_PORT || 9361);
const URL = process.env.TARGET_URL || 'https://permission-aware-kb.app.workbuddy.host';
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
  const t = setTimeout(() => { pending.delete(i); rej(new Error('timeout ' + method)); }, 60000);
  pending.set(i, { resolve: (v) => { clearTimeout(t); res(v); }, reject: (e) => { clearTimeout(t); rej(e); } });
  ws.send(JSON.stringify({ id: i, method, params }));
});
const ev = async (e) => {
  const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return r.result.value;
};

/* Each card is one `st.container(border=True)`, which in Streamlit 1.65 is not a special
   testid — it is an ordinary `stVerticalBlock` that happens to carry a border. So the cards are
   found the way the browser sees them: a vertical block with a visible top border. */
const CARDS = `(() => {
  /* Streamlit 1.65 gives st.columns columns the stColumn testid. An earlier probe looked for
     plain "column" and always counted zero, which made the row look ungridded even when the
     cards were perfectly aligned. (No backticks anywhere inside these template literals.) */
  const cols = [...document.querySelectorAll('[data-testid="stColumn"], [data-testid="column"]')];
  const cardCol = cols.filter(c => c.querySelector('[data-testid="stVerticalBlock"]'));
  const cards = [...document.querySelectorAll('[data-testid="stVerticalBlock"]')]
    .filter(e => getComputedStyle(e).borderTopWidth !== '0px');
  const rows = {};
  for (const c of cards) {
    const b = c.getBoundingClientRect();
    const key = Math.round(b.top / 8) * 8;
    (rows[key] = rows[key] || []).push({
      top: Math.round(b.top * 10) / 10, bottom: Math.round(b.bottom * 10) / 10,
      h: Math.round(b.height * 10) / 10, w: Math.round(b.width * 10) / 10,
    });
  }
  const spread = (arr, f) => arr.length
    ? Math.round((Math.max(...arr.map(f)) - Math.min(...arr.map(f))) * 10) / 10 : null;
  const lines = [];
  for (const [k, v] of Object.entries(rows).sort((a, b) => a[0] - b[0])) {
    lines.push('row cards=' + v.length
      + '  top spread=' + spread(v, x => x.top)
      + '  bottom spread=' + spread(v, x => x.bottom)
      + '  height spread=' + spread(v, x => x.h)
      + '  width spread=' + spread(v, x => x.w));
    for (const c of v) lines.push('    top=' + c.top + ' bottom=' + c.bottom
      + ' h=' + c.h + ' w=' + c.w);
  }
  return { columns: cols.length, cardCols: cardCol.length, cards: cards.length,
           rows: lines.join('\\n') };
})()`;

/* The footnote line inside each card. `margin-top:auto` pins it to the bottom, so the
   "Open in ..." links of one row should share a y — that was 39px apart before the fix. */
/* The footnote is `st.markdown("[Open in <source>](url)")`, so it lands as an `<a>` inside a
   `stMarkdownContainer` — not inside a `stCaptionContainer`, which is where the day/ location
   caption goes (and where an earlier version of this probe looked, finding nothing). */
const LINKS = `(() => {
  const links = [...document.querySelectorAll('a')]
    .filter(a => /^Open in /i.test((a.innerText || '').trim()));
  if (!links.length) {
    const any = [...document.querySelectorAll('a')]
      .map(a => JSON.stringify((a.innerText || '').trim().slice(0, 24)));
    return '(no "Open in" links; anchors present: ' + (any.join(' ') || 'none') + ')';
  }
  const rows = {};
  for (const l of links) {
    const b = l.getBoundingClientRect();
    (rows[Math.round(b.top / 8) * 8] = rows[Math.round(b.top / 8) * 8] || []).push(
      Math.round(b.top * 10) / 10);
  }
  return [...Object.entries(rows)].map(([k, v]) =>
    'row top=' + k + ' links=' + v.length + ' y=[' + v.join(', ') + ']').join('\\n');
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
  await sleep(3000);
  await clickText('Chat');
  await sleep(1500);

  /* Wait for the input instead of assuming it is there. Straight after a deploy the first render
     seeds the audit history and warms the vector index, so the chat input is missing for the
     first ~15s; probing before that produced a misleading "no chat textarea". */
  const hasInput = () => ev(
    `!!document.querySelector('textarea[data-testid="stChatInputTextArea"]')`);
  let waited = 0;
  while (waited < 180000 && !(await hasInput())) { await sleep(2000); waited += 2000; }
  if (!(await hasInput())) throw new Error('no chat input after ' + (waited / 1000) + 's');
  await sleep(1500);

  /* Prefer the suggestion button over typing. A earlier version of this synthesised keystrokes
     into the textarea (`Input.insertText` + an Enter key event) and it is fragile: it depends on
     React having attached its handlers by the time the keys land, and when it silently fails the
     probe reports "no cards" as if the layout were broken. The welcome screen already offers this
     exact question as a button, and clicking it is what a visitor does. */
  let clicked = await ev(`(() => {
    const btns = [...document.querySelectorAll('button')];
    const el = btns.find(b => (b.innerText || '').trim().includes(${JSON.stringify(ASK.slice(0, 40))}));
    if (!el) return 'no-match'; el.click(); return 'clicked'; })()`);

  if (clicked === 'no-match') {
    /* Already asked once, so the suggestion is gone: type it instead. */
    await ev(`document.querySelector('textarea[data-testid="stChatInputTextArea"]').focus()`);
    await send('Input.insertText', { text: ASK });
    await sleep(500);
    for (const type of ['keyDown', 'keyUp']) {
      await send('Input.dispatchKeyEvent',
                 { type, key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13,
                   nativeVirtualKeyCode: 13, text: '\r' });
    }
    clicked = 'typed';
  }
  console.log('submitted via:', clicked);

  /* Poll instead of a fixed sleep: generation time on the hosted box varies, and a fixed wait
    that is too short is exactly what produced the misleading "(no stColumn)" earlier. */
  let cards = null;
  for (let i = 0; i < 40; i++) {
    await sleep(2000);
    cards = await ev(CARDS);
    if (cards.cards >= 3) break;
  }

  console.log('live url :', await ev('location.href'));
  console.log('columns=%d  columns-with-cards=%d  cards=%d',
              cards.columns, cards.cardCols, cards.cards);
  console.log('\n--- card rows ---');
  console.log(cards.rows || '(none)');
  console.log('\n--- "Open in ..." link rows ---');
  console.log(await ev(LINKS));

  if (cards.cards < 3) {
    console.log('\nPAGE TEXT (why no cards?):');
    console.log((await body()).slice(0, 1500));
  }
} catch (err) {
  console.log('FAILED:', err.message);
  process.exitCode = 1;
} finally {
  try { chrome.kill(); } catch { /* gone */ }
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* best effort */ }
}
