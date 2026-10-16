import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CHROME = process.env.CHROME_PATH, PORT = 9339, URL = process.env.TARGET_URL;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-'));
const chrome = spawn(CHROME, [`--remote-debugging-port=${PORT}`, '--remote-allow-origins=*',
  '--no-sandbox','--disable-dev-shm-usage','--user-data-dir='+profile,'--no-first-run',
  '--no-default-browser-check','--disable-gpu','--window-size=1440,1100','about:blank'],
  { stdio: ['ignore','pipe','pipe'] });
let id = 0; const pending = new Map(); let ws;
const send = (method, params={}) => new Promise((res,rej)=>{ const i=++id;
  const t=setTimeout(()=>{pending.delete(i);rej(new Error('timeout '+method));},30000);
  pending.set(i,{resolve:v=>{clearTimeout(t);res(v);},reject:e=>{clearTimeout(t);rej(e);}});
  ws.send(JSON.stringify({id:i,method,params})); });
const ev = async (e) => { const r = await send('Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true});
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails)); return r.result.value; };
try {
  let pg; for (let i=0;i<120&&!pg;i++){ try{ const r=await fetch(`http://127.0.0.1:${PORT}/json/list`);
    if(r.ok) pg=(await r.json()).find(t=>t.type==='page'); }catch{} if(!pg) await sleep(250); }
  ws = new WebSocket(pg.webSocketDebuggerUrl);
  await new Promise((res,rej)=>{ ws.addEventListener('open',()=>res()); ws.addEventListener('error',()=>rej(new Error('ws'))); });
  ws.addEventListener('message', (e)=>{ const m=JSON.parse(e.data);
    if(m.id&&pending.has(m.id)){ const {resolve,reject}=pending.get(m.id); pending.delete(m.id);
      m.error?reject(new Error(JSON.stringify(m.error))):resolve(m.result); } });
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
  await send('Page.navigate',{url:URL});
  const body = ()=>ev('document.body?document.body.innerText:""');
  for (let i=0;i<200;i++){ if((await body()).includes('Sign in to Internal Brain')) break; await sleep(400); }
  await sleep(1500);
  for (let i=0;i<60;i++){ const ok = await ev(`(()=>{const el=[...document.querySelectorAll('button')].find(e=>(e.innerText||'').trim().includes('Shared demo'));if(!el)return false;el.click();return true;})()`); if(ok)break; await sleep(400); }
  await sleep(1500);
  const detail = await ev(`(() => {
    const root = document.querySelector('[data-testid="stDialog"]');
    const panel = root && root.querySelector(':scope > div');
    const out = { panelRadius: panel ? getComputedStyle(panel).borderRadius : null,
                  panelShadow: panel ? getComputedStyle(panel).boxShadow : null };
    out.elements = [...root.querySelectorAll('h2, h2 p, section[role=dialog] > div p, [data-testid=stMarkdownContainer] p, [data-testid=stCaptionContainer] p, button[kind=primary]')]
      .map(e => ({ tag: e.tagName.toLowerCase(), text: (e.innerText||'').trim().slice(0,32),
                   font: getComputedStyle(e).fontFamily.split(',')[0],
                   weight: getComputedStyle(e).fontWeight,
                   size: getComputedStyle(e).fontSize,
                   color: getComputedStyle(e).color }));
    return out;
  })()`);
  console.log(JSON.stringify(detail, null, 1));
} catch (e) { console.log('FAILED:', e.message); process.exitCode = 1; }
finally { try{chrome.kill();}catch{} try{fs.rmSync(profile,{recursive:true,force:true});}catch{} }
