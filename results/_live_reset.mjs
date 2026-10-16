/**
 * One-off: put the hosted demo back to a clean state.
 *
 * The demo instance is shared — every visitor sees the same audit log and the same transcripts —
 * so the probes that verify the live site (which have to submit a real question to make citation
 * cards render) leave artefacts behind that a judge would then see. This signs in, opens
 * Admin and clicks **Reset demo state**, which is the last item on the pre-submission checklist.
 *
 *   CHROME_PATH="..." node results/_live_reset.mjs
 *
 * It then prints the audit-log header, which is what tells you the reset actually happened:
 * `simulate.reset` drops `state/` and restores the seeded audit log, so the record count drops
 * back to the seeded value and the transcript for the signed-in user goes empty.
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const CHROME = process.env.CHROME_PATH;
const PORT = Number(process.env.CDP_PORT || 9381);
const URL = process.env.TARGET_URL || 'https://permission-aware-kb.app.workbuddy.host';
const ACCOUNT = process.env.ACCOUNT || 'Jamie Doe';

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

const AUDIT = `(() => {
  const text = document.body.innerText;
  const m = text.match(/(\\d+) records, (\\d+) signed checkpoints/);
  const evt = text.match(/state_reset/g);
  return { header: m ? m[0] : '(audit header not found)',
           records: m ? Number(m[1]) : null,
           stateResetEvents: evt ? evt.length : 0 };
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
  const clickText = async (sub, tags = 'button, label') => {
    for (let i = 0; i < 80; i++) {
      const ok = await ev(`(() => {
        const el = [...document.querySelectorAll(${JSON.stringify(tags)})]
          .find(e => (e.innerText || '').trim().includes(${JSON.stringify(sub)}));
        if (!el) return false; el.click(); return true; })()`);
      if (ok) return;
      await sleep(500);
    }
    throw new Error('no clickable ' + sub);
  };
  const hasInput = () => ev(
    `!!document.querySelector('textarea[data-testid="stChatInputTextArea"]')`);

  await waitText('Sign in to Internal Brain');
  await sleep(1200);
  await clickText(ACCOUNT);
  await sleep(800);
  await clickText(`Continue as ${ACCOUNT}`);
  await waitText('Internal Brain', 90000);

  let waited = 0;
  while (waited < 180000 && !(await hasInput())) { await sleep(2000); waited += 2000; }
  if (!(await hasInput())) throw new Error('chat input never appeared');
  await sleep(1500);

  /* The radio lives in the sidebar; its options are `label` elements, which is why `clickText`
     is called with the label tag set rather than the default button/label mix — "Admin" is not
     a button. */
  await clickText('Admin');
  await sleep(2500);

  const before = await ev(AUDIT);
  console.log('before reset :', JSON.stringify(before));

  await clickText('Reset demo state');
  /* `run_action` calls `simulate.reset` then `reload_sessions`, so give the rerun time. */
  await sleep(9000);

  const after = await ev(AUDIT);
  console.log('after reset  :', JSON.stringify(after));

  const chatEmpty = (await body()).includes(
    'Internal Brain answers questions about LumenPay');
  console.log('\nwelcome message back on the chat page:', chatEmpty ? 'YES' : '(need to switch to Chat to see)');

  await clickText('Chat');
  await sleep(4000);
  console.log('chat page shows the welcome screen   :',
    (await body()).includes('Internal Brain answers questions about LumenPay') ? 'YES' : 'NO');
} catch (err) {
  console.log('FAILED:', err.message);
  process.exitCode = 1;
} finally {
  try { chrome.kill(); } catch { /* gone */ }
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* best effort */ }
}
