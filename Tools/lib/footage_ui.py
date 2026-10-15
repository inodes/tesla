"""footage_ui.py - HTML/CSS/JS of the dashcam footage section on a drive page (REQ-194).

Used by drive_report.py. The page gets DATA.foot = {c: [[start_epoch, drive, rel, src, cams], ...], off: seconds}:
  start_epoch  clip start in Tessie local time (clip clock + off), as seconds (naive, read as UTC)
  drive        volume name of the drive holding the original, for the file:// link only (names change with mount order)
  rel          path below TeslaCam without the camera suffix, e.g. RecentClips/2026-10-11_15-25-27
  src          r = RecentClips, s = SavedClips, y = SentryClips
  cams         digits of the cameras that exist (0 front 1 back 2 left_repeater 3 right_repeater 4 left_pillar 5 right_pillar)
  role         A Archive, J JOWUA, U Tesla USB: what the drive IS (marker folder / size), used for delete, save and the queue
The page itself cannot touch files: copy / delete / save are queued (localStorage) and "Download queue" writes
tesla_clip_queue_<time>.json, which Tools/drive_actions.py carries out.
"""


CSS = r"""
.frow{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin:4px 0}
.fscroll{overflow-x:auto;padding:0 0 4px}.fin{position:relative;height:74px;min-width:100%}
.ftrack{position:absolute;left:0;right:0;top:26px;height:40px;border-radius:8px;background:var(--line)}
.tk{position:absolute;top:0;bottom:0;width:0;border-left:1px solid var(--line)}.tk span{position:absolute;top:0;left:3px;font-size:11px;color:var(--mut);white-space:nowrap}
.fb{position:absolute;top:0;bottom:0;border-radius:4px;border:1px solid var(--card);cursor:pointer;font-size:11px;color:#fff;text-align:center;line-height:38px;overflow:hidden;user-select:none}
.fkey{display:flex;flex-wrap:wrap;align-items:center;gap:6px 16px;margin:6px 0 10px;font-size:14px}.fkey .fk{display:inline-flex;align-items:center;gap:6px;font-weight:600}.fkey .fk i{display:inline-block;width:16px;height:16px;border-radius:4px;position:static}.fkey .fk b{font-variant-numeric:tabular-nums}.fkey .fh{color:var(--mut,#888);font-size:12px;margin-left:auto}
.fb.r{background:var(--ok)}.fb.s{background:#a855f7}.fb.y{background:var(--warn)}.fb:hover{filter:brightness(1.15)}
.fb.sel{box-shadow:inset 0 0 0 3px #fff,0 0 0 2px var(--acc);z-index:1}.fb.sel::after{content:"✓";position:absolute;right:2px;top:-14px;font-size:11px;color:var(--acc)}
.fhead{position:absolute;top:-6px;bottom:-6px;width:2px;background:var(--ink);pointer-events:none;z-index:3}
.fb.open{outline:3px solid var(--ink);outline-offset:1px;z-index:2}
.qout{background:#111;color:#cfe;font:12px ui-monospace,monospace;padding:10px;border-radius:8px;white-space:pre-wrap;max-height:320px;overflow:auto}.fpanel,.ftool,.fqueue{border-top:1px solid var(--line);margin-top:12px;padding-top:10px}
.fph{display:flex;gap:8px;flex-wrap:wrap;align-items:center;justify-content:space-between}.fpb button,.fact button,.fvbar button,.ftl button,.fq button,.ff button,.frow button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;padding:3px 10px;cursor:pointer;margin-left:4px}
.fact button:disabled,.ff button:disabled{opacity:.4;cursor:default}.fact button{margin:0 6px 4px 0}
.fstrip{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:8px 0}
.fsn{position:relative;cursor:pointer;border-radius:8px;overflow:hidden;background:#000}.fsn video{display:block;width:100%;pointer-events:none;border-radius:0}.fsn span{position:absolute;left:6px;bottom:4px;background:rgba(0,0,0,.6);color:#fff;font-size:11px;padding:0 6px;border-radius:6px}
.fsn:hover{outline:2px solid var(--acc)}
.fvbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:8px 0}.fvbar input[type=range]{flex:1;min-width:140px}
.fgrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px}.fgrid.front{grid-template-columns:minmax(0,1fr);max-width:900px}
.fgrid.front .fc:not([data-k="0"]){display:none}
.fc{position:relative;background:#000;border-radius:8px;overflow:hidden;min-height:60px;color:#999}.fc video{display:block;width:100%;border-radius:0}.fc b{position:absolute;left:6px;top:4px;font-size:11px;color:#fff;background:rgba(0,0,0,.55);padding:0 6px;border-radius:6px;font-weight:500}
.fc.none{display:flex;align-items:center;justify-content:center;aspect-ratio:4/3;font-size:12px}.fc.none b{position:static;background:none;color:#999;margin-right:6px}
.fcams label,.ftl label{margin-right:10px;white-space:nowrap}.ff{margin:8px 0;display:grid;gap:6px}.ff input[type=text]{font:inherit;padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);width:min(100%,420px)}
.ff .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.warnbox{border:1px solid var(--bad);border-radius:8px;padding:6px 10px;color:var(--bad)}
.fq code{display:block;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:6px 8px;margin:4px 0;word-break:break-all;font-size:12px}
.fq li{margin:2px 0}.fq .t{display:inline-block;min-width:54px;font-weight:600}.fq .t.delete{color:var(--bad)}.fq .t.save{color:#a855f7}
"""

JS = r"""
(function(){const F=D.foot,root=$("#foot");if(!F||!root)return;
const CAMN=["front","back","left_repeater","right_repeater","left_pillar","right_pillar"],
 CAML=["Front","Back","Left repeater","Right repeater","Left pillar","Right pillar"],SRC={r:"RecentClips",s:"SavedClips",y:"SentryClips"},RN={A:"Archive",J:"JOWUA",U:"Tesla USB","?":"unknown drive"};
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const clips=F.c.map(a=>({t:a[0],drive:a[1],rel:a[2],src:a[3],cams:a[4],role:a[5]||"?"}));
const t0=D.pts[0][0],t1=D.pts[D.pts.length-1][0],span=Math.max(t1-t0,1);
const APP=/^https?:/.test(location.protocol);   // served by tesla_app.py: footage over http, actions run by the app
const url=(c,k)=>APP?"/footage/"+c.role+"/"+c.rel.split("/").map(encodeURIComponent).join("/")+"-"+CAMN[k]+".mp4":"file:///Volumes/"+encodeURIComponent(c.drive)+"/TeslaCam/"+c.rel.split("/").map(encodeURIComponent).join("/")+"-"+CAMN[k]+".mp4";
const has=(c,k)=>c.cams.indexOf(String(k))>=0,iso=t=>new Date(t*1000).toISOString(),hms=t=>iso(t).substr(11,8),hm=t=>iso(t).substr(11,5);
const car=t=>iso(t-F.off).substr(0,19);                       // Tessie-local epoch -> the car's own clock, as the files and event.json use it
const sel=new Set(),camsOn=[1,1,1,1,1,1],fv={dest:"~/Movies/TeslaCam exports/"+D.stamp,confirm:""};
let open=-1,shown=-2,anchor=-1,zoom=1,form="",mode="all";
// ---- queue (kept in localStorage so actions from several drive pages end up in one file)
let memQ=[];const QK="clipQueue";
const qget=()=>{try{return JSON.parse(localStorage.getItem(QK)||"[]")}catch(e){return memQ}};
const qset=q=>{memQ=q;try{localStorage.setItem(QK,JSON.stringify(q))}catch(e){}};
// ---- skeleton
const cnt=clips.reduce((o,c)=>(o[c.src]=(o[c.src]||0)+1,o),{});
root.innerHTML=`<h2>Dashcam footage</h2>
<div class="fkey"><b>${clips.length} min</b>${["r","s","y"].filter(k=>cnt[k]).map(k=>`<span class="fk"><i class="fb ${k}"></i>${({r:"Recent",s:"Saved",y:"Sentry"})[k]} <b>${cnt[k]}</b></span>`).join("")}<span class="fh" title="Click a minute to open it · shift-click selects a range · ⌘/ctrl-click adds or removes one">click · shift = range · ⌘ = add</span></div>
<div class="frow"><span><b>Start</b> ${hm(t0)}</span><span>Zoom <button id="fzm">−</button><button id="fzp">+</button><button id="fzf">Fit</button></span><span><b>Finish</b> ${hm(t1)}</span></div>
<div class="fscroll" id="fscroll"><div class="fin" id="fin"></div></div>
<div class="ftool"><div id="fsel"></div><div id="fform"></div></div><div class="fpanel" id="fpanel" hidden></div><div class="fqueue fq" id="fqueue"></div>`;
// ---- bar with time codes
function bar(){const fin=$("#fin"),W=$("#fscroll").clientWidth*zoom;fin.style.width=(zoom*100)+"%";
 const steps=[1,2,5,10,15,30,60,120,180,360],st=steps.find(s=>W*s*60/span>=64)||360;let h="";
 for(let t=Math.ceil(t0/(st*60))*st*60;t<=t1;t+=st*60)h+=`<div class="tk" style="left:${(t-t0)/span*100}%"><span>${hm(t)}</span></div>`;
 let b="";clips.forEach((c,i)=>{const l=(c.t-t0)/span*100,r=(c.t+60-t0)/span*100;if(r<=0||l>=100)return;const L=Math.max(0,l),R=Math.min(100,r);
  b+=`<div class="fb ${c.src}${sel.has(i)?" sel":""}${open===i?" open":""}" data-i="${i}" style="left:${L}%;width:${R-L}%" title="${hms(c.t)} · ${SRC[c.src]} · ${RN[c.role]} · ${c.cams.length} camera(s)">${W*60/span>=36?hm(c.t):""}</div>`});
 fin.innerHTML=h+`<div class="ftrack">${b}</div>`}
$("#fin").onclick=e=>{const el=e.target.closest(".fb");if(!el)return;const i=+el.dataset.i;
 if(e.shiftKey&&anchor>=0){for(let k=Math.min(anchor,i);k<=Math.max(anchor,i);k++)sel.add(k)}
 else if(e.metaKey||e.ctrlKey){sel.has(i)?sel.delete(i):sel.add(i);anchor=i}
 else{open=open===i?-1:i;anchor=i}
 refresh()};
$("#fzp").onclick=()=>{zoom=Math.min(48,zoom*2);bar()};$("#fzm").onclick=()=>{zoom=Math.max(1,zoom/2);bar()};$("#fzf").onclick=()=>{zoom=1;bar()};
addEventListener("resize",bar);
// ---- opened minute: front-camera snippets, then the six-camera viewer
function panel(){if(open===shown)return;shown=open;vids.forEach(x=>x.pause());const p=$("#fpanel");if(open<0){p.hidden=true;p.innerHTML="";return}
 const c=clips[open];p.hidden=false;
 p.innerHTML=`<div class="fph"><span><b>${hms(c.t)}</b> · ${SRC[c.src]} · ${RN[c.role]} (${esc(c.drive)}) · ${esc(c.rel.split("/").pop())}</span>
 <span class="fpb"><button id="fprev">◀ previous</button><button id="fnext">next ▶</button><button id="fpsel">${sel.has(open)?"Unselect":"Select"} this minute</button><button id="fclose">Close</button></span></div>
 ${has(c,0)?`<div class="sub">Front camera every 10 s. Click a snippet to play all cameras from that point.</div>
 <div class="fstrip">${[5,15,25,35,45,55].map(s=>`<div class="fsn" data-s="${s}"><video muted preload="metadata" playsinline src="${url(c,0)}#t=${s}"></video><span>:${String(s).padStart(2,"0")}</span></div>`).join("")}</div>`
 :`<div class="sub">This minute has no front-camera file.</div>`}<div id="fview"></div>`;
 $("#fclose").onclick=()=>{open=-1;refresh()};$("#fprev").onclick=()=>step(-1);$("#fnext").onclick=()=>step(1);
 $("#fpsel").onclick=()=>{sel.has(open)?sel.delete(open):sel.add(open);shown=-2;refresh()};
 p.querySelectorAll(".fsn").forEach(el=>el.onclick=()=>view(c,+el.dataset.s,true))}
function step(d){const n=open+d;if(n>=0&&n<clips.length){open=n;anchor=n;refresh()}}
let vids=[],main=null;
function view(c,s,auto){const v=$("#fview");vids.forEach(x=>{x.pause();x.removeAttribute("src");x.load()});vids=[];
 const order=[4,0,5,2,1,3];
 v.innerHTML=`<div class="fvbar"><label><input type="radio" name="vm" value="all" ${mode==="all"?"checked":""}> All six</label><label><input type="radio" name="vm" value="front" ${mode==="front"?"checked":""}> Front only</label>
 <button id="fpl">▶ Play</button><input type="range" id="fsk" min="0" max="60" step="0.1" value="0"><span id="ftm">0:00</span>
 <select id="fsp"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option></select>
 <label><input type="checkbox" id="fau" checked> carry on into the next minute</label></div>
 <div class="fgrid ${mode}">${order.map(k=>has(c,k)?`<div class="fc" data-k="${k}"><video muted playsinline preload="auto" src="${url(c,k)}"></video><b>${CAML[k]}</b></div>`:`<div class="fc none"><b>${CAML[k]}</b> no file</div>`).join("")}</div>
 <div class="sub" id="fmsg"></div>`;
 vids=[...v.querySelectorAll("video")];main=vids.find(x=>x.parentNode.dataset.k==="0")||vids[0];if(!main)return;
 const sk=$("#fsk"),pl=$("#fpl"),tm=$("#ftm"),sp=$("#fsp"),au=$("#fau");
 const seek=t=>vids.forEach(x=>{try{x.currentTime=t}catch(e){}}),fmt=t=>Math.floor(t/60)+":"+String(Math.floor(t%60)).padStart(2,"0");
 main.onloadedmetadata=()=>{sk.max=main.duration||60;seek(s||0);if(auto)vids.forEach(x=>x.play().catch(()=>{}))};
 main.onplay=()=>pl.textContent="❚❚ Pause";main.onpause=()=>pl.textContent="▶ Play";
 main.ontimeupdate=()=>{sk.value=main.currentTime;if(D.cursor)D.cursor(c.t+main.currentTime);head(c.t+main.currentTime);tm.textContent=fmt(main.currentTime)+" · "+hms(c.t+main.currentTime);
  vids.forEach(x=>{if(x!==main&&Math.abs(x.currentTime-main.currentTime)>.35)x.currentTime=main.currentTime})};
 main.onerror=()=>{$("#fmsg").innerHTML=`Can't load the original from <a href="${url(c,0)}" target="_blank">${esc(url(c,0))}</a>. Is the ${RN[c.role]} drive mounted, under the name ${esc(c.drive)}? (Drive names change with mount order. Safari blocks links to local files; Chrome opens them.)`};
 main.onended=()=>{vids.forEach(x=>x.pause());const n=open+1;if(au.checked&&n<clips.length&&Math.abs(clips[n].t-(c.t+60))<5){open=n;anchor=n;refresh();const v2=clips[n];view(v2,0,true)}};
 pl.onclick=()=>{if(main.paused)vids.forEach(x=>x.play().catch(()=>{}));else vids.forEach(x=>x.pause())};
 sk.oninput=()=>seek(+sk.value);sp.onchange=()=>vids.forEach(x=>x.playbackRate=+sp.value);
 v.querySelectorAll("input[name=vm]").forEach(r=>r.onchange=()=>{mode=r.value;const t=main.currentTime,pl_=!main.paused;view(c,t,pl_)});
 v.scrollIntoView({block:"nearest",behavior:"smooth"})}
// ---- selection toolbar, forms and the queue
const eligible=()=>[...sel].sort((a,b)=>a-b).filter(i=>clips[i].src==="r"&&clips[i].role==="A");
const nearest=t=>{let lo=0,hi=D.pts.length-1;while(lo<hi){const m=(lo+hi)>>1;if(D.pts[m][0]<t)lo=m+1;else hi=m}return D.pts[lo]};
function tool(){const idx=[...sel].sort((a,b)=>a-b),n=idx.length;
 const nf=idx.reduce((s,i)=>s+[0,1,2,3,4,5].filter(k=>camsOn[k]&&has(clips[i],k)).length,0);
 $("#fsel").innerHTML=`<div class="ftl"><b>${n?n+" minute"+(n>1?"s":"")+" selected":"Nothing selected"}</b>${n?` · ${hm(clips[idx[0]].t)}–${hm(clips[idx[n-1]].t+60)}`:""}
 <button id="fall">Select all</button><button id="fnone">Clear selection</button></div>
 <div class="fcams">Cameras for copy: ${CAML.map((l,k)=>`<label><input type="checkbox" data-cam="${k}" ${camsOn[k]?"checked":""}> ${l}</label>`).join("")}</div>
 <div class="fact"><button id="bcopy" ${n?"":"disabled"}>Copy originals…</button><button id="bdel" ${n?"":"disabled"}>Delete from archive…</button><button id="bsave" ${n?"":"disabled"}>Save as a clip…</button></div>`;
 $("#fall").onclick=()=>{clips.forEach((c,i)=>sel.add(i));refresh()};$("#fnone").onclick=()=>{sel.clear();refresh()};
 $("#fsel").querySelectorAll("[data-cam]").forEach(el=>el.onchange=()=>{camsOn[+el.dataset.cam]=el.checked?1:0;tool();formHtml()});
 $("#bcopy").onclick=()=>{form="copy";formHtml()};$("#bdel").onclick=()=>{form="del";fv.confirm="";formHtml()};
 $("#bsave").onclick=()=>{form="save";const el=eligible();if(el.length){const last=clips[el[el.length-1]],ts=car(last.t+60),p=nearest(last.t+60);
   Object.assign(fv,{ts:ts,city:fv.city||"",street:fv.street||"",lat:p&&p[1]!=null?p[1]:"",lon:p&&p[2]!=null?p[2]:""})}formHtml()}}
const items=(list,withCams)=>list.map(i=>{const c=clips[i];return{drive:c.drive,role:c.role,rel:c.rel,src:{r:"recent",s:"saved",y:"sentry"}[c.src],start:car(c.t),cams:withCams?CAMN.filter((_,k)=>camsOn[k]&&has(c,k)):CAMN.filter((_,k)=>has(c,k))}});
function enqueue(a){const q=qget();q.push(Object.assign({id:"a"+Date.now(),drive:D.stamp},a));qset(q);form="";formHtml();queueHtml()}
function formHtml(){const f=$("#fform"),all=[...sel].sort((a,b)=>a-b),el=eligible();
 if(!form||!all.length){f.innerHTML="";return}
 const rng=hm(clips[all[0]].t)+"–"+hm(clips[all[all.length-1]].t+60);
 if(form==="copy"){const nf=all.reduce((s,i)=>s+CAMN.filter((_,k)=>camsOn[k]&&has(clips[i],k)).length,0);
  f.innerHTML=`<div class="ff"><b>Copy originals</b><div class="sub">${all.length} minute(s) ${rng}, ${nf} file(s) from where they are now (Archive, JOWUA or Tesla USB).</div>
  <label>Copy to <input type="text" id="fdest" value="${esc(fv.dest)}"></label><div class="sub">A folder on your Mac; it is created if needed. Files already there with the same size are skipped.</div>
  <div><button id="fadd" ${nf?"":"disabled"}>Add copy to queue</button><button id="fcx">Cancel</button></div></div>`;
  $("#fdest").oninput=e=>fv.dest=e.target.value;
  $("#fadd").onclick=()=>enqueue({type:"copy",label:rng+" · "+D.title,dest:fv.dest,items:items(all,true)})}
 else if(form==="del"){const sk=all.length-el.length;
  f.innerHTML=`<div class="ff"><b>Delete from the Archive</b>
  <div class="warnbox">Flags ${el.length} RecentClips minute(s) ${el.length?rng:""} (all cameras) for deletion. The files go when drive_actions.py is run with --purge, and the same flag removes them from the Tesla USB / JOWUA the next time those are synced. It cannot be undone after that.</div>
  ${sk?`<div class="sub">${sk} selected minute(s) are skipped: only RecentClips on the Archive drive can be deleted here.</div>`:""}
  <label>Type DELETE to confirm <input type="text" id="fcf" value="${esc(fv.confirm)}" style="width:120px"></label>
  <div><button id="fadd" disabled>Add delete to queue</button><button id="fcx">Cancel</button></div></div>`;
  const ok=()=>{$("#fadd").disabled=!(el.length&&fv.confirm==="DELETE")};ok();
  $("#fcf").oninput=e=>{fv.confirm=e.target.value;ok()};
  $("#fadd").onclick=()=>enqueue({type:"delete",label:rng+" · "+D.title,items:items(el,false)})}
 else if(form==="save"){const sk=all.length-el.length;
  const fold=()=>/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$/.test(fv.ts||"")?"SavedClips/"+fv.ts.replace("T","_").replace(/:/g,"-"):"(timestamp must look like 2026-10-11T16:27:00)";
  f.innerHTML=`<div class="ff"><b>Save as a clip</b>
  <div class="sub">Moves ${el.length} minute(s) ${el.length?rng:""} on the Archive from RecentClips into a new SavedClips folder and writes an event.json (reason user_interaction_dashcam_launcher_action_tapped), as if it had been saved from the car. The Archive only; other drives are not touched.</div>
  ${sk?`<div class="sub">${sk} selected minute(s) are skipped: only RecentClips on the Archive can be saved.</div>`:""}
  <div class="row"><label>Event time (car clock) <input type="text" id="fts" value="${esc(fv.ts||"")}" style="width:190px"></label><span class="sub" id="ffold"></span></div>
  <div class="row"><label>City <input type="text" id="fcity" value="${esc(fv.city)}" style="width:170px"></label><label>Street <input type="text" id="fstreet" value="${esc(fv.street)}" style="width:200px"></label>
  <label>Lat <input type="text" id="flat" value="${esc(fv.lat)}" style="width:100px"></label><label>Lon <input type="text" id="flon" value="${esc(fv.lon)}" style="width:100px"></label></div>
  <div><button id="fadd" ${el.length?"":"disabled"}>Add save to queue</button><button id="fcx">Cancel</button></div></div>`;
  const pf=()=>{$("#ffold").textContent="→ "+fold()};pf();
  [["fts","ts"],["fcity","city"],["fstreet","street"],["flat","lat"],["flon","lon"]].forEach(([id,k])=>$("#"+id).oninput=e=>{fv[k]=e.target.value;pf()});
  $("#fadd").onclick=()=>{if(!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$/.test(fv.ts||"")){$("#ffold").textContent="→ "+fold();return}
   enqueue({type:"save",label:rng+" · "+D.title,event:{timestamp:fv.ts,city:fv.city,street:fv.street,est_lat:fv.lat,est_lon:fv.lon,camera:"0"},items:items(el,false)})}}
 const cx=$("#fcx");if(cx)cx.onclick=()=>{form="";formHtml()}}
function queueHtml(){const q=qget(),el=$("#fqueue");
 if(!q.length){el.innerHTML=`<b>Queue</b> <span class="sub">empty. Actions you add are kept in this browser and collected into one file; the page itself never changes any file.</span>`;return}
 el.innerHTML=`<b>Queue</b> (${q.length}, shared by all drive pages in this browser)<ul>${q.map((a,i)=>`<li><span class="t ${a.type}">${a.type}</span> ${esc(a.label)} · ${a.items.length} minute(s)${a.dest?" → "+esc(a.dest):""} <button data-rm="${i}">remove</button></li>`).join("")}</ul>
 ${APP?`<button id="qpv">Preview (changes nothing)</button><button id="qrun">Run for real</button><button id="qclr">Clear queue</button><pre class="qout" id="qout" hidden></pre>`
 :`<button id="qdl">Download queue</button><button id="qclr">Clear queue</button>
 <div class="sub">Then in Terminal (a dry run first, then for real):</div>
 <code>./Tools/drive_actions.py</code><code>./Tools/drive_actions.py --apply${q.some(a=>a.type==="delete")?" --purge":""}</code>`}`;
 const run=async apply=>{const o=$("#qout");o.hidden=false;o.textContent=apply?"Running...":"Checking...";
  try{const r=await fetch("/api/queue/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({actions:qget(),apply:apply,purge:qget().some(a=>a.type==="delete")})});const j=await r.json();o.textContent=j.output||j.error||"(no output)";
   if(apply&&j.ok){qset([]);o.textContent+="\n\nDone. Queue cleared."}}catch(e){o.textContent="Could not reach the app: "+e}};
 if(APP){$("#qpv").onclick=()=>run(false);$("#qrun").onclick=()=>{if(confirm("Run these actions for real? Deletes are flagged and then purged from the Archive."))run(true)}}
 el.querySelectorAll("[data-rm]").forEach(b=>b.onclick=()=>{const q2=qget();q2.splice(+b.dataset.rm,1);qset(q2);queueHtml()});
 $("#qclr").onclick=()=>{qset([]);queueHtml()};
 if($("#qdl"))$("#qdl").onclick=()=>{const n=new Date(),p=x=>String(x).padStart(2,"0"),name=`tesla_clip_queue_${n.getFullYear()}${p(n.getMonth()+1)}${p(n.getDate())}-${p(n.getHours())}${p(n.getMinutes())}${p(n.getSeconds())}.json`;
  const blob=new Blob([JSON.stringify({version:1,created:n.toISOString(),actions:qget()},null,1)],{type:"application/json"}),a=document.createElement("a");
  a.href=URL.createObjectURL(blob);a.download=name;document.body.appendChild(a);a.click();a.remove();
  $("#fqueue").insertAdjacentHTML("beforeend",`<div class="sub">Saved ${esc(name)} to your Downloads folder. Clear the queue once you have run it.</div>`)}}
function refresh(){bar();tool();formHtml();panel();queueHtml()}
// playhead on the bar, and the two hooks shared with the charts (drive_charts.py)
function head(t){const tr=$("#fin .ftrack");if(!tr)return;let h=$("#fhead");if(!h){h=document.createElement("div");h.id="fhead";h.className="fhead";tr.appendChild(h)}h.style.left=Math.max(0,Math.min(100,(t-t0)/span*100))+"%"}
D.seekFootage=t=>{const i=clips.findIndex(c=>t>=c.t&&t<c.t+60);if(i<0)return;open=i;anchor=i;refresh();view(clips[i],t-clips[i].t,false);head(t);$("#foot").scrollIntoView({block:"start",behavior:"smooth"})};
refresh();
})();
"""
