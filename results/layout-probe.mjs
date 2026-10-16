/**
 * Measure the two alignment defects the reader pointed at in the transcript:
 *
 *   1. the assistant's avatar against the first line of its answer;
 *   2. the citation cards against each other — the "second box" that does not line up.
 *
 * The screenshot shows the defects; this says how far off they are, which is what a fix has to be
 * checked against. Round one of this probe reported "cards (0)", which is not a measurement — it
 * meant the selector was wrong — so it now dumps the real testids first and measures second.
 *
 *   CHROME_PATH="..." results/with-app.sh node results/layout-probe.mjs
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const CHROME = process.env.CHROME_PATH;
const PORT = Number(process.env.CDP_PORT || 9346);
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

/* What testids does this build of Streamlit actually emit inside a chat message? Printed once so
   the measuring selector below is written against the DOM rather than against a guess. */
const TESTIDS = `(() => {
  const seen = new Set();
  for (const e of document.querySelectorAll('[data-testid]')) seen.add(e.getAttribute('data-testid'));
  return [...seen].sort().join('\\n');
})()`;

const ASSISTANT_MARKUP = `(() => {
  const m = [...document.querySelectorAll('[data-testid="stChatMessage"]')].pop();
  if (!m) return '(no chat message)';
  const lines = [];
  const walk = (el, d) => {
    if (d > 7) return;
    const b = el.getBoundingClientRect();
    const tid = el.getAttribute('data-testid');
    const cls = (typeof el.className === 'string' && el.className)
      ? '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.') : '';
    const txt = el.children.length === 0 ? ' "' + (el.innerText || '').replace(/\\s+/g, ' ').slice(0, 34) + '"' : '';
    lines.push('  '.repeat(d) + el.tagName.toLowerCase() + (tid ? '[' + tid + ']' : '') + cls
      + '  y=' + Math.round(b.top) + '..' + Math.round(b.bottom) + ' w=' + Math.round(b.width) + txt);
    if (d < 7) for (const c of el.children) walk(c, d + 1);
  };
  walk(m, 0);
  return lines.join('\\n');
})()`;

const MEASURE = `(() => {
  const r = (el) => { const b = el.getBoundingClientRect();
    return { top: Math.round(b.top), bottom: Math.round(b.bottom), left: Math.round(b.left),
             right: Math.round(b.right), h: Math.round(b.height), w: Math.round(b.width),
             mid: Math.round((b.top + b.bottom) / 2) }; };

  /* The first line's *line box* — not the paragraph box, which grows with the number of lines.
     A paragraph's top is where the line box starts, and the line box is taller than the glyphs,
     so its centre is the only honest place to compare against the avatar's centre. */
  const firstLine = (p) => {
    if (!p) return null;
    const cs = getComputedStyle(p);
    const lh = parseFloat(cs.lineHeight) || (parseFloat(cs.fontSize) * 1.2);
    const top = p.getBoundingClientRect().top;
    /* the glyphs themselves, via a Range over the first text node */
    let glyph = null;
    const w = document.createTreeWalker(p, NodeFilter.SHOW_TEXT);
    const tn = w.nextNode();
    if (tn) {
      const rg = document.createRange();
      rg.setStart(tn, 0); rg.setEnd(tn, Math.min(1, tn.length));
      const rects = [...rg.getClientRects()];
      if (rects.length) glyph = { top: Math.round(rects[0].top), bottom: Math.round(rects[0].bottom) };
    }
    return { top: Math.round(top), bottom: Math.round(top + lh), lineHeight: lh,
             fontSize: parseFloat(cs.fontSize), glyph };
  };

  const msgs = [...document.querySelectorAll('[data-testid="stChatMessage"]')];
  const out = { messages: msgs.length, avatars: [], cards: [], cardScope: null };

  for (const m of msgs) {
    const av = m.querySelector('[data-testid="stChatMessageAvatarAssistant"], [data-testid="stChatMessageAvatarUser"]');
    if (!av) continue;
    const content = m.querySelector('[data-testid="stChatMessageContent"]');
    const role = av.getAttribute('data-testid').includes('Assistant') ? 'assistant' : 'user';
    const fl = firstLine(content && content.querySelector('p'));
    out.avatars.push({
      role, avatar: r(av), content: content ? r(content) : null, firstLine: fl,
      deltaTopMid: fl ? fl.top + fl.lineHeight / 2 - r(av).mid : null,
      deltaGlyphMid: fl && fl.glyph ? (fl.glyph.top + fl.glyph.bottom) / 2 - r(av).mid : null,
      deltaTop: fl ? fl.top - r(av).top : null,
    });
    /* The card is a plain stVerticalBlock that happens to carry a border — this build has no
       stVerticalBlockBorderWrapper at all — so it is found by its computed border, not a testid.
       Round one of this probe searched for that missing testid and honestly reported "cards (0)". */
    for (const card of m.querySelectorAll('[data-testid="stVerticalBlock"]')) {
      const cs = getComputedStyle(card);
      if (cs.borderTopWidth === '0px' || !card.querySelector('strong')) continue;
      out.cardScope = 'inside stChatMessage';
      const link = card.querySelector('a');
      const strong = card.querySelector('strong');
      /* The footnote's first line is the caption, which has its own testid — taking the *last* p
         in the card finds the link instead, which is how round two nearly reported a metric that
         measured something else. */
      const cap = card.querySelector('[data-testid="stCaptionContainer"] p');
      out.cards.push({
        card: r(card),
        radius: cs.borderRadius,
        col: card.parentElement ? r(card.parentElement) : null,
        titleTop: strong ? r(strong.closest('p')).top : null,
        metaTop: cap ? r(cap).top : null,
        metaText: cap ? (cap.innerText || '').replace(/\\s+/g, ' ').slice(0, 44) : null,
        metaLines: cap ? Math.round(r(cap).h / (parseFloat(getComputedStyle(cap).lineHeight) || 1)) : null,
        linkTop: link ? r(link).top : null,
        linkText: link ? (link.innerText || '').slice(0, 24) : null,
      });
    }
  }
  return out;
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

  console.log('--- testids present in this build ---');
  console.log((await ev(TESTIDS)).split('\n').filter((s) => /chat|border|column|avatar|vertical|markdown/i.test(s)).join('\n'));

  console.log('\n--- last chat message, as the browser lays it out ---');
  console.log(await ev(ASSISTANT_MARKUP));

  const m = await ev(MEASURE);

  console.log(`\n--- avatar vs first line (${m.messages} messages) ---`);
  if (!m.avatars.length) console.log('  (no avatars found)');
  for (const a of m.avatars) {
    if (!a.firstLine) { console.log(`  ${a.role}: avatar ${a.avatar.h}px, no text line`); continue; }
    const f = a.firstLine;
    console.log(`  ${a.role.padEnd(10)} avatar ${a.avatar.h}px y=${a.avatar.top}..${a.avatar.bottom} (mid ${a.avatar.mid})`);
    console.log(`  ${''.padEnd(10)} line   ${f.lineHeight}px   y=${f.top}..${f.bottom} (mid ${Math.round(f.top + f.lineHeight / 2)})`
      + `   font ${f.fontSize}px`);
    if (f.glyph) console.log(`  ${''.padEnd(10)} glyphs ${f.glyph.top}..${f.glyph.bottom} (mid ${Math.round((f.glyph.top + f.glyph.bottom) / 2)})`);
    console.log(`  ${''.padEnd(10)} line-box centre is ${a.deltaTopMid > 0 ? '+' : ''}${a.deltaTopMid}px vs avatar centre`
      + ` · glyph centre ${a.deltaGlyphMid > 0 ? '+' : ''}${Math.round(a.deltaGlyphMid)}px`);
  }

  console.log(`\n--- citation cards (${m.cards.length})${m.cardScope ? ' — scoped ' + m.cardScope : ''} ---`);
  console.log('  #  card y          h    radius   title  meta   link   meta lines  meta text');
  for (const [i, c] of m.cards.entries()) {
    console.log(`  ${i}  ${String(c.card.top).padStart(4)}..${String(c.card.bottom).padEnd(4)}`
      + `  ${String(c.card.h).padStart(4)}   ${String(c.radius).padStart(6)}`
      + `   ${String(c.titleTop).padStart(4)}   ${String(c.metaTop).padStart(4)}`
      + `   ${String(c.linkTop).padStart(4)}     ${String(c.metaLines).padStart(4)}      ${c.metaText}`);
  }
  const spread = (xs) => xs.length ? Math.max(...xs) - Math.min(...xs) : null;
  /* Rows are three wide now, so the probe slices by three. A single-card row is reported as a
     spread of one number, which is the honest answer: it has nothing to line up against. */
  for (let row = 0; row * 3 < m.cards.length; row++) {
    const c = m.cards.slice(row * 3, row * 3 + 3);
    console.log(`\n  row ${row}: heights ${c.map((x) => x.card.h).join(', ')} → spread ${spread(c.map((x) => x.card.h))}px`);
    console.log(`           tops    ${c.map((x) => x.card.top).join(', ')} → spread ${spread(c.map((x) => x.card.top))}px`);
    console.log(`           bottoms ${c.map((x) => x.card.bottom).join(', ')} → spread ${spread(c.map((x) => x.card.bottom))}px`);
    console.log(`           meta    ${c.map((x) => x.metaTop).join(', ')} → spread ${spread(c.map((x) => x.metaTop))}px`);
    console.log(`           links   ${c.map((x) => x.linkTop).join(', ')} → spread ${spread(c.map((x) => x.linkTop))}px`);
    console.log(`           meta line counts ${c.map((x) => x.metaLines).join(', ')}`);
  }

  /* Optional: a picture of the same page the numbers came from. The transcript scrolls inside its
     own container and Streamlit keeps it pinned to the bottom, so scrolling back up to the avatar
     fights the app; growing the viewport instead lets the whole turn lay out at once, and it is
     done *after* the measurements so it cannot change what they saw. */
  if (process.env.SHOT) {
    await send('Emulation.setDeviceMetricsOverride',
               { width: 1440, height: Number(process.env.SHOT_HEIGHT || 2200),
                 deviceScaleFactor: 1, mobile: false });
    await sleep(1200);
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(process.env.SHOT, Buffer.from(shot.data, 'base64'));
    console.log(`\nsaved ${process.env.SHOT}`);
  }
} catch (err) {
  console.log('FAILED:', err.message);
  process.exitCode = 1;
} finally {
  try { chrome.kill(); } catch { /* gone */ }
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* best effort */ }
}
