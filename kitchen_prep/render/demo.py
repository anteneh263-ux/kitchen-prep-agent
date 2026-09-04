"""Interactive public demo page for the isolated synthetic sandbox."""
from __future__ import annotations


def render_demo() -> str:
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Interactive Demo — Kitchen Prep Agent</title>
<style>
:root { color-scheme: light; --ink:#14201a; --muted:#5c6b62; --line:#dce6df; --paper:#fff;
  --canvas:#eef3ef; --brand:#16513a; --brand2:#1f6a49; --soft:#e4f0ea; --warn:#9a5406;
  --danger:#a32828; --good:#147a3e; --shadow:0 10px 34px rgba(20,50,35,.1); }
* { box-sizing:border-box; }
body { margin:0; background:var(--canvas); color:var(--ink); font-family:Inter,ui-sans-serif,-apple-system,
  BlinkMacSystemFont,"Segoe UI",sans-serif; line-height:1.5; }
.shell { width:min(960px,calc(100% - 1.4rem)); margin:auto; padding:1rem 0 3rem; }
.top { display:flex; align-items:center; justify-content:space-between; gap:1rem; margin-bottom:1rem; }
.brand { font-weight:850; } a { color:var(--brand); }
.badge { display:inline-block; padding:.3rem .6rem; border-radius:999px; background:#fff3df; color:#7a4204;
  font-size:.7rem; font-weight:850; text-transform:uppercase; letter-spacing:.05em; }
.hero,.panel { background:var(--paper); border:1px solid var(--line); border-radius:16px; box-shadow:var(--shadow); }
.hero { padding:clamp(1.2rem,4vw,2rem); background:linear-gradient(135deg,#123d2b,#1f6a49); color:white; }
.hero h1 { margin:.35rem 0; font-size:clamp(1.75rem,5vw,2.5rem); line-height:1.1; }
.hero p { max-width:48rem; margin:.45rem 0; color:#d9ebe1; }
.button { display:inline-flex; align-items:center; justify-content:center; min-height:2.65rem; margin-top:.75rem;
  padding:.65rem 1rem; border:0; border-radius:.7rem; color:white; background:var(--brand); font:inherit;
  font-size:.85rem; font-weight:850; cursor:pointer; }
.hero .button { color:var(--brand); background:white; }
.button.secondary { color:var(--brand); background:white; border:1px solid #aac8b6; }
.button.reject { color:var(--danger); background:white; border:1px solid #ddb4b4; }
.button:disabled { opacity:.55; cursor:not-allowed; }
.grid { display:grid; grid-template-columns:1.25fr .85fr; gap:1rem; margin-top:1rem; align-items:start; }
.stack { display:grid; gap:1rem; }
.head { padding:1rem 1.1rem .75rem; border-bottom:1px solid var(--line); }
.head h2 { margin:0; font-size:1rem; }.head p { margin:.2rem 0 0; color:var(--muted); font-size:.78rem; }
.status { display:inline-block; margin-top:.55rem; padding:.25rem .55rem; border-radius:999px;
  background:var(--soft); color:var(--brand); font-size:.72rem; font-weight:850; text-transform:uppercase; }
.events,.audit { list-style:none; margin:0; padding:0; }
.event { display:grid; grid-template-columns:auto 1fr; gap:.7rem; padding:.75rem 1.05rem; border-bottom:1px solid var(--line); }
.event:last-child { border-bottom:0; }.tick { display:grid; place-items:center; width:1.7rem; height:1.7rem;
  border-radius:50%; background:var(--soft); color:var(--brand); font-weight:900; }
.event strong { display:block; font-size:.79rem; }.tool { color:var(--muted); font: .69rem ui-monospace,SFMono-Regular,Menlo,monospace; }
.empty { margin:0; padding:1rem 1.1rem; color:var(--muted); font-size:.82rem; }
.decision { padding:1rem 1.1rem; }.decision h3 { margin:0 0 .45rem; font-size:1rem; }
.math { padding:.75rem; border-radius:.7rem; background:var(--soft); font-size:.82rem; font-variant-numeric:tabular-nums; }
.math div + div { margin-top:.25rem; }.actions { display:flex; flex-wrap:wrap; gap:.5rem; margin-top:.5rem; }
.kv { display:flex; justify-content:space-between; gap:1rem; padding:.55rem 0; border-bottom:1px solid var(--line); font-size:.8rem; }
.kv span { color:var(--muted); }.result { margin:0; padding:1rem 1.1rem; color:#0f5b2d; background:#eff9f2;
  border-top:1px solid #b9d8c5; font-weight:800; }
.audit li { padding:.65rem 1.05rem; border-bottom:1px solid var(--line); font-size:.76rem; }
.audit li:last-child { border-bottom:0; }.audit time { color:var(--muted); font-size:.68rem; }
.note { margin-top:1rem; color:var(--muted); font-size:.72rem; text-align:center; }
@media(max-width:720px){.grid{grid-template-columns:1fr}.top{align-items:flex-start}.hero{border-radius:14px}}
</style></head><body><main class="shell">
<nav class="top"><div class="brand">Kitchen Prep Agent</div><a href="/?lang=en">Back to live plans</a></nav>
<section class="hero"><span class="badge">Interactive demo · simulated restaurant data</span>
<h1>Run the morning operations agent</h1>
<p>Watch the real deterministic planning tools process 139 covers, detect one controlled service risk and pause before a simulated external action.</p>
<button class="button" id="run" type="button">Run morning plan</button>
<div class="status" id="status">Ready to run</div></section>

<div class="grid"><div class="stack">
<section class="panel"><header class="head"><h2>Live agent activity</h2><p>Each row is emitted by a server-side planning step.</p></header>
<ol class="events" id="events"><li class="empty">Press “Run morning plan” to begin.</li></ol></section>
<section class="panel" id="decision-panel" hidden><header class="head"><h2>Human decision</h2><p>The sandbox pauses before the simulated supplier action.</p></header><div class="decision" id="decision"></div></section>
</div><div class="stack">
<section class="panel"><header class="head"><h2>Run state</h2><p>Session-scoped; never written to production.</p></header><div class="decision" id="state"><p class="empty">No run yet.</p></div><p class="result" id="result" hidden></p></section>
<section class="panel"><header class="head"><h2>Audit trail</h2><p>Append-only events for this demo session.</p></header><ul class="audit" id="audit"><li class="empty">No events yet.</li></ul></section>
</div></div>
<p class="note">This public sandbox uses synthetic data and a simulated supplier connector. It cannot change production plans, inventory or external systems.</p>
</main>
<script>
const runButton=document.getElementById('run'),statusEl=document.getElementById('status'),eventsEl=document.getElementById('events');
const decisionPanel=document.getElementById('decision-panel'),decisionEl=document.getElementById('decision');
const stateEl=document.getElementById('state'),auditEl=document.getElementById('audit'),resultEl=document.getElementById('result');
let sessionId=null;
const label=s=>String(s||'').replaceAll('_',' ');
function setStatus(value){statusEl.textContent=label(value);}
function eventRow(event){const li=document.createElement('li');li.className='event';const tick=document.createElement('span');tick.className='tick';tick.textContent='✓';
 const body=document.createElement('div'),strong=document.createElement('strong'),tool=document.createElement('span');strong.textContent=event.detail;tool.className='tool';tool.textContent=event.tool;
 body.append(strong,tool);li.append(tick,body);return li;}
function renderAudit(events){auditEl.replaceChildren();if(!events.length){const li=document.createElement('li');li.className='empty';li.textContent='No events yet.';auditEl.append(li);return;}
 events.forEach(event=>{const li=document.createElement('li'),detail=document.createElement('div'),time=document.createElement('time');detail.textContent=`${event.sequence}. ${event.detail}`;time.textContent=event.occurred_at;li.append(detail,time);auditEl.append(li);});}
function renderState(state){setStatus(state.status);stateEl.replaceChildren();const rows=[['Session',state.session_id.slice(0,8)+'…'],['Status',label(state.status)]];
 if(state.plan){rows.push(['Expected covers',state.plan.expected_covers],['Critical shortfalls',state.plan.prep_shortfalls.length]);}
 if(state.order){rows.push(['Order',state.order.order_id],['Supplier ETA',state.order.eta],['External effect','Simulated only']);}
 rows.forEach(([key,value])=>{const row=document.createElement('div');row.className='kv';const k=document.createElement('span'),v=document.createElement('strong');k.textContent=key;v.textContent=value;row.append(k,v);stateEl.append(row);});
 renderAudit(state.events||[]);resultEl.hidden=!state.result;resultEl.textContent=state.result||'';
 if(state.status==='awaiting_decision')renderDecision(state.plan);else decisionPanel.hidden=true;}
function renderDecision(plan){const s=plan.prep_shortfalls[0],p=plan.proposal;decisionPanel.hidden=false;decisionEl.replaceChildren();
 const title=document.createElement('h3');title.textContent='Resolve chicken-wings shortfall';const math=document.createElement('div');math.className='math';
 [`${s.required} kg required`, `− ${s.available} kg usable inventory`, `= ${s.shortfall} kg service shortfall`].forEach(text=>{const d=document.createElement('div');d.textContent=text;math.append(d);});
 const recommendation=document.createElement('p');recommendation.textContent=`Recommendation: submit a simulated ${p.qty} kg emergency order to ${p.supplier}. ETA ${p.eta}; required before ${p.required_before}.`;
 const actions=document.createElement('div');actions.className='actions';const approve=document.createElement('button'),reject=document.createElement('button');approve.className='button';approve.textContent='Approve simulated order';reject.className='button reject';reject.textContent='Reject';
 approve.onclick=()=>decide('approve');reject.onclick=()=>decide('reject');actions.append(approve,reject);decisionEl.append(title,math,recommendation,actions);}
async function fetchState(){const response=await fetch(`/demo/sessions/${sessionId}`,{cache:'no-store'});if(!response.ok)throw new Error('Could not load demo state');const state=await response.json();renderState(state);return state;}
async function decide(action){decisionEl.querySelectorAll('button').forEach(button=>button.disabled=true);const response=await fetch(`/demo/sessions/${sessionId}/decision`,{method:'POST',headers:{'Content-Type':'application/json','X-Demo-Request':'1'},body:JSON.stringify({action})});
 const state=await response.json();if(!response.ok)throw new Error(state.detail||'Decision failed');renderState(state);}
runButton.onclick=async()=>{runButton.disabled=true;eventsEl.replaceChildren();statusEl.textContent='Creating isolated session…';
 try{const response=await fetch('/demo/sessions',{method:'POST',headers:{'X-Demo-Request':'1'}});const state=await response.json();if(!response.ok)throw new Error(state.detail||'Could not create demo');sessionId=state.session_id;renderState(state);setStatus('running');
  const stream=new EventSource(`/demo/sessions/${sessionId}/events`);stream.addEventListener('step',message=>{const event=JSON.parse(message.data);eventsEl.append(eventRow(event));});
  stream.addEventListener('complete',async()=>{stream.close();await fetchState();runButton.textContent='Run completed';});
  stream.onerror=()=>{stream.close();statusEl.textContent='Demo interrupted — refresh and try again';runButton.disabled=false;};
 }catch(error){statusEl.textContent=error.message;runButton.disabled=false;}};
</script></body></html>"""
