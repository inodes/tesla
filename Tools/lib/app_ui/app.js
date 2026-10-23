"use strict";
// Tesla app front end (REQ-199). Plain JS, hash routing, data from /api/*; drive pages are the existing drive.html files.
const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
const esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const n0=(v,d=0)=>v==null||isNaN(v)?"–":(+v).toLocaleString("en-AU",{minimumFractionDigits:d,maximumFractionDigits:d});
const MON=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"],DOW=["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
const dayName=d=>{const t=new Date(d+"T12:00:00");return DOW[t.getDay()]+" "+t.getDate()+" "+MON[t.getMonth()]+" "+t.getFullYear()};
const addMin=(hm,m)=>{const [h,mi]=hm.split(":").map(Number),t=h*60+mi+Math.round(m||0);return String(Math.floor(t/60)%24).padStart(2,"0")+":"+String(t%60).padStart(2,"0")};
const dur=m=>m==null?"–":m>=120?Math.floor(m/60)+" h "+Math.round(m%60)+" min":Math.round(m)+" min";
const money=v=>v==null||isNaN(v)?"–":"$"+(+v).toFixed(2);
const rate=v=>{if(v==null||isNaN(v))return"–";let s=(+v).toFixed(4);if(s.endsWith("00"))s=s.slice(0,-2);else if(s.endsWith("0"))s=s.slice(0,-1);return"$"+s};          // a price per kWh keeps its cents: $0.1752, not $0.18
const cache={};
const main=$("#main");
// ---------- remembered pages: a list seen before is drawn at once from the browser's own copy and refreshed behind it; the page is redrawn only if the fresh data differs
const IDB=(()=>{let db;const open=()=>db||(db=new Promise(res=>{try{const r=indexedDB.open("tesla-ui",1);r.onupgradeneeded=()=>r.result.createObjectStore("kv");r.onsuccess=()=>res(r.result);r.onerror=r.onblocked=()=>res(null)}catch(e){res(null)}}));
  const tx=async(mode,f)=>{const d=await open();if(!d)return undefined;return new Promise(res=>{try{const t=d.transaction("kv",mode),rq=f(t.objectStore("kv"));t.oncomplete=()=>res(rq&&rq.result);t.onerror=t.onabort=()=>res(undefined)}catch(e){res(undefined)}})};
  return{get:k=>tx("readonly",s=>s.get(k)),set:(k,v)=>tx("readwrite",s=>s.put(v,k)),del:k=>tx("readwrite",s=>s.delete(k)),clear:()=>tx("readwrite",s=>s.clear())}})();
const UIV=fetch("/api/status").then(r=>r.json()).then(s=>String(s.ui)).catch(()=>"0");     // a changed page (app.js, app.css) forgets what was remembered
const mkey=async p=>(await UIV)+"|"+p,same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
let FRESHT=0;const STALEP=new Set();       // FRESHT: when the remembered lists were last forgotten (after a sync or an edit): for a minute the server is asked for the newest, not what it kept
const fetchJ=async p=>{const q=Date.now()-FRESHT<60000?(p.includes("?")?"&":"?")+"fresh=1":"";const r=await fetch("/api/"+p+q);if(!r.ok)throw new Error(p+": "+r.status);if(r.headers.get("X-Stale")==="1")STALEP.add(p);else STALEP.delete(p);return r.json()};
// the server answered from what it kept and is refreshing: ask again in a few seconds and redraw if the list changed
const recheck=(p,shown,k=0)=>{if(k>4)return;setTimeout(async()=>{let n;try{n=await fetchJ(p)}catch(e){return}
  if(!same(n,shown)){if(cache[p]===shown||cache[p]===undefined)cache[p]=n;mkey(p).then(key=>IDB.set(key,n));redrawQuietly()}else if(STALEP.has(p))recheck(p,shown,k+1)},4000)};
const KEEPING=new Set(["summary","activity","places","alerts","battery","analytics","chargers"]),LISTS=new Set(["activity","driveslist","charges","parked","idles","places","alerts","battery","analytics","health","teslacam"]);
let QUIET=false,QT=0;
const redrawQuietly=()=>{clearTimeout(QT);QT=setTimeout(async()=>{const h=(location.hash||"#/activity").replace(/^#\//,"");if(!LISTS.has(h))return;const a=document.activeElement;
  if(a&&main.contains(a)&&/^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName))return;if($$("video",main).some(v=>!v.paused)||$(".stbar",main))return;
  const y=main.scrollTop;QUIET=true;try{await route()}finally{QUIET=false}main.scrollTop=y},400)};
// forget the remembered copies after an edit or a sync (one page, or all)
const bust=p=>{FRESHT=Date.now();if(p){delete cache[p];mkey(p).then(IDB.del);return}Object.keys(cache).forEach(k=>delete cache[k]);IDB.clear()};
const api=async p=>{if(cache[p])return cache[p];
  if(!KEEPING.has(p))return cache[p]=await fetchJ(p);
  const k=await mkey(p),old=await IDB.get(k);
  if(old!==undefined){cache[p]=old;fetchJ(p).then(n=>{IDB.set(k,n);if(cache[p]===old&&!same(n,old)){cache[p]=n;redrawQuietly()}if(STALEP.has(p))recheck(p,n)}).catch(()=>{});return old}
  const n=await fetchJ(p);IDB.set(k,n);if(STALEP.has(p))recheck(p,n);return cache[p]=n};
const place=x=>x.place||x.addr||"Unknown place";
const driveTitle=d=>(d.frm||"?")+" → "+(d.to||"?");
// An error you can copy: the text is selectable and a button puts it on the clipboard.
const errBox=(msg,lead)=>`<div class="errbox"><div>${lead||""}</div><pre>${esc(msg)}</pre><button class="btn" data-copy>Copy error</button></div>`;
document.addEventListener("click",e=>{const b=e.target.closest("[data-copy]");if(!b)return;const pre=b.parentNode.querySelector("pre"),sel=()=>{const r=document.createRange();r.selectNodeContents(pre);const s=getSelection();s.removeAllRanges();s.addRange(r);b.textContent="Selected - press Cmd+C"};
  (navigator.clipboard?navigator.clipboard.writeText(pre.textContent):Promise.reject()).then(()=>{b.textContent="Copied"},sel)});
// Settings come from the "app" block of config.json (see lib/app_config.py), loaded once at start.
let SET={};const EFWH=()=>cfg("units.efficiency")==="whkm",EF=wh=>wh==null||isNaN(wh)?"–":EFWH()?n0(wh):n0(wh/10,1),EFU=()=>EFWH()?"Wh/km":"kWh/100 km";    // energy use: kWh/100 km by default, Wh/km when app.units.efficiency says so
const cfg=k=>k.split(".").reduce((o,x)=>o&&o[x],SET),REPM=()=>Math.round(cfg("drives.repark_max_km")*1000)+" m",MAPSTYLE=dark=>cfg("map.tiles_base")+cfg(dark?"map.style_dark":"map.style_light");
const loading=()=>{if(!QUIET)main.innerHTML='<div class="empty">Loading…</div>'};
const QK="clipQueue",qget=()=>{try{return JSON.parse(localStorage.getItem(QK)||"[]")}catch(e){return[]}};

// ---------- tooltip + map helpers
const tip=document.body.appendChild(Object.assign(document.createElement("div"),{id:"tip"}));
const showTip=(ev,html)=>{tip.innerHTML=html;tip.style.display="block";const w=tip.offsetWidth;tip.style.left=Math.min(innerWidth-w-8,ev.clientX+14)+"px";tip.style.top=(ev.clientY+14)+"px"};
const hideTip=()=>tip.style.display="none";
// "Open in" links for a coordinate: Google Maps, Apple Maps, OpenStreetMap, Waze (external, so they open in the browser)
function mapLinks(lat,lon,name,stop){
  if(lat==null||lon==null)return"";const ll=(+lat).toFixed(6)+","+(+lon).toFixed(6),q=encodeURIComponent(name||ll),sp=stop?' onclick="event.stopPropagation()"':"";
  const L=[["Google Maps","https://www.google.com/maps/search/?api=1&query="+ll],["Apple Maps","https://maps.apple.com/?ll="+ll+"&q="+q],["OpenStreetMap","https://www.openstreetmap.org/?mlat="+(+lat).toFixed(6)+"&mlon="+(+lon).toFixed(6)+"#map=18/"+(+lat).toFixed(6)+"/"+(+lon).toFixed(6)],["Waze","https://waze.com/ul?ll="+ll+"&navigate=yes"]];
  const sh={"Google Maps":"Google","Apple Maps":"Apple","OpenStreetMap":"OSM"};
  return L.map(([t,u])=>`<a class="ml" href="${u}" target="_blank" rel="noopener"${sp}>${stop?(sh[t]||t):t}</a>`).join(" · ");
}
// zoom in / out plus a button that puts the view back (the whole route, every place, or the pin)
// A map reacts to the wheel and to a trackpad pinch only after it has been clicked, and lets go again when the pointer leaves it (so scrolling the page never moves a map)
function newMap(o){const m=new maplibregl.Map(o),c=m.getContainer();m.scrollZoom.disable();c.addEventListener("mousedown",e=>{if(e.button===0)m.scrollZoom.enable()});c.addEventListener("contextmenu",e=>{e.preventDefault();m.scrollZoom.disable()});c.addEventListener("mouseleave",()=>m.scrollZoom.disable());return m}   // a click turns wheel / pinch zoom on, a right-click (or ctrl-click) turns it off
function mapNav(m,reset,title){const nav=new maplibregl.NavigationControl({showCompass:false});m.addControl(nav,"top-right");
  const b=document.createElement("button");b.type="button";b.className="maplibregl-ctrl-icon";b.title=title;b.setAttribute("aria-label",title);
  b.innerHTML='<span style="display:block;text-align:center;font-size:18px;line-height:29px">⤢</span>';b.onclick=reset;nav._container.appendChild(b);return b}
function miniMap(el,lat,lon,col){
  if(lat==null||lon==null){el.outerHTML='<div class="sub">No location.</div>';return}
  if(!window.maplibregl){el.innerHTML='<div class="empty">Map library not loaded (offline?).</div>';return}
  const dark=matchMedia("(prefers-color-scheme: dark)").matches;
  const m=newMap({container:el,style:MAPSTYLE(dark),center:[lon,lat],zoom:14.5,attributionControl:{compact:true}});
  mapNav(m,()=>m.flyTo({center:[lon,lat],zoom:14.5}),"Back to the pin");
  new maplibregl.Marker({color:col||"#3b6cf6"}).setLngLat([lon,lat]).addTo(m);
}
function tile(b,s){return `<div class="tile"><b>${b}</b><span>${s}</span></div>`}
// Back links: the crumb at the top of a page goes back to the page the user came from (the path is kept per tab in sessionStorage), not to a fixed parent.
// h and label are only the fallback, for a page opened directly. A main-menu click starts a new path; saving or deleting a place replaces the page it was done on.
const NAVK="nav.stack",ROOTS={activity:"Activity",drives:"Drives",driveslist:"Drives (new)",charges:"Charges",parked:"Parked",idles:"Parked",places:"Places",alerts:"Alerts",software:"Software",battery:"Battery",health:"Health",analytics:"Analytics",teslacam:"TeslaCam",queue:"Queue"};
let NAV=(()=>{try{return JSON.parse(sessionStorage.getItem(NAVK)||"[]")}catch(e){return[]}})(),NAVREPL=false,NAVRESET=false;
const navSeg=h=>String(h).replace(/^#\//,"").split("/"),navPage=h=>navSeg(h).slice(0,2).join("/");
const navLabel=h=>ROOTS[navSeg(h)[0]]||({drive:"Drive",charge:"Charge",place:"Place",idle:"Parked"})[navSeg(h)[0]]||"Back";
function navTrack(){
  const h=location.hash||"#/activity";let L=NAV.length;
  if(L&&NAV[L-1].h!==h){const t=navSeg(NAV[L-1].h).length>1?(document.querySelector("#main h1, main h1")||{}).textContent:"";NAV[L-1].label=(t||"").trim().slice(0,40)||navLabel(NAV[L-1].h)}   // name the page being left
  if(NAVRESET){NAV=[];NAVRESET=false}
  L=NAV.length;
  if(L&&NAV[L-1].h===h){}                                                          // a refresh
  else if(L>1&&NAV[L-2].h===h)NAV.pop();                                           // went back
  else if(L&&(NAVREPL||navPage(NAV[L-1].h)===navPage(h)))NAV[L-1]={h};            // the same page (another tab of it) or a replacement
  else NAV.push({h});
  NAVREPL=false;
  if(NAV.length>1&&NAV[NAV.length-1].h===NAV[NAV.length-2].h)NAV.pop();
  NAV=NAV.slice(-30);try{sessionStorage.setItem(NAVK,JSON.stringify(NAV))}catch(e){}
}
function crumbs(h,label){const p=NAV.length>1?NAV[NAV.length-2]:null;if(p&&p.h!==(location.hash||"")){h=p.h;label=esc(p.label||navLabel(p.h))}return `<div class="crumb"><a href="${h}">← ${label}</a></div>`}

// ---------- stacked charts on one shared axis (charge pages)
function stack(host,t,panels,xfmt){
  const W=900,L=52,R=12,H=96,G=24,n=t.length;if(n<2){host.innerHTML='<div class="sub">No telemetry for this one.</div>';return}
  const t0=t[0],t1=t[n-1]||1,X=v=>L+(v-t0)/(t1-t0)*(W-L-R);let h="",y=0;
  const ps=panels.filter(p=>p.vals.some(v=>v!=null));
  ps.forEach((p,pi)=>{const v=p.vals.filter(x=>x!=null),lo=Math.min(...v,p.min!=null?p.min:Infinity),hi=Math.max(...v,p.max!=null?p.max:-Infinity),sp=(hi-lo)||1,Y=q=>y+H-(q-lo)/sp*(H-8)-4;
    let d="";p.vals.forEach((q,i)=>{if(q!=null)d+=(d&&p.vals[i-1]!=null?"L":"M")+X(t[i]).toFixed(1)+","+Y(q).toFixed(1)});
    p.Y=Y;p.y=y;
    h+=`<g><text x="${L-6}" y="${y+10}" text-anchor="end">${n0(hi,hi<20?1:0)}</text><text x="${L-6}" y="${y+H-2}" text-anchor="end">${n0(lo,lo<20?1:0)}</text>
     <line class="grid" x1="${L}" x2="${W-R}" y1="${y+H}" y2="${y+H}"/><path d="${d}" fill="none" stroke="${p.col}" stroke-width="2" stroke-linejoin="round"/>
     <text x="${L+4}" y="${y+12}" style="fill:${p.col};font-weight:600">${p.name} <tspan style="fill:var(--mut);font-weight:400">${p.unit}</tspan></text></g>`;y+=H+G});
  const ticks=5;let ax="";for(let i=0;i<=ticks;i++){const v=t0+(t1-t0)*i/ticks;ax+=`<text x="${X(v)}" y="${y+4}" text-anchor="middle">${xfmt(v)}</text>`}
  host.innerHTML=`<svg viewBox="0 0 ${W} ${y+14}" style="width:100%;height:auto;display:block">${h}${ax}<line class="cl" id="cur" y1="0" y2="${y-G}" style="display:none"/></svg>`;
  const svg=$("svg",host),cur=$("#cur",host);
  svg.onmousemove=ev=>{const r=svg.getBoundingClientRect(),x=(ev.clientX-r.left)/r.width*W;if(x<L||x>W-R){svg.onmouseleave();return}
    const tv=t0+(x-L)/(W-L-R)*(t1-t0);let i=0,b=1e18;t.forEach((q,k)=>{const d=Math.abs(q-tv);if(d<b){b=d;i=k}});
    cur.setAttribute("x1",X(t[i]));cur.setAttribute("x2",X(t[i]));cur.style.display="";
    showTip(ev,`<b>${xfmt(t[i])}</b><br>`+ps.map(p=>`<span style="color:${p.col}">●</span> ${p.name}: ${p.vals[i]==null?"–":n0(p.vals[i],p.dec==null?1:p.dec)+" "+p.unit}`).join("<br>"))};
  svg.onmouseleave=()=>{cur.style.display="none";hideTip()};
}

// ---------- Activity
const AF={drive:true,repark:false,charge:true,idle:true,q:"",days:30,r:{p:"all"}};
async function pageActivity(){
  loading();const act=await api("activity");
  const draw=()=>{
    const q=AF.q.toLowerCase(),rows=act.filter(x=>AF[x.k==="drive"&&x.repark?"repark":x.k]&&inRange(x,AF.r)&&(!q||(driveTitle(x)+place(x)+(x.addr||"")).toLowerCase().includes(q)));
    const byDay=new Map();rows.forEach(x=>{if(!byDay.has(x.day))byDay.set(x.day,[]);byDay.get(x.day).push(x)});
    const days=[...byDay.keys()].slice(0,AF.days);let h="";
    days.forEach(d=>{const xs=byDay.get(d),km=xs.filter(x=>x.k==="drive").reduce((a,x)=>a+(x.km||0),0),used=xs.filter(x=>x.k==="drive").reduce((a,x)=>a+(x.kwh||0),0),add=xs.filter(x=>x.k==="charge").reduce((a,x)=>a+(x.kwh||0),0);
      h+=`<div class="day">${dayName(d)}<small>${km?n0(km,1)+" km · "+n0(used,1)+" kWh used":""}${add?(km?" · ":"")+"+"+n0(add,1)+" kWh charged":""}</small></div>`;
      xs.sort((a,b)=>b.t0-a.t0).forEach(x=>h+=actRow(x))});
    $("#list").innerHTML=h||'<div class="empty">Nothing matches.</div>';
    $("#more").hidden=days.length>=byDay.size;
  };
  main.innerHTML=`<h1>Activity</h1><div class="sub">${act.length.toLocaleString()} drives, charges and parked periods, newest first.</div>
   <div class="bar"><span id="rg"></span><button class="chip ${AF.drive?"on":""}" data-k="drive">Drives</button><button class="chip ${AF.charge?"on":""}" data-k="charge">Charges</button><button class="chip ${AF.idle?"on":""}" data-k="idle">Parked</button><button class="chip ${AF.repark?"on":""}" data-k="repark" title="Drives under ${REPM()}, or a short move that ends at the address it started from: the car was only moved a little">Re-parks</button>
   <input type="search" id="q" placeholder="Search a place…" value="${esc(AF.q)}"></div><div id="list"></div><div class="bar"><button class="btn" id="more">Show 30 more days</button></div>`;
  $$(".chip").forEach(c=>c.onclick=()=>{AF[c.dataset.k]=!AF[c.dataset.k];c.classList.toggle("on");draw()});
  rangePicker($("#rg"),AF.r,daysOf(act),()=>{AF.days=30;draw()});$("#q").oninput=e=>{AF.q=e.target.value;draw()};$("#more").onclick=()=>{AF.days+=30;draw()};draw();
}
function actRow(x){
  if(x.k==="drive"&&x.repark)return `<a class="row" href="#/drive/${x.id}" style="opacity:.75"><span class="t">${x.hm}</span><span class="k drive"></span><div style="min-width:0"><div class="ti">Car moved a few metres</div><div class="st">${esc(x.to||x.frm||"")} · ${dur(x.min)}</div></div><div class="nums"><span>${n0(x.km*1000)}<small>m</small></span></div></a>`;
  const end=addMin(x.hm,x.min),alb=x.al?` · ⚠ ${x.al} alert${x.al>1?"s":""}`:"",href=x.k==="drive"?"#/drive/"+x.id:x.k==="charge"?"#/charge/"+x.id:"#/parked/"+x.id;
  let ti,st,nums;
  if(x.k==="drive"){ti=driveTitle(x);st=`${dur(x.min)} · ${EF(x.whkm)} ${EFU()} · ${n0(x.b0)}→${n0(x.b1)}%${x.saved?" · "+x.saved+" saved clip":""}${x.foot?" · dashcam "+x.foot+"%":""}${alb}`;nums=`<span>${n0(x.km,1)}<small>km</small></span><span>${n0(x.kwh,1)}<small>kWh used</small></span>`}
  else if(x.k==="charge"){ti=place(x);st=`${dur(x.min)} · ${n0(x.b0)}→${n0(x.b1)}% · ${x.kind==="supercharger"?"Supercharger":x.kind==="dc"?"DC fast":"AC"}${x.parts&&x.parts.length>1?" · "+x.parts.length+" parts":""}${alb}`;nums=`<span>+${n0(x.kwh,1)}<small>kWh</small></span><span>${money(x.cost)}<small>cost</small></span>`}
  else{ti="Parked · "+place(x);st=`${dur(x.min)} · ${n0(x.b0)}→${n0(x.b1)}%${x.sentry>0.01?" · Sentry "+Math.round(x.sentry*100)+"%":""}${x.climate>0.01?" · Climate "+Math.round(x.climate*100)+"%":""}${alb}`;nums=`<span>${n0(x.kwh,2)}<small>kWh used</small></span>`}
  return `<a class="row ${x.k==="idle"?"faded":""}" ${href?`href="${href}"`:""} ${x.k==="drive"&&!x.page?'title="No dashcam / deep-dive page for this drive - shows the summary"':""}><span class="t">${x.hm} – ${end}</span><span class="k ${x.k}"></span><div style="min-width:0"><div class="ti">${esc(ti)}</div><div class="st">${esc(st)}</div></div><div class="nums">${nums}</div></a>`;
}

// ---------- generic sortable table
function table(host,cols,rows,onRow,def,dir0){
  let sk=def||cols[0].k,dir=dir0||-1;
  const cmp=(x,y)=>typeof x==="string"&&typeof y==="string"?x.localeCompare(y):x<y?-1:x>y?1:0,byDay=sk0=>sk0==="t0"&&rows.length&&rows[0].day!==undefined;
  const draw=()=>{const rs=rows.filter(r=>r.k!=="charge"||sk==="t0").sort((a,b)=>{const x=a[sk],y=b[sk];if(x==null||y==null)return (x==null)-(y==null);return dir*cmp(x,y)});
    host.innerHTML=`<table><thead><tr>${cols.map(c=>`<th class="${c.l?"l":""}" data-k="${c.k}">${c.t}${sk===c.k?(dir<0?" ▾":" ▴"):""}</th>`).join("")}</tr></thead><tbody>${rs.slice(0,500).map((r,i)=>`<tr data-i="${rows.indexOf(r)}" ${r.k==="charge"?'class="chline"':""}>${cols.map(c=>`<td class="${c.l?"l":""}">${r.k==="charge"?(c.c?c.c(r):""):c.f?c.f(r):esc(r[c.k])}</td>`).join("")}</tr>`).join("")}</tbody></table>${rs.length>500?`<div class="sub" style="padding:8px">Showing the first 500 of ${rs.length}. Narrow the filter.</div>`:""}`;
    $$("th",host).forEach(th=>th.onclick=()=>{if(sk===th.dataset.k)dir=-dir;else{sk=th.dataset.k;dir=-1}draw()});
    $$("tbody tr",host).forEach(tr=>tr.onclick=ev=>{if(ev.target.closest("a"))return;onRow(rows[+tr.dataset.i])})};
  draw();
}
// ---------- date range, like Tessie: presets and a calendar. Days are local days (what is shown); the order everywhere is UTC (t0), which never repeats or skips.
const ymd=d=>d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0");
const addDays=(d,n)=>{const r=new Date(d);r.setDate(r.getDate()+n);return r};
const PRESETS=[["today","Today"],["yday","Yesterday"],["7","Last 7 days"],["30","Last 30 days"],["year","This year"],["lastyear","Last year"],["all","All time"]];
function rangeOf(st){const t=new Date(),T=ymd(t),y=t.getFullYear();
  switch(st.p){case"today":return[T,T];case"yday":{const d=ymd(addDays(t,-1));return[d,d]}case"7":return[ymd(addDays(t,-6)),T];case"30":return[ymd(addDays(t,-29)),T];
    case"year":return[y+"-01-01",T];case"lastyear":return[(y-1)+"-01-01",(y-1)+"-12-31"];case"custom":return[st.a,st.b||st.a];default:return null}}
const inRange=(x,st)=>{const r=rangeOf(st);return !r||(x.day>=r[0]&&x.day<=r[1])};
const rangeLabel=st=>{if(st.p==="custom"){const[a,b]=rangeOf(st);return a===b?dayName(a):dayName(a)+" – "+dayName(b)}return(PRESETS.find(p=>p[0]===st.p)||[0,"All time"])[1]};
const daysOf=rows=>{const s=new Set();rows.forEach(x=>s.add(x.day));return s};
// an item that runs over several days (a park of four days) belongs to every one of them, not only the day it starts or ends on
const spanOf=v=>{const a=v.start.slice(0,10),e=(v.end||v.start).slice(0,10);return[a,e>=a?e:a]};
const inSpan=(v,st)=>{const r=rangeOf(st);if(!r)return true;const[a,e]=spanOf(v);return a<=r[1]&&e>=r[0]};
const spanDays=items=>{const s=new Set();items.forEach(v=>{const[a,e]=spanOf(v);for(let n=0,d=a;d<=e&&n<400;n++){s.add(d);d=new Date(Date.parse(d+"T00:00:00Z")+864e5).toISOString().slice(0,10)}});return s};
// one day (or, with "Range" on, a first and last day) from a calendar drop-down: the days that have something are bold; a single day is one click
function dayPicker(host,o){
  let open=false,rng=!!o.range,pend=null,ym=o.cur.slice(0,7);
  const draw=()=>{
    host.innerHTML=`<span class="rngw"><button class="btn rng" type="button">▦ ${esc(o.text||o.label(o.cur))} ▾</button>${open?'<div class="rpop"><div class="rcal"></div></div>':""}</span>`;
    $(".rng",host).onclick=e=>{e.stopPropagation();open=!open;pend=null;ym=o.cur.slice(0,7);draw()};
    if(!open)return;
    const [Y,M]=ym.split("-").map(Number),lead=(new Date(Y,M-1,1).getDay()+6)%7,dim=new Date(Y,M,0).getDate(),r=pend?[pend,pend]:(rng&&o.range)||[o.cur,o.cur];let g="";
    for(let i=0;i<lead;i++)g+="<i></i>";
    for(let d=1;d<=dim;d++){const k=Y+"-"+String(M).padStart(2,"0")+"-"+String(d).padStart(2,"0"),has=o.days.has(k),c=[has?"has":"fut",k>=r[0]&&k<=r[1]?"in":"",k===r[0]||k===r[1]?"sel":""].join(" ");g+=`<button type="button" class="${c}" data-d="${k}" ${has||rng?"":"disabled"}>${d}</button>`}
    $(".rcal",host).innerHTML=`<div class="rhd"><button type="button" data-m="-1">‹</button><b>${MON[M-1]} ${Y}</b><button type="button" data-m="1">›</button><button type="button" class="chip ${rng?"on":""}" data-rg title="Pick a first and a last day">Range</button></div><div class="rdw"><i>M</i><i>T</i><i>W</i><i>T</i><i>F</i><i>S</i><i>S</i></div><div class="rgd">${g}</div><div class="sub" style="margin-top:6px">${rng?(pend?"Now the last day (or the same day again).":"Pick the first day."):"Days in bold have something to show."}</div>`;
    $$(".rhd button[data-m]",host).forEach(b=>b.onclick=e=>{e.stopPropagation();ym=ymd(new Date(Y,M-1+ +b.dataset.m,1)).slice(0,7);draw()});
    $(".rhd [data-rg]",host).onclick=e=>{e.stopPropagation();rng=!rng;pend=null;draw()};
    $$(".rgd button",host).forEach(b=>b.onclick=e=>{e.stopPropagation();const k=b.dataset.d;
      if(!rng){open=false;o.onPick(k);return}
      if(!pend){pend=k;draw();return}
      const a=pend<k?pend:k,z=pend<k?k:pend;open=false;pend=null;o.onRange(a,z)})};
  if(!rangePicker.bound){rangePicker.bound=true;document.addEventListener("click",e=>{if(!e.target.closest(".rngw"))document.dispatchEvent(new CustomEvent("rngclose"))})}
  const close=()=>{if(!document.body.contains(host)){document.removeEventListener("rngclose",close);return}if(open){open=false;pend=null;draw()}};
  document.addEventListener("rngclose",close);
  draw()}
function rangePicker(host,st,days,onChange){
  let open=false,ym=null,pend=null;
  const first=()=>{const r=rangeOf(st);return r?r[1]:ymd(new Date())};
  const draw=()=>{
    host.innerHTML=`<span class="rngw"><button class="btn rng" type="button">▦ ${esc(rangeLabel(st))} ▾</button>${open?`<div class="rpop"><div class="rpre">${PRESETS.map(([k,t])=>`<button type="button" class="${st.p===k?"on":""}" data-p="${k}">${t}</button>`).join("")}</div><div class="rcal"></div></div>`:""}</span>`;
    $(".rng",host).onclick=e=>{e.stopPropagation();open=!open;if(open){ym=ym||first().slice(0,7)}pend=null;draw()};
    if(!open)return;
    $$(".rpre button",host).forEach(b=>b.onclick=e=>{e.stopPropagation();st.p=b.dataset.p;open=false;pend=null;draw();onChange()});
    const [Y,M]=ym.split("-").map(Number),f=new Date(Y,M-1,1),lead=(f.getDay()+6)%7,dim=new Date(Y,M,0).getDate(),r=pend?[pend,pend]:rangeOf(st),T=ymd(new Date());
    let g="";for(let i=0;i<lead;i++)g+="<i></i>";
    for(let d=1;d<=dim;d++){const k=Y+"-"+String(M).padStart(2,"0")+"-"+String(d).padStart(2,"0"),c=[days.has(k)?"has":"",r&&k>=r[0]&&k<=r[1]?"in":"",r&&(k===r[0]||k===r[1])?"sel":"",k===T?"today":"",k>T?"fut":""].join(" ");g+=`<button type="button" class="${c}" data-d="${k}">${d}</button>`}
    $(".rcal",host).innerHTML=`<div class="rhd"><button type="button" data-m="-1">‹</button><b>${MON[M-1]} ${Y}</b><button type="button" data-m="1">›</button></div><div class="rdw"><i>M</i><i>T</i><i>W</i><i>T</i><i>F</i><i>S</i><i>S</i></div><div class="rgd">${g}</div><div class="sub" style="margin-top:6px">${pend?"Pick the last day (or the same day again).":"Pick a day, or a first and last day."} Days with records are bold.</div>`;
    $$(".rhd button",host).forEach(b=>b.onclick=e=>{e.stopPropagation();const d=new Date(Y,M-1+ +b.dataset.m,1);ym=ymd(d).slice(0,7);draw()});
    $$(".rgd button",host).forEach(b=>b.onclick=e=>{e.stopPropagation();const k=b.dataset.d;
      if(!pend){pend=k;draw();return}
      st.p="custom";st.a=pend<k?pend:k;st.b=pend<k?k:pend;pend=null;open=false;draw();onChange()});
  };
  if(!rangePicker.bound){rangePicker.bound=true;document.addEventListener("click",e=>{if(!e.target.closest(".rngw"))document.dispatchEvent(new CustomEvent("rngclose"))});}
  const close=()=>{if(!document.body.contains(host)){document.removeEventListener("rngclose",close);return}if(open){open=false;pend=null;draw()}};
  document.addEventListener("rngclose",close);
  draw();
}

// ---------- Drives
let DP={r:{p:"all"},q:"",ch:"all",ex:"all",rp:"drive",mk:0,ma:0};
async function pageDrives(){
  loading();const act=await api("activity"),all=act.filter(x=>x.k==="drive"),chg=act.filter(x=>x.k==="charge");
  // how much of each drive has been reviewed (Review tab): the share of its events' stills that were looked at for signs
  const rvd=await fetch("/api/drivereview/all").then(r=>r.json()).catch(()=>({}));
  all.forEach(d=>{d.rvd=rvd[d.id]==null?null:rvd[d.id]});
  // a charge has one drive that ends at it (the last one before it) and one that leaves it (the first after it); the server decides which
  const chById=new Map(chg.map(c=>[c.id,c]));
  all.forEach(d=>{d.toCh=chById.get(d.to_ch)||null;d.fromCh=chById.get(d.from_ch)||null});
  const CHF={all:()=>true,to:x=>x.toCh,from:x=>x.fromCh,any:x=>x.toCh||x.fromCh,none:x=>!x.toCh&&!x.fromCh,low:x=>x.b1!=null&&x.b1<=cfg("drives.low_battery_pct")},
   EXF={all:()=>true,foot:x=>x.foot,fsd:x=>x.ap>0,al:x=>x.al>0,hwy:x=>x.km>=cfg("drives.long_highway_km")&&x.max>=cfg("drives.long_highway_fast_kmh")};
  const draw=()=>{const q=DP.q.toLowerCase(),rs=all.filter(x=>inRange(x,DP.r)&&(!q||driveTitle(x).toLowerCase().includes(q))&&CHF[DP.ch](x)&&EXF[DP.ex](x)&&x.km>=DP.mk&&(x.min?x.km/(x.min/60):0)>=DP.ma&&(DP.rp==="both"||(DP.rp==="repark")===!!x.repark)),
    plain=!DP.q&&!DP.mk&&!DP.ma&&DP.ch==="all"&&DP.ex==="all",     // no filter but the dates: the charges show as a line between the drives
    km=rs.reduce((a,x)=>a+x.km,0),kwh=rs.reduce((a,x)=>a+(x.kwh||0),0),ap=rs.reduce((a,x)=>a+(x.ap||0),0),mn=rs.reduce((a,x)=>a+(x.min||0),0);
    $("#tl").innerHTML=tile(n0(rs.length),"drives")+tile(n0(km)+" km","distance")+tile(km?EF(kwh*1000/km)+" "+EFU():"–","average efficiency")+tile(km?n0(100*ap/km)+"%":"–","self-driving")+tile(dur(mn),"time driving")+tile(n0(Math.max(0,...rs.map(x=>x.max||0)))+" km/h","top speed");
    table($("#tb"),[{k:"t0",t:"Date",l:1,c:r=>`${r.day} ${r.hm} <span class="sub">${esc(r.tz||"")}</span>`,f:r=>`${r.day} ${r.hm} <span class="sub">${esc(r.tz||"")}</span>`},{k:"frm",t:"Trip",l:1,c:r=>`<b>Charging</b> · ${esc(r.place||"?")} · +${n0(r.kwh,1)} kWh`,f:r=>(r.fromCh?`<span class="pill" title="A charge ended just before this drive">from charge</span> `:"")+(r.fromCh?`<a class="chn" href="#/charge/${encodeURIComponent(r.fromCh.id)}" title="Left a charge here - open the charge">⚡ ${esc(r.frm||"?")}</a>`:esc(r.frm||"?"))+" → "+(r.toCh?`<a class="chn" href="#/charge/${encodeURIComponent(r.toCh.id)}" title="Charged here - open the charge">⚡ ${esc(r.to||"?")}</a>`:esc(r.to||"?"))+(r.toCh?` <span class="pill ok" title="A charge started right after this drive">to charge</span>`:"")+(r.repark?` <span class="pill" title="Under ${REPM()}: the car was only moved a little">re-park</span>`:"")},{k:"km",t:"km",f:r=>n0(r.km,1)},{k:"min",t:"Time",c:r=>dur(r.min),f:r=>dur(r.min)},{k:"whkm",t:EFU(),f:r=>EF(r.whkm)},{k:"max",t:"Max",f:r=>n0(r.max)},{k:"ap",t:"Self-driving",f:r=>r.km?n0(100*r.ap/r.km)+"%":"–"},{k:"b0",t:"Battery",c:r=>`${n0(r.b0)}→${n0(r.b1)}%`,f:r=>`${n0(r.b0)}→${n0(r.b1)}%`},{k:"foot",t:"Dashcam",f:r=>r.foot==null?"–":r.foot+"%"},{k:"rvd",t:"Reviewed",f:r=>r.rvd==null?"–":r.rvd+"%"}],rs.concat(plain?chg.filter(c=>inRange(c,DP.r)):[]),r=>{location.hash=(r.k==="charge"?"#/charge/":"#/drive/")+r.id},"t0",(()=>{const g=rangeOf(DP.r);return g&&g[0]===g[1]?1:-1})())};     // one day picked: first drive of the day on top
  main.innerHTML=`<h1>Drives</h1><div class="bar"><span id="rg"></span><select id="fc"><option value="all">Any charging</option><option value="to">Ending at a charge</option><option value="from">Leaving a charge</option><option value="any">Either end at a charge</option><option value="none">No charge at either end</option><option value="low">Finished at ${cfg("drives.low_battery_pct")}% or less</option></select><select id="fr"><option value="drive">Drives</option><option value="repark">Re-parks (under ${REPM()})</option><option value="both">Drives and re-parks</option></select><select id="fx"><option value="all">All drives</option><option value="hwy">Long highway (${cfg("drives.long_highway_km")} km+)</option><option value="fsd">Used self-driving</option><option value="foot">With dashcam footage</option><option value="al">With alerts</option></select><input type="number" id="mk" min="0" step="5" style="width:90px" placeholder="min km" title="Only drives at least this long" value="${DP.mk||""}"><input type="number" id="ma" min="0" step="5" style="width:130px" placeholder="min avg km/h" title="Only drives at least this fast on average (distance over time)" value="${DP.ma||""}"><button class="btn" id="rc" title="Long, fast drives that have footage: the ones worth a Review">Review candidates</button><input type="search" id="q" placeholder="Search a place…" value="${esc(DP.q)}"></div><div class="tiles" id="tl"></div><div id="tb"></div>`;
  rangePicker($("#rg"),DP.r,daysOf(all),draw);$("#q").oninput=e=>{DP.q=e.target.value;draw()};$("#mk").oninput=e=>{DP.mk=+e.target.value||0;draw()};$("#ma").oninput=e=>{DP.ma=+e.target.value||0;draw()};
  $("#rc").onclick=()=>{DP.mk=cfg("review.candidate_km");DP.ma=cfg("review.candidate_avg_kmh");DP.ex="foot";$("#mk").value=DP.mk;$("#ma").value=DP.ma;$("#fx").value="foot";draw()};$("#fc").value=DP.ch;$("#fc").onchange=e=>{DP.ch=e.target.value;draw()};$("#fr").value=DP.rp;$("#fr").onchange=e=>{DP.rp=e.target.value;draw()};$("#fx").value=DP.ex;$("#fx").onchange=e=>{DP.ex=e.target.value;draw()};draw();
}
// UTC is the clock everywhere; the local clock is only for showing. lo = seconds the local clock is ahead of UTC (the server sends it with a drive).
const lclock=(u,lo)=>new Date((u+lo)*1000).toISOString().substr(11,8);
const localISO=ms=>{const t=new Date(ms),z=n=>String(n).padStart(2,"0");return `${t.getFullYear()}-${z(t.getMonth()+1)}-${z(t.getDate())}T${z(t.getHours())}:${z(t.getMinutes())}:${z(t.getSeconds())}`};
// fit an embedded generated page to its content (and keep it fitted while it changes)
function fitFrame(fr,onDoc){fr.onload=()=>{try{const doc=fr.contentDocument;if(onDoc)onDoc(doc);const fit=()=>{fr.style.height=(doc.documentElement.scrollHeight+20)+"px"};fit();new ResizeObserver(fit).observe(doc.body)}catch(e){}}}
// hide a heading and the cards that follow it, up to the next heading
function hideSection(doc,title){$$(".wrap > h2",doc).forEach(h=>{if(h.textContent.startsWith(title)){let n=h;n.style.display="none";n=n.nextElementSibling;while(n&&n.tagName!=="H2"&&!n.classList.contains("sub")){n.style.display="none";n=n.nextElementSibling}}})}
// ---------- the manual review of one drive (lib/app_drive_review.py): FSD interventions, accelerator pressed while FSD is on, speed changes while FSD is on
let RVAPI=null,RVOV=null;      // set by teslaDriveBody: what the review viewer may do to the page (cursor, video, chart)
async function driveReviewPanel(host,stamp,lo,onResult){
  // an event's t is on the drive page's clock (Tessie's file, app.timezone) read as UTC seconds; it is SHOWN on the clock the drives list shows the drive on (lo)
  const hm=t=>{const po=S&&S.result&&S.result.poff!=null?S.result.poff:null;return lclock(po==null?t:t-po,lo==null?(po||0):lo)};
  const mem=()=>{try{return JSON.parse(localStorage.getItem("drv.rev")||"{}")}catch(e){return{}}},save=o=>{try{localStorage.setItem("drv.rev",JSON.stringify(o))}catch(e){}};
  let S=null,timer=null;
  const live=()=>document.body.contains(host)&&location.hash.indexOf(encodeURIComponent(stamp))>=0;
  const get=async()=>{S=await (await fetch("/api/drivereview?id="+encodeURIComponent(stamp))).json()};
  const post=async b=>{S=await (await fetch("/api/drivereview",{method:"POST",body:JSON.stringify(Object.assign({id:stamp},b))})).json()};
  // ---- the events, one at a time under the map: tabs by kind, Back / Next, the stills of the event as clickable cells that light up the map, the chart and the video
  let tab="all",ei=0,si=0,hv=null,vkey="",tries=0,MC=null,OV=null,lb=false;
  const KCOL={intervention:"#dc2626",accelerator:"#f59e0b",speed:"#2563eb"},KNAME={intervention:"FSD interventions",accelerator:"Accelerator pressed",speed:"Speed changes"};
  // road signs by kind (colour of the sign): what a sign is, and how a chip for it looks
  const SCAT={speed:["Speed limit","#dc2626"],regulatory:["Regulatory","#b91c1c"],traffic:["Traffic in our lane","#7c3aed"],warning:["Warning","#ca8a04"],roadwork:["Road work","#ea580c"],info:["Information","#2563eb"],directional:["Directional","#16a34a"],safety:["Safety","#0891b2"]};
  const sgCat=x=>SCAT[x.cat||"speed"]||SCAT.speed;
  // the lanes under the strip of an event (as in the old flip review): which lane a kind of sign goes in, and its colour
  const LANES=[["Limit","#b91c1c"],["Warn","#d4a72c"],["Blue","#2f5fbf"],["Green","#3f9d5f"],["White","#f4f4f5"],["Traffic","#7c3aed"]];
  const LANE_OF={speed:0,warning:1,roadwork:1,info:2,safety:2,directional:3,regulatory:4,traffic:5};
  const sgTitle=x=>`${sgCat(x)[0]} sign, seen ${x.n} time(s)${x.conf>0?", "+x.conf+"% sure of the reading":""}`+(x.anom?" · the car was doing "+n0(x.car)+" km/h: probably an exit or side road, not this road":"")+(x.osm!=null?" · the map says "+x.osm+(x.map?" on "+x.map:""):"")+(x.conflict?" · differs from the map":"")+(x.temp?(x.cat==="roadwork"?" · temporary road works":" · probably a temporary limit (road works nearby)"):"");
  const sgChip=x=>`<span class="sgn" style="border-left:4px solid ${sgCat(x)[1]};${x.anom?"opacity:.55;border-style:dashed;":""}" title="${esc(sgTitle(x))}">${x.f?`<img src="/review/${encodeURIComponent(x.f)}" alt="">`:""}<b>${esc(x.text||sgCat(x)[0])}</b> <span class="sub">${x.cat&&x.cat!=="speed"&&x.text?sgCat(x)[0]+" · ":""}${hm(x.t)}${x.anom?" · exit / side road?":""}${x.conflict?` · <span class="sgw">map ${x.osm}</span>`:x.osm!=null&&(x.cat||"speed")==="speed"?" · map agrees":""}${x.temp?' · <span class="sgw">temporary</span>':""}</span></span>`;
  // the badges on a thumbnail, as on the old flip review: a speed limit as its red ring, the other kinds as a small coloured sign, traffic in our lane as a car
  const GLY={info:"i",safety:"i",directional:"➜",warning:"!",roadwork:"!",regulatory:"",traffic:"🚗"};
  const badges=(e,k)=>{const r=e.shots[k],h=Math.max(.6,(e.shots.length>1?(e.shots[e.shots.length-1]-e.shots[0])/(e.shots.length-1):1)/2+.05);
    const L=(e.signs||[]).filter(g=>{const a=(g.t0!=null?g.t0:g.t)-e.t,b=(g.t1!=null?g.t1:g.t)-e.t;return r>=a-h&&r<=b+h});
    return L.length?`<span class="rvb">${L.map(g=>(g.cat||"speed")==="speed"?`<i class="bsp${g.temp||g.conflict?" w":""}" title="${esc(sgTitle(g))}">${esc(g.text||"")}</i>`:`<i class="bk" style="background:${sgCat(g)[1]}${g.cat==="regulatory"?";border:2px solid #b91c1c;background:#fff;color:#b91c1c":""}" title="${esc(sgTitle(g))}">${GLY[g.cat]||""}</i>`).join("")}</span>`:""};
  const sgSum=L=>{const c={};L.forEach(x=>{const k=x.cat||"speed";c[k]=(c[k]||0)+1});return Object.keys(c).map(k=>c[k]+" "+(SCAT[k]||SCAT.speed)[0].toLowerCase()).join(", ")};
  const vwEl=()=>document.getElementById("drevw"),curRes=()=>S&&S.result&&S.result.events?S.result:null;
  // the speed filter (kept in this browser): a speed change matches when it went from `sf` to `st` km/h (either may be blank = any), either way round when `sb`
  const spf=()=>{const m=mem();return{f:m.sf==null||m.sf===""?null:+m.sf,t:m.st==null||m.st===""?null:+m.st,both:m.sb!==false,tol:m.sx>0?+m.sx:(+cfg("review.speed_tol_kmh")||6)}};
  const spOk=e=>{if(e.kind!=="speed")return true;const q=spf();if(q.f==null&&q.t==null)return true;const tol=q.tol,
    ok=(a,b)=>(q.f==null||Math.abs(a-q.f)<=tol)&&(q.t==null||Math.abs(b-q.t)<=tol),back=(a,b)=>(q.t==null||Math.abs(a-q.t)<=tol)&&(q.f==null||Math.abs(b-q.f)<=tol);
    return ok(e.v,e.v1)||(q.both&&back(e.v,e.v1))};
  const evs=()=>{const R=curRes();return R?R.events.filter(spOk):[]};
  const list=()=>{if(!curRes())return[];return(tab==="all"?evs():evs().filter(e=>e.kind===tab)).slice().sort((a,b)=>a.t-b.t)};        // in the order of the drive        // in the order of the drive
  const geo=e=>e&&e.tile&&e.shots&&e.shots.length&&(e.pics&&e.pics.length||e.sheet)?e.tile:null;      // new reviews: one kept still per second (e.pics); older ones: a contact sheet
  const base=e=>(e.utc!=null?e.utc:e.t)-(RVAPI?RVAPI.t0():0),ea=e=>Math.round(base(e)),eb=e=>Math.round(base(e)+(e.t1-e.t));        // second of the drive (UTC both)
  const stillI=(e,k)=>Math.round(base(e)+e.shots[k]);
  const k0=e=>{let b=0;e.shots.forEach((x,k)=>{if(Math.abs(x)<Math.abs(e.shots[b]))b=k});return b};
  const sprite=(e,k)=>{const g=e.tile;
    if(e.pics&&e.pics[k])return `background-image:url(/still/${e.pics[k][0]}/${e.pics[k][1]}.jpg);background-size:cover;background-position:center;aspect-ratio:${g.w}/${g.h}`;
    const rows=Math.ceil(e.shots.length/g.cols),c=k%g.cols,r=Math.floor(k/g.cols);
    return `background-image:url(/revev/${encodeURIComponent(S.result.stamp)}/${encodeURIComponent(e.sheet)});background-size:${g.cols*100}% ${rows*100}%;background-position:${g.cols>1?c/(g.cols-1)*100:0}% ${rows>1?r/(rows-1)*100:0}%;aspect-ratio:${g.w}/${g.h}`};
  const big=(e,k)=>{const g=e.tile;if(e.hi&&e.hi[k])return `background-image:url(/revev/${encodeURIComponent(S.result.stamp)}/${encodeURIComponent(e.hi[k])});background-size:contain;background-repeat:no-repeat;background-position:center;aspect-ratio:${g.w}/${g.h}`;return sprite(e,k)};        // the big picture (full-size frame, kept small enough) while it was kept, else the small still
  const desc=e=>{
    const gm=e.la!=null?` · <a target="_blank" rel="noopener" href="https://www.google.com/maps/@${e.la},${e.lo},19z">map</a>`:"";
    let h="",tags="";
    if(e.kind==="intervention"){
      h=`${esc(e.from_mode)} → <b>${esc(e.to_mode)}</b> at ${n0(e.v)} km/h`;
      tags=(e.flags||[]).map(f=>`<span class="pill warn">${f==="brake"?"braking":f==="steering"?"steering":"accelerator"}</span>`).join(" ")+(e.at_stop?' <span class="pill" title="Stopped: the end of the FSD part of the trip, not an intervention on the road">stopped</span>':"")
        +(!(e.flags||[]).length&&!e.at_stop?' <span class="pill" title="No brake, steering or accelerator around it: a stalk, button or the car itself">no pedal / wheel input</span>':"");
      h+=`<div class="sub">${e.off_s==null?"FSD did not come back":"FSD was off for "+n0(e.off_s)+" s"} · it had been on for ${e.fsd_before_s>=120?dur(Math.round(e.fsd_before_s/60)):n0(e.fsd_before_s)+" s"}</div>`;
    }else if(e.kind==="accelerator"){
      h=`Accelerator held <b>${n0(e.dur_s)} s</b> · peak ${n0(e.peak)}% · ${n0(e.v)} → ${n0(e.v1)} km/h`;
      tags=e.ended_in_off?'<span class="pill warn">FSD switched off right after</span>':"";
    }else{
      h=`<b>${n0(e.v)} → ${n0(e.v1)} km/h</b> in ${n0(e.dur_s)} s`;
      tags=e.pressed?'<span class="pill warn">accelerator pressed during it</span>':'<span class="pill ok">no pedal input</span>';
    }
    return{h,tags,gm};
  };
  const info=(e,k)=>{if(!RVAPI||!geo(e))return"";const s=RVAPI.series(),i=stillI(e,k),rel=e.shots[k];
    return `${RVAPI.clk(i)} · ${rel?(rel>0?"+":"")+n0(rel)+" s":"the moment"}${s.v[i]!=null?" · "+n0(s.v[i])+" km/h":""}${s.accel[i]?" · accelerator "+n0(s.accel[i])+"%":""}${s.br[i]?" · braking":""}${APN[s.ap[i]]?" · "+APN[s.ap[i]]:" · manual"}`};
  // the strip across the drive: where the events of this tab are; click one to go to it
  const ovDraw=()=>{const cv=$("#rvo"),R=curRes(),L=list();if(!cv||!R)return;const dpr=devicePixelRatio||1,W=cv.clientWidth,H=cv.clientHeight;if(!W)return;cv.width=Math.round(W*dpr);cv.height=Math.round(H*dpr);
    const c=cv.getContext("2d");c.setTransform(dpr,0,0,dpr,0,0);const st=getComputedStyle(document.documentElement),css=n=>st.getPropertyValue(n).trim()||"#888";
    const t0=R.t0!=null?R.t0:Math.min(...R.events.map(e=>e.t)),t1=Math.max(R.t1!=null?R.t1:t0+1,t0+1),X=t=>8+(W-16)*(t-t0)/(t1-t0);
    c.strokeStyle=css("--line");c.lineWidth=3;c.lineCap="round";c.beginPath();c.moveTo(8,H/2);c.lineTo(W-8,H/2);c.stroke();
    if(RVAPI&&RVAPI.saved){const po=R.poff||0;c.strokeStyle="#9333ea";c.lineWidth=6;RVAPI.saved().forEach(([u0,u1])=>{const x0=X(Math.max(t0,u0+po)),x1=X(Math.min(t1,u1+po));if(x1>x0){c.beginPath();c.moveTo(x0,H/2);c.lineTo(Math.max(x1,x0+2),H/2);c.stroke()}})}     // where saved footage exists
    OV=L.map((e,k)=>({k,x:X(e.t)}));
    L.forEach((e,k)=>{const x=X(e.t),on=k===ei;c.fillStyle=KCOL[e.kind];c.beginPath();c.arc(x,H/2,on?7:4,0,7);c.fill();if(on){c.strokeStyle=css("--fg")||"#000";c.lineWidth=2;c.beginPath();c.arc(x,H/2,10,0,7);c.stroke()}})};
  RVOV=ovDraw;
  // the strip of this event: speed, FSD, accelerator, the signs, a tick for every still (the picked one bold)
  const miniDraw=()=>{const m=MC;if(!m||!RVAPI)return;const cv=m.cv,e=m.e,s=RVAPI.series(),dpr=devicePixelRatio||1,W=cv.clientWidth,H=cv.clientHeight;if(!W||!H)return;
    if(cv.width!==Math.round(W*dpr)||cv.height!==Math.round(H*dpr)){cv.width=Math.round(W*dpr);cv.height=Math.round(H*dpr)}
    const c=cv.getContext("2d");c.setTransform(dpr,0,0,dpr,0,0);c.clearRect(0,0,W,H);
    const st=getComputedStyle(document.documentElement),css=n=>st.getPropertyValue(n).trim()||"#888",ink=css("--mut"),line=css("--line"),acc=css("--acc"),ok=css("--charge"),bad=css("--bad");
    const LB=0,a=m.a,b=m.b,L=44,R=8,T=14,B=18+LB,X=i=>L+(W-L-R)*(i-a)/Math.max(1,b-a),i0=Math.max(0,Math.floor(a)),i1=Math.min(s.v.length-1,Math.ceil(b)),sw=Math.max(1,X(i0+1)-X(i0));
    // the speed zones of this section: each speed sign's limit from where it was seen to the next sign (a sign for an exit or side road is left out)
    const zs=(e.signs||[]).filter(g=>(g.cat||"speed")==="speed"&&!g.anom&&/^\d+/.test(g.text||"")).map(g=>({v:+String(g.text).match(/^\d+/)[0],i:base(e)+(g.t0!=null?g.t0:g.t)-e.t})).sort((p,q)=>p.i-q.i);
    let vm=40;for(let i=i0;i<=i1;i++)if(s.v[i]!=null&&s.v[i]>vm)vm=s.v[i];zs.forEach(z=>{if(z.v>vm)vm=z.v});vm=Math.ceil(vm/20)*20;const Y=v=>T+(H-T-B)*(1-v/vm);
    c.fillStyle=KCOL[e.kind];c.globalAlpha=.14;c.fillRect(X(ea(e)),T,Math.max(3,X(eb(e))-X(ea(e))),H-T-B);c.globalAlpha=1;
    c.font="11px system-ui";c.fillStyle=ink;c.strokeStyle=line;c.lineWidth=1;c.beginPath();c.moveTo(L,Y(0));c.lineTo(W-R,Y(0));c.stroke();c.fillText("0",3,Y(0)+4);c.fillText(vm,3,Y(vm)+4);
    zs.forEach((z,k)=>{const x0=X(Math.max(a,z.i)),x1=X(k<zs.length-1?Math.min(b,zs[k+1].i):b);if(x1<=L||x0>=W-R||x1<=x0)return;c.strokeStyle="#b91c1c";c.lineWidth=1.5;c.setLineDash([5,3]);c.beginPath();c.moveTo(x0,Y(z.v));c.lineTo(x1,Y(z.v));c.stroke();c.setLineDash([]);
      c.fillStyle="#b91c1c";c.font="bold 10px system-ui";c.fillText(z.v+" km/h",x0+3,Y(z.v)-3);c.font="11px system-ui";c.fillStyle=ink});
    for(let i=i0;i<=i1;i++){if(s.v[i]==null)continue;const q=s.ap[i];c.fillStyle=apCol(q>0&&q<4?APN[q]:"Manual");c.fillRect(X(i),1,sw,7)}
    c.fillStyle=ok;c.globalAlpha=.4;for(let i=i0;i<=i1;i++)if(s.accel[i]){const h=(H-T-B)*.4*Math.min(100,s.accel[i])/100;c.fillRect(X(i),H-B-h,sw,h)}c.globalAlpha=1;
    c.fillStyle=bad;c.globalAlpha=.5;for(let i=i0;i<=i1;i++)if(s.br[i])c.fillRect(X(i),H-B-3,sw,3);c.globalAlpha=1;
    c.beginPath();let on=false;for(let i=i0;i<=i1;i++){const v=s.v[i];if(v==null){on=false;continue}const x=X(i),y=Y(v);on?c.lineTo(x,y):c.moveTo(x,y);on=true}c.strokeStyle=acc;c.lineWidth=2;c.stroke();
    c.textBaseline="alphabetic";c.textAlign="start";
    const hot=hv!=null?hv:si;m.xs=[];
    (geo(e)?e.shots:[]).forEach((x,k)=>{const px=X(stillI(e,k)),h=k===hot;m.xs.push(px);c.strokeStyle=c.fillStyle=h?"#f59e0b":KCOL[e.kind];c.globalAlpha=h?1:.45;c.lineWidth=h?2.5:1;c.beginPath();c.moveTo(px,T);c.lineTo(px,H-B);c.stroke();c.beginPath();c.arc(px,T,h?4.5:2.5,0,7);c.fill();c.globalAlpha=1});
    c.strokeStyle=KCOL[e.kind];c.lineWidth=1.5;c.setLineDash([4,3]);c.beginPath();c.moveTo(X(base(e)),T);c.lineTo(X(base(e)),H-B);c.stroke();c.setLineDash([]);
    c.fillStyle=ink;c.font="11px system-ui";for(let k=0;k<=4;k++){const i=a+(b-a)*k/4,t=RVAPI.clk(Math.round(i)).slice(0,8);c.fillText(t,Math.max(2,Math.min(W-52,X(i)-24)),H-4)}};
  // is the video of this event still on a drive? (the log of the TeslaCam files); the stills stay either way
  const DRN={A:"the Archive",J:"JOWUA",U:"the Tesla USB"};
  const vstat=e=>{if(!e.pics||!e.pics.length)return;const sts=[...new Set(e.pics.map(p=>p[0]))];
    fetch("/api/clipstatus?stamps="+sts.join(",")).then(r=>r.json()).then(m=>{const el=$("#rvst");if(!el||list()[ei]!==e)return;
      const v=sts.map(s=>m[s]||{state:"unknown"}),del=v.filter(x=>x.state==="deleted"),kept=v.filter(x=>x.state==="kept"),on=[...new Set(kept.flatMap(x=>x.on||[]))].map(r=>DRN[r]||r);
      el.textContent=del.length===v.length?`Video deleted${del[0].gone?" "+dayName(del[0].gone.slice(0,10)):""}. The stills are kept.`:del.length?`${del.length} of ${v.length} minutes of video deleted. The stills are kept.`:kept.length?`Video on ${on.join(" and ")}.`:""}).catch(()=>{})};
  // what is lit: the cells, the big still, the strip, the chart under the map
  const hot=()=>{const L=list(),e=L[ei];if(!e)return;const k=hv!=null?hv:si;
    $$("#drevw .rvc").forEach(b=>{b.classList.toggle("on",+b.dataset.k===si);b.classList.toggle("hot",hv!=null&&+b.dataset.k===hv)});
    if(geo(e)){const bg=$("#rvbig");if(bg)bg.setAttribute("style",big(e,k));const cp=$("#rvcap");if(cp)cp.textContent=info(e,k)}
    miniDraw();if(RVAPI&&geo(e))RVAPI.focus({a:ea(e),b:eb(e),ticks:e.shots.map((x,j)=>stillI(e,j)),hot:k,col:KCOL[e.kind]})};
  const pick=(k,seek)=>{const e=list()[ei];if(!e||!geo(e))return;si=Math.max(0,Math.min(e.shots.length-1,k));hv=null;hot();
    if(RVAPI){RVAPI.cursor(stillI(e,si));if(seek)RVAPI.seek(stillI(e,si))}if(lb)lbShow()};
  const arrive=e=>{if(!RVAPI||!e)return;const g=geo(e),t=g?e.shots.map((x,j)=>stillI(e,j)):[];       // the chart under the map shows only this event's section (Review: nothing else), the map dot goes to it
    RVAPI.lanes((e.signs||[]).map(g=>{const k=LANE_OF[g.cat||"speed"];return k==null?null:{k,a:base(e)+(g.t0!=null?g.t0:g.t)-e.t,b:base(e)+(g.t1!=null?g.t1:g.t)-e.t,txt:(g.cat||"speed")==="speed"?g.text:"",col:LANES[k][1],hot:!!(g.temp||g.conflict),tip:sgTitle(g)}}).filter(Boolean));
    RVAPI.focus({a:ea(e),b:eb(e),ticks:t,hot:si,col:KCOL[e.kind]});RVAPI.limit(Math.min(ea(e),t.length?t[0]:ea(e))-10,Math.max(eb(e),t.length?t[t.length-1]:eb(e))+10);RVAPI.map(Math.min(ea(e),t.length?t[0]:ea(e))-3,Math.max(eb(e),t.length?t[t.length-1]:eb(e))+3,(e.signs||[]).filter(x=>["speed","warning","roadwork"].includes(x.cat||"speed")).map(x=>({i:Math.round(base(e)+(x.t-e.t)),side:x.side,cat:x.cat||"speed",text:x.text,temp:x.temp,conflict:x.conflict,osm:x.osm,car:x.car})));RVAPI.cursor(g?stillI(e,si):ea(e));RVAPI.seek(g?stillI(e,si):ea(e))};      // the video below goes to the event too, not on playing from where it was
  const go=n=>{const L=list();if(!L.length)return;ei=Math.max(0,Math.min(L.length-1,n));const e=L[ei];si=geo(e)?k0(e):0;hv=null;viewer(true);arrive(e)};
  // the big picture
  const lbShow=()=>{const e=list()[ei],o=document.getElementById("rvlb");if(!e||!o||!geo(e)){return}
    o.querySelector(".rvlbi").setAttribute("style",big(e,si)+";width:min(92vw,"+(e.hi&&e.hi[si]?1600:e.tile.w*2.2)+"px,calc(78vh*"+e.tile.w+"/"+e.tile.h+"))");o.querySelector(".rvlbc").textContent=e.id+" · "+info(e,si)+" · "+(si+1)+" of "+e.shots.length};
  const lbClose=()=>{lb=false;const o=document.getElementById("rvlb");if(o)o.remove()};
  const lbOpen=()=>{const e=list()[ei];if(!e||!geo(e))return;lbClose();lb=true;const o=document.createElement("div");o.id="rvlb";o.className="rvlb";
    o.innerHTML='<div class="rvlbi"></div><div class="rvlbc"></div><div class="rvlbn"><button class="btn" data-d="-1">‹ Earlier</button><button class="btn" data-d="1">Later ›</button><button class="btn" data-d="0">Close</button></div>';
    o.onclick=ev=>{const d=ev.target.dataset&&ev.target.dataset.d;if(d==null){if(ev.target===o)lbClose();return}if(d==="0")lbClose();else pick(si+ +d,true)};document.body.appendChild(o);lbShow()};
  const keys=ev=>{if(!live()){removeEventListener("keydown",keys);lbClose();return}if(!list().length||/INPUT|TEXTAREA|SELECT/.test((ev.target||{}).tagName||"")||!vwEl())return;
    const L=list(),e=L[ei];
    if(ev.key==="Escape"){if(lb){lbClose();ev.preventDefault()}return}
    if(ev.key==="ArrowLeft"||ev.key==="ArrowRight"){const d=ev.key==="ArrowLeft"?-1:1;ev.preventDefault();if(ev.shiftKey)go(ei+d);else if(e&&geo(e)){if(si+d<0)go(ei-1);else if(si+d>=e.shots.length)go(ei+1);else pick(si+d,true)}else go(ei+d)}};
  addEventListener("keydown",keys);
  const viewer=force=>{
    const box=vwEl(),R=curRes();
    if(!box){if(R&&tries++<40)setTimeout(()=>{if(live())viewer(force)},300);return}
    if(!R){box.innerHTML="";return}
    let first=false;if(vkey!==R.made){vkey=R.made;tab="all";ei=0;si=0;hv=null;force=true;first=true}
    if(!force&&box.firstChild)return;
    const L=list();if(ei>=L.length)ei=Math.max(0,L.length-1);const e=L[ei],cnt=k=>evs().filter(x=>x.kind===k).length,nsp=R.events.filter(x=>x.kind==="speed").length;
    const tabs=[["all","All",evs().length],["intervention",KNAME.intervention,cnt("intervention")],["accelerator",KNAME.accelerator,cnt("accelerator")],["speed",KNAME.speed,cnt("speed")]];
    let h=`<div class="card rvw"><div class="bar" style="margin:0 0 8px">${tabs.map(([k,t,n])=>`<button class="chip ${k===tab?"on":""}" data-tab="${k}" ${n?"":"disabled"}>${t} <span class="sub">${n}</span></button>`).join("")}</div>`;
    if(nsp&&(tab==="all"||tab==="speed")){const q=spf();h+=`<div class="bar" style="margin:0 0 8px"><span class="sub">Speed changes from</span><input type="number" id="spf" value="${q.f==null?"":q.f}" placeholder="any" step="5" min="0" max="250" style="width:70px"><span class="sub">to</span><input type="number" id="spt" value="${q.t==null?"":q.t}" placeholder="any" step="5" min="0" max="250" style="width:70px"><span class="sub">km/h, give or take</span><input type="number" id="spx" value="${q.tol}" step="1" min="0" max="30" style="width:56px" title="The car rarely sits exactly on the set speed: 100 to 110 with a Hurry to Standard switch is about 105 to 112"><label class="sub"><input type="checkbox" id="spb" ${q.both?"checked":""}> either way round</label><button class="btn" id="spc" ${q.f==null&&q.t==null?"disabled":""}>Show all speed changes</button><span class="sub">${n0(evs().filter(x=>x.kind==="speed").length)} of ${n0(nsp)} shown</span></div>`}
    if(!e){h+=`<div class="sub">${R.events.length?"Nothing in this tab.":"No events found in this drive."}</div></div>`}
    else{
      const dd=desc(e),g=geo(e);
      h+=`<div class="rvnav"><button class="btn" id="rvp" ${ei?"":"disabled"}>‹ Back</button><div style="flex:1;text-align:center;min-width:0"><b>${esc(e.id)}</b> · ${hm(e.t)}${dd.gm}<div class="sub">${esc(KNAME[e.kind])} · ${ei+1} of ${L.length}, in the order of the drive</div></div><button class="btn pri" id="rvn" ${ei<L.length-1?"":"disabled"}>Next ›</button></div>
        <canvas id="rvo" style="width:100%;height:26px;display:block;cursor:pointer" title="Where the events are in the drive; click one"></canvas>
        <div class="rvlg" style="margin:0 0 4px">${Object.keys(KCOL).map(k=>`<span><i style="background:${KCOL[k]};border-radius:50%"></i>${KNAME[k]}</span>`).join("")}<span><i style="border:2px solid var(--fg);border-radius:50%;width:7px;height:7px"></i>this event</span><span><i style="background:#9333ea;width:18px;height:5px;border-radius:3px"></i>Saved footage</span><span>Along the drive, left to right; click a dot to go to it</span></div>
        <div style="margin:8px 0 2px">${dd.h}</div><div>${dd.tags}</div>`;
      if(e.look_error)h+=`<div class="sub" style="color:var(--bad)">${esc(e.look_error)}</div>`;
      if(g){
        h+=`<div class="rvmain"><div><div id="rvbig" class="rvimg" style="${big(e,si)}" title="Click to enlarge"></div><div class="sub" id="rvcap" style="margin-top:4px"></div><div class="sub" id="rvst"></div></div>
          <div><canvas id="rvc" class="rvcv"></canvas><div class="sgs">${(e.signs||[]).filter(x=>x.cat!=="traffic").map(sgChip).join("")}</div>
          <div class="sub" style="margin-top:4px">${(e.signs||[]).filter(x=>x.cat!=="traffic").length?"Signs found in these stills: "+sgSum(e.signs.filter(x=>x.cat!=="traffic"))+". The kind comes from the sign's colour; check each reading against its picture.":"No signs found in the stills."}</div></div></div>
          <div class="rvfilm">${e.shots.map((x,k)=>`<button class="rvc" data-k="${k}" style="${sprite(e,k)}" title="${x>0?"+":""}${n0(x)} s">${badges(e,k)}</button>`).join("")}</div>
          <div class="sub" style="margin-top:6px">Click a still to light it up on the map, the chart and the video. ← → step through the stills, Shift + ← → through the events.</div>`}
      else if(e.sheet)h+=`<div style="margin-top:8px"><a href="/revev/${encodeURIComponent(R.stamp)}/${encodeURIComponent(e.sheet)}" target="_blank"><img class="rvsheet" src="/revev/${encodeURIComponent(R.stamp)}/${encodeURIComponent(e.sheet)}"></a><div class="sub">This review was run before the stills could be picked one by one. Run it again for that.</div></div>`;
      else h+=`<div class="sub" style="margin-top:8px">${R.stills?"No stills for this event.":"No stills: the drive holding this footage was not connected when this review ran."}</div>`;
      h+="</div>"}
    box.innerHTML=h;
    $$("[data-tab]",box).forEach(b=>b.onclick=()=>{tab=b.dataset.tab;ei=0;const x=list()[0];si=x&&geo(x)?k0(x):0;hv=null;viewer(true);if(x)arrive(x)});
    const spSet=o=>{save(Object.assign(mem(),o));ei=0;const x=list()[0];si=x&&geo(x)?k0(x):0;hv=null;viewer(true);if(x)arrive(x)};
    if($("#spf",box)){const rd=()=>({sf:$("#spf",box).value,st:$("#spt",box).value,sx:$("#spx",box).value,sb:$("#spb",box).checked});
      $("#spf",box).onchange=$("#spt",box).onchange=$("#spx",box).onchange=$("#spb",box).onchange=()=>spSet(rd());$("#spc",box).onclick=()=>spSet({sf:"",st:""})}
    if(!e){MC=null;OV=null;return}
    const g=geo(e);
    $("#rvp").onclick=()=>go(ei-1);$("#rvn").onclick=()=>go(ei+1);
    const ov=$("#rvo");ov.onclick=ev=>{const r=ov.getBoundingClientRect(),x=ev.clientX-r.left,n=(OV||[]).slice().sort((p,q)=>Math.abs(p.x-x)-Math.abs(q.x-x))[0];if(n)go(n.k)};
    ovDraw();
    if(g){
      const t=e.shots.map((x,j)=>stillI(e,j)),cv=$("#rvc");MC={cv,e,a:Math.min(ea(e),t[0])-4,b:Math.max(eb(e),t[t.length-1])+4,xs:[]};
      const near=ev=>{const r=cv.getBoundingClientRect(),x=ev.clientX-r.left;let b=0;(MC.xs||[]).forEach((px,k)=>{if(Math.abs(px-x)<Math.abs(MC.xs[b]-x))b=k});return b};
      cv.onmousemove=ev=>{const k=near(ev);if(k!==hv){hv=k;hot()}};cv.onmouseleave=()=>{hv=null;hot()};cv.onclick=ev=>pick(near(ev),true);
      $$("#drevw .rvc").forEach(b=>{b.onmouseenter=()=>{hv=+b.dataset.k;hot()};b.onmouseleave=()=>{hv=null;hot()};b.onclick=()=>pick(+b.dataset.k,true)});
      $("#rvbig").onclick=lbOpen;
      hot();vstat(e);if(first)arrive(e)}
    else MC=null};
  const draw=()=>{
    if(!live())return;
    const R=S.result,job=S.job||{},run=!!job.running;
    const ms=S.missing||{py:[],cmd:[],hint:[]};
    let h=`<div class="card"><h2 style="margin-top:0">Review this drive</h2>
      <div class="sub" style="margin-bottom:8px">Finds every moment FSD was switched off, every time the accelerator was pressed while FSD was on, and every lasting speed change while FSD was on (filter them by speed afterwards, e.g. 100 → 110). The events come from the dashcam's telemetry; only the front camera is looked at, in the seconds around each event (a still every second, every speed-limit sign read). The events open under the map, one at a time.</div>
      <div class="bar" style="margin:0">
      ${run?'<button class="btn" id="rvx">Stop</button>':`<button class="btn pri" id="rvg">${R?"Run again":"Run review"}</button>`}</div>`;
    if(run){const pc=job.total?Math.min(100,Math.round(100*(job.done||0)/job.total)):null,el=job.t0?Math.max(0,Math.round(Date.now()/1000-job.t0)):0;
      h+=`<div style="margin-top:10px"><div class="sub">${esc(job.stage||"")}${job.total?(job.pct?" · "+pc+"%":" · "+n0(job.done)+" of "+n0(job.total)+" ("+pc+"%)"):""}${el?" · "+dur(Math.max(1,Math.round(el/60)))+" so far":""}</div><div class="pbar${pc==null?" ind":""}"><i ${pc==null?"":`style="width:${pc}%"`}></i></div></div>`}
    if(job.error)h+=`<div class="sub" style="margin-top:8px;color:var(--bad);user-select:text">${esc(job.error)}</div>`;
    if(ms.py.length+ms.cmd.length)h+=`<div class="sub" style="margin-top:8px">Stills and sign reading need ${esc(ms.py.concat(ms.cmd).join(", "))} installed. <code>${esc((ms.hint||[]).join(" ; "))}</code></div>`;
    else if(!S.connected&&!(R&&R.stills))h+=`<div class="sub" style="margin-top:8px">The drive holding this footage is not connected: events can be found from telemetry already read, but no stills.</div>`;
    if(R&&R.clock==null&&!(R.events||[]).length){h+=`<div class="sub" style="margin-top:8px;user-select:text"><b>Not reviewed:</b> ${esc(R.note||"no dashcam telemetry matches this drive")}</div>`}
    else if(R){const c=R.counts||{};
      h+=`<div class="sub" style="margin-top:8px">Last run ${new Date(R.made).toLocaleString()} · ${n0(c.intervention||0)} FSD interventions · ${n0(c.accelerator||0)} accelerator presses · ${n0(c.speed||0)} speed changes · dashcam telemetry covers ${R.coverage?n0(R.coverage.pct):0}% of the drive${R.stills?" · front-camera stills read":""}</div>`;
      if(R.note)h+=`<div class="sub" style="margin-top:4px;user-select:text">${esc(R.note)}</div>`}
    h+="</div>";
    host.innerHTML=h;
    const g=$("#rvg",host),x=$("#rvx",host);
    if(g)g.onclick=async()=>{await post({action:"start"});draw();poll()};
    if(x)x.onclick=async()=>{await post({action:"stop"});draw()};
    if(onResult)onResult(!!curRes());
    tries=0;viewer();
  };
  const poll=()=>{clearTimeout(timer);if(!live()||!(S&&S.job&&S.job.running))return;timer=setTimeout(async()=>{await get();draw();poll()},2000)};
  host.innerHTML='<div class="sub">Loading…</div>';
  try{await get();draw();poll()}catch(e){host.innerHTML=errBox(e.message,"Could not load the drive review")}
}
// Where the line on a drive's map comes from: Tessie's point file for the drive, the dashcam's telemetry, and any other drive's file that only touches this one
async function pathSources(host,d,lo){
  if(!host)return;
  let j;try{const r=await fetch("/api/pathsources?id="+encodeURIComponent(d.page?d.id:"")+"&t0="+d.t0+"&t1="+(d.t1||d.t0+d.min*60)+"&lo="+lo);if(!r.ok)throw new Error("HTTP "+r.status);j=await r.json()}catch(e){host.innerHTML="";return}
  if(!document.body.contains(host))return;
  const NM={A:"Archive",J:"JOWUA",U:"Tesla USB"},clk=t=>new Date((t+lo)*1000).toISOString().slice(11,19),T=j.tessie,C=j.dashcam,mm=s=>s<90?Math.round(s)+" s":n0(s/60,1)+" min",dur=Math.max(1,j.t1-j.t0);
  const tp=T.points>0,dc=C.read>0,pct=tp?Math.min(100,Math.round(100*T.covered_s/dur)):0;
  const tline=tp?`<b>Present.</b> ${n0(T.points)} points from ${clk(T.first)} to ${clk(T.last)}, one about every ${T.every_s} s${T.longest_gap_s>180?" (longest gap "+mm(T.longest_gap_s)+")":""}; they follow ${pct}% of the drive.${T.file?` <span class="sub" style="user-select:text">${esc(T.file)}</span>`:""}`
    :T.file?`<b>The file has no points inside this drive.</b> <span class="sub" style="user-select:text">${esc(T.file)}</span>`:`<b>Not fetched.</b> There is no point file for this drive yet: Sync Tessie fetches it.`;
  const hold=Object.entries(C.drives||{}).map(([r,v])=>`${NM[r]||r}: ${v.here?v.here+" min on the drive":""}${v.here&&v.gone?", ":""}${v.gone?v.gone+" min since removed":""}`).join(" · ");
  const dline=dc?`<b>${n0(C.read)} of ${n0(C.minutes)} minute${C.minutes===1?"":"s"} read</b>${C.moving<C.read?` (${n0(C.moving)} with the car moving)`:""}. ${hold?`<span class="sub">Footage: ${esc(hold)}</span>`:""}`
    :C.minutes?`<b>${n0(C.minutes)} minute${C.minutes===1?"":"s"} of footage, none read yet.</b> ${hold?`<span class="sub">Footage: ${esc(hold)}</span>`:""} <span class="sub">Sync footage reads the telemetry.</span>`:`<b>None.</b> <span class="sub">No dashcam footage or telemetry is kept for these times.</span>`;
  const use=tp&&dc?"the dashcam's telemetry where it exists (a point a second), Tessie's points in between":dc?"the dashcam's telemetry only":tp?"Tessie's points only (a straight line between points about every "+T.every_s+" s)":"nothing: there is no path to draw";
  const oth=(j.others||[]).map(o=>`<div class="sub">The file of the drive ${esc(o.stamp)} (${esc((o.start||"").slice(11,19))}–${esc((o.end||"").slice(11,19))}) runs ${mm(o.overlap_s)} into this one. It belongs to that drive and is not used here.</div>`).join("");
  const fe=j.fetched,fd=fe?new Date(fe.at*1000).toLocaleString("en-AU",{day:"numeric",month:"short",year:"numeric",hour:"2-digit",minute:"2-digit"}):"",
    fline=fe?`<div class="sub" style="margin-top:8px">Asked Tessie for this drive's path on ${esc(fd)}: ${fe.status==="complete"?"fetched in full.":fe.status==="empty"?"Tessie has no points for it, so it is not asked again.":"could not be fetched; the next sync tries again."}</div>`:"";
  host.innerHTML=`<details class="card" ${tp||dc?"":"open"}><summary><b>Path sources</b> <span class="sub">· drawn from ${esc(use)}</span></summary>
    <div style="display:grid;grid-template-columns:max-content minmax(0,1fr);gap:8px 16px;margin-top:10px"><span class="sub">Tessie's path file</span><span>${tline}</span><span class="sub">Dashcam telemetry</span><span>${dline}</span></div>${oth?`<div style="margin-top:8px">${oth}</div>`:""}${fline}</details>`;
}
async function pageDrive(stamp,tab){
  const act=await api("activity"),d=act.find(x=>x.k==="drive"&&x.id===stamp);
  const als=await alertsFor("drive",stamp);
  if(tab==="flips")tab="review";tab=tab==="review"||(tab==="detail"&&d&&d.page)?tab:(d?"drive":"detail");       // "overview" and "footage" (older links) are the one drive view
  const tabs=[["drive","Drive"],["review","Review"]].concat(d&&d.page?[["detail","Tessie detail"]]:[]);
  main.innerHTML=crumbs("#/driveslist","Drives (new)")+alertsCard(als,"drive")+(d?`<div class="bar" id="dtabs">${tabs.map(([k,t])=>`<a class="chip ${k===tab?"on":""}" href="#/drive/${encodeURIComponent(stamp)}/${k}">${t}</a>`).join("")}</div>`:"")+'<div id="dtab"></div>';
  wireAlerts(main);const box=$("#dtab");
  if(tab==="detail"){
    // Tessie's own page for the drive (its extra series, the enrichment tick box under the map); the footage and the flips are in the other tabs
    const pref=()=>{try{return localStorage.getItem("drv.enrich")!=="0"}catch(e){return true}},src=on=>`/data/drives/deepdive/${encodeURIComponent(stamp)}/drive.html?enrich=${on?1:0}`;
    box.innerHTML=`<iframe class="drive" id="fr"></iframe>`;
    $("#fr").src=src(pref());
    fitFrame($("#fr"),doc=>{$$("a",doc).forEach(a=>{if(/^(\.\.\/)+index\.html$/.test(a.getAttribute("href")||"")){a.parentNode.style.display="none"}});
      const f=doc.getElementById("foot");if(f)f.style.display="none";hideSection(doc,"FSD speed changes");hideSection(doc,"Saved clips")});
    return;
  }
  const t0=d.t0,t1=d.t1||d.t0+d.min*60,lo=Math.round((Date.parse(d.day+"T"+d.hm+":00Z")/1000-d.t0)/900)*900,pl=await api("places").catch(()=>({saved:[]}));
  const rv=tab==="review";
  // the clip tools (copy, save as a clip, delete) belong to Tessie's page and exist only where that page was built with footage
  const tools=!rv&&d.page&&(d.foot||d.saved);
  box.innerHTML=(rv?'<div id="drr"></div>':'')+'<div id="tdh"></div>'+(rv?'':'<div id="psrc" style="margin-top:14px"></div>')+(tools?'<details class="card" id="mg" style="margin-top:14px"><summary><b>Copy, save as a clip, or delete clips</b></summary><div id="mgb" style="margin-top:10px"></div></details>':'');
  const fetchSeg=async b=>{const r=await fetch("/api/teslacam/auto/window?t0="+t0+"&t1="+t1+"&lo="+lo+"&id="+encodeURIComponent(stamp)+"&budget="+(b==null?20:b));
    if(!r.ok){const e=(await r.json().catch(()=>({}))).error||("HTTP "+r.status);const x=new Error(e);x.none=r.status===404;throw x}return r.json()};
  if(tools)$("#mg").ontoggle=()=>{if(!$("#mg").open||$("#mgb").innerHTML)return;$("#mgb").innerHTML=`<iframe class="drive" id="mf" src="/data/drives/deepdive/${encodeURIComponent(stamp)}/drive.html"></iframe>`;
    fitFrame($("#mf"),doc=>{const w=doc.querySelector(".wrap");if(!w)return;if(!doc.getElementById("foot")){$("#mg").remove();return}
      [...w.children].forEach(c=>{if(c.id!=="foot")c.style.display="none"})})};
  if(!rv)pathSources($("#psrc"),d,lo);
  let bodyOn=false;
  const body=async()=>{
    try{await teslaDriveBody($("#tdh"),"A",fetchSeg,pl.saved||[],{heading:false,review:null,rvtab:rv,tessie:d})}
    catch(e){const h=$("#tdh");if(!h)return;
      if(e.none&&!rv){          // no footage or telemetry: Tessie's summary, its map and the sync
        h.innerHTML=driveLiteBody(d);miniMap($("#mp"),d.lat,d.lon,"#3b6cf6");
        tessieSync($("#tsb2"),{missing:{no_page:1},tried:LITETRIED[d.id],after:()=>liteSynced(d)});return}
      h.innerHTML=e.none?'<div class="card"><b>No dashcam footage or telemetry for this drive.</b><div class="sub" style="margin-top:6px">No connected drive (Archive, JOWUA or Tesla USB) has footage for these times, and no telemetry for them was read earlier.</div></div>':errBox(e.message,"Could not rebuild this drive")}};
  // Review: nothing but the Review card until the drive has been reviewed; then the map, chart, video and the events
  if(rv)driveReviewPanel($("#drr"),stamp,lo,has=>{if(has&&!bodyOn){bodyOn=true;body()}});else body();
}

// A drive Tessie recorded that has no per-drive file (so no dashcam / deep-dive page): the summary from the drives list.
function driveLite(d,als){
  main.innerHTML=crumbs("#/driveslist","Drives (new)")+driveLiteBody(d,als);
  wireAlerts(main);miniMap($("#mp"),d.lat,d.lon,"#3b6cf6");
  tessieSync($("#tsb2"),{missing:{no_page:1},tried:LITETRIED[d.id],after:()=>liteSynced(d)});
}
// after a sync the drive has its own page under a new id: open that one
const LITETRIED={};
async function liteSynced(d){
  bust();
  const a=await api("activity").catch(()=>[]),x=a.find(v=>v.k==="drive"&&Math.abs(v.t0-d.t0)<=60);
  if(!x||!x.page)LITETRIED[d.id]=1;               // synced and still no page: Tessie has nothing more, do not offer it again
  const h="#/drive/"+encodeURIComponent(x?x.id:d.id);if(location.hash===h)route();else location.hash=h}
function driveLiteBody(d,als){
  return `<h1>${esc(driveTitle(d))}</h1><div class="sub">${dayName(d.day)} · ${d.hm} – ${addMin(d.hm,d.min)} · ${dur(d.min)} · ${n0(d.km,1)} km</div>
   <div class="tiles">${tile(n0(d.km,1)+" km","distance")}${tile(n0(d.kwh,1)+" kWh","energy used")}${tile(d.km?EF(d.whkm||d.kwh*1000/d.km)+" "+EFU():"–","efficiency")}${tile(n0(d.b0)+"% → "+n0(d.b1)+"%","battery")}${tile(n0(d.avg)+" / "+n0(d.max)+" km/h","average / top speed")}${tile(d.km?n0(100*(d.ap||0)/d.km)+"%":"–","autopilot, per Tessie")}</div>
   ${als?alertsCard(als,"drive"):""}<div class="card"><h2 style="margin-top:0">Where</h2><div class="map" id="mp"></div><div class="sub" style="margin-top:8px">From ${esc(d.frm||"?")} to ${esc(d.to||"?")}${d.pkey1?' · <a href="#/place/'+encodeURIComponent(d.pkey1)+'">where it ended</a>':""}</div></div>
   <div class="sub" style="margin:12px 0 8px">Tessie's detailed record of this drive (its route points) has not been fetched yet. If the dashcam footage or telemetry for it is kept, the <b>Footage</b> tab shows the route and the video.</div><div id="tsb2"></div>`;
}

// ---------- Charges: the charge book (analyse_charging.py's reconcile - invoices, plug-ins, Tessie sync) (REQ-207)
let CP={r:{p:"all"},kind:"all",q:"",inv:"all",tab:"charges",open:{},sk:"t0",dir:-1};
const cmpv=(x,y)=>typeof x==="string"&&typeof y==="string"?x.localeCompare(y):x<y?-1:x>y?1:0;
let BOOKSEEN=false,BOOKFRESH=null;
const getBook=async()=>{try{if(BOOKFRESH){const v=BOOKFRESH;BOOKFRESH=null;return v}
  const k=await mkey("chargebook"),old=BOOKSEEN?undefined:await IDB.get(k);BOOKSEEN=true;
  const fresh=async()=>{const j=await (await fetch("/api/chargebook")).json();if(j&&j.status==="ready"&&j.book)IDB.set(k,j);return j};
  if(old!==undefined&&old.status==="ready"){fresh().then(n=>{if(n&&n.status==="ready"&&!same(n,old)){BOOKFRESH=n;redrawQuietly()}}).catch(()=>{});return old}
  return await fresh()}catch(e){return{status:"error",error:String(e),book:null}}};
const SYNC_T={synced:["Invoice in Tessie","ok","The charge is in Tessie, and Tessie already holds the invoice cost for every record of this charge."],pending:["Invoice not in Tessie","warn","The charge is in Tessie, but its cost differs from the invoice. To send the invoice cost run ./analyse_charging.py --sync-tessie-costs --apply (this app never changes Tessie)."],partial:["Invoice part in Tessie","warn","Some records already hold the invoice cost, others do not. Run ./analyse_charging.py --sync-tessie-costs --apply to send the rest."]};
const syncPill=s=>SYNC_T[s]?`<span class="pill ${SYNC_T[s][1]}" title="${esc(SYNC_T[s][2])}">${SYNC_T[s][0]}</span>`:'<span class="sub">–</span>';
const kindPill=k=>`<span class="pill ${k==="supercharger"?"sc":k}">${k==="supercharger"?"Supercharger":k==="dc"?"DC":"AC"}</span>`;
const gstOf=t=>t==null?null:t/11;
async function bookWithRaw(){
  const [act,B0]=await Promise.all([api("activity"),getBook()]);
  if(B0.book){const raw={};act.filter(x=>x.k==="charge").forEach(x=>raw[x.id]=x);B0.book.plugs.forEach(p=>{const r=raw[p.id];p.where=r?(r.place||p.place):p.place;p.pkey=r?r.pkey:"";p.raw=r||null;p.parts.forEach(x=>{x.raw=raw[x.id]||null})})}
  return B0;
}
function bookWait(B0,title,again){
  main.innerHTML=`<h1>${title}</h1><div class="empty">${B0.status==="error"?"Could not reconcile the charges with the invoices: "+esc(B0.error||""):"Reconciling the charges with their invoices… about 20 seconds the first time, then it is remembered."}</div>`;
  if(B0.status!=="error")bookPoll(title==="Charges"?"#/charges":"#/charge",again);
}
// wait for the charge book without redrawing the page every few seconds (that made it flicker): look quietly, redraw once when it is ready
function bookPoll(prefix,again,fromBuilding){
  const tick=async()=>{if(!location.hash.startsWith(prefix))return;const b=await getBook();if(!location.hash.startsWith(prefix))return;
    if(b.status==="error"||(b.book&&b.status!=="building")||(b.book&&!fromBuilding))again();else setTimeout(tick,4000)};
  setTimeout(tick,3000);
}
async function pageCharges(){
  loading();const B0=await bookWithRaw();if(!B0.book){bookWait(B0,"Charges",pageCharges);return}
  const B=B0.book,P=B.plugs;
  const COLS=[["t0","Date",1],["where","Where",1],["kind","Type"],["min","Time"],["b0","Battery"],["peak","Peak kW"],["kmh","km/h"],["disp","Dispenser kWh"],["kwh","Battery kWh"],["eff","Eff %"],["rate","$/kWh"],["cost","Cost"],["invn","Invoice"],["sync","Tessie"]];
  const invn=p=>p.invoice&&p.invoice.number||(p.invoice_only?"":null);
  const draw=()=>{
    const q=CP.q.toLowerCase();
    const rs=P.filter(p=>inRange(p,CP.r)&&(CP.kind==="all"||p.kind===CP.kind)&&(!q||((p.where||"")+" "+(p.addr||"")+" "+(p.network||"")+" "+(p.invoice&&p.invoice.number||"")).toLowerCase().includes(q))
      &&(CP.inv==="all"||(CP.inv==="with"&&p.invoice)||(CP.inv==="without"&&!p.invoice&&!p.home)||(CP.inv==="unsynced"&&(p.sync==="pending"||p.sync==="partial"))));
    const kwh=rs.reduce((a,x)=>a+(x.kwh||0),0),disp=rs.reduce((a,x)=>a+(x.disp||x.kwh||0),0),cost=rs.reduce((a,x)=>a+(x.cost||0),0),recs=rs.reduce((a,x)=>a+x.parts.length,0);
    const withInv=rs.filter(x=>x.invoice).length,billed=rs.filter(x=>!x.home).length,uns=rs.filter(x=>x.sync==="pending"||x.sync==="partial").length;
    $("#tl").innerHTML=tile(n0(rs.length),"charges"+(recs!==rs.length?" ("+recs+" Tessie records)":""))+tile(n0(kwh)+" kWh","battery energy added")+tile(money(cost),"cost")+tile(kwh?money(cost/kwh)+"/kWh":"–","average price")+tile(withInv+" of "+billed,"public charges with an invoice")+tile(uns?uns+" to send":"all sent",uns?"invoice costs not yet in Tessie":"invoice costs are in Tessie");
    const val=(p,k)=>k==="where"?(p.where||"").toLowerCase():k==="invn"?invn(p):p[k];
    rs.sort((a,b)=>{const x=val(a,CP.sk),y=val(b,CP.sk);if(x==null||y==null)return (x==null)-(y==null);return CP.dir*cmpv(x,y)});
    const row=p=>{const multi=p.parts.length>1,op=CP.open[p.id];
      let h=`<tr class="chr" data-id="${p.id}"><td class="l">${p.day} ${p.hm}${multi?` <span class="pill ptg" data-tg="${p.id}" title="${op?"Hide":"Show"} the ${p.parts.length} Tessie records">${op?"▾":"▸"} ${p.parts.length} parts</span>`:""}</td><td class="l">${whereLink({pkey:p.pkey,place:p.where,addr:p.addr})}</td><td>${kindPill(p.kind)}</td><td>${dur(p.min)}</td><td>${n0(p.b0)}→${n0(p.b1)}%</td><td>${p.peak==null?"–":n0(p.peak)}</td><td>${p.kmh==null?"–":n0(p.kmh)}</td><td>${p.disp==null?"–":n0(p.disp,1)}</td><td>${n0(p.kwh,1)}</td><td>${p.eff==null?"–":n0(p.eff)+"%"}</td><td>${p.rate==null?"–":rate(p.rate)}</td><td>${money(p.cost)}</td><td>${p.invoice?esc(p.invoice.number||"yes"):p.home?'<span class="sub">–</span>':'<span class="pill warn" title="No invoice matched this charge">none</span>'}</td><td>${syncPill(p.sync)}</td></tr>`;
      if(multi&&op)h+=p.parts.map(x=>`<tr class="chs" data-id="${x.id}"><td class="l" style="padding-left:30px">↳ ${x.day} ${x.hm}</td><td class="l sub">part of the charge above</td><td></td><td>${dur(x.min)}</td><td>${n0(x.b0)}→${n0(x.b1)}%</td><td>${x.peak==null?"–":n0(x.peak)}</td><td>${x.kmh==null?"–":n0(x.kmh)}</td><td>${x.disp==null?"–":n0(x.disp,1)}</td><td>${n0(x.kwh,1)}</td><td></td><td>${x.irate==null&&x.trate==null?"–":rate(x.irate!=null?x.irate:x.trate)}</td><td>${money(x.icost!=null?x.icost:x.tcost)}</td><td class="sub">${p.invoice?"same invoice":""}</td><td>${syncPill(x.sync)}</td></tr>`).join("");
      return h};
    $("#tb").innerHTML=`<table><thead><tr>${COLS.map(c=>`<th class="${c[2]?"l":""}" data-k="${c[0]}">${c[1]}${CP.sk===c[0]?(CP.dir<0?" ▾":" ▴"):""}</th>`).join("")}</tr></thead><tbody>${rs.map(row).join("")}</tbody></table>${rs.length?"":'<div class="empty">No charges here.</div>'}`;
    $$("#tb th").forEach(th=>th.onclick=()=>{const k=th.dataset.k;if(CP.sk===k)CP.dir=-CP.dir;else{CP.sk=k;CP.dir=k==="t0"||typeof P[0][k]==="number"?-1:1}draw()});
    $$("#tb tr.chr,#tb tr.chs").forEach(tr=>tr.onclick=ev=>{const tg=ev.target.closest("[data-tg]");if(tg){CP.open[tg.dataset.tg]=!CP.open[tg.dataset.tg];draw();return}if(ev.target.closest("a"))return;try{sessionStorage.setItem("chargeSet",JSON.stringify({ids:rs.slice().sort((a,b)=>a.t0-b.t0).map(p=>p.id),label:"the Charges list as shown"}))}catch(_){}location.hash="#/charge/"+tr.dataset.id});
  };
  const invoices=()=>{
    const I=B.invoices.filter(i=>inRange({day:i.date},CP.r)&&(!CP.q||((i.location||"")+" "+(i.network||"")+" "+(i.number||"")).toLowerCase().includes(CP.q.toLowerCase())));
    const tot=I.reduce((a,i)=>a+(i.total||0),0),kw=I.reduce((a,i)=>a+(i.kwh||0),0),un=I.filter(i=>!i.plug).length;
    $("#tl").innerHTML=tile(n0(I.length),"invoices")+tile(money(tot),"invoiced")+tile(n0(kw,1)+" kWh","energy invoiced")+tile(kw?money(tot/kw)+"/kWh":"–","average price")+tile(money(gstOf(tot)),"GST included")+tile(un?String(un):"none",un?"invoices with no matching charge":"unmatched invoices");
    table($("#tb"),[{k:"date",t:"Date",l:1},{k:"network",t:"Network",l:1,f:i=>esc(i.network||"")},{k:"location",t:"Location",l:1,f:i=>esc((i.location||"").split(",").slice(0,2).join(","))},{k:"number",t:"Invoice",l:1,f:i=>esc(i.number||"")},{k:"kwh",t:"kWh",f:i=>n0(i.kwh,2)},{k:"rate",t:"$/kWh",f:i=>i.rate==null?"–":rate(i.rate)},{k:"gst",t:"GST",f:i=>money(gstOf(i.total))},{k:"total",t:"Total",f:i=>money(i.total)},{k:"plug",t:"Charge",f:i=>i.plug?`<a href="#/charge/${i.plug}" onclick="event.stopPropagation()">open</a>`:'<span class="pill warn">no match</span>'},{k:"file",t:"File",f:i=>i.file?`<a href="/api/invoice/${encodeURIComponent(i.file)}" target="_blank" onclick="event.stopPropagation()" title="${esc(i.file)}">open</a>`:""}],I,i=>{if(i.plug)location.hash="#/charge/"+i.plug},"date");
  };
  const show=()=>{$$("[data-tab]").forEach(c=>c.classList.toggle("on",c.dataset.tab===CP.tab));$("#kd").style.display=$("#iv").style.display=CP.tab==="charges"?"":"none";CP.tab==="charges"?draw():invoices()};
  main.innerHTML=`<h1>Charges</h1><div class="sub">Every charge reconciled with its invoice, as in <code>./analyse_charging.py</code>. A plug-in that Tessie recorded as several sessions is one charge with its records as parts.${B0.status==="building"?" · refreshing in the background…":""}</div>
   <div id="tsb" style="margin:10px 0"></div>
   <div class="bar"><button class="chip" data-tab="charges">Charges</button><button class="chip" data-tab="invoices">Invoices (${B.invoices.length})</button><span id="rg"></span><select id="kd"><option value="all">All types</option><option value="ac">AC / home</option><option value="dc">DC fast</option><option value="supercharger">Supercharger</option></select><select id="iv"><option value="all">Any invoice state</option><option value="with">With an invoice</option><option value="without">Public, no invoice</option><option value="unsynced">Invoice cost not in Tessie</option></select><input type="search" id="q" placeholder="Search a place or invoice…" value="${esc(CP.q)}"></div><div class="tiles" id="tl"></div><div id="tb" style="overflow-x:auto"></div>`;
  $("#kd").value=CP.kind;$("#iv").value=CP.inv;rangePicker($("#rg"),CP.r,daysOf(P),show);$("#kd").onchange=e=>{CP.kind=e.target.value;show()};$("#iv").onchange=e=>{CP.inv=e.target.value;show()};$("#q").oninput=e=>{CP.q=e.target.value;show()};
  $$("[data-tab]").forEach(c=>c.onclick=()=>{CP.tab=c.dataset.tab;show()});show();
  if(B0.status==="building")bookPoll("#/charges",pageCharges,true);
  tessieSync($("#tsb"),{kind:"charges",steps:8,always:true,always_note:"Fetches new charges from Tessie, their charging curves and the invoices. It takes a few minutes.",after:()=>{bust();route()}});
}

// time-axis chart. series: [{k,name,unit,col,axis}], axes: {id:{side:"left"|"right",label,col,min,max}}. `sel` = part drawn solid (others dashed); strip = charging-state bar
function tsChart(host,segs,sel,tz,series,axes,o){
  o=o||{};const all=segs.flatMap(s=>s.t);if(all.length<2){host.innerHTML='<div class="sub">No telemetry file for this charge.</div>';return}
  const ser=series.filter(z=>segs.some(s=>(s[z.k]||[]).some(v=>v!=null)));if(!ser.length){host.innerHTML='<div class="sub">No data for this chart.</div>';return}
  const used=[...new Set(ser.map(z=>z.axis))],nr=used.filter(a=>axes[a].side==="right").length,W=720,H=o.h||260,T=24,B=o.strip?44:30,L=50,R=Math.max(14,48*nr),t0=Math.min(...all),t1=Math.max(...all),X=v=>L+(v-t0)/((t1-t0)||1)*(W-L-R);
  const fm=new Intl.DateTimeFormat("en-AU",{hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false,timeZone:tz||"Australia/Sydney"}),hm=v=>fm.format(new Date(v*1000)).replace(/^24/,"00");
  const ext={};used.forEach(a=>{const v=ser.filter(z=>z.axis===a).flatMap(z=>segs.flatMap(s=>s[z.k].filter(q=>q!=null))),lo=Math.min(...v),hi=Math.max(...v),ax=axes[a],pad=((hi-lo)||1)*.08;
    let a0=ax.min!=null?ax.min:lo-pad,a1=ax.max!=null?ax.max:hi+pad;if(ax.min!=null&&ax.min===0&&hi<=0)a1=1;ext[a]=[a0,a1]});
  const Y=(a,v)=>T+(H-T-B)*(1-(v-ext[a][0])/((ext[a][1]-ext[a][0])||1)),solid=s=>sel==null||s.part===sel,gaps=segs.slice(1).map((s,i)=>[segs[i],s]);
  let h="";
  gaps.forEach(([a,b])=>{const x0=X(a.t[a.t.length-1]),x1=X(b.t[0]);h+=`<rect x="${x0}" y="${T}" width="${Math.max(1,x1-x0)}" height="${H-T-B}" fill="var(--hover)"/>`+(x1-x0>46?`<text x="${(x0+x1)/2}" y="${T+12}" text-anchor="middle">paused ${dur((b.t[0]-a.t[a.t.length-1])/60)}</text>`:"")});
  let ri=0;const pos={};used.forEach(a=>{pos[a]=axes[a].side==="right"?W-R+6+48*(ri++):L-6});
  used.forEach((a,ai)=>{const ax=axes[a],r=ax.side==="right",span=ext[a][1]-ext[a][0];for(let g=0;g<=4;g++){const v=ext[a][0]+span*g/4;h+=`<text x="${pos[a]}" y="${Y(a,v)+4}" text-anchor="${r?"start":"end"}" style="fill:${ax.col}">${n0(v,span<12?1:0)}</text>`+(ai===0?`<line class="grid" x1="${L}" x2="${W-R}" y1="${Y(a,v)}" y2="${Y(a,v)}"/>`:"")}
    h+=`<text x="${r?pos[a]:6}" y="${T-8}" text-anchor="${r?"start":"start"}" style="fill:${ax.col};font-weight:600">${esc(ax.label)}</text>`});
  const path=(s,arr,a)=>{const P=[];s.t.forEach((t,i)=>{if(arr[i]!=null)P.push([X(t),Y(a,arr[i])])});if(!P.length)return"";
    if(!o.smooth||P.length<3)return P.map((q,i)=>(i?"L":"M")+q[0].toFixed(1)+","+q[1].toFixed(1)).join("");
    let d="M"+P[0][0].toFixed(1)+","+P[0][1].toFixed(1);for(let i=0;i<P.length-1;i++){const p0=P[i-1]||P[i],p1=P[i],p2=P[i+1],p3=P[i+2]||p2;d+=`C${(p1[0]+(p2[0]-p0[0])/6).toFixed(1)},${(p1[1]+(p2[1]-p0[1])/6).toFixed(1)} ${(p2[0]-(p3[0]-p1[0])/6).toFixed(1)},${(p2[1]-(p3[1]-p1[1])/6).toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`}return d};
  const edge=(p,q,k,a)=>{const i=p[k].map((v,j)=>[v,j]).filter(z=>z[0]!=null).pop(),j=q[k].map((v,m)=>[v,m]).find(z=>z[0]!=null);return i&&j?`M${X(p.t[i[1]]).toFixed(1)},${Y(a,i[0]).toFixed(1)}L${X(q.t[j[1]]).toFixed(1)},${Y(a,j[0]).toFixed(1)}`:""};
  ser.forEach(z=>{segs.forEach(s=>{h+=`<path d="${path(s,s[z.k],z.axis)}" fill="none" stroke="${z.col}" stroke-width="2" stroke-linejoin="round" ${solid(s)?"":'stroke-dasharray="5 4" opacity=".6"'}/>`});gaps.forEach(([a,b])=>{h+=`<path d="${edge(a,b,z.k,z.axis)}" fill="none" stroke="${z.col}" stroke-width="1.5" stroke-dasharray="2 4"/>`})});
  if(o.strip){const ST={Charging:"#16a34a",Starting:"#84cc16",Complete:"#9aa3b5",NoPower:"#d98a00",Disconnected:"#d4332b",Stopped:"#d98a00"},yy=H-B+4,runs=[];segs.forEach(s=>{let a=0;s.state.forEach((st,i)=>{if(i===s.state.length-1||s.state[i+1]!==st){runs.push([s.t[a],s.t[Math.min(i+1,s.t.length-1)],st]);a=i+1}})});
    h+=runs.map(([a,b,st])=>`<rect x="${X(a)}" y="${yy}" width="${Math.max(1,X(b)-X(a))}" height="7" fill="${ST[st]||"#c7ccd9"}"><title>${esc(st||"?")}</title></rect>`).join("")+`<text x="${L-6}" y="${yy+7}" text-anchor="end" style="font-size:10px">state</text>`}
  for(let i=0;i<=6;i++){const v=t0+(t1-t0)*i/6;h+=`<text x="${X(v)}" y="${H-4}" text-anchor="middle">${hm(v).slice(0,5)}</text>`}
  host.innerHTML=`<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">${h}<line class="cl" id="cur" y1="${T}" y2="${H-B}" style="display:none"/></svg><div class="sub" style="margin-top:4px">${ser.map(z=>`<span style="margin-right:16px"><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${z.col};margin-right:5px"></span>${esc(z.name)}${axes[z.axis].side==="right"?" (right axis)":""}</span>`).join("")}${o.note?" · "+o.note:""}</div>`;
  const svg=$("svg",host),cur=$("#cur",host),pts=[];segs.forEach(s=>s.t.forEach((t,i)=>pts.push([t,s,i])));
  svg.onmousemove=ev=>{const r=svg.getBoundingClientRect(),x=(ev.clientX-r.left)/r.width*W;if(x<L||x>W-R){svg.onmouseleave();return}
    const tv=t0+(x-L)/(W-L-R)*(t1-t0);let b=1e18,bp=null;pts.forEach(p=>{const d=Math.abs(p[0]-tv);if(d<b){b=d;bp=p}});const [t,s,i]=bp;cur.setAttribute("x1",X(t));cur.setAttribute("x2",X(t));cur.style.display="";
    showTip(ev,`<b>${hm(t)}</b>${s.state[i]?" · "+esc(s.state[i]):""}<br>`+ser.map(z=>`<span style="color:${z.col}">●</span> ${esc(z.name)}: ${s[z.k][i]==null?"–":n0(s[z.k][i],z.dec==null?1:z.dec)+" "+esc(z.unit)}`).join("<br>"))};
  svg.onmouseleave=()=>{cur.style.display="none";hideTip()};
}

// generic x/y chart with named axes and dashed estimates (power vs SoC, electrical profile)
function lineChart(host,series,axes,xlabel,xfmt){
  const S=series.filter(s=>s.x&&s.x.length>1);if(!S.length){host.innerHTML='<div class="sub">Nothing to plot.</div>';return}
  const W=720,H=260,L=52,R=54,T=26,B=34,xs=S.flatMap(s=>s.x),x0=Math.min(...xs),x1=Math.max(...xs),X=v=>L+(v-x0)/((x1-x0)||1)*(W-L-R);
  const ext={};Object.keys(axes).forEach(a=>{const v=S.filter(s=>s.axis===a).flatMap(s=>s.y.filter(z=>z!=null));if(v.length){const lo=Math.min(...v),hi=Math.max(...v);ext[a]=[a==="power"||a==="amp"||a==="soc"?Math.min(0,lo):lo-(hi-lo)*.1,hi+(hi-lo||1)*.08]}});
  const Y=(a,v)=>T+(H-T-B)*(1-(v-ext[a][0])/((ext[a][1]-ext[a][0])||1));
  let h="";const side={left:0,right:0};
  Object.keys(ext).forEach(a=>{const ax=axes[a],xr=ax.side==="right"?W-R+6:L-6;side[ax.side]++;for(let g=0;g<=4;g++){const v=ext[a][0]+(ext[a][1]-ext[a][0])*g/4;h+=`<text x="${xr}" y="${Y(a,v)+4}" text-anchor="${ax.side==="right"?"start":"end"}" style="fill:${ax.col}">${n0(v,ext[a][1]-ext[a][0]<20?1:0)}</text>`+(a===Object.keys(ext)[0]?`<line class="grid" x1="${L}" x2="${W-R}" y1="${Y(a,v)}" y2="${Y(a,v)}"/>`:"")}
    h+=`<text x="${ax.side==="right"?W-4:6}" y="${T-10}" text-anchor="${ax.side==="right"?"end":"start"}" style="fill:${ax.col};font-weight:600">${esc(ax.label)}</text>`});
  for(let g=0;g<=6;g++){const v=x0+(x1-x0)*g/6;h+=`<text x="${X(v)}" y="${H-B+16}" text-anchor="middle">${xfmt?xfmt(v):n0(v)}</text>`}
  h+=`<text x="${(L+W-R)/2}" y="${H-4}" text-anchor="middle" style="fill:var(--mut)">${esc(xlabel)}</text>`;
  S.forEach(s=>{let d="";s.x.forEach((x,i)=>{const v=s.y[i];if(v!=null)d+=(d?"L":"M")+X(x).toFixed(1)+","+Y(s.axis,v).toFixed(1)});h+=`<path d="${d}" fill="none" stroke="${s.col}" stroke-width="2" ${s.dash?'stroke-dasharray="6 4" opacity=".75"':""} stroke-linejoin="round"/>`});
  host.innerHTML=`<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">${h}<line class="cl" id="cur" y1="${T}" y2="${H-B}" style="display:none"/></svg><div class="sub" style="margin-top:4px">${[...new Map(S.map(s=>[s.name,s])).values()].map(s=>`<span style="margin-right:14px"><span style="color:${s.col}">${s.dash?"╌╌":"──"}</span> ${esc(s.name)}</span>`).join("")}</div>`;
  const svg=$("svg",host),cur=$("#cur",host);
  svg.onmousemove=ev=>{const r=svg.getBoundingClientRect(),x=(ev.clientX-r.left)/r.width*W;if(x<L||x>W-R){svg.onmouseleave();return}const xv=x0+(x-L)/(W-L-R)*(x1-x0);cur.setAttribute("x1",X(xv));cur.setAttribute("x2",X(xv));cur.style.display="";
    showTip(ev,`<b>${xfmt?xfmt(xv):n0(xv,1)}</b> ${esc(xlabel.split(" ")[0].toLowerCase()==="state"?"%":"")}<br>`+S.map(s=>{let bi=-1,bd=1e18;s.x.forEach((q,i)=>{const d=Math.abs(q-xv);if(d<bd&&s.y[i]!=null){bd=d;bi=i}});return bi<0||bd>(x1-x0)/12?"":`<span style="color:${s.col}">●</span> ${esc(s.name)}: ${n0(s.y[bi],1)} ${esc(s.unit)}`}).filter(Boolean).join("<br>"))};
  svg.onmouseleave=()=>{cur.style.display="none";hideTip()};
}

// the lines under "Charger power and battery": what was recorded, the 20 → 80 % estimate and what it rests on, what it cost - one idea per line
function winBlock(w,lo=20,hi=80){
  if(!w)return "";
  const head=`<b>${lo}% → ${hi}%</b> `;
  if(w.measured)return `<div class="cs-r">${head}${n0(w.minutes,1)} min <span class="pill ok">measured</span></div>`;
  if(w.minutes==null)return `<div class="cs-r">${head}<span class="sub">${esc(w.text||"")}</span></div>`;
  const src=(w.sources||[]).map(esc).join("; ");
  const why=[w.covered?`This session covered ${esc(w.covered)}.`:"",w.n_hist!=null?`The other ${w.n_hist} of ${w.n_total} percent steps come from `+(src||"earlier sessions")+".":"",
    w.factor!=null?`Scaled ×${w.factor.toFixed(2)} to this session's own pace.`:"Not scaled: too few steps in common with that history to compare this session's pace.",
    w.thin!=null?`Indicative only - this session covered just ${w.thin} of the ${w.n_total} steps.`:"",w.held?"Steps no session reached reuse the nearest one.":""].filter(Boolean);
  return `<div class="cs-r">${head}about ${n0(w.minutes)} min <span class="pill warn">estimate</span></div><div class="cs-w">${why.join(" ")}</div>`;
}
function chartSub(p,part,cur){
  const xs=(part?[part]:p.parts).filter(x=>x.tel),n=xs.reduce((a,x)=>a+(+x.tel.samples||0),0),mn=xs.reduce((a,x)=>a+(x.tel.min||0),0),pk=Math.max(0,...xs.map(x=>x.tel.peak||0)),av=xs.length&&mn?xs.reduce((a,x)=>a+(x.tel.avg||0)*(x.tel.min||0),0)/mn:null;
  const cost=part?(part.icost!=null?part.icost:part.tcost):p.cost,rate=part?(part.irate!=null?part.irate:part.trate):p.rate,kw=part?(part.disp||part.kwh):(p.disp||p.kwh);
  const rec=n?`<div class="cs-r">${n} samples over ${n0(mn,1)} min · peak <b>${n0(pk,1)} kW</b>${av?" · average <b>"+n0(av,1)+" kW</b>":""}</div>`:"";
  const win=cur?(cur.winfo?winBlock(cur.winfo):cur.window?`<div class="cs-r">${esc(cur.window)}</div>`:""):"";
  const paid=kw&&cost!=null?`<div class="cs-r"><b>${n0(kw,2)} kWh</b> for <b>$${n0(cost,2)}</b> AUD${rate?" · $"+n0(rate,2)+"/kWh":""}</div>`:"";
  return `<div class="cs">${rec}${win}${paid}</div>`;
}
// forward and back through a set of charges: the charges of one park, or the Charges list as filtered (kept by the page that opened this charge, dropped when the user leaves the charge pages)
function chargeSet(id,pid){let s=null;try{s=JSON.parse(sessionStorage.getItem("chargeSet")||"null")}catch(e){}if(!s||!s.ids||s.ids.length<2)return null;
  const i=s.ids.findIndex(x=>String(x)===String(id)||String(x)===String(pid));return i<0?null:{ids:s.ids,i,label:s.label}}
const chargeNavHtml=c=>{if(!c)return "";const go=j=>`onclick="location.hash='#/charge/${encodeURIComponent(c.ids[j])}'"`;
  return `<div class="bar cnav"><button class="chip" ${c.i>0?go(c.i-1):"disabled"} title="Earlier charge">‹</button><span class="sub">Charge ${c.i+1} of ${c.ids.length} · ${esc(c.label)}</span><button class="chip" ${c.i<c.ids.length-1?go(c.i+1):"disabled"} title="Later charge">›</button></div>`};
// home (Evnex) charges: how much of the car's power was solar and how much was bought from the grid.
// Same split the cost model uses (lib/pv_history_db.py): in each 15-minute meter reading, the share of the house's use that was met by its own solar (self-consumed / consumption),
// applied to the car's power. The dashed line is the solar left over after the house in that reading, from the car's own energy and the meter. An estimate, labelled as one.
// Evnex does not keep which mode a session ran in (its status call reports only the current one), so this is read from the car's recorded charger current:
// a current that never moves is Override (or a fixed amp setting); a current that follows the sun is solar excess control, where the charger's own clamp sets the amps.
function modeLine(segs){
  const A=segs.flatMap(g=>g.t.map((t,i)=>g.state&&g.state[i]==="Charging"&&g.amps&&g.amps[i]!=null&&g.amps[i]>=5?Math.round(g.amps[i]):null)).filter(v=>v!=null);
  if(A.length<10)return"";const lv=[...new Set(A)].sort((a,b)=>a-b),top=lv[lv.length-1];
  return lv.length<=2?`Charger current held at ${top} A the whole time, which looks like Override (charge regardless of solar) or a fixed amp setting.`
    :`Charger current moved between ${lv[0]} and ${top} A, which looks like solar excess control (the charger's clamp sets the amps from the spare solar).`}
function solarCard(D,p,part){
  const S=D.sources;if(!S||!S.buckets||!S.buckets.length)return"";
  const segs=(D.segments||[]).filter(g=>(!part||g.part===part.id)&&g.t&&g.t.length>1);if(!segs.length)return"";
  const Bk=S.buckets.map(b=>Object.assign({},b,{car:0,w:b.w||900})),bi=t=>Bk.find(b=>t>=b.t&&t<b.t+b.w);
  let unk=0;
  segs.forEach(g=>{for(let i=0;i<g.t.length-1;i++){const dt=g.t[i+1]-g.t[i],a=g.kw[i],c=g.kw[i+1];if(dt<=0||dt>900||a==null||c==null)continue;
    const e=(a+c)/2*dt/3600,b=bi(g.t[i]);if(b)b.car+=e;if(!b||b.f==null)unk+=e}});
  let sol=0,grid=0;
  Bk.forEach(b=>{b.fs=b.f;if(b.car>0&&b.f!=null){b.so=b.car*b.f;b.gr=b.car-b.so;sol+=b.so;grid+=b.gr}});
  const known=sol+grid;if(!(known+unk>0))return"";
  const pc=known>0?100*sol/known:null;
  const all=segs.flatMap(g=>g.t),t0=Math.min(...all),t1=Math.max(...all),W=720,H=210,T=14,Bt=26,L=50,R=14,
    ymax=Math.max(1,...segs.flatMap(g=>g.kw.filter(v=>v!=null)))*1.1,X=t=>L+(t-t0)/((t1-t0)||1)*(W-L-R),Y=v=>T+(H-T-Bt)*(1-Math.min(v,ymax)/ymax),y0=Y(0);
  const fm=new Intl.DateTimeFormat("en-AU",{hour:"2-digit",minute:"2-digit",hour12:false,timeZone:p.tz||"Australia/Sydney"}),hm=t=>fm.format(new Date(t*1000)).replace(/^24/,"00");
  const SOL="#eab308",GRD="#2563eb",SPARE="#a16207";let h="";
  for(let g=0;g<=4;g++){const v=ymax*g/4;h+=`<line class="grid" x1="${L}" x2="${W-R}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${L-6}" y="${Y(v)+4}" text-anchor="end">${n0(v,ymax<12?1:0)}</text>`}
  h+=`<text x="6" y="${T-3}" style="font-weight:600">Power (kW)</text>`;
  segs.forEach(g=>{const P=g.t.map((t,i)=>[t,g.kw[i]]).filter(z=>z[1]!=null);if(P.length<2)return;
    const top=P.map(z=>X(z[0]).toFixed(1)+","+Y(z[1]).toFixed(1)).join(" "),x1=X(P[P.length-1][0]).toFixed(1),x0=X(P[0][0]).toFixed(1);
    h+=`<polygon points="${x0},${y0} ${top} ${x1},${y0}" fill="${GRD}" opacity=".85"/>`;
    h+=`<polygon points="${x0},${y0} ${P.map(z=>{const b=bi(z[0]),f=b&&b.fs!=null?b.fs:0;return X(z[0]).toFixed(1)+","+Y(z[1]*f).toFixed(1)}).join(" ")} ${x1},${y0}" fill="${SOL}"/>`});
  const sp=Bk.filter(b=>b.t+b.w>t0&&b.t<t1&&b.prod!=null&&b.cons!=null).map(b=>{const v=Math.max(0,b.prod-(b.cons-b.car))*3600/b.w,xa=X(Math.max(b.t,t0)),xb=X(Math.min(b.t+b.w,t1));return `M${xa.toFixed(1)},${Y(v).toFixed(1)}L${xb.toFixed(1)},${Y(v).toFixed(1)}`}).join("");
  h+=`<path d="${sp}" fill="none" stroke="${SPARE}" stroke-width="1.6" stroke-dasharray="4 3"/>`;
  Bk.forEach(b=>{const a=Math.max(b.t,t0),z=Math.min(b.t+b.w,t1);if(z<=a)return;
    h+=`<rect x="${X(a)}" y="${T}" width="${X(z)-X(a)}" height="${H-T-Bt}" fill="transparent"><title>${hm(b.t)}–${hm(b.t+b.w)} · car ${n0(b.car,2)} kWh${b.fs!=null?": "+n0(b.so,2)+" solar, "+n0(b.gr,2)+" grid":""} · house with the car used ${n0(b.cons,2)} kWh · solar produced ${n0(b.prod,2)} kWh · bought from the grid ${n0(b.buy,2)} kWh</title></rect>`});
  for(let i=0;i<=6;i++){const t=t0+(t1-t0)*i/6;h+=`<text x="${X(t)}" y="${H-6}" text-anchor="middle">${hm(t)}</text>`}
  const c=S.cli||{},prov=(S.buckets[0]&&S.buckets[0].provider)||c.pv_provider_display_name||"the solar meter";
  const lines=[modeLine(segs),`An estimate: ${esc(String(prov))} gives the share of the house's use that was solar in each 15 minutes, and that share is applied to the car's power, so the split changes in steps. This is the split the charge cost uses. The dashed line is the solar left after the house, which the car could have used.`,
    unk>0.005?`${n0(unk,2)} kWh fell in minutes with no meter reading and is left out of the split.`:"",
    c.solar_pct!=null?`Evnex reports ${n0(c.solar_pct)}% solar for this session.`:"",
    c.base_cost_incl_gst!=null&&c.pv_adjusted_cost_incl_gst!=null?`Evnex's cost with solar: ${money(c.pv_adjusted_cost_incl_gst)} against ${money(c.base_cost_incl_gst)} from the grid alone.`:""].filter(Boolean);
  const dot=(c,t)=>`<span style="margin-right:16px"><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${c};margin-right:5px"></span>${t}</span>`;
  return `<div class="card"><h2 style="margin-top:0">Where the energy came from</h2>
   <div class="tiles tiles2">${tile(n0(sol,2)+" kWh","from solar"+(pc!=null?" · "+n0(pc)+"%":""))}${tile(n0(grid,2)+" kWh","from the grid"+(pc!=null?" · "+n0(100-pc)+"%":""))}</div>
   <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block;margin-top:8px">${h}</svg>
   <div class="sub" style="margin-top:4px">${dot(GRD,"Charger power from the grid")}${dot(SOL,"Charger power from solar")}${dot(SPARE,"Solar left after the house")}</div>
   <div class="cs" style="margin-top:6px">${lines.map(x=>`<div class="cs-r">${x}</div>`).join("")}</div></div>`}
async function pageCharge(id,pid){
  loading();const [B0,D]=await Promise.all([bookWithRaw(),fetch("/api/chargebook/"+encodeURIComponent(id)+(pid?"?part="+encodeURIComponent(pid):"")).then(r=>r.ok?r.json():null).catch(()=>null)]);
  if(!B0.book){bookWait(B0,"Charge",()=>pageCharge(id,pid));return}
  if(!D||!D.plug){return pageChargeBasic(id)}
  const p=D.plug,part=D.part&&p.parts.find(x=>x.id===D.part),v=part||p,multi=p.parts.length>1,raw=B0.book.plugs.find(z=>z.id===p.id)||p;
  const where=raw.where||p.place,end=addMin(v.hm,part?part.min:p.span),inv=p.invoice,cur=D.curves&&!D.curves.error?D.curves:null;
  const eff=v.disp&&v.kwh?100*v.kwh/v.disp:null,costv=part?(part.icost!=null?part.icost:part.tcost):p.cost,ratev=part?(part.irate!=null?part.irate:part.trate):p.rate,minv=part?part.min:p.min;
  const links=multi?p.parts.map((x,i)=>`<a class="chip ${part&&part.id===x.id?"on":""}" href="#/charge/${p.id}/${x.id}" style="text-decoration:none">Part ${i+1} · ${x.hm}</a>`).join(" "):"";
  const amps=D.segments[0]&&D.segments[0].amps_src==="pack";
  main.innerHTML=(part?crumbs("#/charge/"+p.id,"the whole charge ("+p.parts.length+" parts)"):crumbs("#/charges","Charges"))+chargeNavHtml(chargeSet(id,p.id))+`<h1>${whereLink({pkey:raw.pkey,place:where,addr:p.addr})}${part?` <span class="sub" style="font-size:15px">· part ${p.parts.indexOf(part)+1} of ${p.parts.length}</span>`:""}</h1>
   <div class="sub">${dayName(v.day)} · ${v.hm} – ${end} · ${dur(part?part.min:p.span)}${multi&&!part?" plugged in":""} · ${esc(p.network||(p.kind==="supercharger"?"Supercharger":p.kind==="dc"?"DC fast charger":"AC charging"))} · ${esc(String(p.status||"").replace(/[^\x20-\x7e]/g,"").trim())}${raw.pkey?' · <a href="#/place/'+encodeURIComponent(raw.pkey)+'">what is around here</a>':""}</div>
   ${multi?`<div class="bar"><a class="chip ${part?"":"on"}" href="#/charge/${p.id}" style="text-decoration:none">Whole charge</a>${links}<span class="sub">Tessie recorded this plug-in as ${p.parts.length} sessions; it is one charge with one invoice.</span></div>`:""}
   ${await alertsHere(p)}
   <div class="chgrid"><div class="chl">
     <div class="card"><h2 style="margin-top:0">Charger power and battery</h2><div class="sub" style="margin-bottom:6px">${chartSub(p,part,cur)}</div><div id="tc"></div></div>
     ${solarCard(D,p,part)}
     <div class="card"><h2 style="margin-top:0">Pack temperature, current and voltage</h2><div id="tv"></div></div>
     <div class="card"><h2 style="margin-top:0">Power against state of charge</h2>${cur?`<div class="sub" style="margin-bottom:6px"><div class="cs">${[cur.hardware&&cur.tier?esc(cur.hardware)+" · "+esc(cur.tier)+(cur.cap?" · "+n0(cur.cap)+" kW cap":"")+(cur.stalls?" · "+cur.stalls+" stalls":""):"",esc(cur.caption||"")].filter(Boolean).map(x=>`<div class="cs-r">${x}</div>`).join("")}</div></div>`:""}<div id="cc"></div></div>
     <div class="card"><h2 style="margin-top:0">Pack voltage and current, with history</h2><div id="pc"></div></div>
     <div class="card"><h2 style="margin-top:0">Where</h2><div class="map" id="mp"></div><div class="sub" style="margin-top:8px">${esc(p.addr||"")}</div></div>
   </div><div class="chr2">
     <div class="tiles tiles2">${tile("+"+n0(v.kwh,1)+" kWh","into the battery")}${tile(n0(v.b0)+"% → "+n0(v.b1)+"%","battery")}${tile(money(costv),(ratev!=null?money(ratev)+"/kWh · ":"")+(inv&&!part?"invoice total":part&&v.icost!=null?"invoice share":"Tessie cost"))}${tile(v.disp!=null?n0(v.disp,1)+" kWh":"–","from the charger"+(inv&&!part?" (invoice)":""))}${tile(eff?n0(eff,1)+"%":"–","efficiency")}${tile(v.peak?n0(v.peak)+" kW":"–","peak"+(minv&&v.kwh?" · avg "+n0(v.kwh*60/minv)+" kW":""))}${tile(v.kmh?n0(v.kmh)+" km/h":"–","range recovered per hour")}${tile(syncPillText(part?part.sync:p.sync),"invoice cost in Tessie")}</div>
     ${multi&&!part?`<div class="card"><h2 style="margin-top:0">The ${p.parts.length} Tessie records</h2>${partsTable(p)}</div>`:""}
     <div class="card"><h2 style="margin-top:0">Invoice and Tessie</h2>${invoiceCard(p,part)}</div>
     <div class="card"><h2 style="margin-top:0">Where the energy went</h2>${energyCard(p,part)}</div>
     <div class="card"><h2 style="margin-top:0">Telemetry</h2>${telCard(p,part)}</div>
   </div></div>`;
  wireAlerts(main);
  miniMap($("#mp"),raw.raw?raw.raw.lat:(+p.lat||null),raw.raw?raw.raw.lon:(+p.lon||null),"#16a34a");
  const sel=part?part.id:null;
  tsChart($("#tc"),D.segments,sel,p.tz,[{k:"kw",name:"Charger power",unit:"kW",col:"#2563eb",axis:"p",dec:0},{k:"pct",name:"Battery level",unit:"%",col:"#f59e0b",axis:"b",dec:0}],{p:{side:"left",label:"Charger power (kW)",col:"#2563eb",min:0},b:{side:"right",label:"Battery (%)",col:"#f59e0b"}},{h:300,strip:true,smooth:true,note:"times on the charger's clock; dashed = "+(sel==null?"the pause between records (the plug stayed in)":"the rest of the same charge")});
  tsChart($("#tv"),D.segments,sel,p.tz,[{k:"tmax",name:"Pack temperature (max)",unit:"°C",col:"#d4332b",axis:"t",dec:1},{k:"pack_v",name:"Pack voltage",unit:"V",col:"#2563eb",axis:"v",dec:0},{k:"amps",name:amps?"Current (pack)":"Current",unit:"A",col:"#8b5cf6",axis:"a",dec:0}],{t:{side:"left",label:"Temp (°C)",col:"#d4332b"},v:{side:"right",label:"Volts",col:"#2563eb"},a:{side:"right",label:"Amps",col:"#8b5cf6",min:0}},{h:280,smooth:true,note:amps?"a DC charger reports 0 A on the AC side, so the pack current is shown":""});
  const AX={power:{label:"Charger power (kW)",col:"#2563eb",side:"left"},temp:{label:"Pack temperature (°C)",col:"#1baf7a",side:"right"}},COL={"Charger power":"#2563eb","Rest of this charge":"#7aa7ee","Estimated from other sessions":"#2563eb","Pack temperature":"#1baf7a","Pack voltage":"#2563eb","Pack current":"#e8792b"};
  const mk=s=>Object.assign({},s,{col:COL[s.name]||"#6a7385"});
  if(cur){lineChart($("#cc"),cur.curve.map(mk),AX,"State of charge (%)",q=>n0(q)+"%");
    lineChart($("#pc"),cur.profile.map(mk),{volt:{label:"Pack voltage (V)",col:"#2563eb",side:"left"},amp:{label:"Pack current (A)",col:"#e8792b",side:"right"}},"Elapsed time (min)",q=>n0(q)+" min")}
  else{$("#cc").innerHTML=$("#pc").innerHTML='<div class="sub">No telemetry file for this charge.</div>'}
}
const syncPillText=s=>s==="synced"?"Yes":s==="pending"?"No":s==="partial"?"Partly":"n/a";
async function alertsHere(p){const out=[];for(const id of [p.id,...p.parts.map(x=>x.id)]){(await alertsFor("charge",id)).forEach(e=>out.push(e))}return alertsCard([...new Map(out.map(e=>[e.id,e])).values()].sort((a,b)=>b.t0-a.t0),"charge")}
function partsTable(p){
  const rows=p.parts.map((x,i)=>{const gap=i?`<tr><td colspan="7" class="sub" style="text-align:center">paused ${dur((x.t0-p.parts[i-1].t1)/60)} - the plug stayed in</td></tr>`:"";
    return gap+`<tr class="chs" data-go="${x.id}" style="cursor:pointer"><td class="l">Part ${i+1} · ${x.hm}</td><td>${dur(x.min)}</td><td>${n0(x.b0)}→${n0(x.b1)}%</td><td>${x.peak==null?"–":n0(x.peak)}</td><td>${n0(x.kwh,2)}</td><td>${money(x.icost!=null?x.icost:x.tcost)}</td><td>${syncPill(x.sync)}</td></tr>`}).join("");
  setTimeout(()=>$$("[data-go]").forEach(tr=>tr.onclick=()=>location.hash="#/charge/"+tr.dataset.go),0);
  return `<table><thead><tr><th class="l">Record</th><th>Time</th><th>Battery</th><th>Peak kW</th><th>kWh</th><th>Cost</th><th>Tessie</th></tr></thead><tbody>${rows}<tr><td class="l"><b>Whole charge</b></td><td><b>${dur(p.min)}</b></td><td><b>${n0(p.b0)}→${n0(p.b1)}%</b></td><td><b>${p.peak==null?"–":n0(p.peak)}</b></td><td><b>${n0(p.kwh,2)}</b></td><td><b>${money(p.cost)}</b></td><td>${syncPill(p.sync)}</td></tr></tbody></table>`;
}
function invoiceCard(p,part){
  const inv=p.invoice,kv=(a,b)=>b==null||b===""?"":`<tr><td>${a}</td><td>${b}</td></tr>`;
  let h="";
  if(inv){h+=`<table class="kv"><tbody>${kv("Invoice",esc(inv.number||""))}${kv("Date",esc(inv.date||""))}${kv("Network",esc(inv.network||""))}${kv("Location",esc(inv.location||""))}${kv("Energy invoiced",inv.kwh!=null?n0(inv.kwh,2)+" kWh":"")}${kv("Rate",inv.rate!=null?rate(inv.rate)+"/kWh"+(inv.gross_rate!=null&&inv.gross_rate!==inv.rate?" (gross "+rate(inv.gross_rate)+")":""):"")}${kv("GST included",money(gstOf(inv.total)))}${kv("Total",`<b>${money(inv.total)}</b>`)}${inv.file?kv("File",`<a href="/api/invoice/${encodeURIComponent(inv.file)}" target="_blank">${esc(inv.file)}</a>`):""}</tbody></table>`}
  else h+=`<div class="sub">${p.home?"Home charging is not invoiced; the cost here is the Tessie estimate. The real home cost comes from your power bills (see ./analyse_charging.py --reconcile-invoices).":p.invoice_only?"":"No invoice has been matched to this charge. Put the PDF in the invoices folder and run ./analyse_charging.py --update-master."}</div>`;
  if(!p.home){h+=`<h2>Tessie</h2><table><thead><tr><th class="l">Record</th><th>Tessie id</th><th>Tessie's cost at last sync</th><th>Sent by us</th><th>${inv?"Invoice share":"Our figure"}</th><th>Status</th></tr></thead><tbody>${p.parts.map((x,i)=>`<tr${part&&part.id!==x.id?' style="opacity:.55"':""}><td class="l">${multi1(p)?"Part "+(i+1)+" · ":""}${x.hm}</td><td>${x.tessie_id||"–"}</td><td>${x.tessie_cost_now!=null?money(x.tessie_cost_now):"–"}</td><td>${x.ledger!=null?money(x.ledger):"–"}</td><td>${x.icost!=null?money(x.icost):"–"}</td><td>${syncPill(x.sync)} <span class="sub">${esc(x.sync_why||"")}</span></td></tr>`).join("")}</tbody></table>
    <div class="sub" style="margin-top:8px">${p.sync==="synced"?"Tessie holds the invoice cost for this charge.":p.sync==="na"?"Nothing to send yet.":"Send the invoice cost to Tessie with ./analyse_charging.py --sync-tessie-costs --apply. This app only reads."}</div>`}
  else h+=`<div class="sub" style="margin-top:8px">Tessie cost ${money(p.tcost)}${p.trate!=null?"":""}.</div>`;
  return h;
}
const multi1=p=>p.parts.length>1;
function energyCard(p,part){
  const v=part||p,kv=(a,b,c)=>b==null||b===""?"":`<tr><td>${a}</td><td>${b}</td><td class="sub">${c||""}</td></tr>`;
  const loss=v.disp!=null&&v.kwh!=null?v.disp-v.kwh:null;
  return `<table class="kv"><tbody>${kv("From the charger",v.disp!=null?n0(v.disp,2)+" kWh":"",p.invoice&&!part?"what the invoice bills":"Tessie's energy used")}${kv("Lost in the cable and charger",!part&&p.parts.length===1&&p.parts[0].cable_loss!=null?n0(p.parts[0].cable_loss,2)+" kWh":part&&part.cable_loss!=null?n0(part.cable_loss,2)+" kWh":"","dispenser to car")}${kv("Lost in the car",(part||p.parts.length===1)&&(part||p.parts[0]).car_loss!=null?n0((part||p.parts[0]).car_loss,2)+" kWh":"","car to battery")}${kv("Total loss",loss!=null?n0(loss,2)+" kWh":"","")}${kv("Into the battery",n0(v.kwh,2)+" kWh","")}${kv("Efficiency",v.disp&&v.kwh?n0(100*v.kwh/v.disp,1)+"%":"","dispenser to battery")}${kv("Range added",v.range!=null?n0(v.range,1)+" km":"","")}
   ${!part&&p.expected_rate!=null?kv("Expected tariff",rate(p.expected_rate)+"/kWh"+(p.schedule?" · "+esc(p.schedule):""),"from the charger's published rates at that time"):""}${!part&&p.theory!=null&&p.theory>0?kv("Theoretical cost",money(p.theory),"expected rate × kWh"+(p.icost!=null?" · invoice "+(p.icost-p.theory>=0?"+":"-")+money(Math.abs(p.icost-p.theory))+" vs theory":"")):""}${!part&&p.gst?kv("GST included (calculated)",money(p.gst),""):""}</tbody></table>`;
}
function telCard(p,part){
  const parts=part?[part]:p.parts,kv=(a,b)=>b==null||b===""?"":`<tr><td>${a}</td><td>${b}</td></tr>`;
  return parts.map(x=>{const t=x.tel,head=multi1(p)?`<div class="sub" style="margin:8px 0 2px"><b>Part ${p.parts.indexOf(x)+1} · ${x.hm}</b></div>`:"";
    return head+(t?`<table class="kv"><tbody>${kv("Samples",n0(t.samples)+" over "+dur(t.min))}${kv("State of charge",n0(t.soc0)+"% → "+n0(t.soc1)+"%")}${kv("Power",(t.peak==null?"–":"peak "+n0(t.peak,1))+" kW · "+(t.avg==null?"–":"avg "+n0(t.avg,1))+" kW")}${kv("Pack temperature",t.t_start==null?"":n0(t.t_start,1)+" → "+n0(t.t_end,1)+" °C (max "+n0(t.t_max,1)+")")}${kv("Battery heater",esc(t.heater||""))}${kv("Outside / inside",t.outside==null?"":n0(t.outside,1)+" / "+(t.inside==null?"–":n0(t.inside,1))+" °C")}${kv("Energy at the charger",t.int_disp==null?"":n0(t.int_disp,2)+" kWh (integrated)")}${kv("Energy into the pack",t.int_pack==null?"":n0(t.int_pack,2)+" kWh (integrated)")}${kv("Car's own energy gauge",t.d_energy==null?"":n0(t.d_energy,2)+" kWh")}${kv("File",esc(t.file||""))}</tbody></table>`:'<div class="sub">No telemetry file for this record.</div>')}).join("");
}

// the plain page, kept for a charge that is not in the book (e.g. the book could not be built)
async function pageChargeBasic(id){
  loading();const d=await api("charge/"+id),c=d.charge;
  main.innerHTML=crumbs("#/charges","Charges")+chargeNavHtml(chargeSet(id))+`<h1>${whereLink(c)}</h1><div class="sub">${dayName(c.day)} · ${c.hm} – ${addMin(c.hm,c.min)} · ${dur(c.min)} · ${c.kind==="supercharger"?"Supercharger":c.kind==="dc"?"DC fast charger":"AC charging"}${c.pkey?' · <a href="#/place/'+encodeURIComponent(c.pkey)+'">what is around here</a>':""}</div>
   <div class="tiles">${tile("+"+n0(c.kwh,1)+" kWh","energy added")}${tile(n0(c.b0)+"% → "+n0(c.b1)+"%","battery")}${tile(money(c.cost),c.rate!=null?rate(c.rate)+"/kWh":"cost")}${tile(c.used!=null?n0(c.used,1)+" kWh":"–","energy drawn")}${tile(c.used&&c.kwh?n0(100*c.kwh/c.used)+"%":"–","charging efficiency")}${tile(c.min?n0(c.kwh*60/c.min,1)+" kW":"–","average power")}</div>
   ${alertsCard(await alertsFor("charge",id),"charge")}<div class="card"><h2 style="margin-top:0">Charge curve</h2><div id="ch"></div></div><div class="card" style="margin-top:14px"><h2 style="margin-top:0">Where</h2><div class="map" id="mp"></div><div class="sub" style="margin-top:8px">${esc(c.addr||"")}</div></div>`;
  miniMap($("#mp"),c.lat,c.lon,"#16a34a");wireAlerts(main);
  const s=d.series;if(!s){$("#ch").innerHTML='<div class="sub">No telemetry file for this charge.</div>';return}
  const mm=v=>{const h=Math.floor(v/3600),m=Math.floor(v%3600/60);return h?h+":"+String(m).padStart(2,"0"):m+" min"};
  stack($("#ch"),s.t,[{name:"Charger power",unit:"kW",vals:s.kw,col:"#16a34a",min:0},{name:"Battery",unit:"%",vals:s.pct,col:"#3b82f6",dec:0},{name:"Energy added",unit:"kWh",vals:s.kwh,col:"#d98a00",min:0},{name:"Current",unit:"A",vals:s.amps,col:"#8b5cf6",min:0,dec:0},{name:"Pack temp (max)",unit:"°C",vals:s.mod,col:"#d4332b"}],mm);
}

// ---------- Parked (idle periods) with the saved Sentry footage
const hav=(a,b,c,d)=>{const r=Math.PI/180,p=Math.sin((c-a)*r/2)**2+Math.cos(a*r)*Math.cos(c*r)*Math.sin((d-b)*r/2)**2;return 12742000*Math.asin(Math.sqrt(p))};
const whereLink=r=>r.pkey?`<a href="#/place/${encodeURIComponent(r.pkey)}" onclick="event.stopPropagation()" title="Open this place">${esc(place(r))}</a>`:esc(place(r));
const sentryOf=(x,evs)=>{const s=x.t0,e=s+(x.min||0)*60;return evs.filter(v=>{const t=v.t;return t>=s-120&&t<=e+120&&(x.lat==null||v.lat==null||hav(x.lat,x.lon,v.lat,v.lon)<5000)})};
const getSentry=async()=>{try{return await (await fetch("/api/sentry")).json()}catch(e){return{connected:false,events:[]}}};
const REASON={sentry_aware_object_detection:"Object detected",sentry_locked_handle_pulled:"Handle pulled",sentry_accelerometer:"Bumped",user_interaction_dashcam_icon_tapped:"Saved by tap",user_interaction_dashcam_panel_save:"Saved from the screen",user_interaction_honk:"Horn pressed"};
const reason=r=>REASON[r]||(r||"Sentry event").replace(/^(sentry|user_interaction)_/,"").replace(/_/g," ");
const CAMN={front:"Front",back:"Back",left_repeater:"Left repeater",right_repeater:"Right repeater",left_pillar:"Left pillar",right_pillar:"Right pillar"};
const hms=ts=>ts.slice(11,19);
let IP={r:{p:"all"},q:"",min:5,sen:"all",tab:"periods"};
async function pageParked(){
  loading();const [all0,sn]=await Promise.all([api("activity"),getSentry()]),evs=sn.events,all=all0.filter(x=>x.k==="idle").map(x=>Object.assign({},x,{ev:sentryOf(x,evs)}));
  all.forEach(x=>x.nev=x.ev.length);const matched=new Set(all.flatMap(x=>x.ev.map(v=>v.id)));
  const idleOf=v=>all.find(x=>x.ev.some(e=>e.id===v.id));
  const drawEvents=()=>{const q=IP.q.toLowerCase(),rs=evs.filter(v=>inRange({day:v.ts.slice(0,10)},IP.r)&&(!q||(v.city+" "+v.street+" "+reason(v.reason)).toLowerCase().includes(q))),byDay={};
    rs.forEach(v=>(byDay[v.ts.slice(0,10)]=byDay[v.ts.slice(0,10)]||[]).push(v));
    $("#tl").innerHTML=tile(n0(rs.length),"saved Sentry events")+tile(n0(rs.filter(v=>idleOf(v)).length),"inside a recorded parked period")+tile(sn.connected?"Connected":"Not connected","TeslaCam drives");
    $("#tb").innerHTML=(sn.connected?'<div id="spl"></div>':'<div class="empty">No TeslaCam drive is connected.</div>')+Object.keys(byDay).sort().reverse().slice(0,60).map(d=>`<div class="day">${dayName(d)}<small>${byDay[d].length} event${byDay[d].length>1?"s":""}</small></div><div class="evs">${byDay[d].map(v=>`<button class="evc" data-id="${v.id}"><img loading="lazy" src="/footage/${v.role||"A"}/SentryClips/${v.id}/thumb.png" onerror="this.style.visibility='hidden'"><b>${hms(v.ts)}</b><span>${esc(reason(v.reason))}</span><span>${esc(v.city||"")}</span>${idleOf(v)?`<a href="#/parked/${idleOf(v).id}" onclick="event.stopPropagation()">parked period</a>`:""}</button>`).join("")}</div>`).join("");
    $$(".evc").forEach(b=>b.onclick=()=>{$$(".evc").forEach(x=>x.classList.toggle("on",x===b));sentryPlayer($("#spl"),evs.find(v=>v.id===b.dataset.id));$("#spl").scrollIntoView({block:"nearest"})})};
  const draw=()=>{if(IP.tab==="events")return drawEvents();const q=IP.q.toLowerCase(),rs=all.filter(x=>inRange(x,IP.r)&&x.min>=IP.min&&(IP.sen==="all"||x.nev>0)&&(!q||place(x).toLowerCase().includes(q))),kwh=rs.reduce((a,x)=>a+(x.kwh||0),0),mn=rs.reduce((a,x)=>a+x.min,0);
    $("#tl").innerHTML=tile(n0(rs.length),"parked periods")+tile(dur(mn),"time parked")+tile(n0(kwh,1)+" kWh","energy used while parked")+tile(mn?n0(kwh*1000/(mn/60))+" Wh/h":"–","average drain")+tile(n0(rs.reduce((a,x)=>a+x.nev,0)),"saved Sentry events here")+tile(sn.connected?n0(evs.length)+" ("+matched.size+" placed)":"No drive connected","Sentry events on connected drives");
    table($("#tb"),[{k:"t0",t:"Date",l:1,f:r=>`${r.day} ${r.hm} <span class="sub">${esc(r.tz||"")}</span>`},{k:"place",t:"Where",l:1,f:whereLink},{k:"min",t:"Parked",f:r=>dur(r.min)},{k:"b0",t:"Battery",f:r=>`${n0(r.b0)}→${n0(r.b1)}%`},{k:"kwh",t:"kWh",f:r=>n0(r.kwh,2)},{k:"dr",t:"Wh/h",f:r=>r.min?n0(r.kwh*1000/(r.min/60)):"–"},{k:"nev",t:"Sentry clips",f:r=>r.nev?`<b>${r.nev}</b> ▶`:"–"},{k:"sentry",t:"In Sentry",f:r=>r.sentry>0.01?Math.round(r.sentry*100)+"%":"–"},{k:"climate",t:"Climate",f:r=>r.climate>0.01?Math.round(r.climate*100)+"%":"–"}],rs.map(r=>Object.assign({dr:r.min?r.kwh/(r.min/60):0},r)),r=>location.hash="#/parked/"+r.id,"t0")};
  main.innerHTML=`<h1>Parked</h1><div class="sub">Time spent parked: battery drain, Sentry and climate use. Open a parked period, or the All Sentry events tab, to watch saved Sentry footage.</div><div class="bar"><button class="chip" data-tb="periods">Parked periods</button><button class="chip" data-tb="events">All Sentry events</button><span id="rg"></span><select id="mn"><option value="0">Any length</option><option value="5">5 min or more</option><option value="60">1 h or more</option><option value="480">8 h or more</option></select><select id="sn"><option value="all">All parked periods</option><option value="y">Only with Sentry footage</option></select><input type="search" id="q" placeholder="Search a place…" value="${esc(IP.q)}"></div><div class="tiles" id="tl"></div><div id="tb"></div>`;
  const tabs=()=>{$$("[data-tb]").forEach(c=>c.classList.toggle("on",c.dataset.tb===IP.tab));["mn","sn"].forEach(i=>$("#"+i).style.display=IP.tab==="periods"?"":"none")};
  $$("[data-tb]").forEach(c=>c.onclick=()=>{IP.tab=c.dataset.tb;tabs();draw()});
  $("#mn").value=String(IP.min);$("#sn").value=IP.sen;rangePicker($("#rg"),IP.r,daysOf(all.concat(evs.map(v=>({day:v.ts.slice(0,10)})))),draw);$("#mn").onchange=e=>{IP.min=+e.target.value;draw()};$("#sn").onchange=e=>{IP.sen=e.target.value;draw()};$("#q").oninput=e=>{IP.q=e.target.value;draw()};tabs();draw();
}
async function pageParkedOne(id){
  loading();const [x,sn]=await Promise.all([api("idle/"+id),getSentry()]),h=x.min/60,evs=sentryOf(x,sn.events).sort((a,b)=>a.ts<b.ts?1:-1);
  main.innerHTML=crumbs("#/parked","Parked")+`<h1>Parked · ${whereLink(x)}</h1><div class="sub">${dayName(x.day)} · ${x.hm} – ${addMin(x.hm,x.min)} · ${dur(x.min)}</div>
   <div class="tiles">${tile(n0(x.b0)+"% → "+n0(x.b1)+"%","battery")}${tile(n0(x.kwh,2)+" kWh","energy used")}${tile(h?n0(x.kwh*1000/h)+" Wh/h":"–","average drain")}${tile(n0(x.range,1)+" km","rated range lost")}${tile(Math.round(x.sentry*100)+"%","time in Sentry")}${tile(Math.round(x.climate*100)+"%","time with climate on")}</div>
   ${alertsCard(await alertsFor("idle",id),"parked period")}<div class="card"><h2 style="margin-top:0">Saved Sentry footage</h2><div id="sev"></div><div id="spl"></div></div>
   <div class="card" style="margin-top:14px"><h2 style="margin-top:0">Where</h2><div class="map" id="mp"></div><div class="sub" style="margin-top:8px">${esc(x.addr||"")}</div></div>`;
  miniMap($("#mp"),x.lat,x.lon,"#8b5cf6");wireAlerts(main);
  const box=$("#sev");
  if(!sn.connected){box.innerHTML='<div class="sub">No TeslaCam drive holding these clips is connected, so saved Sentry clips cannot be shown.</div>';return}
  if(!evs.length){box.innerHTML='<div class="sub">No saved Sentry events during this parked period.</div>';return}
  box.innerHTML=`<div class="sub">${evs.length} saved event${evs.length>1?"s":""}. Pick one to watch.</div><div class="evs">${evs.map((v,i)=>`<button class="evc" data-i="${i}"><img loading="lazy" src="/footage/${v.role||"A"}/SentryClips/${v.id}/thumb.png" onerror="this.style.visibility='hidden'"><b>${hms(v.ts)}</b><span>${esc(reason(v.reason))}${v.cam?" · camera "+esc(v.cam):""}</span></button>`).join("")}</div>`;
  const pick=i=>{$$(".evc").forEach(b=>b.classList.toggle("on",+b.dataset.i===i));sentryPlayer($("#spl"),evs[i])};
  $$(".evc").forEach(b=>b.onclick=()=>pick(+b.dataset.i));pick(0);
}
// ---------- clips whose video is not on a connected drive: the kept one-second stills, large, with arrows (the footage log says which drive held the video)
const DRN={A:"the Archive",J:"JOWUA",U:"the Tesla USB"},drName=r=>DRN[r]||r;
// a card's picture: the kept still first (the same for every card, whichever drive is connected); the video's first frame when no still is kept; an empty box when neither
function stThumb(stamp,sec,vurl){return `<img loading="lazy" src="/still/${stamp}/${sec}.jpg" data-v="${esc(vurl||"")}" onerror="stFallback(this)">`}
function stFallback(img){const u=img.dataset.v,p=img.parentNode;img.outerHTML=u?vthumb(u):'<div class="noimg"></div>';if(u&&p)lazyThumbs(p)}
const stillSrc=(stamp,sec)=>`/still/${stamp}/${sec}.jpg`;
function stillViewer(host,o){
  const sts=o.stamps||[];if(!sts.length){host.innerHTML='<div class="sub">This has no clips.</div>';return}
  const where=o.roles&&o.roles.length?o.roles.map(drName).join(" and "):"";
  const banner=where?`The video of this footage is on ${esc(where)}, and none of ${o.roles.length>1?"these drives is":"that drive is"} connected, so its stills are shown. Reconnect ${o.roles.length>1?"one of them":"it"} to play the full video.`
    :"The video of this footage has been deleted from every drive. Its stills are kept.";
  host.innerHTML=`<div class="sp"><div class="stbanner">${banner}</div><div class="sub" style="margin:4px 0 8px">${o.head||""}</div><div id="stv"><div class="sub">Looking for the stills…</div></div></div>`;
  const chunks=[];for(let i=0;i<sts.length;i+=80)chunks.push(sts.slice(i,i+80));
  Promise.all(chunks.map(c=>fetch("/api/clipstatus?stamps="+c.join(",")).then(r=>r.json()).catch(()=>({})))).then(parts=>{
    const have=Object.assign({},...parts),minutes=sts.filter(x=>have[x]&&have[x].stills);const box=$("#stv",host);if(!box)return;
    if(!minutes.length){box.innerHTML=`<div class="sub">No stills were kept for this footage.${where?` Connect ${esc(where)} and press Sync footage to make them.`:""}</div>`;return}
    const LOC=s=>(o.loc&&o.loc[s])||s.slice(0,10)+"T"+s.slice(11).replace(/-/g,":"),shownAt=(s,t)=>new Date(Date.parse(LOC(s)+"Z")+t*1000).toISOString().substr(11,8);
    const stMs=s=>Date.parse(s.slice(0,10)+"T"+s.slice(11).replace(/-/g,":"));      // the clip's name, read the same way the player reads it (o.trig is on that clock too)
    const inMin=t=>{const k=minutes.reduce((a,s,i)=>stMs(s)<=t?i:a,0);return [k,Math.max(0,Math.min(59,Math.floor((t-stMs(minutes[k]))/1000)))]};
    let step=+lsGet("tc.ststep","1"),mi=0,sec=0;
    if(o.trig)[mi,sec]=inMin(o.trig);      // an event opens at the moment it was detected
    const seq=()=>{const out=[];minutes.forEach((m,i)=>{for(let t=0;t<60;t+=step)out.push([i,t])});return out};let frames=seq();
    const pos=()=>{const k=frames.findIndex(f=>f[0]===mi&&f[1]>=sec);return k<0?frames.length-1:k};
    const at=k=>{k=Math.max(0,Math.min(frames.length-1,k));mi=frames[k][0];sec=frames[k][1];paint()};
    function paint(){const k=pos(),[i,t]=frames[k],m=minutes[i];
      box.innerHTML=`<div class="stbar"><button class="btn" id="stp0">◀ Back</button><button class="btn" id="stn0">Next ▶</button><input type="range" id="stsk" min="0" max="${frames.length-1}" value="${k}">
        <span>${shownAt(m,t)}</span><select id="stst">${[[1,"every second"],[5,"every 5 seconds"],[10,"every 10 seconds"],[60,"every minute"]].map(([v,l])=>`<option value="${v}" ${v===step?"selected":""}>${l}</option>`).join("")}</select>
        ${o.trig?'<button class="btn" id="stdt">↧ Detection</button>':""}<span class="sub">${n0(k+1)} of ${n0(frames.length)} · arrow keys also step</span></div>
        <img class="stimg" src="${stillSrc(m,t)}" alt="Front camera at ${shownAt(m,t)}">`;
      $("#stp0",box).onclick=()=>at(pos()-1);$("#stn0",box).onclick=()=>at(pos()+1);$("#stsk",box).oninput=e=>at(+e.target.value);
      $("#stst",box).onchange=e=>{step=+e.target.value;lsSet("tc.ststep",String(step));frames=seq();sec=Math.floor(sec/step)*step;paint()};
      if($("#stdt",box))$("#stdt",box).onclick=()=>{[mi,sec]=inMin(o.trig);sec=Math.floor(sec/step)*step;paint()}}
    paint();
    const key=e=>{if(!document.body.contains(host)){removeEventListener("keydown",key);return}
      if(/SELECT|TEXTAREA|INPUT/.test(e.target.tagName||"")&&e.target.type!=="range")return;
      if(e.key==="ArrowRight"||e.key==="ArrowLeft"){e.preventDefault();at(pos()+(e.key==="ArrowRight"?1:-1))}};
    if(window.__stk)removeEventListener("keydown",window.__stk);window.__stk=key;addEventListener("keydown",key)})}
// ---------- Clip player (Sentry, Saved and Recent clips): all six cameras laid out like a drive page, or one camera big
// event.json "camera" -> the camera that saw it (checked against the thumbnails and the clips: 0 and 1 front, 5 left repeater, 6 right repeater, 7 back; others unknown -> all six)
const EVCAM={"0":"front","1":"front","5":"left_repeater","6":"right_repeater","7":"back"};
const MSZ=[["s","Small","260px"],["m","Medium","420px"],["l","Large","640px"],["f","Full","calc(100vh - 120px)"]],TSZ=[["h","Hide","0"],["n","Normal","284px"]];
const CORDER=["left_pillar","front","right_pillar","left_repeater","back","right_repeater"];
const lsGet=(k,d)=>{try{return localStorage.getItem(k)||d}catch(e){return d}},lsSet=(k,v)=>{try{localStorage.setItem(k,v)}catch(e){}};
// o = {head: html line above the video, stamps: [minute, ...], cams: {minute: [camera, ...]}, url: (minute, camera) => url, trig: epoch ms the event happened (start a little before it)}
// A minute of video that is on no connected drive: a black box with the name of the file and the drives that hold it, the biggest drive first. It comes from the footage
// log (/api/clipholders), so it answers with every drive away.
const sizeGB=g=>g>=1000?(g/1000).toFixed(1).replace(/\.0$/,"")+" TB":(g||"?")+" GB";
function lostBox(box,s){
  box.className="vone";
  box.innerHTML=`<figure class="lost"><div class="lostb"><b>${esc(s)}-front.mp4</b><span class="lostx">Looking up where this video is…</span></div><figcaption>Not on a connected drive</figcaption></figure>`;
  const say=t=>{const el=box.querySelector(".lostx");if(el)el.textContent=t};
  fetch("/api/clipholders?stamps="+encodeURIComponent(s)).then(r=>r.json()).then(m=>{const h=m[s]||{state:"unknown"};
    if(h.state==="kept"){const f=h.files.find(x=>x.cam==="front")||h.files[0];
      box.querySelector(".lostb b").textContent=f.name+(h.files.length>1?` and ${h.files.length-1} more camera${h.files.length>2?"s":""}`:"");
      say("On "+f.drives.map(d=>`${d.label} (${sizeGB(d.size_gb)})`).join(", then ")+(f.drives.some(d=>d.connected)?"":". None of these drives is connected")+".")}
    else say(h.state==="deleted"?"This video has been deleted from every drive. The stills are kept.":"This video was never logged on any drive.")}).catch(()=>say("Could not look up where this video is."))}
function clipPlayer(host,o){
  const stamps=o.stamps;if(!stamps.length){host.innerHTML='<div class="sub">This has no video clips.</div>';return}
  // a clip's name is the car's clock (stMs: only used to place things that are on that same clock, like a Sentry trigger); what is SHOWN is the local clock (o.loc: stamp -> local text)
  const stMs=s=>Date.parse(s.slice(0,10)+"T"+s.slice(11).replace(/-/g,":")),LOC=s=>(o.loc&&o.loc[s])||s.slice(0,10)+"T"+s.slice(11).replace(/-/g,":"),clock=s=>LOC(s).slice(11,19),shownAt=(s,t)=>new Date(Date.parse(LOC(s)+"Z")+t*1000).toISOString().substr(11,8),fmt=t=>Math.floor(t/60)+":"+String(Math.floor(t%60)).padStart(2,"0");
  let si=0,seek=0,view=o.view||lsGet("tc.view","all"),rate=lsGet("tc.rate","1"),vs=[],mv=null;
  // a Sentry/Saved event opens at the START of its first clip and plays; the Detection button jumps to the moment it happened
  const toTrig=()=>{si=stamps.reduce((a,s,i)=>stMs(s)<=o.trig?i:a,0);seek=Math.max(0,(o.trig-stMs(stamps[si]))/1000);if($("#stp"))$("#stp").value=si;vids()};
  const shell=()=>{host.innerHTML=`<div class="sp"><div class="bar"><button class="chip ${view==="all"?"on":""}" data-c="all">All six</button>${Object.keys(CAMN).map(c=>`<button class="chip ${view===c?"on":""}" data-c="${c}">${CAMN[c]}</button>`).join("")}</div>
    <div class="sub" style="margin:4px 0 8px">${o.head}</div>
    <div class="vbar"><button class="btn" id="vpl">❚❚ Pause</button>${o.trig?'<button class="btn" id="vtr" title="Go back to the moment it was detected">↧ Detection</button>':""}<input type="range" id="vsk" min="0" max="60" step="0.1" value="0"><span id="vtm">0:00</span>
     <select id="vsp">${["0.5","1","2","4"].map(r=>`<option value="${r}" ${r===rate?"selected":""}>${r}×</option>`).join("")}</select>
     ${stamps.length>1?`<select id="stp">${stamps.map((s,i)=>`<option value="${i}" ${i===si?"selected":""}>${clock(s)}${o.trig&&stMs(s)<=o.trig&&o.trig<stMs(s)+60000?"  ◀ happened":""}</option>`).join("")}</select>`:""}</div>
    <div id="vids"></div></div>`;
    $$(".sp [data-c]").forEach(b=>b.onclick=()=>{view=b.dataset.c;if(!o.view)lsSet("tc.view",view);seek=mv?mv.currentTime:0;shell()});
    $("#vsp").onchange=e=>{rate=e.target.value;lsSet("tc.rate",rate);vs.forEach(v=>v.playbackRate=+rate)};
    if($("#stp"))$("#stp").onchange=e=>{si=+e.target.value;seek=0;vids()};
    if($("#vtr"))$("#vtr").onclick=toTrig;
    vids()};
  // The next minute is loaded in the background (last 25 s of this one) and swapped in when this one ends, so there is no wait between minutes.
  let nx=null,offp=false;
  // A video element keeps its decoder and its buffered minute until its source is removed, and a browser only has a few decoders: six per minute, never released, and after
  // a while only one camera loads. So every element that is let go (the minute that ended, a minute loaded ahead and then not used, everything when the page is left) is emptied.
  const drop=x=>{try{x.pause();x.removeAttribute("src");x.load()}catch(e){}};
  const dropNext=()=>{if(nx){Object.keys(nx.m).forEach(c=>drop(nx.m[c]));nx=null}};
  const dropAll=()=>{vs.forEach(drop);dropNext()};
  const wire=()=>{const sk=$("#vsk"),pl=$("#vpl"),tm=$("#vtm"),box=$("#vids"),seekAll=t=>vs.forEach(x=>{try{x.currentTime=t}catch(e){}});
    if(!mv)return;
    const cam=mv.parentNode.dataset.c;
    const ready=()=>{sk.max=mv.duration||60;vs.forEach(x=>x.playbackRate=+rate);if(seek){seekAll(seek);seek=0}};
    const go=()=>{ready();vs.forEach(x=>x.play().catch(()=>{}))};if(mv.readyState>=1)go();else mv.onloadedmetadata=go;
    // the button always shows what pressing it does: Pause while playing, Play while paused (a clip that ended into the next minute counts as playing)
    const lab=()=>{pl.textContent=(mv.paused||mv.ended)&&!(mv.ended&&si<stamps.length-1)?"▶ Play":"❚❚ Pause"};
    ["play","playing","pause","ended","emptied"].forEach(n=>mv.addEventListener(n,lab));lab();
    mv.ontimeupdate=()=>{if(!document.contains(host)){dropAll();return}sk.value=mv.currentTime;if(o.onTime)o.onTime(stMs(stamps[si])+mv.currentTime*1000);if(o.onAt)o.onAt(stamps[si],mv.currentTime);tm.textContent=fmt(mv.currentTime)+" · "+shownAt(stamps[si],mv.currentTime);
      // keep the cameras together without seeking (a seek stalls that camera): a small drift is nudged out by speeding it up or slowing it down a little, only a large one is jumped
      vs.forEach(x=>{if(x===mv)return;const dr=x.currentTime-mv.currentTime;
        if(Math.abs(dr)>1.2){try{x.currentTime=mv.currentTime}catch(e){}x.playbackRate=+rate}
        else if(Math.abs(dr)>.25&&!mv.paused)x.playbackRate=+rate*(dr>0?.94:1.06);
        else if(x.playbackRate!==+rate)x.playbackRate=+rate});
      if(!nx&&mv.duration&&mv.currentTime>mv.duration-25)prep()};
    mv.onended=()=>{if(si>=stamps.length-1)return;si++;seek=0;if(nx&&nx.i===si&&nx.m[cam]){swap()}else vids()};
    mv.onerror=()=>box.insertAdjacentHTML("afterbegin",'<div class="sub">This clip could not be played (missing, or the drive was disconnected).</div>');
    pl.onclick=()=>{if(mv.paused)vs.forEach(x=>x.play().catch(()=>{}));else vs.forEach(x=>x.pause())};
    sk.oninput=()=>seekAll(+sk.value)};
  // Loading the next minute ahead doubles the decoders (12 streams of 2896x1876 with all six cameras), which is what makes cameras stall: only done for one or two cameras.
  const prep=()=>{if(nx||si>=stamps.length-1||vs.length>2)return;const s2=stamps[si+1],have=o.cams[s2]||[],m={};
    $$("#vids figure[data-c]").forEach(f=>{const c=f.dataset.c;if(have.includes(c)){const v=document.createElement("video");v.muted=true;v.playsInline=true;v.preload="auto";v.src=o.url(s2,c);v.load();m[c]=v}});
    nx={i:si+1,m}};
  const swap=()=>{const m=nx.m;nx=null;vs.forEach(drop);
    $$("#vids figure[data-c]").forEach(f=>{const nv=m[f.dataset.c],old=f.querySelector("video");if(nv&&old)f.replaceChild(nv,old)});
    if($("#stp"))$("#stp").value=si;
    vs=$$("#vids video");mv=vs.find(v=>v.parentNode.dataset.c==="front")||vs[0];wire();vs.forEach(x=>{x.playbackRate=+rate;x.play().catch(()=>{})})};
  const vids=()=>{dropAll();
    const s=stamps[si],have=o.cams[s]||[],all=view==="all",show=all?CORDER:[have.includes(view)?view:have.includes("front")?"front":have[0]];
    const box=$("#vids");if(!have.length){lostBox(box,s);vs=[];mv=null;return}
    box.className=all?"vgrid":"vone";
    box.innerHTML=show.map(c=>have.includes(c)?`<figure data-c="${c}" class="${o.hot===c?"hot":""}" ${all?'title="Click to see this camera large"':""}><video muted playsinline preload="auto" src="${o.url(s,c)}"></video><figcaption>${CAMN[c]}${o.hot===c?" · detected here":""}</figcaption></figure>`:`<figure class="none"><div>not recorded this minute</div><figcaption>${CAMN[c]}</figcaption></figure>`).join("");
    vs=$$("#vids video");mv=vs.find(v=>v.parentNode.dataset.c==="front")||vs[0];if(!mv){return}
    if(all)$$("#vids figure[data-c]").forEach(f=>f.onclick=()=>{view=f.dataset.c;seek=mv.currentTime;shell()});
    wire()};
  // nothing plays while the page is hidden (a hidden tab still decodes six streams otherwise)
  const hide=()=>{if(!document.contains(host)){document.removeEventListener("visibilitychange",hide);dropAll();return}if(document.hidden&&mv&&!mv.paused){offp=true;vs.forEach(x=>x.pause())}else if(!document.hidden&&offp){offp=false;vs.forEach(x=>x.play().catch(()=>{}))}};
  document.addEventListener("visibilitychange",hide);
  // Watchdog: a camera that has stopped moving while the others play (the browser gave up its decoder, or the read from the drive stalled) is loaded again at the same
  // second, up to 4 times a minute each; the page is left alone while paused or hidden.
  const dog=setInterval(()=>{if(!document.contains(host)){clearInterval(dog);dropAll();return}
    if(!mv||mv.paused||mv.ended||document.hidden||!vs.length)return;const now=Date.now();
    vs.forEach(x=>{const at=x.currentTime,same=x._at!=null&&Math.abs(at-x._at)<.01;x._at=at;x._still=same?(x._still||0)+1:0;
      if(x.ended||x._still<2||!x.getAttribute("src"))return;
      x._rv=(x._rv||[]).filter(t=>now-t<60000);if(x._rv.length>=4)return;x._rv.push(now);x._still=0;
      const src=x.getAttribute("src"),t=Math.max(0,(x===mv?at:mv.currentTime)),r=+rate;
      try{x.pause();x.removeAttribute("src");x.load();x.addEventListener("loadedmetadata",()=>{try{x.currentTime=t}catch(e){}x.playbackRate=r;x.play().catch(()=>{})},{once:true});x.src=src;x.load()}catch(e){}})},1500);
  shell();
  // the page can move the video to a clock time (clicking the map or the chart)
  return {seekAt:(st,sec)=>{const i=stamps.indexOf(st);if(i<0)return;                 // a minute and a second in it (the page works these out in UTC)
      if(i===si&&mv&&mv.readyState>0){vs.forEach(x=>{try{x.currentTime=sec}catch(e){}})}
      else{si=i;seek=Math.max(0,sec);if($("#stp"))$("#stp").value=si;vids()}},
    seek:ms=>{let i=stamps.findIndex(s=>stMs(s)<=ms&&ms<stMs(s)+61000);if(i<0)i=stamps.findIndex(s=>stMs(s)>ms);if(i<0)return;
    if(i===si&&mv&&mv.readyState>0){vs.forEach(x=>{try{x.currentTime=(ms-stMs(stamps[i]))/1000}catch(e){}})}
    else{si=i;seek=Math.max(0,(ms-stMs(stamps[i]))/1000);if($("#stp"))$("#stp").value=si;vids()}}};
}
// one Sentry or Saved event folder (role = which drive, folder = SentryClips | SavedClips)
async function sentryPlayer(host,ev,role=ev&&ev.role||"A",folder="SentryClips"){
  let stamps=Object.keys(ev.clips).sort(),cams=ev.clips,loc=ev.loc,more=null;const t=ev.ts,what=ev.reason?reason(ev.reason):folder==="SavedClips"?"Saved clip":"Sentry event",trig=Date.parse(ev.cts||t);
  // A Saved clip keeps a lot from before the moment it was saved and only a little after it; the dashcam went on recording, so as many minutes after as before
  // are added from its continuous recording (when it is on a connected drive): the player then shows the same stretch on both sides of the moment
  if(folder==="SavedClips"&&stamps.length){
    const stMs=x=>Date.parse(x.slice(0,10)+"T"+x.slice(11).replace(/-/g,":")),before=stamps.filter(x=>stMs(x)<=trig).length,have=stamps.filter(x=>stMs(x)>trig).length,want=before-1-have;
    if(want>0){try{const r=await fetch("/api/clips/after/"+encodeURIComponent(stamps[stamps.length-1])+"?n="+want+"&skip="+encodeURIComponent(stamps.join(",")));const d=r.ok?await r.json():null;
      if(d&&d.clips&&Object.keys(d.clips).length){more=d;stamps=stamps.concat(Object.keys(d.clips).sort());cams=Object.assign({},ev.clips,d.clips);loc=Object.assign({},ev.loc||{},d.loc||{})}}catch(e){}}}
  const extra=more?Object.keys(more.clips).length:0;
  clipPlayer(host,{stamps,cams,loc,trig,view:EVCAM[ev.cam]||"all",url:(s,c)=>more&&more.play[s]?`/footage/${more.play[s]}/RecentClips/${s}-${c}.mp4`:`/footage/${role}/${folder}/${ev.id}/${s}-${c}.mp4`,
    head:`${esc(what)} on ${dayName(t.slice(0,10))} at ${hms(t)}${ev.city?" · "+esc(ev.city):""}${ev.street&&folder==="SavedClips"?" · "+esc(ev.street):""}. One-minute clips, ${stamps.length} in this event${extra?` (${extra} after it from the continuous recording)`:""}; the next clip plays on its own. It plays from the start; press Detection to jump to the moment it happened${EVCAM[ev.cam]?" (the "+CAMN[EVCAM[ev.cam]].toLowerCase()+" camera saw it)":""}.`});
}

// ---------- Places (the places manager) and the page for one place
let PL={tab:"saved",q:"",visited:false,net:"all",state:"all",view:"list"},SYNCPOLL=null,PLM=null;
// A map of the rows of the current Places tab (same filters as the list). Dots are small so they do not hide each other;
// places you have used are solid, the rest faded. New Zealand is left out for now.
function placesMap(host,rows,tab){
  const COL={saved:"#3b6cf6",sc:"#e0201b",chargers:"#16a34a",found:"#f59e0b"}[tab]||"#3b6cf6",byNet=tab==="chargers"||tab==="sc",ALIAS={"Tesla Supercharger":"Tesla","Evie Networks":"Evie"};
  const netName=r=>{const n=r.network||(tab==="sc"?"Tesla":"Other");return ALIAS[n]||n};
  const netCol=n=>{const c=cfg("map.network_colors")||{};if(c[n])return c[n];let h=0;for(const ch of n)h=(h*31+ch.charCodeAt(0))%360;return `hsl(${h},62%,42%)`};
  const used=r=>tab==="found"?true:tab==="saved"?(r.charges||r.idles||r.drives)>0:r.charges>0;
  const hidden=(PLM&&PLM.tab===tab&&PLM.hidden)||new Set();
  const feats=()=>rows.filter(r=>r.lat!=null&&r.lon!=null&&+r.lon<cfg("map.places_max_lon")&&!(byNet&&hidden.has(netName(r)))).map(r=>({type:"Feature",geometry:{type:"Point",coordinates:[+r.lon,+r.lat]},properties:{u:used(r)?1:0,key:r.key,name:r.name||r.addr||"Unnamed stop",lat:+r.lat,lon:+r.lon,c:byNet?netCol(netName(r)):COL,
    sub:[r.network||(tab==="sc"?"Supercharger":tab==="saved"?"Saved place":tab==="found"?"Stop to review":""),r.addr||"",r.charges?r.charges+" charge"+(r.charges>1?"s":"")+" · "+n0(r.kwh,1)+" kWh":(tab==="found"?r.visits+" days there":"")].filter(Boolean).join(" · ")}})).sort((x,y)=>x.properties.u-y.properties.u);
  const legend=()=>{if(!byNet)return;const cnt={};rows.forEach(r=>{if(r.lat!=null&&+r.lon<cfg("map.places_max_lon"))cnt[netName(r)]=(cnt[netName(r)]||0)+1});
    $("#nl").innerHTML=Object.entries(cnt).sort((a,b)=>b[1]-a[1]).map(([n,c])=>`<button class="chip nw${hidden.has(n)?" off":""}" data-nw="${esc(n)}" title="Show / hide ${esc(n)}"><i style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${netCol(n)};margin-right:6px"></i>${esc(n)} <b>${n0(c)}</b></button>`).join("");
    $$("[data-nw]",host).forEach(b=>b.onclick=()=>{const n=b.dataset.nw;hidden.has(n)?hidden.delete(n):hidden.add(n);PLM.hidden=hidden;PLM.update(false);legend()})};
  if(PLM&&PLM.tab===tab&&PLM.el&&host.contains(PLM.el)){PLM.update(true,rows);legend();return}
  if(PLM){try{PLM.m.remove()}catch(e){}PLM=null}
  if(!window.maplibregl){host.innerHTML='<div class="empty">Map library not loaded (offline?).</div>';return}
  host.innerHTML=`<div class="sub" style="margin-bottom:6px">Solid dot = used, faded = not used. Dots grow and get names as you zoom in. Click a dot for details.</div>${byNet?'<div class="bar" id="nl"></div>':""}<div class="map big" id="pm"></div>`;
  const el=$("#pm"),dark=matchMedia("(prefers-color-scheme: dark)").matches,m=newMap({container:el,style:MAPSTYLE(dark),center:[151.2,-33.8],zoom:8,attributionControl:{compact:true}});
  mapNav(m,()=>update(true),"Show every place");
  let ready=false,first=true;
  const update=(refit,rs)=>{if(rs)rows=rs;const F=feats();$("#cnt").textContent=F.length+" on the map";if(!ready)return;m.getSource("pl").setData({type:"FeatureCollection",features:F});
    if((refit||first)&&F.length){first=false;const b=new maplibregl.LngLatBounds();F.forEach(f=>b.extend(f.geometry.coordinates));m.fitBounds(b,{padding:50,maxZoom:13,duration:400})}};
  PLM={m,el,update,tab,hidden};legend();
  m.on("load",()=>{ready=true;m.addSource("pl",{type:"geojson",data:{type:"FeatureCollection",features:[]}});
    // the dot grows with the zoom, so it stays as easy to see against the detailed street map as against the plain country map
    const R=["interpolate",["linear"],["zoom"],...[[3,2.5],[8,4],[12,7],[15,10],[18,13]].flatMap(([z,r])=>[z,["case",["==",["get","u"],1],r*1.35,r]])];
    m.addLayer({id:"pl",type:"circle",source:"pl",layout:{"circle-sort-key":["get","u"]},paint:{"circle-color":["get","c"],"circle-radius":R,"circle-opacity":["case",["==",["get","u"],1],1,0.7],"circle-stroke-width":["interpolate",["linear"],["zoom"],3,0.75,12,1.5,16,2.5],"circle-stroke-color":dark?"#111":"#fff"}});
    m.addLayer({id:"pl-name",type:"symbol",source:"pl",minzoom:12.5,layout:{"text-field":["get","name"],"text-font":["Noto Sans Regular"],"text-size":12,"text-anchor":"top","text-offset":[0,1.1],"text-optional":true,"text-max-width":9},paint:{"text-color":dark?"#e5e7eb":"#1f2937","text-halo-color":dark?"#111":"#fff","text-halo-width":1.5}});
    m.on("mouseenter","pl",()=>m.getCanvas().style.cursor="pointer");m.on("mouseleave","pl",()=>m.getCanvas().style.cursor="");
    m.on("click","pl",e=>{const p=e.features[0].properties;new maplibregl.Popup({offset:10,maxWidth:"300px"}).setLngLat(e.features[0].geometry.coordinates).setHTML(`<div style="font:13px system-ui;color:#111"><b>${esc(p.name)}</b><div style="color:#555;margin:3px 0 6px">${esc(p.sub)}</div><a href="#/place/${encodeURIComponent(p.key)}">Open place</a><div style="margin-top:6px;font-size:12px">${mapLinks(p.lat,p.lon,p.name)}</div></div>`).addTo(m)});
    update(true)});
  update(false);
}
const AU_STATES={"new south wales":"NSW","victoria":"VIC","queensland":"QLD","south australia":"SA","western australia":"WA","tasmania":"TAS","australian capital territory":"ACT","northern territory":"NT"};
function stateCode(q){q=q.trim();if(!q)return null;const l=q.toLowerCase();return AU_STATES[l]||(Object.values(AU_STATES).includes(q.toUpperCase())?q.toUpperCase():null)}
async function pagePlaces(){
  loading();cache.places=null;const L=await api("places");let C=null;const need=async()=>{if(!C)C=await api("chargers")};if(PL.tab==="chargers")await need();
  const draw=()=>{const q=PL.q.toLowerCase(),host=$("#tb"),qs=stateCode(q);let rows,cols;     // a search that is just a state ("Victoria", "vic") means that state, not streets named after it
    const stat=[{k:"charges",t:"Charges"},{k:"idles",t:"Parked"},{k:"drives",t:"Arrivals"}];
    if(PL.tab==="saved"){rows=L.saved.filter(p=>!q||(p.name+" "+p.notes).toLowerCase().includes(q));
      cols=[{k:"name",t:"Place",l:1},{k:"notes",t:"Notes",l:1},...stat,{k:"reparks",t:"Re-parks"},{k:"kwh",t:"kWh charged",f:r=>r.charges?n0(r.kwh,1):"–"},{k:"last",t:"Last visit",f:r=>r.last||"–"},{k:"radius",t:"Radius",f:r=>n0(r.radius)+" m"}]}
    else if(PL.tab==="sc"){rows=L.superchargers.filter(p=>(!PL.visited||p.charges)&&(!q||(qs?p.name.endsWith(", "+qs):(p.name+" "+p.addr).toLowerCase().includes(q))));
      cols=[{k:"name",t:"Supercharger (name on tesla.com)",l:1},{k:"addr",t:"Address",l:1},{k:"stalls",t:"Stalls"},{k:"kw",t:"Max kW",f:r=>n0(r.kw)},{k:"charges",t:"Charges"},{k:"kwh",t:"kWh",f:r=>r.charges?n0(r.kwh,1):"–"},{k:"cost",t:"Cost",f:r=>r.charges?money(r.cost):"–"},{k:"last",t:"Last visit",f:r=>r.last||"–"}]}
    else if(PL.tab==="chargers"){rows=C.rows.filter(p=>(PL.net==="all"||p.network===PL.net)&&(PL.state==="all"||p.state===PL.state)&&(!PL.visited||p.charges)&&(!q||(qs?p.state===qs:(p.name+" "+p.addr+" "+p.network+" "+p.suburb).toLowerCase().includes(q))));
      cols=[{k:"name",t:"Charger",l:1},{k:"network",t:"Network",l:1},{k:"addr",t:"Address",l:1,f:r=>esc(r.addr||r.suburb||"")},{k:"state",t:"State"},{k:"stalls",t:"Stalls",f:r=>r.stalls==null?"–":n0(r.stalls)},{k:"kw",t:"Max kW",f:r=>r.kw?n0(r.kw):"–"},{k:"tier",t:"Type",l:1,f:r=>esc((r.tier||"").replace(/_/g," "))},{k:"conn",t:"Connectors",l:1},{k:"charges",t:"Charges"},{k:"kwh",t:"kWh",f:r=>r.charges?n0(r.kwh,1):"–"},{k:"last",t:"Last charge",f:r=>r.last||"–"}]}
    else{rows=L.found.filter(p=>!q||(p.addr||"").toLowerCase().includes(q));
      cols=[{k:"addr",t:"Address of a place you return to, not saved yet",l:1,f:r=>esc(r.addr||(r.lat.toFixed(4)+", "+r.lon.toFixed(4)))},{k:"lat",t:"Lat, Lon",l:1,f:r=>`${(+r.lat).toFixed(5)}, ${(+r.lon).toFixed(5)}`},{k:"map",t:"Open in",l:1,f:r=>mapLinks(r.lat,r.lon,r.addr,1)},{k:"visits",t:"Days there"},{k:"last",t:"Last"}]}
    if(PL.view==="map"){placesMap(host,rows,PL.tab);return}
    table(host,cols,rows,r=>{sessionStorage.removeItem("placePrefill");location.hash="#/place/"+encodeURIComponent(r.key)},PL.tab==="found"?"visits":PL.tab==="sc"||PL.tab==="chargers"?"charges":"last");
    $("#cnt").textContent=rows.length+" shown"};
  main.innerHTML=`<h1>Places</h1><div class="sub">Your saved places, the Tesla Superchargers (named as on tesla.com) and stops worth naming. Open one to see what is around it on OpenStreetMap.</div>
   <div class="tiles">${tile(L.saved.length,"saved places")}${tile(L.superchargers.filter(s=>s.charges).length+" of "+L.superchargers.length,"Superchargers used")}${tile(L.found.length,"stops to review")}</div>
   <div class="bar"><button class="chip" data-t="saved">Saved places</button><button class="chip" data-t="sc">Superchargers</button><button class="chip" data-t="chargers">Chargers</button><button class="chip" data-t="found">Stops to review</button><span class="vw"><button class="chip" data-v="list">List</button><button class="chip" data-v="map">Map</button></span>
   <select id="cn"></select><select id="cs"></select><label class="sub" id="vl"><input type="checkbox" id="vis"> used only</label><button class="btn" id="sy">Sync chargers…</button><input type="search" id="q" placeholder="Search…" value="${esc(PL.q)}"><button class="btn pri" id="add">+ New place</button><span class="sub" id="cnt"></span></div><div id="sp"></div><div id="tb"></div>`;
  const tabs=()=>{$$("[data-t]").forEach(c=>c.classList.toggle("on",c.dataset.t===PL.tab));const ch=PL.tab==="chargers";$("#vl").style.display=PL.tab==="sc"||ch?"":"none";$$("[data-v]").forEach(c=>c.classList.toggle("on",c.dataset.v===PL.view));$("#cn").style.display=$("#cs").style.display=$("#sy").style.display=ch?"":"none";$("#vis").checked=PL.visited;
    if(ch&&C){$("#cn").innerHTML='<option value="all">All networks</option>'+C.networks.map(([n,c])=>`<option value="${esc(n)}">${esc(n||"Other")} (${n0(c)})</option>`).join("");$("#cn").value=PL.net;
      const st=[...new Set(C.rows.map(r=>r.state).filter(Boolean))].sort();$("#cs").innerHTML='<option value="all">All states</option>'+st.map(x=>`<option>${esc(x)}</option>`).join("");$("#cs").value=PL.state}};
  $$("[data-t]").forEach(c=>c.onclick=async()=>{PL.tab=c.dataset.t;if(PL.tab==="chargers")await need();tabs();draw()});
  $$("[data-v]").forEach(c=>c.onclick=()=>{PL.view=c.dataset.v;tabs();draw()});
  $("#cn").onchange=e=>{PL.net=e.target.value;draw()};$("#cs").onchange=e=>{PL.state=e.target.value;draw()};
  $("#sy").onclick=()=>syncPanel($("#sp"),async()=>{cache.chargers=null;cache.places=null;C=await api("chargers");tabs();draw()});$("#vis").onchange=e=>{PL.visited=e.target.checked;draw()};$("#q").oninput=e=>{PL.q=e.target.value;draw()};
  $("#add").onclick=()=>{sessionStorage.setItem("placePrefill",JSON.stringify({name:"",lat:"",lon:"",radius_m:150}));location.hash="#/place/"+encodeURIComponent("@new")};tabs();draw();
}
// ---------- sync_charger_all.sh from the app: Run, live log, Stop
function syncPanel(host,done){
  if(host.firstChild){host.innerHTML="";if(SYNCPOLL){clearInterval(SYNCPOLL);SYNCPOLL=null}return}
  host.innerHTML=`<div class="card"><h2 style="margin-top:0">Sync chargers</h2><div class="sub">Runs <b>sync_charger_all.sh</b>, the one command for the chargers: Tesla Superchargers and Destination chargers (near Home, 50 km), then Chargefox, EVX, Evie, bp pulse, PlugShare (near Home), AmpCharge, Exploren, Jolt and NRMA, and finally the consolidated export. It needs the network, can take many minutes, and updates charging_stations.json in your data folder. Existing entries are kept; new ones are added.</div>
    <div class="bar"><button class="btn pri" id="sr">Run now</button><button class="btn bad" id="ss" disabled>Stop</button><span class="sub" id="sm"></span></div><pre class="out" id="slog" style="max-height:340px;overflow:auto" hidden></pre></div>`;
  const log=$("#slog");let since=0,was=false;
  const poll=async()=>{let j;try{j=await (await fetch("/api/chargers-sync?since="+since)).json()}catch(e){$("#sm").textContent="Could not reach the app.";return}
    if(!document.body.contains(log)){clearInterval(SYNCPOLL);SYNCPOLL=null;return}
    if(j.lines&&j.lines.length){log.hidden=false;const bot=log.scrollTop+log.clientHeight>=log.scrollHeight-20;log.textContent+=(log.textContent?"\n":"")+j.lines.join("\n");if(bot)log.scrollTop=log.scrollHeight}
    since=j.n;$("#sr").disabled=j.running;$("#ss").disabled=!j.running;
    $("#sm").textContent=j.running?"Running…":j.started?(j.rc===0?"Finished.":"Stopped or failed (exit "+j.rc+").")+"":"";
    if(j.running)was=true;
    if(!j.running&&was){was=false;clearInterval(SYNCPOLL);SYNCPOLL=null;done()}
    if(!j.running&&!was&&SYNCPOLL&&!j.started){clearInterval(SYNCPOLL);SYNCPOLL=null}};
  const go=()=>{if(!SYNCPOLL)SYNCPOLL=setInterval(poll,2000);poll()};
  $("#sr").onclick=async()=>{if(!confirm("Run sync_charger_all.sh now? It contacts the charger networks and PlugShare and can take many minutes."))return;log.textContent="";since=0;was=true;
    const j=await (await fetch("/api/chargers/sync",{method:"POST"})).json();if(j.ok===false){$("#sm").textContent=j.error;return}go()};
  $("#ss").onclick=async()=>{await fetch("/api/chargers/sync/stop",{method:"POST"});go()};
  poll();fetch("/api/chargers-sync?since=0").then(r=>r.json()).then(j=>{if(j.running)go()}).catch(()=>{});
}
const OSMG={};
async function pagePlace(key){
  loading();
  let p;if(key==="@new"){let pre={};try{pre=JSON.parse(sessionStorage.getItem("placePrefill")||"{}")}catch(e){}p=Object.assign({kind:"spot",key,name:"New place",lat:null,lon:null,radius:150,keywords:[],notes:"",visits:[],nvisits:0},pre,{radius:pre.radius_m||150,name:pre.name||"New place",lat:pre.lat===""||pre.lat==null||isNaN(+pre.lat)?null:+pre.lat,lon:pre.lon===""||pre.lon==null||isNaN(+pre.lon)?null:+pre.lon})}
  else{let nm="",ad="";try{if(key[0]==="@"){const q=JSON.parse(sessionStorage.getItem("placePrefill")||"{}");nm=q.name||"";ad=q.addr||""}}catch(e){}
    const r=await fetch("/api/place?key="+encodeURIComponent(key)+(nm?"&name="+encodeURIComponent(nm):"")+(ad?"&addr="+encodeURIComponent(ad):""));if(!r.ok){main.innerHTML=crumbs("#/places","Places")+'<div class="empty">That place is not in your places any more.</div>';return}p=await r.json()}
  const kinds={saved:"Saved place",supercharger:"Tesla Supercharger",charger:"Charger"+(p.network?" · "+p.network:""),spot:"Not saved yet"},vis=p.visits||[],n=k=>vis.filter(v=>v.k===k).length,editable=p.kind!=="supercharger"&&p.kind!=="charger";
  const kw=(p.keywords||[]).join(", "),pre=(()=>{try{return JSON.parse(sessionStorage.getItem("placePrefill")||"{}")}catch(e){return{}}})();
  main.innerHTML=crumbs("#/places","Places")+`<h1>${esc(p.name)}</h1><div class="sub">${kinds[p.kind]}${p.addr?" · "+esc(p.addr):""}${p.lat!=null?" · "+p.lat.toFixed(5)+", "+p.lon.toFixed(5):""}</div>
   <div class="tiles">${tile(n("charge"),"charges")}${tile(n("idle")+n("stay"),"parked periods")}${tile(n("drive"),"arrivals by drive")}${n("repark")?tile(n("repark"),"re-parks (car moved &lt; "+REPM()+")"):""}${p.kind==="supercharger"||p.kind==="charger"?tile(n0(p.stalls)+(p.kw?" × "+n0(p.kw)+" kW":" stalls"),p.kw?"stalls × max power":"max power not listed"):tile(n0(p.radius)+" m","match radius")}</div>
   <div class="two"><div class="card"><h2 style="margin-top:0">Map</h2><div class="map" id="mp"></div>${p.lat!=null?`<div class="sub" style="margin-top:8px">Open in: ${mapLinks(p.lat,p.lon,p.name)}</div>`:""}</div><div class="card" id="ed"></div></div>
   <div class="card" style="margin-top:14px"><h2 style="margin-top:0">Around here on OpenStreetMap</h2><div id="osm"><div class="sub">Loading…</div></div></div>
   <h2>Visits</h2><div id="vs"></div>`;
  if(p.lat!=null)miniMap($("#mp"),p.lat,p.lon,p.kind==="supercharger"?"#e0201b":p.kind==="charger"?"#16a34a":"#3b6cf6");else $("#mp").outerHTML='<div class="sub">Enter coordinates below, or start from a stop in the Places list.</div>';
  const ed=$("#ed");
  if(editable){ed.innerHTML=`<h2 style="margin-top:0">${p.kind==="saved"?"Edit this place":"Save as a place"}</h2><div class="form">
    <label class="w">Name<input id="f_name" value="${esc(p.kind==="spot"?specific(pre.name||"",p):p.name)}" placeholder="e.g. Corner Shop Chargers"></label>
    <label>Latitude<input id="f_lat" value="${p.lat==null?"":p.lat}"></label><label>Longitude<input id="f_lon" value="${p.lon==null?"":p.lon}"></label>
    <label>Radius (m)<input id="f_rad" value="${p.kind==="spot"&&pre.radius_m?pre.radius_m:(p.radius||150)}"></label>${p.kind==="spot"&&pre.radnote?`<div class="sub" style="align-self:end">${esc(pre.radnote)}</div>`:""}
    <label class="w">Keywords (addresses or names to match, comma separated)<input id="f_kw" value="${esc(kw)}"></label>
    <label class="w">Notes<input id="f_notes" value="${esc(p.notes||"")}"></label></div>
    <div class="bar"><button class="btn pri" id="sv">${p.kind==="saved"?"Save changes":"Save place"}</button>${p.kind==="saved"?'<button class="btn bad" id="dl">Delete</button>':""}<span class="sub" id="msg"></span></div>
    <div class="sub">Saved places are written to places.json (a dated backup is kept first). The sync scripts use the same file.</div>`;
    $("#sv").onclick=async()=>{const m=$("#msg");m.textContent="Saving…";const body={old_name:p.kind==="saved"?p.name:"",name:$("#f_name").value,lat:$("#f_lat").value,lon:$("#f_lon").value,radius_m:$("#f_rad").value,keywords:$("#f_kw").value,notes:$("#f_notes").value};
      try{const j=await (await fetch("/api/places/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})).json();
        if(j.ok){bust();sessionStorage.removeItem("placePrefill");NAVREPL=true;location.hash="#/place/"+encodeURIComponent(j.key);route()}else m.textContent="Not saved: "+j.error}catch(e){m.textContent="Could not reach the app: "+e}};
    if($("#dl"))$("#dl").onclick=async()=>{if(!confirm("Delete the saved place '"+p.name+"'? Past drives keep their addresses."))return;
      const j=await (await fetch("/api/places/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:p.name})})).json();
      if(j.ok){bust();NAVREPL=true;location.hash="#/places"}else $("#msg").textContent="Not deleted: "+j.error}}
  else ed.innerHTML=`<h2 style="margin-top:0">About this ${p.kind==="charger"?"charger":"Supercharger"}</h2><div class="sub">${p.kind==="charger"?"From the charger registry (sync_charger_all.sh). Use Sync chargers on the Places page to refresh it.":"The name is the one used on tesla.com."}</div><table class="kv">${p.kind==="charger"?`<tr><td>Network</td><td>${esc(p.network||"–")}</td></tr><tr><td>Connectors</td><td>${esc(p.conn||"–")}</td></tr><tr><td>Access</td><td>${esc(p.access||"–")}</td></tr>`:""}<tr><td>Stalls</td><td>${n0(p.stalls)}</td></tr><tr><td>Max power</td><td>${n0(p.kw)} kW${p.tier?" ("+esc(p.tier)+")":""}</td></tr><tr><td>Hours</td><td>${esc(p.hours||"–")}</td></tr><tr><td>Address</td><td>${esc(p.addr||"–")}</td></tr></table>${p.notes?`<div class="sub" style="margin-top:8px;white-space:pre-line">${esc(p.notes)}</div>`:""}`;
  $("#vs").innerHTML=vis.length?vis.filter(v=>!(v.k==="repark"&&v.in_stay)).map(v=>{const href=v.k==="charge"?"#/charge/"+v.id:v.k==="idle"?"#/parked/"+v.id:v.k==="stay"?"":v.id?"#/drive/"+v.id:"";
    return `<a class="row" ${href?`href="${href}"`:""}><span class="t">${v.day} ${v.hm}</span><span class="k ${v.k==="stay"?"idle":v.k==="repark"?"drive":v.k}"></span><div style="min-width:0"><div class="ti">${v.k==="charge"?"Charge":v.k==="idle"||v.k==="stay"?"Parked":v.k==="repark"?"Car moved a few metres":"Arrived"}</div><div class="st">${dur(v.min)}${v.k==="stay"&&v.reparks?" · "+v.reparks+" re-park"+(v.reparks>1?"s":""):""}${v.frm&&v.k==="drive"?" · from "+esc(v.frm):""}${v.parts&&v.parts.length>1?" · "+v.parts.length+" parts (Tessie recorded "+v.parts.length+" sessions)":""}</div></div><div class="nums">${v.k==="charge"?`<span>+${n0(v.kwh,1)}<small>kWh</small></span><span>${money(v.cost)}<small>cost</small></span>`:v.k==="drive"?`<span>${n0(v.km,1)}<small>km</small></span>`:v.k==="repark"?`<span>${n0(v.km*1000)}<small>m</small></span>`:v.k==="stay"?`<span>${v.reparks||0}<small>re-parks</small></span>`:`<span>${n0(v.kwh,2)}<small>kWh</small></span>`}</div></a>`+(v.parts&&v.parts.length>1?v.parts.map((x,i)=>`<a class="row nest" href="#/charge/${x.id}"><span class="t">${x.day} ${x.hm}</span><span class="k charge"></span><div style="min-width:0"><div class="st">↳ part ${i+1} · ${dur(x.min)} · ${n0(x.b0)}→${n0(x.b1)}%</div></div><div class="nums"><span>+${n0(x.kwh,1)}<small>kWh</small></span><span>${money(x.cost)}<small>cost</small></span></div></a>`).join(""):"")}).join(""):'<div class="empty">No recorded visits.</div>';
  if(p.lat==null){$("#osm").innerHTML='<div class="sub">Needs a location.</div>';return}
  osmBlock($("#osm"),p);
}
const OSM_FIRST="Supermarkets & Groceries";   // listed first: what the car is usually parked for
// a name taken from OpenStreetMap ("Ampol") is made specific with the suburb and state of the spot ("Ampol, Ermington, NSW")
const specific=(nm,p)=>nm&&p.suburb&&!nm.toLowerCase().includes(p.suburb.toLowerCase())?[nm,p.suburb,p.state].filter(Boolean).join(", "):nm;
async function osmBlock(host,p){
  const ok=p.key[0]==="@"?p.key+"|"+(+p.lat).toFixed(5)+","+(+p.lon).toFixed(5):p.key;   // an unsaved spot is keyed @new / @lat,lon: the lookup is kept per position, never per key alone
  let R=OSMG[ok+"r"]||300,q="";
  const load=async refresh=>{host.querySelector("#ol")&&(host.querySelector("#ol").innerHTML='<div class="sub">Asking OpenStreetMap…</div>');
    try{const r=await fetch(`/api/place-osm?key=${encodeURIComponent(p.key)}&lat=${p.lat}&lon=${p.lon}&r=${R}${refresh?"&refresh=1":""}`),j=await r.json();OSMG[ok]=j;paint()}catch(e){host.innerHTML='<div class="sub">Could not load: '+esc(e)+'</div>'}};
  const paint=()=>{const j=OSMG[ok]||{items:[]},its=j.items.filter(i=>!q||(i.name+" "+i.group+" "+i.category).toLowerCase().includes(q.toLowerCase())),by={};its.forEach(i=>(by[i.group]=by[i.group]||[]).push(i));
    host.innerHTML=`<div class="bar"><select id="orad">${[100,300,500,1000].map(v=>`<option value="${v}" ${v===R?"selected":""}>within ${v} m</option>`).join("")}</select><input type="search" id="oq" placeholder="Filter these…" value="${esc(q)}"><button class="btn" id="orf">Refresh from OpenStreetMap</button><span class="sub">${its.length} places · ${esc(j.source||"")}${j.fetched?" · "+esc(String(j.fetched).slice(0,10)):""}</span></div>
     <div id="ol">${its.length?Object.keys(by).sort((a,b)=>(b===OSM_FIRST)-(a===OSM_FIRST)||a.localeCompare(b)).map(g=>`<div class="og"><h3>${esc(by[g][0].emoji||"")} ${esc(g)} <span>${by[g].length}</span></h3>${by[g].map(i=>`<div class="oi"><span class="on">${esc(i.name)}</span><span class="sub">${esc(i.category&&i.category!==g?i.category:"")}</span><span class="od">${i.dist_m==null?"":n0(i.dist_m)+" m"}</span>${i.lat!=null?`<a href="https://www.openstreetmap.org/?mlat=${i.lat}&mlon=${i.lon}#map=19/${i.lat}/${i.lon}" target="_blank">map</a><a href="#" data-sv="${esc(i.name)}|${i.lat}|${i.lon}|${esc(i.addr||"")}">save as place</a>`:""}</div>`).join("")}</div>`).join("")
      :(j.error?errBox(j.error,"OpenStreetMap did not answer. This is the server being busy, not an empty street: press Refresh from OpenStreetMap to try again."):'<div class="sub">Nothing found within this radius. Try a wider radius or Refresh.</div>')}</div>`;
    $("#orad").onchange=e=>{R=+e.target.value;OSMG[ok+"r"]=R;load(false)};$("#oq").oninput=e=>{q=e.target.value;const pos=e.target.selectionStart;paint();const o=$("#oq");o.focus();o.setSelectionRange(pos,pos)};$("#orf").onclick=()=>load(true);
    $$("[data-sv]",host).forEach(a=>a.onclick=ev=>{ev.preventDefault();const [name,la,lo,ad]=a.dataset.sv.split("|"),pts=[[p.lat,p.lon],...(p.pts||[])].filter(q=>q[0]!=null),far=Math.max(0,...pts.map(q=>hav(+la,+lo,q[0],q[1]))),rad=Math.min(5000,Math.max(cfg("places.save_radius_min_m"),Math.ceil((far+cfg("places.save_radius_margin_m"))/10)*10));sessionStorage.setItem("placePrefill",JSON.stringify({name,addr:ad||"",lat:+la,lon:+lo,radius_m:rad,radnote:far>0?"Radius reaches "+n0(far)+" m, to include where the car recorded stopping ("+pts.length+" recorded point"+(pts.length>1?"s":"")+" within "+cfg("places.save_radius_cluster_m")+" m)":""}));location.hash="#/place/"+encodeURIComponent("@"+(+la).toFixed(5)+","+(+lo).toFixed(5))})};
  load(false);
}

// ---------- Battery health (the same two charts as analyse_battery_health.py: capacity and range against odometer, least-squares trend)
function scatter(host,pts,key,unit,col,dec){
  const W=900,H=360,L=58,R=16,T=12,B=40,xs=pts.map(p=>p.odo),ys=pts.map(p=>p[key]);
  if(pts.length<2){host.innerHTML='<div class="sub">Not enough readings with an odometer yet.</div>';return}
  const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys),py=(y1-y0)*.12||1,lo=y0-py,hi=y1+py,X=v=>L+(v-x0)/((x1-x0)||1)*(W-L-R),Y=v=>T+(hi-v)/(hi-lo)*(H-T-B);
  const n=pts.length,mx=xs.reduce((a,v)=>a+v,0)/n,my=ys.reduce((a,v)=>a+v,0)/n,sxx=xs.reduce((a,v)=>a+(v-mx)**2,0),m=sxx?xs.reduce((a,v,i)=>a+(v-mx)*(ys[i]-my),0)/sxx:0,c=my-m*mx;
  let g="";for(let i=0;i<=5;i++){const v=lo+(hi-lo)*i/5;g+=`<line class="grid" x1="${L}" x2="${W-R}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${L-6}" y="${Y(v)+4}" text-anchor="end">${n0(v,dec)}</text>`}
  for(let i=0;i<=6;i++){const v=x0+(x1-x0)*i/6;g+=`<text x="${X(v)}" y="${H-B+16}" text-anchor="middle">${n0(v)}</text>`}
  host.innerHTML=`<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">${g}<text x="${(L+W-R)/2}" y="${H-4}" text-anchor="middle">Odometer (km)</text>
   <line x1="${X(x0)}" y1="${Y(m*x0+c)}" x2="${X(x1)}" y2="${Y(m*x1+c)}" stroke="${col}" stroke-width="2" stroke-dasharray="5 3" opacity=".6"/>
   ${pts.map((p,i)=>`<circle cx="${X(p.odo)}" cy="${Y(p[key])}" r="4.5" fill="${col}" stroke="var(--card)" stroke-width="1.2"/><circle class="hit" data-i="${i}" cx="${X(p.odo)}" cy="${Y(p[key])}" r="11" fill="transparent"/>`).join("")}</svg>`;
  $$(".hit",host).forEach(c=>{const p=pts[+c.dataset.i];c.onmousemove=ev=>showTip(ev,`<b>${n0(p.odo)} km</b> · ${p.t.slice(0,10)}<br>${n0(p[key],dec)} ${unit}`);c.onmouseleave=hideTip});
  const d=ys[n-1]-ys[0];return `${n0(ys[0],dec)} → ${n0(ys[n-1],dec)} ${unit} (${d>=0?"+":""}${n0(d,dec)} ${unit}) across ${n} readings; trend ${m>=0?"+":""}${n0(m*10000,dec)} ${unit} per 10,000 km`;
}
function healthInto(host,b){          // the health charts, drawn under the Battery page's charge-level chart
  const r=b.readings,sp=b.spec.battery||{},orig=sp.original_capacity_kwh,last=r[r.length-1],lastOdo=[...r].reverse().find(x=>x.odo!=null);
  const pts=r.filter(x=>x.odo!=null).map(x=>({odo:x.odo,cap:x.kwh,rng:x.range,t:x.t})).sort((a,c)=>a.odo-c.odo),rp=pts.filter(p=>p.rng!=null);
  const rec=r.slice(-7),avg=rec.length?rec.reduce((a,x)=>a+x.kwh,0)/rec.length:null;
  host.innerHTML=`<h2 style="margin:26px 0 4px">Battery health</h2><div class="sub">From Tessie: usable capacity and rated range against odometer, ${r.length} readings (${pts.length} with an odometer). The dashed line is the least-squares trend; single readings wobble by about ±1 kWh.</div>
   <div class="tiles">${tile(last?n0(last.kwh,1)+" kWh":"–","latest capacity"+(last?" ("+last.t.slice(0,10)+")":""))}${tile(orig?n0(orig,1)+" kWh":"–","when new (reference)")}${tile(avg&&orig?n0(100*avg/orig,1)+"%":"–","capacity vs new (last 7 readings)")}${tile(last?n0(last.range)+" km":"–","max rated range")}${tile(lastOdo?n0(lastOdo.odo)+" km":"–","odometer")}${tile(sp.chemistry||"–","chemistry")}</div>
   <div class="card"><h2 style="margin-top:0">Usable capacity vs odometer</h2><div class="sub" id="s1"></div><div id="c1"></div></div>
   <div class="card" style="margin-top:14px"><h2 style="margin-top:0">Max rated range vs odometer</h2><div class="sub" id="s2"></div><div id="c2"></div></div>`;
  const a1=scatter($("#c1"),pts.map(p=>Object.assign({},p)),"cap","kWh","#3b82f6",2),a2=scatter($("#c2"),rp,"rng","km","#16a34a",0);
  if(a1)$("#s1").textContent=a1;if(a2)$("#s2").textContent=a2;
}

async function pageHealth(){location.replace("#/battery")}      // the health charts moved onto the Battery page; an old link lands there
// ---------- Battery (Tessie's Battery page: where the charge level is now and how it moved, and below it the battery health)
let BP={r:{p:"30"}};
async function pageBattery(){
  loading();const act=await api("activity"),b=await api("battery"),hr=b.readings,lastH=hr[hr.length-1];
  const T1=x=>x.t1||x.t0+(x.min||0)*60,seg=act.filter(x=>x.b0!=null&&x.b1!=null).map(x=>({x,k:x.k,t0:x.t0,t1:Math.max(T1(x),x.t0),b0:x.b0,b1:x.b1})).sort((a,c)=>a.t0-c.t0);
  if(!seg.length){main.innerHTML='<h1>Battery</h1><div class="empty">No battery levels in the drives, charges or parked periods yet.</div>';return}
  const now=seg.reduce((a,s)=>s.t1>a.t1?s:a,seg[0]),cap=lastH?lastH.kwh:null,rng=lastH?lastH.range:null,d30=Date.now()/1000-30*86400;
  const id=act.filter(x=>x.k==="idle"&&x.t0>d30&&x.min>=30),hrs=id.reduce((a,x)=>a+x.min/60,0),drain=hrs?id.reduce((a,x)=>a+(x.kwh||0),0)*1000/hrs:null,pdrop=hrs?id.reduce((a,x)=>a+Math.max(0,(x.b0||0)-(x.b1||0)),0)/(hrs/24):null;
  const ch=act.filter(x=>x.k==="charge"&&x.b1!=null).slice(0,20).map(x=>x.b1).sort((a,c)=>a-c),lim=ch.length?ch[Math.floor(ch.length/2)]:null,lc=act.find(x=>x.k==="charge"&&(x.kwh||0)>=0.5);
  const lo=seg.filter(s=>s.t0>d30).reduce((a,s)=>Math.min(a,s.b0,s.b1),100);
  main.innerHTML=`<h1>Battery</h1><div class="sub">Charge level over time from your drives, charges and parked periods. As at ${new Date(now.t1*1000).toLocaleString("en-AU",{dateStyle:"medium",timeStyle:"short"})} (the end of the latest period), so it is the last known level, not live. Below it: how the capacity and range have changed over the years.</div>
   <div class="tiles">${tile(n0(now.b1)+"%","battery level")}${tile(rng?n0(rng*now.b1/100)+" km":"–","rated range (estimated)")}${tile(cap?n0(cap*now.b1/100,1)+" kWh":"–","energy in the pack (estimated)")}${tile(drain!=null?n0(drain)+" Wh/h":"–","parked drain, last 30 days")}${tile(pdrop!=null?n0(pdrop,1)+"% / day":"–","parked loss, last 30 days")}${tile(lim!=null?n0(lim)+"%":"–","usual charge target (median of last 20)")}${tile(lc?n0(lc.b0)+"% → "+n0(lc.b1)+"%":"–","last charge"+(lc?" ("+lc.day+")":""))}${tile(lo<100?n0(lo)+"%":"–","lowest level, last 30 days")}</div>
   <div class="bar"><span id="rg"></span><span class="sub"><b style="color:#16a34a">━</b> charging &nbsp; <b style="color:#3b82f6">━</b> driving &nbsp; <b style="color:#94a3b8">━</b> parked</span></div>
   <div class="card"><div id="bc"></div></div><div id="bh"></div>`;
  const col={charge:"#16a34a",drive:"#3b82f6",idle:"#94a3b8"},kn={charge:"Charging",drive:"Driving",idle:"Parked"};
  const draw=()=>{const ss=seg.filter(s=>inRange(s.x,BP.r)),cut=0;if(!ss.length){$("#bc").innerHTML='<div class="sub">No periods in this range.</div>';return}
    const W=900,H=320,L=44,R=14,T=12,B=34,x0=Math.max(cut,ss[0].t0),x1=ss[ss.length-1].t1,X=t=>L+(t-x0)/((x1-x0)||1)*(W-L-R),Y=v=>T+(100-v)/100*(H-T-B);
    let g="";for(let v=0;v<=100;v+=20)g+=`<line class="grid" x1="${L}" x2="${W-R}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${L-6}" y="${Y(v)+4}" text-anchor="end">${v}%</text>`;
    const span=(x1-x0)/86400,nt=6;for(let i=0;i<=nt;i++){const t=x0+(x1-x0)*i/nt,d=new Date(t*1000);g+=`<text x="${X(t)}" y="${H-B+16}" text-anchor="middle">${d.toLocaleDateString("en-AU",span>400?{month:"short",year:"2-digit"}:span>3?{day:"numeric",month:"short"}:{hour:"2-digit",minute:"2-digit"})}</text>`}
    let path="",prev=null;ss.forEach(s=>{if(prev&&s.t0>=prev.t1)path+=`<line x1="${X(Math.max(prev.t1,x0))}" y1="${Y(prev.b1)}" x2="${X(Math.max(s.t0,x0))}" y2="${Y(s.b0)}" stroke="#94a3b8" stroke-width="1" opacity=".35" stroke-dasharray="2 3"/>`;prev=s});
    const lines=ss.map((s,i)=>{const ta=Math.max(s.t0,x0),tb=Math.max(s.t1,x0);return `<line x1="${X(ta)}" y1="${Y(s.b0)}" x2="${X(tb)}" y2="${Y(s.b1)}" stroke="${col[s.k]}" stroke-width="2.4" stroke-linecap="round"/><line class="hit" data-i="${i}" x1="${X(ta)}" y1="${Y(s.b0)}" x2="${X(tb)}" y2="${Y(s.b1)}" stroke="transparent" stroke-width="14"/>`}).join("");
    $("#bc").innerHTML=`<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">${g}${path}${lines}</svg>`;
    $$(".hit",$("#bc")).forEach(l=>{const s=ss[+l.dataset.i],x=s.x;l.style.cursor="pointer";l.onmousemove=ev=>showTip(ev,`<b>${kn[s.k]}</b> · ${x.day} ${x.hm||""}<br>${n0(s.b0)}% → ${n0(s.b1)}%${x.min?" in "+dur(x.min):""}<br>${esc(s.k==="drive"?driveTitle(x):place(x))}`);l.onmouseleave=hideTip;l.onclick=()=>{location.hash="#/"+(s.k==="idle"?"parked":s.k+"s")+"/"+encodeURIComponent(x.id)}})};
  rangePicker($("#rg"),BP.r,daysOf(seg.map(s=>s.x)),draw);draw();
  healthInto($("#bh"),b);
}

// ---------- Analytics
let AM="km";
const METRICS={km:["Distance","km",0],kwh:["Energy used driving","kWh",0],whkm:["Efficiency","Wh/km",0],ap_pct:["Self-driving","% of distance",0],added:["Energy charged","kWh",0],cost:["Charging cost","$",0],cost_km:["Cost per 100 km","$",2],drives:["Drives","",0],idle_kwh:["Parked drain","kWh",1],max:["Top speed","km/h",0]};
async function pageAnalytics(){
  loading();const m=await api("analytics"),rows=m.slice(-36).map(x=>({...x,whkm:x.whkm==null||EFWH()?x.whkm:x.whkm/10}));METRICS.whkm=["Efficiency",EFU(),EFWH()?0:1];
  const draw=()=>{const [t,u,dec]=METRICS[AM],vals=rows.map(x=>x[AM]),mx=Math.max(...vals.map(v=>v||0),1e-9);
    $("#bars").innerHTML=`<div class="bars">${rows.map(x=>`<div style="height:${Math.max(2,100*(x[AM]||0)/mx)}%" data-m="${x.month}"><span>${x.month.slice(5)}${x.month.endsWith("-01")?"<br>"+x.month.slice(0,4):""}</span></div>`).join("")}</div><div style="height:30px"></div>`;
    $$("#bars .bars div").forEach(b=>{const x=rows.find(r=>r.month===b.dataset.m);b.onmousemove=ev=>showTip(ev,`<b>${x.month}</b><br>${t}: ${x[AM]==null?"–":n0(x[AM],dec)+" "+u}`);b.onmouseleave=hideTip});
    $$(".chip").forEach(c=>c.classList.toggle("on",c.dataset.m===AM))};
  const tot=f=>m.reduce((a,x)=>a+(x[f]||0),0),km=tot("km");
  main.innerHTML=`<h1>Analytics</h1><div class="sub">By month, from the drive, charge and idle records.</div>
   <div class="tiles">${tile(n0(km)+" km","total distance")}${tile(n0(tot("drives")),"drives")}${tile(km?EF(tot("kwh")*1000/km)+" "+EFU():"–","lifetime efficiency")}${tile(n0(tot("added"))+" kWh","charged")}${tile(money(tot("cost")),"charging cost")}${tile(km?n0(100*tot("ap")/km)+"%":"–","self-driving")}</div>
   <div class="bar">${Object.entries(METRICS).map(([k,v])=>`<button class="chip" data-m="${k}">${v[0]}</button>`).join("")}</div><div class="card" id="bars"></div><h2>Month by month</h2><div id="tb"></div>`;
  $$(".chip").forEach(c=>c.onclick=()=>{AM=c.dataset.m;draw()});draw();
  table($("#tb"),[{k:"month",t:"Month",l:1},{k:"drives",t:"Drives"},{k:"km",t:"km",f:r=>n0(r.km)},{k:"whkm",t:EFU(),f:r=>EF(r.whkm)},{k:"ap_pct",t:"Self-driving",f:r=>r.ap_pct==null?"–":n0(r.ap_pct)+"%"},{k:"charges",t:"Charges"},{k:"added",t:"kWh added",f:r=>n0(r.added)},{k:"home",t:"Home kWh",f:r=>n0(r.home)},{k:"super",t:"Fast kWh",f:r=>n0(r.super)},{k:"cost",t:"Cost",f:r=>money(r.cost)},{k:"cost_km",t:"$/100 km",f:r=>r.cost_km==null?"–":money(r.cost_km)},{k:"idle_kwh",t:"Parked kWh",f:r=>n0(r.idle_kwh,1)}],m,()=>{},"month");
}

// ---------- Alerts (Tessie firmware alerts in plain English)
const SEV={safety:["Safety","var(--bad)"],attention:["Check","var(--warn)"],notice:["Notice","var(--acc)"],info:["Routine","var(--mut)"]};
const sevDot=v=>`<span class="k" style="background:${SEV[v][1]}" title="${SEV[v][0]}"></span>`;
const ctxText=c=>!c?"No drive, charge or parked record at that time":c.k==="drive"?"While driving: "+(c.label||"a drive"):c.k==="charge"?"While charging"+(c.label?" at "+c.label:""):"While parked"+(c.label?" at "+c.label:"");
const ctxHref=c=>!c||!c.id?"":c.k==="drive"?"#/drive/"+c.id:c.k==="charge"?"#/charge/"+c.id:"#/parked/"+c.id;
const alertRow=(e,open)=>`<div class="al ${open?"open":""}" data-id="${esc(e.id)}"><div class="alh"><span class="t">${e.hm}</span>${sevDot(e.severity)}<div style="min-width:0"><div class="ti">${esc(e.title)}${e.n>1?` <span class="sub">× ${e.n}</span>`:""}</div><div class="st">${esc(e.what)}</div></div><span class="pill">${esc(e.category)}</span></div>
  <div class="alx"><div>${esc(e.what)}</div>${e.advice?`<div class="sub" style="margin-top:4px"><b>What to do:</b> ${esc(e.advice)}</div>`:""}${e.screen?`<div class="sub" style="margin-top:4px">The car showed: “${esc(e.screen)}”</div>`:""}
   <div class="sub" style="margin-top:6px">${ctxHref(e.ctx)?`<a href="${ctxHref(e.ctx)}">${esc(ctxText(e.ctx))}</a>`:esc(ctxText(e.ctx))}${e.n>1?" · repeated "+e.n+" times over "+dur((e.t1-e.t0)/60):""} · Tesla code <code>${esc(e.name)}</code>${e.rate?" · about "+esc(String(e.rate).replace(/%?$/,"%"))+" of the fleet sees it":""}</div></div></div>`;
async function alertsFor(k,id){const A=await api("alerts");return A.episodes.filter(e=>e.ctx&&e.ctx.k===k&&e.ctx.id===id).sort((a,b)=>b.t0-a.t0)}
function alertsCard(es,what){if(!es.length)return"";const w=es.filter(e=>e.severity==="safety"||e.severity==="attention").length;
  return `<div class="card" style="margin:14px 0"><h2 style="margin-top:0">Alerts during this ${what}</h2><div class="sub">${es.length} alert${es.length>1?"s":""}${w?", "+w+" worth a look":""}. Tap one for what it means.</div><div class="als">${es.map(e=>alertRow(e)).join("")}</div></div>`}
function wireAlerts(host){$$(".alh",host).forEach(h=>h.onclick=()=>h.parentNode.classList.toggle("open"))}
let ALP={tab:"timeline",sev:{safety:true,attention:true,notice:true,info:false},cat:"all",q:"",days:30};
async function pageAlerts(){
  loading();const A=await api("alerts"),cats=[...new Set(A.episodes.map(e=>e.category))].sort();
  const draw=()=>{const q=ALP.q.toLowerCase();
    if(ALP.tab==="types"){$("#tb").innerHTML=`<table><thead><tr><th class="l">Alert</th><th class="l">What it means</th><th>Seen</th><th>Last</th><th>Fleet</th></tr></thead><tbody>${A.types.filter(t=>(ALP.cat==="all"||t.category===ALP.cat)&&(!q||(t.title+t.what+t.name).toLowerCase().includes(q))).map(t=>`<tr><td class="l" style="white-space:normal;min-width:220px">${sevDot(t.severity)} <b>${esc(t.title)}</b><div class="sub">${esc(t.category)} · <code>${esc(t.name)}</code></div></td><td class="l" style="white-space:normal;min-width:320px">${esc(t.what)}${t.advice?`<div class="sub"><b>What to do:</b> ${esc(t.advice)}</div>`:""}</td><td>${t.episodes}</td><td>${new Date(t.last*1000).toISOString().slice(0,10)}</td><td>${t.rate?esc(String(t.rate).replace(/%?$/,"%")):"–"}</td></tr>`).join("")}</tbody></table>`;return}
    const rs=A.episodes.filter(e=>ALP.sev[e.severity]&&(ALP.cat==="all"||e.category===ALP.cat)&&(!q||(e.title+e.what+e.name+ctxText(e.ctx)).toLowerCase().includes(q))),by=new Map();
    rs.sort((a,b)=>b.t0-a.t0);rs.forEach(e=>{if(!by.has(e.day))by.set(e.day,[]);by.get(e.day).push(e)});const days=[...by.keys()].slice(0,ALP.days);
    $("#tb").innerHTML=days.map(d=>`<div class="day">${dayName(d)}<small>${by.get(d).length} alert${by.get(d).length>1?"s":""}</small></div><div class="als">${by.get(d).map(e=>alertRow(e)).join("")}</div>`).join("")||'<div class="empty">Nothing matches.</div>';
    if(days.length<by.size)$("#tb").insertAdjacentHTML("beforeend",'<div class="bar"><button class="btn" id="more">Show 30 more days</button></div>'),$("#more").onclick=()=>{ALP.days+=30;draw()};
    wireAlerts($("#tb"))};
  const n=s=>A.episodes.filter(e=>e.severity===s).length,top=A.types.slice().sort((a,b)=>b.episodes-a.episodes)[0];
  main.innerHTML=`<h1>Alerts</h1><div class="sub">Tessie firmware alerts in plain English, tied to the drive, charge or parked period they happened in. ${A.events.toLocaleString()} alerts, shown as ${A.episodes.length.toLocaleString()} episodes (the same alert repeating within 10 minutes counts once), ${A.first?new Date(A.first*1000).toISOString().slice(0,10)+" to "+new Date(A.last*1000).toISOString().slice(0,10):""}.</div>
   <div class="tiles">${tile(n("safety"),"safety system acted / door open in gear")}${tile(n("attention"),"features unavailable or faulty")}${tile(n("notice"),"notices")}${tile(n("info"),"routine")}${top?tile(top.episodes+" ×","most common: "+esc(top.title)):""}</div>
   <div class="bar"><button class="chip" data-t="timeline">Timeline</button><button class="chip" data-t="types">What the alerts mean</button>
   ${Object.keys(SEV).map(k=>`<button class="chip sevc" data-s="${k}">${SEV[k][0]}</button>`).join("")}
   <select id="cat"><option value="all">All categories</option>${cats.map(c=>`<option>${esc(c)}</option>`).join("")}</select><input type="search" id="q" placeholder="Search…" value="${esc(ALP.q)}"></div><div id="tb"></div>`;
  const chips=()=>{$$("[data-t]").forEach(c=>c.classList.toggle("on",c.dataset.t===ALP.tab));$$(".sevc").forEach(c=>{c.classList.toggle("on",ALP.sev[c.dataset.s]);c.style.display=ALP.tab==="timeline"?"":"none"})};
  $$("[data-t]").forEach(c=>c.onclick=()=>{ALP.tab=c.dataset.t;chips();draw()});$$(".sevc").forEach(c=>c.onclick=()=>{ALP.sev[c.dataset.s]=!ALP.sev[c.dataset.s];chips();draw()});
  $("#cat").value=ALP.cat;$("#cat").onchange=e=>{ALP.cat=e.target.value;draw()};$("#q").oninput=e=>{ALP.q=e.target.value;draw()};chips();draw();
}

// A thumbnail taken from the video itself (first frame of a clip), loaded only when it scrolls into view - Recent clips have no thumb.png
const VT=window.IntersectionObserver?new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){const v=e.target;VT.unobserve(v);v.src=v.dataset.src+"#t="+(v.dataset.t||2)}}),{rootMargin:"300px"}):null;
const vthumb=(url,t)=>`<video class="vt" muted playsinline preload="metadata" data-src="${esc(url)}" data-t="${t||2}" onerror="this.outerHTML='<div class=\\'noimg\\'></div>'"></video>`;
const lazyThumbs=host=>$$("video.vt",host).forEach(v=>{if(VT)VT.observe(v);else v.src=v.dataset.src+"#t="+(v.dataset.t||2)});
// ---------- TeslaCam: the clips on any connected drive, and one continuous timeline built from the files alone
let TCS={role:null,kind:"clips",q:"",r:{p:"all"},src:{sentry:1,saved:1,recent:1}},TLOFF=null;
const SWRSEEN=new Set(),FRESH={};
const tcGet=async p=>{if(p!=="drivelist"&&p!=="clips")return fetchJ(p);       // these two are remembered; the first read in a visit comes from the copy, later reads (polling, Refresh) are fresh
  if(FRESH[p]){const v=FRESH[p];delete FRESH[p];return v}
  if(SWRSEEN.has(p))return fetchJ(p);SWRSEEN.add(p);
  const k=await mkey("tc/"+p),old=await IDB.get(k);
  if(old===undefined){const n=await fetchJ(p);IDB.set(k,n);return n}
  fetchJ(p).then(n=>{IDB.set(k,n);if(!same(n,old)){FRESH[p]=n;redrawQuietly()}}).catch(()=>{});return old};
const tcPost=async(p,b)=>(await fetch("/api/"+p,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)})).json();
const TCF={sentry:"SentryClips",saved:"SavedClips"},TCSRC={sentry:"Sentry",saved:"Saved",recent:"Recent"};
const isoMs=t=>Date.parse(t),mins=(a,b)=>Math.round((isoMs(b)-isoMs(a))/60000);
const clockHM=t=>t.slice(11,16);
// the saved place (or nothing) a position is inside
function nameAt(places,p){if(!p||p[0]==null)return "";let best="",bd=1e9;for(const x of places||[]){if(x.lat==null)continue;const d=hav(p[0],p[1],x.lat,x.lon);if(d<=Math.max(x.radius||100,60)&&d<bd){bd=d;best=x.name}}return best}
// ---------- the TeslaCam timeline: for each day a map on the left, the day's drives, Sentry events and parked spells (tick boxes) on the right
// Ticking a row shows it on that day's map: a drive as its route (speed gradient or who was driving), a Sentry event as a red dot, a parked spell as a 🅿️.
// A drive row still opens the drive when clicked; a Sentry row plays its clips. A drive shorter than the re-park limits (settings app.drives) counts as parked.
const TLR={},TLF={},TLS={on:{},sel:"none",range:null,mode:"v"};let TLQ=Promise.resolve();      // sel: what a day starts with (none / drives / all), kept while moving between days; on: rows ticked or unticked by hand
function tlClass(v){
  const rp=v.kind==="drive"&&v.km<cfg("drives.repark_max_km")&&(v.t1-v.t0)/60<cfg("drives.repark_max_minutes");
  return v.kind==="drive"&&!rp?"drive":v.kind==="charge"?"charge":v.kind==="sentry"?"sentry":(v.kind==="parked"||v.kind==="stopped"||rp)?"parked":v.kind==="recording"?"rec":null}
// where a parked spell was: its own position, else where the drive before it ended (or the next one began)
function tlPos(items){
  const o=items.slice().sort((a,b)=>a.t0-b.t0);
  o.forEach((v,i)=>{let p=v.p0&&v.p0[0]!=null?v.p0:v.p1&&v.p1[0]!=null?v.p1:null;
    for(let k=i-1;!p&&k>=0&&i-k<4;k--)if(o[k].p1&&o[k].p1[0]!=null)p=o[k].p1;
    for(let k=i+1;!p&&k<o.length&&k-i<4;k++)if(o[k].p0&&o[k].p0[0]!=null)p=o[k].p0;
    v._pos=p})}
async function tlRoute(role,id,v){
  if(TLR[id]!==undefined)return TLR[id];
  try{
    if(role==="auto"&&v){      // the Drives list: the telemetry kept for the drive's time window (Tessie's, or the dashcam's own for a dashcam-only drive); nothing is read from a drive
      const d=await tcGet("teslacam/auto/window?t0="+v.t0+"&t1="+v.t1+(v.lo!=null?"&lo="+v.lo:"")+(v.src&&v.src!=="cam"?"&id="+encodeURIComponent(v.id):"")+"&budget=0");TLR[id]=d&&d.series?{S:d.series,start:d.item.start,u0:d.item.t0,lo:d.item.lo}:null;return TLR[id]}
    const d=await tcGet("teslacam/"+role+"/seg/"+encodeURIComponent(id)+"?budget=0");TLR[id]=d&&d.series?{S:d.series,start:d.item.start,u0:d.item.t0,lo:d.item.lo}:null;if(d&&d.pending)tlFill(role,id,d.pending)}catch(e){TLR[id]=null}
  return TLR[id]}
// minutes of a drive not read yet leave holes in its line: read them in the background, one drive at a time, and tell the maps after each pass
function tlFill(role,id,left){
  if(TLF[id])return;TLF[id]=1;
  TLQ=TLQ.then(async()=>{for(let k=0;k<40&&left>0;k++){try{const d=await tcGet("teslacam/"+role+"/seg/"+encodeURIComponent(id)+"?budget=15");if(!d||!d.series)break;TLR[id]={S:d.series,start:d.item.start,u0:d.item.t0,lo:d.item.lo};dispatchEvent(new CustomEvent("tlroute",{detail:id}));if(!d.pending||d.pending>=left)break;left=d.pending}catch(e){break}}})}
// "Read telemetry" on a recording: reads its minutes from the connected dashcam drive here and now (15 s at a time, with what is left shown on the button), then lists again
async function tlRead(b,role,id){
  b.disabled=true;let left=null;
  try{for(let k=0;k<40;k++){b.textContent=left?"Reading… "+left+" left":"Reading…";
      const d=await (await fetch("/api/teslacam/"+role+"/seg/"+encodeURIComponent(id)+"?budget=15")).json();      // not fetchJ: a 404 carries the reason
      if(!d||d.error)throw new Error((d&&d.error)||"Could not read it");
      if(!d.pending||(left!=null&&d.pending>=left))break;left=d.pending}}
  catch(e){b.disabled=false;b.textContent=/not connected/i.test(e.message)?"Drive not connected":"Try again";b.title=e.message;return}
  bust("tc/drivelist");bust("activity");route()}
// one map: routes [{id,S,start}] and marks [{id,type,pos,title}]
function dayMap(el,role,hooks){
  const dark=matchMedia("(prefers-color-scheme: dark)").matches,m=newMap({container:el,style:MAPSTYLE(dark),center:[151.2,-33.8],zoom:8,attributionControl:{compact:true}});
  let ready=false,pend=null,pts=[],mk=[],bounds=null,lastKey="",lastMarks=[];
  const fit=()=>{if(bounds)m.fitBounds(bounds,{padding:40,maxZoom:15,duration:400})};
  mapNav(m,fit,"Show everything ticked");
  const hint=document.createElement("div");hint.className="tmempty";hint.textContent="Tick a drive, a Sentry event or a parked spell to see it here.";el.appendChild(hint);
  const pop=new maplibregl.Popup({closeButton:false,closeOnClick:false,offset:12,className:"rp"});
  const draw=()=>{if(!ready||!pend)return;const {routes,marks}=pend;lastMarks=marks;const F=[],key=routes.map(r=>r.id).join()+"|"+marks.map(k=>k.id).join(),refit=key!==lastKey;lastKey=key;pts=[];bounds=new maplibregl.LngLatBounds();let any=false;
    routes.forEach(r=>{const S=r.S,n=S.v.length,step=Math.max(1,Math.ceil(n/1500));let prev=-1,km=0;
      for(let i=0;i<n;i+=step){km+=(S.v[i]||0)*step/3600;if(S.lat[i]==null||Math.abs(S.lat[i])<1)continue;const q={x:S.lon[i],y:S.lat[i],v:S.v[i]||0,ap:S.ap[i]||0,u:r.u0+i,lo:r.lo,km,id:r.id};pts.push(q);bounds.extend([q.x,q.y]);any=true;
        if(prev>=0&&i-prev<=Math.max(5*step,600)){const a=pts[pts.length-2];F.push({type:"Feature",geometry:{type:"LineString",coordinates:[[a.x,a.y],[q.x,q.y]]},properties:{v:q.v,ap:q.ap,id:r.id}})}prev=i}});
    m.getSource("rt").setData({type:"FeatureCollection",features:F});
    mk.forEach(x=>x.remove());mk=[];
    marks.forEach(k=>{if(!k.pos)return;const e=document.createElement("div");e.className="mk "+k.type;e.title=k.title;if(k.type==="parked")e.textContent="🅿️";if(k.type==="charge")e.textContent="⚡";e.onclick=()=>hooks.pick&&hooks.pick(k.id);
      mk.push(new maplibregl.Marker({element:e,anchor:"center"}).setLngLat([k.pos[1],k.pos[0]]).addTo(m));bounds.extend([k.pos[1],k.pos[0]]);any=true});
    hint.style.display=any?"none":"";if(!any)bounds=null;if(refit)fit()};
  m.on("load",()=>{ready=true;m.addSource("rt",{type:"geojson",tolerance:0,data:{type:"FeatureCollection",features:[]}});
    m.addLayer({id:"rt-case",type:"line",source:"rt",layout:{"line-cap":"round","line-join":"round"},paint:{"line-color":dark?"#000":"#fff","line-width":["interpolate",["linear"],["zoom"],3,8,8,7.5,16,9]}});
    m.addLayer({id:"rt",type:"line",source:"rt",layout:{"line-cap":"round","line-join":"round"},paint:{"line-color":TLS.mode==="v"?spdExpr():apExpr(),"line-width":["interpolate",["linear"],["zoom"],3,5,8,4.5,16,6]}});
    // hovering the route reads out the moment under the pointer, like the Drives page
    let busy=false;
    m.on("mousemove",e=>{if(busy)return;busy=true;requestAnimationFrame(()=>{busy=false;let b=null,bd=22*22;for(const q of pts){const p=m.project([q.x,q.y]),d=(p.x-e.point.x)**2+(p.y-e.point.y)**2;if(d<bd){bd=d;b=q}}
      if(b){m.getCanvas().style.cursor="pointer";pop.setLngLat([b.x,b.y]).setHTML(`<b>${lclock(b.u,b.lo)}</b> &nbsp;${n0(b.v)} km/h<br>${APN[b.ap]||"Manual"} · ${n0(b.km,1)} km into the drive`).addTo(m)}else{m.getCanvas().style.cursor="";pop.remove()}})});
    m.on("mouseout",()=>pop.remove());
    m.on("click",e=>{let b=null,bd=22*22;for(const q of pts){const p=m.project([q.x,q.y]),d=(p.x-e.point.x)**2+(p.y-e.point.y)**2;if(d<bd){bd=d;b=q}}if(b&&hooks.open)hooks.open(b.id)});
    draw()});
  return{set(routes,marks){pend={routes,marks};draw()},focus(id){const b=new maplibregl.LngLatBounds();let any=false;pts.forEach(q=>{if(q.id===id){b.extend([q.x,q.y]);any=true}});lastMarks.forEach(k=>{if(k.id===id&&k.pos){b.extend([k.pos[1],k.pos[0]]);any=true}});if(any)m.fitBounds(b,{padding:70,maxZoom:16,duration:500})},mode(x){if(ready)m.setPaintProperty("rt","line-color",x==="v"?spdExpr():apExpr())},fit,destroy(){try{m.remove()}catch(e){}}}}
// the whole view: items are the server's timeline items (newest first); host is where to draw
function tlDays(host,items,o){
  items.forEach(v=>v._c=tlClass(v));tlPos(items);
  const rs=items.filter(v=>v._c),byDay={};
  // Sentry events during a park belong to that park: one row for the park, with the events counted in it (and listed on click), not a row for each
  let parks=rs.filter(v=>v._c==="parked").sort((a,b)=>a.t0-b.t0);
  rs.forEach(v=>{v._sent=null;v._in=null;v._rec=null;v._chg=null});
  // parks the dashcam timeline cut around a Sentry event are one stay: consecutive parks at one place with no drive or charge between them are joined (the first keeps the row)
  {const hard=rs.filter(v=>v._c==="drive"||v._c==="charge"),keep=[];let a=null;
    parks.forEach(b=>{const pa=a&&(a.p1||a.p0||a._pos),pb=b.p0||b.p1||b._pos,same=!pa||!pb||pa[0]==null||pb[0]==null||hav(pa[0],pa[1],pb[0],pb[1])<=150;
      if(a&&same&&b.t0-a.t1<=3600&&!hard.some(h=>h.t1>a.t1&&h.t0<b.t0)){a.t1=Math.max(a.t1,b.t1);if(b.end>a.end)a.end=b.end;if(b.ongoing)a.ongoing=true;b._in=a.id}
      else{keep.push(b);a=b}});parks=keep}
  // a charge during a park belongs to that park: one row for the stay (parked, charged, parked again) with its charges counted on the row's button;
  // a charge with no park beside it keeps its own row
  {const chs=rs.filter(v=>v._c==="charge").sort((a,b)=>a.t0-b.t0),pp=k=>k.p0||k.p1||k._pos,cp=c=>c.p0||c.p1||c._pos,
     hop=(t0,t1)=>rs.some(d=>d._c==="drive"&&d.t0>=t0&&d.t1<=t1),   // a drive between them: the car left, so they are not one stay
     same=(k,c)=>{const a=pp(k),b=cp(c);return !a||!b||a[0]==null||b[0]==null||hav(a[0],a[1],b[0],b[1])<=300};
    chs.forEach(c=>{const at=parks.filter(k=>same(k,c)),before=at.filter(k=>k.t0<=c.t0+60&&k.t1>=c.t0-600&&!hop(k.t1-30,c.t0+30)).sort((a,b)=>b.t1-a.t1)[0],
        after=at.filter(k=>k!==before&&k.t0>=c.t0-60&&k.t0<=c.t1+600&&k.t1>=c.t1-60&&!hop(c.t1-30,k.t0+30)).sort((a,b)=>a.t0-b.t0)[0],host=before||after;
      if(!host)return;
      host.t0=Math.min(host.t0,c.t0);host.t1=Math.max(host.t1,c.t1);if(c.start<host.start)host.start=c.start;if(c.end>host.end)host.end=c.end;
      if(before&&after){host.t1=Math.max(host.t1,after.t1);if(after.end>host.end)host.end=after.end;after._in=host.id;parks=parks.filter(k=>k!==after)}
      (host._chg=host._chg||[]).push(c);c._in=host.id})}
  // an event belongs to the park its trigger fell in (it can start a few minutes before the park does: the car was still going into Park); the clock text of an item is local, so the trigger is put on the epoch through the item's own offset
  const trigAt=v=>{if(!v.trig)return v.t0;const off=v.t0-Date.parse(v.start+"Z")/1000;return Date.parse(v.trig+"Z")/1000+off};
  const inPark=(t,k)=>k.t0-60<=t&&t<=(k.ongoing?Infinity:k.t1+60);
  rs.forEach(v=>{if(v._c!=="sentry")return;const p=parks.find(k=>inPark(trigAt(v),k))||parks.find(k=>inPark(v.t0,k));if(p){(p._sent=p._sent||[]).push(v);v._in=p.id}});
  // an event with no park around it (Tessie knows nothing of that time) still gets no line of its own: events a half hour apart or less are listed under one parked row made from them
  {const loose=rs.filter(v=>v._c==="sentry"&&!v._in).sort((a,b)=>a.t0-b.t0);let g=null;
    loose.forEach(v=>{if(g&&v.t0-g._last<=1800){g._sent.push(v);g._last=Math.max(g._last,v.t1);g.t1=g._last;g.end=v.end>g.end?v.end:g.end;v._in=g.id}
      else{g={id:"sp:"+v.id,kind:"parked",_c:"parked",_syn:true,src:null,start:v.start,end:v.end,t0:v.t0,t1:v.t1,_last:v.t1,p0:v.p0||null,p1:null,_pos:v._pos||v.p0||null,city:v.city,street:v.street,_sent:[v],lo:v.lo};rs.push(g);parks.push(g);v._in=g.id}});
    parks.sort((a,b)=>a.t0-b.t0)}
  // dashcam minutes that lie inside a park (the car woke for a Sentry event and recorded, nothing moved) are part of that park
  rs.forEach(v=>{if(v.kind!=="recording"&&v.kind!=="stopped")return;const p=parks.find(k=>k!==v&&k.kind==="parked"&&inPark(v.t0+30,k)&&inPark(v.t1-30,k));if(p){(p._rec=p._rec||[]).push(v);v._in=p.id}});
  parks.forEach(p=>{if(p._sent)p._sent.sort((a,b)=>a.t0-b.t0)});
  const dd=(a,b)=>Math.round((Date.parse(b+"T00:00:00Z")-Date.parse(a+"T00:00:00Z"))/864e5),plus=(d,n)=>new Date(Date.parse(d+"T00:00:00Z")+n*864e5).toISOString().slice(0,10);
  // an event that crosses midnight is listed on every day it touches (whole, with a note), and is one and the same tick on all of them
  rs.filter(v=>!v._in).forEach(v=>{v._d0=v.start.slice(0,10);v._d1=v._c==="sentry"||!v.end||v.end<v.start?v._d0:v.end.slice(0,10);for(let n=0;n<=Math.min(dd(v._d0,v._d1),400);n++){const d=plus(v._d0,n);(byDay[d]=byDay[d]||[]).push(v)}});
  if(TLS.wide===undefined){try{TLS.wide=localStorage.getItem("tlWide")==="1"}catch(e){TLS.wide=false}}if(TLS.dayDesc===undefined)TLS.dayDesc=cfg("teslacam.days_newest_first")!==false;if(TLS.rowDesc===undefined)TLS.rowDesc=cfg("teslacam.rows_newest_first")!==false;
  const allDays=Object.keys(byDay).filter(d=>!o.dayOk||o.dayOk(d)).sort().reverse(),cur=TLS.day&&allDays.includes(TLS.day)?TLS.day:allDays[0],role=o.role,places=o.places;TLS.day=cur;
  // one day at a time: a picker (newest day first by default) with arrows to the earlier / later day
  const asc=allDays.slice().reverse();
  // a range of dates (TLS.range) shows its days one under the other; otherwise the one day. A long range is drawn a block of days at a time (drives.range_days_shown, 31 by default), the end the list starts at first; "Show more days" adds the next block
  let selAsc=TLS.range?asc.filter(d=>d>=TLS.range[0]&&d<=TLS.range[1]):[];if(!selAsc.length){selAsc=cur?[cur]:[];TLS.range=null}
  const rk=TLS.range?TLS.range.join():"";if(TLS.rk!==rk){TLS.rk=rk;TLS.more=0}
  const MAXD=Math.max(1,+cfg("drives.range_days_shown")||31)+(TLS.more||0),allSel=selAsc,cut=allSel.length>MAXD;
  const shownAsc=cut?(TLS.dayDesc?allSel.slice(-MAXD):allSel.slice(0,MAXD)):allSel;
  const days=TLS.dayDesc?shownAsc.slice().reverse():shownAsc,isRange=allSel.length>1||!!TLS.range;
  selAsc=allSel;
  const i0=asc.indexOf(selAsc[0]),i1=asc.indexOf(selAsc[selAsc.length-1]),nn=selAsc.length||1,prevW=asc.slice(Math.max(0,i0-nn),Math.max(0,i0)),nextW=asc.slice(i1+1,i1+1+nn);
  const win=w=>w.length?w[0]+","+w[w.length-1]:"",nItems=days.reduce((a,d)=>a+byDay[d].length,0);
  const key=(v,d)=>d>v._d0?"00:00:00":hms(v._c==="sentry"?(v.trig||v.start):v.start),rowsOf=d=>byDay[d].slice().sort((a,b)=>{const x=key(a,d),y=key(b,d);return(x<y?-1:x>y?1:a.t0-b.t0)*(TLS.rowDesc?-1:1)});
  const canTickV=v=>v._c==="drive"||!!v._pos;
  const isOn=v=>TLS.on[v.id]!==undefined?TLS.on[v.id]:(TLS.sel==="all"?canTickV(v):TLS.sel==="drives"&&v._c==="drive");        // a hand choice wins; else what the day starts with
  const place=v=>nameAt(places,v.p0)||nameAt(places,v.p1)||nameAt(places,v._pos);
  const tm=v=>clockHM(v.start)+" – "+(v.ongoing?"now":clockHM(v.end)),mins=v=>Math.max(1,Math.round(((v.ongoing?Math.max(v.t1,Date.now()/1000):v.t1)-v.t0)/60));
  // where things were: a saved place first; else what Tessie's own record of the drive says; else (Sentry) the city and street; a drive's ends are where the stopped spells next to it were
  const TS=(o.tess||[]).map(x=>({s:x.t0,e:x.t0+x.min*60,frm:x.frm||"",to:x.to||""})).sort((a,b)=>a.s-b.s),WIN=20*60;       // UTC seconds
  const tOver=v=>{const s0=v.t0,e0=v.t1;let b=null,bo=0;for(const t of TS){const ov=Math.min(e0,t.e)-Math.max(s0,t.s);if(ov>bo){bo=ov;b=t}}return b};
  // a spot that is not a saved place, where the car has just charged: the charger's name, as the charge is stored (not the street address)
  const CH=(o.chg||[]).filter(c=>c.place&&c.lat!=null),CR=cfg("drives.charger_name_m")||300,CW=cfg("drives.charge_window_minutes")*60;
  const chNear=(c,p)=>p&&p[0]!=null&&hav(p[0],p[1],c.lat,c.lon)<=CR;
  // a charge has one drive that ends at it (the last one before) and one that leaves it (the first after); the others are named by where they really are
  const DRV=rs.filter(v=>v._c==="drive"),chTo=new Map(),chFr=new Map();
  CH.forEach(c=>{const a=DRV.filter(v=>v.t1>=c.t0-CW&&v.t1<=c.t0+300).sort((x,y)=>y.t1-x.t1)[0],b=DRV.filter(v=>v.t0>=c.t1-300&&v.t0<=c.t1+CW).sort((x,y)=>x.t0-y.t0)[0];if(a)chTo.set(c,a.id);if(b)chFr.set(c,b.id)});
  const chPick=(c,p)=>c&&(c.kind==="supercharger"||!nameAt(places,p))?c.place:"";       // a Supercharger's name always wins; another charger's only where no saved place covers the spot
  const chLeft=c=>{const id=chFr.get(c),d=id&&DRV.find(x=>x.id===id);return d?d.t0:1e18};
  const chStay=v=>{const p=v.p0||v.p1||v._pos;return chPick(CH.find(c=>c.t0<=v.t1+300&&c.t1>=v.t0-300&&v.t0<chLeft(c)&&chNear(c,p)),p)};
  const chEnd=v=>chPick(CH.find(c=>chTo.get(c)===v.id&&chNear(c,v.p1)),v.p1);
  const chStart=v=>chPick(CH.find(c=>chFr.get(c)===v.id&&chNear(c,v.p0)),v.p0);
  const stayAt=v=>{if(v._c==="parked"){const cn=chStay(v);if(cn)return cn}const n=nameAt(places,v.p0)||nameAt(places,v.p1)||nameAt(places,v._pos);if(n)return n;
    const cs=[v.city,v.street].filter(Boolean).join(" · ");if((v._c==="sentry"||v._syn)&&cs)return cs;
    const s0=v.t0,e0=v.t1,a=TS.filter(t=>t.to&&Math.abs(t.e-s0)<=WIN).sort((x,y)=>Math.abs(x.e-s0)-Math.abs(y.e-s0))[0];if(a)return a.to;
    const z=TS.filter(t=>t.frm&&Math.abs(t.s-e0)<=WIN).sort((x,y)=>Math.abs(x.s-e0)-Math.abs(y.s-e0))[0];return z?z.frm:cs};
  const chron=rs.slice().sort((a,b)=>a.t0-b.t0),stays=chron.filter(v=>v._c==="parked"||v._c==="sentry");
  const around=(v,end)=>{const t=end?v.t1:v.t0;let best=null,bd=WIN;for(const k of stays){const d=end?Math.abs(k.t0-t):Math.abs(k.t1-t);if(d<bd){const n=stayAt(k);if(n){bd=d;best=n}}}return best||""};
  const ends=v=>{const t=tOver(v);return[chStart(v)||nameAt(places,v.p0)||(t&&t.frm)||around(v,false)||"Unknown",chEnd(v)||nameAt(places,v.p1)||(t&&t.to)||around(v,true)||"Unknown"]};
  // a parked row says what the spot is: where a charge happened during the park, the charge ("Supercharger" / "Charger", opens the charging session); a saved place (opens it); else a button to save the spot.
  // Being parked at a charger is not a charge, so a charger's listing is never offered from position alone
  const CA=(o.chg||[]).filter(c=>c.t0!=null);
  const near=(list,p,f)=>{let b=null,bd=1e9;for(const x of list){if(x.lat==null||Math.abs(x.lat-p[0])>.003)continue;const d=hav(p[0],p[1],x.lat,x.lon);if(d<=f(x)&&d<=bd){bd=d;b=x}}return b};
  const placeBtn=v=>{if(v._c!=="parked"||(v.kind==="drive"&&!v._chg)||!o.sc)return "";       // a re-park (a tiny drive shown as a park) gets no place button
    const p=v.p0||v.p1||v._pos;if(!p||p[0]==null)return "";
    const cl=(v._chg||[]).slice().sort((a,b)=>a.t0-b.t0),sv=near(places,p,x=>Math.max(x.radius||100,60));
    // the charges of this stay (Tessie's sessions): one opens it; several open the first, with arrows on the charge page to go through them
    const ch1=cl.length?null:CA.find(c=>c.t0<=v.t1+300&&(c.t1||c.t0)>=v.t0-300&&v.t0<chLeft(c)&&(c.lat!=null?chNear(c,p):c.t0<=v.t1&&(c.t1||c.t0)>=v.t0));
    const chB=cl.length?`<button class="btn sm" data-go="#/charge/${encodeURIComponent(cl[0].id)}"${cl.length>1?` data-cs="${esc(JSON.stringify({ids:cl.map(c=>c.id),label:"this park"}))}"`:""} title="${esc(cl.length>1?cl.length+" charges here, "+n0(cl.reduce((a,c)=>a+(c.kwh||0),0),1)+" kWh: the first opens, arrows go through them":(cl[0].place||"Charge")+" · +"+n0(cl[0].kwh,1)+" kWh")}">${cl.length>1?"Charges":"Charge"}</button>`
      :ch1?`<button class="btn sm" data-go="#/charge/${encodeURIComponent(ch1.id)}" title="${esc((ch1.place||"Charge")+" · +"+n0(ch1.kwh,1)+" kWh")}">Charge</button>`:"";
    return chB+(sv?`<button class="btn sm" data-pl="${esc(sv.key)}" title="${esc(sv.name)}">Saved</button>`:chB?"":`<button class="btn sm" data-save="${esc(JSON.stringify({name:stayAt(v)||"",lat:+p[0].toFixed(6),lon:+p[1].toFixed(6),radius_m:150}))}" title="Save this spot as a place">Save</button>`)};
  // a charge row names what kind of place it is and opens it: a Supercharger or other charger's listing, or the saved place (the charge itself opens with Open)
  const chargeBtn=v=>{const k=v.pkey||"";if(!k||k[0]==="@")return "";const t=k.startsWith("s:")?"Supercharger":k.startsWith("c:")?"Charger":k.startsWith("p:")?"Saved":"";
    return t?`<button class="btn sm" data-go="#/place/${encodeURIComponent(k)}" title="${esc(v.place||"")}">${t}</button>`:""};
  const ico=c=>c==="drive"?"":c==="charge"?"⚡":c==="parked"?"🅿️":c==="sentry"?'<i class="hal" title="Sentry"></i>':"🎬";
  const wideCells=v=>{const c=v._c,cell=t=>`<span class="td xw">${t}</span>`,dash="";
    if(c==="drive")return cell(n0(v.km,1)+" km")+cell(v.kwh!=null?n0(v.kwh,1)+" kWh":dash)+cell(v.km&&(v.whkm||v.kwh)?EF(v.whkm||v.kwh*1000/v.km)+" "+EFU():dash)+cell(v.b0!=null?n0(v.b0)+"→"+n0(v.b1)+"%":dash)+cell(v.km&&v.ap!=null?n0(100*v.ap/v.km)+"%":dash)+cell(v.vmax!=null?n0(v.vmax)+" km/h":dash);
    if(c==="charge")return cell(dash)+cell("+"+n0(v.kwh,1)+" kWh")+cell(dash)+cell(v.b0!=null?n0(v.b0)+"→"+n0(v.b1)+"%":dash)+cell(dash)+cell(dash);
    return cell(dash).repeat(6)};
  // a park that runs over several days is one park, shown on each day as that day's part of it: 9:22–23:59 on the first, 00:00–23:59 (24 h) on the ones between, 00:00 to the end on the last
  const slice=(v,d)=>{if(v._c!=="parked"||!(v._d1>v._d0))return null;const f=d===v._d0,l=d===v._d1,mn=t=>(+t.slice(11,13))*60+(+t.slice(14,16)),a=f?mn(v.start):0,e=l&&v.end?mn(v.end):1440;return{when:(f?clockHM(v.start):"00:00")+" – "+(l?(v.ongoing?"now":clockHM(v.end)):"23:59"),min:Math.max(1,e-a),sent:(v._sent||[]).filter(x=>String(x.trig||x.start).slice(0,10)===d),rec:(v._rec||[]).filter(x=>String(x.start).slice(0,10)===d)}};
  const row=(v,d)=>{const sl=slice(v,d),rcs=sl?sl.rec:(v._rec||[]),sns=sl?sl.sent:(v._sent||[]),c=v._c,id=esc(v.id),canTick=c==="drive"||!!v._pos,chk=c==="rec"?"<span></span>":`<label class="ck"><input type="checkbox" data-id="${id}" ${isOn(v)?"checked":""} ${canTick?"":"disabled"} title="${canTick?"Show on the map":"No position known"}"></label>`;
    const when=c==="sentry"?hms(v.trig||v.start).slice(0,5):sl?sl.when:tm(v),e2=c==="drive"?ends(v):null;
    const foot=v.cam&&v.cam.length?` <span class="sub" title="Dashcam footage covers ${Math.round(v.cov*100)}% of this drive">🎬${v.cov<0.9?" "+Math.round(v.cov*100)+"%":""}</span>`:"",only=v.src==="cam"?' <span class="sub" title="The dashcam saw this drive; Tessie has no record of it (yet)">dashcam only</span>':"";
    const where=c==="drive"?`${esc(e2[0])} <span class="ar">→</span> ${esc(e2[1])}${v.short?' <span class="sub" title="The car moved less than 200 m before going into Park again">short move</span>':""}${foot}${only}`:c==="charge"?`${esc(v.place||"Charge")} <span class="sub">+${n0(v.kwh,1)} kWh · ${n0(v.b0)}→${n0(v.b1)}%</span>`:c==="rec"?`Recording · ${v.n} clip${v.n>1?"s":""} <span class="sub">telemetry not read yet</span>`:esc(stayAt(v)||"Unknown location")+(rcs.length?` <span class="sub" title="The dashcam recorded ${rcs.length} minute${rcs.length>1?"s":""} while the car was parked here">🎬 ${rcs.length} min</span>`:"")+(sns.length?` <span class="pill sn" data-sn="${id}" role="button" tabindex="0" title="Sentry events during this park: click to list">${sns.length} Sentry ▾</span>`:"");
    const go=o.go?o.go(v):c==="drive"?`#/teslacam/${role}/${encodeURIComponent(v.id)}`:c==="sentry"?`#/teslacam/${role}/sentry-${encodeURIComponent(v.ev)}`:c==="rec"?`#/teslacam/${role}/${encodeURIComponent(v.id)}`:"";
    return `<div class="tr ${c}" data-id="${id}">${chk}<span class="tm">${when}</span><span class="ti"${c==="parked"&&(v._chg||[]).length?' title="Parked and charged"':""}>${c==="parked"&&(v._chg||[]).length?"⚡":ico(c)}</span><span class="tw">${where}</span><span class="td">${dur(sl?sl.min:mins(v))}</span>${TLS.wide?wideCells(v):""}<span class="to">${c==="charge"?chargeBtn(v):placeBtn(v)}${c==="rec"?`<button class="btn sm" data-rd="${esc(v.id)}" title="Reads the speed, position and steering of this recording from the dashcam drive, so it shows as a drive or a stop">Read telemetry</button>`:go?`<button class="btn sm" data-go="${go}">Open ▸</button>`:""}</span></div>`+(sns.length?`<div class="sentl" data-for="${id}" hidden>${sns.map(s=>`<div class="tr sb"><span></span><span class="tm">${esc(hms(s.trig||s.start).slice(0,5))}</span><span class="ti"><i class="hal" title="Sentry"></i></span><span class="tw">${esc(s.reason?reason(s.reason):"Sentry event")}</span><span class="td">${n0(s.n)} min</span>${TLS.wide?'<span class="td xw"></span>'.repeat(6):""}<span class="to"><button class="btn sm" data-go="#/teslacam/${role}/sentry-${encodeURIComponent(s.ev)}">Open ▸</button></span></div>`).join("")}</div>`:"")};
  host.innerHTML=days.length?`<div class="bar" style="margin:0 0 4px"><button class="chip ${TLS.mode==="v"?"on":""}" data-tm="v">Speed</button><button class="chip ${TLS.mode==="ap"?"on":""}" data-tm="ap">Autopilot / FSD</button><span class="sub" id="tlleg"></span><span class="sub" style="margin-left:auto;cursor:help" title="Tick a row to show it on the map. Click a drive to open it, a Sentry event to watch it. Hover a route for the moment under the pointer; click it to open the drive. Short drives under the re-park limits count as parked. An event that runs past midnight is shown whole on both days, and ticking it ticks it on both. Click a row to find it on the map; Open shows a drive or Sentry event in full.">ⓘ how this works</span></div>`
    +days.map((d,di)=>(di?`<div class="dhd">${esc(dayName(d))}<small>${byDay[d].length} item${byDay[d].length>1?"s":""}</small></div>`:`<div class="dpick"><button class="chip" data-nav="${win(prevW)}" ${prevW.length?"":"disabled"} title="${isRange?"Earlier days":"Earlier day"}">‹</button><span id="dsel"></span><button class="chip" data-nav="${win(nextW)}" ${nextW.length?"":"disabled"} title="${isRange?"Later days":"Later day"}">›</button><small>${nItems} item${nItems>1?"s":""}${cut?" · "+(TLS.dayDesc?"newest ":"first ")+MAXD+" of "+allSel.length+" days shown":""}</small><button class="chip ico ${TLS.wide?"on":""}" data-so="wide" aria-label="${TLS.wide?"Narrow table":"Wide table"}" title="${TLS.wide?"Narrow table":"Wide table: full width with distance, energy, efficiency, battery, self-driving, top speed"}" style="margin-left:auto">${TLS.wide?`<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M14 6h-4V2M2 10h4v4M10 6l4.5-4.5M6 10l-4.5 4.5"/></svg>`:`<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10 2h4v4M6 14H2v-4M14 2L9.5 6.5M2 14l4.5-4.5"/></svg>`}</button></div>`)+`<div class="tday ${TLS.wide?"wide":""}" data-day="${d}"><div class="tl2"><div class="tr trh"><span class="ck"><button class="cyc" data-cyc="${d}" data-s="none" aria-label="Choose rows to show on the map" title="Click: tick the drives. Again: tick everything. Again: tick nothing."></button></span><span class="srt" data-so="rows" role="button" tabindex="0" title="Sort by time">Time ${TLS.rowDesc?"▼":"▲"}</span><span></span><span>Where</span><span class="td">Duration</span>${TLS.wide?'<span class="td xw">Distance</span><span class="td xw">Energy</span><span class="td xw">Efficiency</span><span class="td xw">Battery</span><span class="td xw">Self-driving</span><span class="td xw">Top speed</span>':""}<span></span></div>${rowsOf(d).map(v=>row(v,d)).join("")}</div><div class="tmap"></div></div>`).join("")+(cut?`<div class="bar" style="margin:10px 0"><button class="btn" id="tlmore">Show ${Math.min(31,allSel.length-MAXD)} more days</button><span class="sub">${MAXD} of ${allSel.length} days in the range are shown</span></div>`:"")
    :'<div class="empty">Nothing here for these dates.</div>';
  if(!days.length)return;
  if($("#tlmore",host))$("#tlmore",host).onclick=()=>{TLS.more=(TLS.more||0)+31;o.redraw&&o.redraw()};
  const leg=()=>{$("#tlleg").innerHTML=TLS.mode==="v"?spdLegend():apLegend()};leg();
  const maps={},order=[];
  const dayItems=d=>byDay[d];
  const populate=async d=>{const mp=maps[d];if(!mp)return;const tok=mp.tok=(mp.tok||0)+1,on=dayItems(d).filter(v=>isOn(v)&&(v._c==="drive"||v._pos));
    const rt=(await Promise.all(on.filter(v=>v._c==="drive").map(async v=>{const r=await tlRoute(role,v.id,v);return r?{id:v.id,S:r.S,start:r.start,u0:r.u0,lo:r.lo}:null}))).filter(Boolean);
    if(mp.tok!==tok||!maps[d])return;
    mp.m.set(rt,on.filter(v=>v._c!=="drive").map(v=>({id:v.id,type:v._c,pos:v._pos,title:v._c==="sentry"?"Sentry · "+reason(v.reason)+" · "+hms(v.trig||v.start):v._c==="charge"?"Charge · "+(v.place||"")+" · "+tm(v):"Parked "+tm(v)})))};
  const build=d=>{if(maps[d])return;if(!window.maplibregl)return;if(order.length>=6){const old=order.shift();if(maps[old]){maps[old].m.destroy();delete maps[old];const e=$(`.tday[data-day="${old}"] .tmap`,host);if(e)e.innerHTML=""}}
    const el=$(`.tday[data-day="${d}"] .tmap`,host);maps[d]={m:dayMap(el,role,{open:id=>{const v=rs.find(x=>x.id===id),g=o.go&&v?o.go(v):"";location.hash=g||"#/teslacam/"+role+"/"+encodeURIComponent(id)},pick:id=>{const c=$(`input[data-id="${CSS.escape(id)}"]`,host);if(c)c.scrollIntoView({block:"center"})}})};order.push(d);populate(d)};
  const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting)build(e.target.parentNode.dataset.day)}),{rootMargin:"300px"});
  $$(".tmap",host).forEach(e=>io.observe(e));
  const setOn=(ids,val)=>{const ds=new Set();ids.forEach(id=>{TLS.on[id]=val;$$(`input[data-id="${CSS.escape(id)}"]`,host).forEach(x=>{if(!x.disabled)x.checked=val;ds.add(x.closest(".tday").dataset.day)})});ds.forEach(populate);syncAll()};
  $$("input[data-id]",host).forEach(c=>c.onchange=()=>setOn([c.dataset.id],c.checked));
  const onRoute=e=>{const v0=rs.find(x=>x.id===e.detail);if(!v0||!isOn(v0))return;new Set($$(`input[data-id="${CSS.escape(e.detail)}"]`,host).map(x=>x.closest(".tday").dataset.day)).forEach(populate)};addEventListener("tlroute",onRoute);
  const rng=selAsc.length?[selAsc[0],selAsc[selAsc.length-1]]:null;
  dayPicker($("#dsel",host),{cur:selAsc[0]||cur,range:isRange?rng:null,days:new Set(allDays),text:isRange?dayName(rng[0])+" – "+dayName(rng[1]):dayName(selAsc[0])+" ("+byDay[selAsc[0]].length+")",
    onPick:d=>{TLS.day=d;TLS.range=null;o.redraw&&o.redraw()},onRange:(a,b)=>{TLS.range=a===b?null:[a,b];TLS.day=a;o.redraw&&o.redraw()}});
  $$("[data-nav]",host).forEach(b=>b.onclick=()=>{const w=(b.dataset.nav||"").split(",");if(!w[0])return;if(isRange){TLS.range=w[0]===w[w.length-1]?null:[w[0],w[w.length-1]];TLS.day=w[0]}else{TLS.day=w[0];TLS.range=null}o.redraw&&o.redraw()});
  // the day's list and map are as tall as what is left of the window below them
  const fitH=()=>{const e=$(".tday",host);if(!e)return;e.style.setProperty("--tdh",Math.max(380,innerHeight-(e.getBoundingClientRect().top+scrollY)-16)+"px")};fitH();addEventListener("resize",fitH);
  $$("[data-so]",host).forEach(b=>{b.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();b.click()}};b.onclick=()=>{if(b.dataset.so==="days")TLS.dayDesc=!TLS.dayDesc;else if(b.dataset.so==="wide"){TLS.wide=!TLS.wide;try{localStorage.setItem("tlWide",TLS.wide?"1":"")}catch(e){}}else TLS.rowDesc=!TLS.rowDesc;o.redraw&&o.redraw()}});
  // one control above the tick column cycles a day: the drives, then everything, then nothing (ticking rows by hand works too)
  const dayBoxes=d=>$$(`.tday[data-day="${d}"] input[data-id]:not(:disabled)`,host),isDrv=x=>{const v=rs.find(y=>y.id===x.dataset.id);return !!v&&v._c==="drive"};
  const cycState=d=>{const bx=dayBoxes(d),on=bx.filter(x=>x.checked);if(!on.length)return "none";if(on.length===bx.length)return "all";const dr=bx.filter(isDrv);return dr.length&&on.length===dr.length&&on.every(isDrv)?"drives":"mix"};
  const CYT={none:"Nothing ticked. Click to tick the drives.",drives:"Drives ticked. Click to tick everything.",all:"Everything ticked. Click to tick nothing.",mix:"Some rows ticked. Click to tick the drives."};
  const syncAll=()=>$$("[data-cyc]",host).forEach(b=>{const st=cycState(b.dataset.cyc);b.dataset.s=st;b.title=CYT[st];b.setAttribute("aria-label",CYT[st])});syncAll();
  $$("[data-cyc]",host).forEach(b=>b.onclick=e=>{e.stopPropagation();const d=b.dataset.cyc,bx=dayBoxes(d),dr=bx.filter(isDrv),st=cycState(d),nx=st==="all"?"none":st==="drives"?"all":(dr.length&&dr.length<bx.length?"drives":"all");
    TLS.sel=nx;TLS.on={};                                  // the choice is for every day, now and as you move between days; hand choices start again
    $$("input[data-id]:not(:disabled)",host).forEach(x=>{const v=rs.find(y=>y.id===x.dataset.id);x.checked=!!v&&isOn(v)});
    $$(".tday",host).forEach(t=>populate(t.dataset.day));syncAll()})
  $$("[data-tm]",host).forEach(b=>b.onclick=()=>{TLS.mode=b.dataset.tm;$$("[data-tm]",host).forEach(x=>x.classList.toggle("on",x===b));leg();Object.values(maps).forEach(x=>x.m.mode(TLS.mode))});
  $$("[data-go]",host).forEach(b=>b.onclick=e=>{e.stopPropagation();try{if(b.dataset.cs)sessionStorage.setItem("chargeSet",b.dataset.cs);else sessionStorage.removeItem("chargeSet")}catch(_){}location.hash=b.dataset.go});
  $$("[data-rd]",host).forEach(b=>b.onclick=e=>{e.stopPropagation();tlRead(b,role,b.dataset.rd)});
  $$("[data-pl]",host).forEach(b=>b.onclick=e=>{e.stopPropagation();try{sessionStorage.removeItem("placePrefill")}catch(_){}location.hash="#/place/"+encodeURIComponent(b.dataset.pl)});
  $$("[data-save]",host).forEach(b=>b.onclick=e=>{e.stopPropagation();try{sessionStorage.setItem("placePrefill",b.dataset.save)}catch(_){}location.hash="#/place/"+encodeURIComponent("@new")});
  $$("[data-sn]",host).forEach(b=>{const t=e=>{e.stopPropagation();const x=$(`.sentl[data-for="${CSS.escape(b.dataset.sn)}"]`,host);if(x){x.hidden=!x.hidden;b.textContent=b.textContent.replace(/[▾▴]/,x.hidden?"▾":"▴");b.setAttribute("aria-expanded",x.hidden?"false":"true")}};b.onclick=t;b.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();t(e)}}});
  // clicking a row ticks it and brings it up on the map
  $$(".tr[data-id]",host).forEach(r=>r.onclick=async e=>{if(e.target.closest("input,button,a,label"))return;const v=rs.find(x=>x.id===r.dataset.id);if(!v||!(v._c==="drive"||v._pos))return;
    $$(".tr.sel",host).forEach(x=>x.classList.remove("sel"));r.classList.add("sel");const c=$("input[data-id]",r);if(c&&!c.checked){c.checked=true;TLS.on[v.id]=true;syncAll()}
    const dd=r.closest(".tday").dataset.day;await populate(dd);const mp=maps[dd];if(mp)mp.m.focus(v.id)});
  // ← and → move to the earlier / later day (or the same number of days again, with a range), as the ‹ › buttons do; not while typing in a box or choosing in a list
  const onKey=e=>{if(e.altKey||e.ctrlKey||e.metaKey||e.shiftKey||(e.key!=="ArrowLeft"&&e.key!=="ArrowRight"))return;const t=e.target;if(t&&t.closest&&t.closest("input,select,textarea,[contenteditable]"))return;
    if(!document.body.contains(host)||$(".rpop"))return;const b=$$("[data-nav]",host)[e.key==="ArrowLeft"?0:1];if(b&&!b.disabled){e.preventDefault();b.click()}};
  document.addEventListener("keydown",onKey);
  return()=>{document.removeEventListener("keydown",onKey);removeEventListener("tlroute",onRoute);removeEventListener("resize",fitH);io.disconnect();Object.values(maps).forEach(x=>x.m.destroy())}}

function routeSvg(pts,w=64,h=40){if(!pts||pts.length<2)return "";const la=pts.map(p=>p[0]),lo=pts.map(p=>p[1]),a=Math.min(...la),b=Math.max(...la),c=Math.min(...lo),d=Math.max(...lo),k=Math.cos((a+b)/2*Math.PI/180),sx=(d-c)*k||1e-6,sy=(b-a)||1e-6,s=Math.min((w-6)/sx,(h-6)/sy),ox=(w-sx*s)/2,oy=(h-sy*s)/2;
  return `<svg class="rt" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round" points="${pts.map(p=>((p[1]-c)*k*s+ox).toFixed(1)+","+(h-((p[0]-a)*s+oy)).toFixed(1)).join(" ")}"/></svg>`}

// ---------- Drives (new): one list from every source. Tessie's drives are the rows; the dashcam's attach by time; with no Tessie the dashcam alone makes it (lib/app_drivelist.py)
let DLP={r:{p:"all"},q:"",src:"all"};
// ---------- Tessie sync: the app runs sync_drives_all.sh itself (Tessie's drives, their detail, the drive pages) and shows how far it has got
let TSPOLL=null;
// what a sync step is called on screen (the scripts' own step names are for the log, not for the page)
const STEPW=n=>/^Tessie drives/i.test(n)?"Fetching your drives from Tessie":/telemetry backfill/i.test(n)?"Fetching the path of each drive":/summary pages/i.test(n)?"Building the drive pages":/Battery health/i.test(n)?"Updating battery health":/Firmware alerts/i.test(n)?"Checking firmware alerts":/software and FSD/i.test(n)?"Checking the car's software version":n;
async function tessieSync(host,o){
  o=o||{};clearTimeout(TSPOLL);const alive=()=>document.body.contains(host);
  const K=o.kind==="charges"?"?kind=charges":"";let info;try{info=await (await fetch("/api/tessie"+K)).json()}catch(e){host.innerHTML="";return}
  const m=o.missing||{},n=m.cam_only||0,np=m.no_page||0,pl=(k,a,b)=>k+(k===1?a:b);
  const what=o.what||[n?pl(n," drive the dashcam saw is not in Tessie yet"," drives the dashcam saw are not in Tessie yet"):"",np?pl(np," drive has no path from Tessie yet"," drives have no path from Tessie yet"):""].filter(Boolean).join("; ");
  const cap=t=>t.charAt(0).toUpperCase()+t.slice(1);
  let since=0,tail=[],was=false,err="",step=0,stepName="",pct=null;const STEPS=o.steps||6;
  const barW=()=>Math.min(100,Math.round(100*((Math.max(step,1)-1)+(pct==null?0:pct/100))/STEPS));
  const stepHead=()=>step?"Step "+Math.min(step,STEPS)+" of "+STEPS+": "+stepName+(pct!=null?" · "+pct+"%":""):"Syncing from Tessie…";
  const paint=j=>{
    if(!alive())return;
    const run=!!(j&&j.running);
    if(!info.configured){host.innerHTML=what?`<div class="card"><b>${esc(cap(what))}.</b><div class="sub" style="margin-top:6px">Tessie is not set up in this app, so only the dashcam can fill them in. Put your Tessie access token and VIN in config.json (<code>tessie.access_token</code>, <code>tessie.vin</code>) and the app will fetch them itself.</div></div>`:"";return}
    if(run){host.innerHTML=`<div class="card ixb"><div style="flex:1;min-width:0"><b id="tsh">${esc(stepHead())}</b><div style="height:6px;border-radius:3px;background:var(--line,#ddd);margin:8px 0;overflow:hidden"><div id="tsp" style="height:100%;width:${barW()}%;background:var(--accent,#3b6cf6);transition:width .3s"></div></div></div><button class="btn" id="tsx">Stop</button></div>`;
      $("#tsx",host).onclick=async()=>{await fetch("/api/tessie/sync/stop"+K,{method:"POST"});poll()};return}
    let h="";
    if(err)h+=`<div class="card"><b style="color:var(--bad)">${esc(err)}</b></div>`;
    else if(j&&j.ended&&j.rc!==0)h+=`<div class="card"><b style="color:var(--bad)">The Tessie sync stopped (exit ${j.rc}).</b><pre class="out" style="max-height:200px;overflow:auto">${esc(tail.slice(-14).join("\n"))}</pre></div>`;
    else if(j&&j.ended&&was)h+=`<div class="sub" style="margin-bottom:8px">Synced from Tessie.</div>`;
    if(what&&o.tried)h+=`<div class="sub">Synced from Tessie: it has no detailed record for this drive${o.triedNote?" ("+esc(o.triedNote)+")":""}.</div>`;
    else if(what)h+=`<div class="bar"><button class="btn" id="tsg">Sync Tessie</button><span class="sub"><b>${esc(cap(what))}.</b> ${esc(o.note||"Sync Tessie fetches them.")}</span></div>`;
    else if(o.always&&info.synced_at&&Date.now()/1000-info.synced_at<(info.fresh_minutes||60)*60)h+=`<div class="sub">Tessie synced ${Math.max(1,Math.round((Date.now()/1000-info.synced_at)/60))} min ago</div>`;
    else if(o.always)h+=`<div class="bar"><button class="btn" id="tsg">Sync Tessie</button><span class="sub">${esc(o.always_note||"Fetches new drives and their detail and pages from Tessie.")}${m.tried?" "+pl(m.tried," drive stays as the dashcam saw it: Tessie has no record of it."," drives stay as the dashcam saw them: Tessie has no record of them."):""}</span></div>`;
    host.innerHTML=h;const g=$("#tsg",host);
    if(g)g.onclick=async()=>{g.disabled=true;err="";tail=[];since=0;was=true;
      let r;try{r=await (await fetch("/api/tessie/sync"+K,{method:"POST"})).json()}catch(e){r={ok:false,error:"Could not reach the app."}}
      if(r.ok===false){err=r.error||"Could not start the sync.";was=false;paint(null);return}poll()}};
  const poll=async()=>{
    clearTimeout(TSPOLL);let j;try{j=await (await fetch("/api/tessie-sync?since="+since+(K?"&kind=charges":""))).json()}catch(e){return}
    if(!alive())return;
    if(j.lines&&j.lines.length){tail=tail.concat(j.lines).slice(-80);
      j.lines.forEach(x=>{if(x.startsWith("==> ")){const nm=x.slice(4).replace(/\s*\(.*\)\s*$/,"");if(/^Done/.test(nm)){pct=100;return}step++;stepName=STEPW(nm);pct=null}else{const q=x.match(/(\d{1,3})%/);if(q)pct=+q[1]}});
}
    since=j.n;if(j.running)was=true;
    if(j.running){if(!$("#tsh",host))paint(j);else{$("#tsh",host).textContent=stepHead();$("#tsp",host).style.width=barW()+"%"}TSPOLL=setTimeout(poll,1500);return}
    if(was&&j.rc===0&&o.after){was=false;o.after();return}
    paint(j);was=false};
  paint(null);
  if(info.running)poll()
}
async function pageDriveList(){
  loading();const [dl,act,pl,chg]=await Promise.all([tcGet("drivelist"),api("activity"),api("places").catch(()=>({saved:[]})),api("chargers").catch(()=>({rows:[]}))]),places=pl.saved||[];
  const open=v=>v.kind==="charge"?"#/charge/"+encodeURIComponent(v.id):v.kind==="drive"?(v.src==="cam"?"#/teslacam/auto/"+encodeURIComponent(v.id):"#/drive/"+encodeURIComponent(v.id)):v.kind==="sentry"?"#/teslacam/A/sentry-"+encodeURIComponent(v.ev):v.kind==="recording"||v.kind==="stopped"?"#/teslacam/auto/"+encodeURIComponent(v.id):"";
  main.innerHTML=`<h1>Drives</h1><div class="sub">Source: ${dl.tessie?"Tessie + dashcam":"dashcam"} · 🎬 = dashcam video and route</div>
   <div id="stl" class="sub" style="margin-top:8px" ${dl.stale?"":"hidden"}>Updating with the latest data…</div><div id="tsb" style="margin-top:10px"></div><div id="fsb"></div><div class="bar"><span id="rg"></span><select id="fs"><option value="all">All drives</option><option value="cam">With dashcam footage</option><option value="nocam">Without footage</option></select><input type="search" id="q" placeholder="Search a place…" value="${esc(DLP.q)}"></div><div class="tiles" id="tl"></div><div id="tb"></div>`;
  const draw=()=>{if(TLOFF){TLOFF();TLOFF=null}const q=DLP.q.toLowerCase(),keep=v=>{if(v.kind==="drive"){if(DLP.src==="cam"&&!(v.cam&&v.cam.length))return false;if(DLP.src==="nocam"&&v.cam&&v.cam.length)return false}
      return !q||((v.frm||"")+" "+(v.to||"")+" "+(v.place||"")+" "+(v.city||"")+" "+(v.street||"")+" "+nameAt(places,v.p0)+" "+nameAt(places,v.p1)).toLowerCase().includes(q)},
     rs=dl.items.filter(v=>inSpan(v,DLP.r)&&keep(v)),dr=rs.filter(v=>v.kind==="drive"&&!v.short);
    $("#tl").innerHTML=tile(n0(dr.length),"drives")+tile(n0(dr.reduce((a,v)=>a+(v.km||0),0))+" km","driven")+tile(n0(dr.filter(v=>v.cam&&v.cam.length).length),"with dashcam footage")+tile(n0(rs.filter(v=>v.kind==="charge").length),"charges")+tile(n0(dr.filter(v=>v.src==="cam").length),"dashcam only");
    $("#tb").innerHTML='<div id="tlh"></div>';
    TLOFF=tlDays($("#tlh"),rs,{role:"auto",places,sc:pl.superchargers||[],cg:chg.rows||[],tess:act.filter(x=>x.k==="drive"),chg:act.filter(x=>x.k==="charge"),dayOk:d=>inRange({day:d},DLP.r),redraw:draw,go:open,
      onSentry:()=>{}})||null};
  rangePicker($("#rg"),DLP.r,spanDays(dl.items),()=>{       // a range chosen here is the range listed: its days one under the other, not only its newest day
    const r=rangeOf(DLP.r),a=r?r[0]:"0000-01-01",z=r?r[1]:"9999-12-31";TLS.range=a===z?null:[a,z];if(a===z)TLS.day=a;draw()});$("#q").oninput=e=>{DLP.q=e.target.value;draw()};$("#fs").value=DLP.src;$("#fs").onchange=e=>{DLP.src=e.target.value;draw()};draw();
  if(dl.stale){const again=async()=>{if(location.hash!=="#/driveslist")return;let j;try{j=await tcGet("drivelist")}catch(e){return}
      if(!j.stale){bust("activity");pageDriveList()}else setTimeout(again,3000)};setTimeout(again,3000)}
  tessieSync($("#tsb"),{missing:dl.missing,always:true,after:()=>{bust();route()}});
  footageSync($("#fsb"));
}
const localWhen=iso=>iso?new Date(iso).toLocaleString("en-AU",{day:"numeric",month:"short",year:"numeric",hour:"2-digit",minute:"2-digit"}):"Never";      // stored in UTC, shown on this computer's clock
// pressing an action that reads or ejects a drive stops every video on the page first (a playing video holds the drive and slows what is asked for)
function stopVideos(){$$("video").forEach(v=>{try{v.pause();v.removeAttribute("src");v.querySelectorAll("source").forEach(x=>x.remove());v.load()}catch(e){}})}
// eject a drive from the app: the app stops reading it first (POST /api/eject); -> {ok, text}
async function ejectDrive(role){stopVideos();try{return await (await fetch("/api/eject",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({role})})).json()}catch(e){return{ok:false,text:e.message}}}
// the footage on the connected drives (lib/app_keeper.py does the work in the background, lib/footage_todo.py says what is left): all connected drives, nothing to pick.
// opts.table: a row per drive with what the log holds and what is left to do, an Eject button, and a button only for the work that is left
function footageSync(host,opts){
  if(!host)return;const table=!!(opts&&opts.table);let until=0,was=false,lg=null,msg="",busyEject="";
  const gb=b=>b>=1073741824?(b/1073741824).toFixed(1)+" GB":Math.round(b/1048576)+" MB",NM={A:"Archive",J:"JOWUA",U:"Tesla USB"};
  const STEP=[["log","Update log",["log","events"],"Lists the files on the connected drives, so the footage log and the Saved and Sentry events are up to date"],["telemetry","Read telemetry",["telemetry"],"Reads the speed, position and steering from the video, so drives show on the map"],["stills","Make stills",["stills"],"Takes a picture every second of each clip, kept after the video is deleted"]];
  const left=(t,r)=>{const d=t&&t.drives&&t.drives[r];return d?{log:d.log_new+d.log_gone,telemetry:d.telemetry,stills:d.stills}:null};
  const paint=async()=>{if(!document.body.contains(host))return;let k,roots={},todo=null;
    try{k=await (await fetch("/api/keeper")).json();roots=(await (await fetch("/api/status")).json()).roots||{}}catch(e){return}
    try{const r=await fetch("/api/footage/todo");todo=r.ok?await r.json():null}catch(e){}      // an older server has no such answer: every button is offered
    const run=!!k.working,pc=k.total?Math.round(100*k.done/k.total):0,conn=Object.keys(roots).filter(r=>roots[r]);
    if((was&&!run)||(table&&!lg)){try{lg=await tcGet("footage/log")}catch(e){}}was=run;
    const tot={log:0,telemetry:0,stills:0};const checking=!!(todo&&todo.checking);let unknown=!todo;conn.forEach(r=>{const l=left(todo,r);if(!l){unknown=true;return}tot.log+=l.log;tot.telemetry+=l.telemetry;tot.stills+=l.stills});
    const kinds=unknown?STEP:STEP.filter(x=>tot[x[0]]>0),go=async list=>{stopVideos();until=Date.now()+20000;try{await fetch("/api/keeper",{method:"POST",body:JSON.stringify({action:"run",stages:list})})}catch(e){}paint()};
    const g0=todo&&todo.progress,chkTx=!g0?"Checking for outstanding tasks...":g0.step==="list"?"Checking the "+(NM[g0.role]||g0.role)+" for new files":g0.step==="telemetry"?"Checking for minutes without telemetry":"Checking for clips without stills",chkPc=g0?Math.round(100*(g0.done+.4)/g0.total):3;      // a bar whenever something is being checked
    const barOf=(txt,q)=>`<div style="min-width:170px"><div>${esc(txt)}</div><div style="height:6px;border-radius:3px;background:var(--line);margin-top:5px;overflow:hidden"><div style="height:100%;width:${q}%;background:var(--accent);transition:width .3s"></div></div></div>`;
    let h="";
    if(!conn.length)h+='<div class="bar"><span class="sub">No TeslaCam drive connected.</span></div>';
    else if(run&&!table)h+=`<div class="card ixb"><div style="flex:1;min-width:0"><b>${esc(k.text)}${k.total?" · "+n0(k.done)+" of "+n0(k.total):""}</b>${k.total?`<div style="height:6px;border-radius:3px;background:var(--line);margin:8px 0 0;overflow:hidden"><div style="height:100%;width:${pc}%;background:var(--accent);transition:width .3s"></div></div>`:""}</div><button class="btn" id="fsx">Stop</button></div>`;
    else if(checking&&!table){const g=g0,tx=chkTx,pc2=chkPc;      // in the table the bar is on each drive's row (Tasks)
      h+=`<div class="card ixb"><div style="flex:1;min-width:0"><b>${esc(tx)}${g?" · "+n0(g.done+1)+" of "+n0(g.total):""}</b><div style="height:6px;border-radius:3px;background:var(--line);margin:8px 0 0;overflow:hidden"><div style="height:100%;width:${pc2}%;background:var(--accent);transition:width .3s"></div></div></div></div>`}
    else{      // the same four buttons always; one with nothing left to do is greyed out (and so is everything while a job runs or the check is under way)
      const busy=run||checking,left0=x=>unknown||tot[x[0]]>0,dis=(x)=>busy||!left0(x),any=STEP.some(left0),tip=x=>dis(x)&&!busy?"Nothing left to do":x[3];
      h+=`<div class="bar">${k.paused&&!table?'<button class="btn pri" id="fsr">Resume</button>':""}<button class="btn" id="fsg" ${busy||!any?"disabled":""} title="${busy?"":any?"Does all of these, one after the other":"Nothing left to do"}">Sync footage</button>${STEP.map(x=>`<button class="btn" data-st="${x[0]}" ${dis(x)?"disabled":""} title="${esc(tip(x))}">${x[1]}</button>`).join("")}</div>`}
    if(k.error)h+=errBox(k.error,"Syncing stopped");
    if(todo&&todo.error&&!checking)h+=errBox(todo.error,"Could not check what is left to do");
    if(table&&lg&&lg.scans&&Object.keys(lg.scans).length){const t=lg.total,rs=Object.keys(lg.drives).sort();
      const VERB={log:"Listing files",telemetry:"Reading telemetry",stills:"Making stills"};
      const todoCell=r=>{if(!roots[r])return'<span class="sub">Not connected</span>';
        const dp=run&&k.drives&&k.drives[r];      // this drive's share of the job running now: its own bar
        if(dp&&dp.total)return barOf((VERB[k.stage]||"Working")+" "+n0(Math.min(dp.total,dp.done+1))+" of "+n0(dp.total),Math.round(100*dp.done/dp.total));
        if(!run&&checking)return barOf(chkTx+(g0?" · "+n0(g0.done+1)+" of "+n0(g0.total):""),chkPc);const l=left(todo,r);if(!l)return todo?'<span class="sub">Checking…</span>':"";
        const p=[];if(l.log)p.push(n0(l.log)+" files to list");if(l.telemetry)p.push(n0(l.telemetry)+" minutes without telemetry");if(l.stills)p.push(n0(l.stills)+" clips without stills");
        return p.length?p.map(esc).join(" · "):'<span style="color:var(--ok)">Up to date</span>'};
      h+=`<table style="margin-top:8px"><thead><tr><th class="l">Drive</th><th>Files</th><th>Size</th><th>Archived</th><th>Deleted</th><th class="l">Last updated</th><th class="l">Tasks</th><th>${run?'<button class="btn sm" id="fsx">Stop</button>':k.paused?'<button class="btn sm pri" id="fsr">Resume</button>':""}</th></tr></thead><tbody>${rs.map(r=>{const d=lg.drives[r],c=lg.scans[r]||{};return `<tr><td class="l"><b>${esc(NM[r]||r)}</b></td><td>${n0(d.listed)}</td><td>${gb(d.listed_bytes)}</td><td>${n0(d.archived)}</td><td>${n0(d.deleted)}</td><td class="l">${esc(localWhen(c.at))}</td><td class="l">${todoCell(r)}</td><td>${roots[r]?`<button class="btn sm" data-ej="${r}" ${busyEject?"disabled":""}>${busyEject===r?"Ejecting…":"Eject"}</button>`:""}</td></tr>`}).join("")}</tbody></table>
        <div class="tiles" style="margin-top:8px">${tile(n0(t.files),"files logged · "+gb(t.bytes))}${tile(n0(t.deleted),"deleted from every drive · "+gb(t.deleted_bytes))}${tile(n0(t.only_secondary),"not on the Archive · "+gb(t.only_secondary_bytes))}${tile(n0(t.purgeable),"copies also on the Archive · "+gb(t.purgeable_bytes))}</div>`}
    if(msg)h+=`<div class="sub" style="margin-top:8px;${/ejected/.test(msg)?"color:var(--ok)":"color:var(--bad)"}">${esc(msg)}</div>`;
    host.innerHTML=h;
    const g=$("#fsg",host);if(g)g.onclick=()=>go(null);
    $$("[data-st]",host).forEach(b=>b.onclick=()=>go(STEP.find(x=>x[0]===b.dataset.st)[2]));
    $$("[data-ej]",host).forEach(b=>b.onclick=async()=>{busyEject=b.dataset.ej;msg="";paint();const r=await ejectDrive(busyEject);busyEject="";msg=r.text;paint()});
    const x=$("#fsx",host);if(x)x.onclick=async()=>{try{await fetch("/api/keeper",{method:"POST",body:JSON.stringify({action:"pause"})})}catch(e){}until=Date.now()+20000;paint()};      // Stop; the numbers left are worked out again once the job has let go of the drive
    const rs=$("#fsr",host);if(rs)rs.onclick=async()=>{stopVideos();try{await fetch("/api/keeper",{method:"POST",body:JSON.stringify({action:"resume"})})}catch(e){}until=Date.now()+20000;paint()};
    if(run||checking||Date.now()<until)setTimeout(paint,run?2000:2500)};
  paint()}
async function pageTeslaCam(role,id){
  if(role&&id)return /^sentry-/.test(id)?pageTeslaCamSentry(role,id.slice(7)):/^at-/.test(id)?pageTeslaCamAt(+id.slice(3)):pageTeslaCamDrive(role,id);
  loading();const [dr,act,pl]=await Promise.all([tcGet("teslacam"),api("activity"),api("places").catch(()=>({saved:[]}))]),drives=dr.drives,places=(pl.saved||[]);
  const head='<h1>TeslaCam</h1>';
  const trips=act.filter(x=>x.k==="drive"&&!x.repark).map(x=>({id:x.id,t0:x.t0,t1:x.t0+x.min*60,ti:driveTitle(x)}));
  const tripOf=v=>{const a=v.t0!=null?v.t0:v.t,b=v.t1!=null?v.t1:(v.t||0)+60;return trips.find(d=>d.t0<b&&d.t1>a)};
  main.innerHTML=head+`<div id="fsb" style="margin-bottom:12px"></div><div class="bar" id="tsr"></div><div class="bar"><span id="rg"></span><input type="search" id="q" placeholder="Search…" value="${esc(TCS.q)}"></div><div class="tiles" id="tl"></div><div id="spl"></div><div id="tb"></div>`;
  let data=null,sel=null,token=0,sentryList=null;
  footageSync($("#fsb"),{table:true});
  const chips=()=>{
    $("#tsr").innerHTML='<span class="sub" style="margin:0 4px 0 0">Show:</span>'+Object.keys(TCSRC).map(k=>`<button class="chip ${TCS.src[k]?"on":""}" data-s="${k}">${TCSRC[k]}${data&&data[k]?" · "+data[k].length:""}</button>`).join("")+'';
    $$("#tsr [data-s]").forEach(b=>b.onclick=()=>{const on=Object.keys(TCSRC).filter(k=>TCS.src[k]);if(TCS.src[b.dataset.s]&&on.length<2)return;TCS.src[b.dataset.s]=TCS.src[b.dataset.s]?0:1;$("#spl").innerHTML="";chips();draw()});
    };
  const load=async(quiet)=>{const my=++token;if(!quiet){data=null;$("#tl").innerHTML="";$("#tb").innerHTML='<div class="empty">Reading the drive…</div>'}
    try{const d=await tcGet("clips");if(my!==token)return;data=d}catch(e){$("#tb").innerHTML=errBox(e.message,"Could not read the list of clips");return}
    const list=[].concat(data.sentry,data.saved,data.recent);if(!quiet)rangePicker($("#rg"),TCS.r,daysOf((list||[]).map(v=>({day:(v.ts||v.start).slice(0,10)}))),draw);chips();draw()};
  const draw=()=>{if(!data)return;if(!data.sentry){return}
    const q=TCS.q.toLowerCase(),srcs=Object.keys(TCSRC).filter(k=>TCS.src[k]);
    const all=[].concat(...srcs.map(k=>data[k].map(v=>Object.assign({ty:k},v)))).filter(v=>inRange({day:v.ts.slice(0,10)},TCS.r)&&(!q||(v.ty==="recent"?((tripOf(v)||{}).ti||""):(v.city+" "+v.street+" "+reason(v.reason))).toLowerCase().includes(q)||TCSRC[v.ty].toLowerCase().includes(q))).sort((a,b)=>a.ts<b.ts?1:-1),byDay={};
    all.forEach(v=>(byDay[v.ts.slice(0,10)]=byDay[v.ts.slice(0,10)]||[]).push(v));
    const days=Object.keys(byDay).sort().reverse(),cnt=k=>all.filter(v=>v.ty===k).length,mins=all.reduce((a,v)=>a+(v.ty==="recent"?v.n:Object.keys(v.clips).length),0);
    $("#tl").innerHTML=srcs.map(k=>tile(n0(cnt(k)),k==="recent"?"continuous recordings":TCSRC[k]+" events")).join("")+tile(n0(mins),"minutes of video");
    // the drive (Tessie's) the event was saved in, and the map and telemetry of it: a Saved clip is kept while driving, so it belongs to a drive
    const evLinks=v=>{const t=v.t!=null?tripOf(v):null,st=e=>'onclick="event.stopPropagation()"';
      return (t?`<span>${esc(t.ti)}</span><a href="#/drive/${t.id}" ${st()}>open the drive</a>`:"")+(v.play&&v.ty==="saved"&&v.t!=null?`<a href="${t?"#/drive/"+t.id+"/footage":"#/teslacam/auto/at-"+Math.round(v.t)}" ${st()}>map and telemetry</a>`:"")};
    // where the footage is: the drives that hold it, and when none of them is connected (or it is gone from all) say so
    const held=v=>v.play?"":`<span class="mis">${v.roles.length?"Media missing · on "+v.roles.map(drName).join(", "):"Video deleted · stills kept"}</span>`;
    const card=v=>{const bd=`<span class="srcb ${v.ty}">${TCSRC[v.ty]}</span>`;
      if(v.ty==="recent"){const t=tripOf(v);return `<button class="evc run" data-ty="recent" data-id="${esc(v.id)}">${stThumb(v.id,2,v.play?"/footage/"+v.play+"/RecentClips/"+v.id+"-front.mp4":"")}${bd}<b>${hms(v.ts).slice(0,5)} – ${hms(v.end).slice(0,5)}</b><span>${v.n} minute${v.n>1?"s":""}${v.full<v.n?" · "+(v.n-v.full)+" with fewer than 6 cameras":""}</span>${held(v)}${t?`<span>${esc(t.ti)}</span><a href="#/drive/${t.id}" onclick="event.stopPropagation()">open the drive</a>`:""}${v.play?`<a href="#/teslacam/auto/${esc(v.id)}" onclick="event.stopPropagation()">map and telemetry</a>`:""}</button>`}
      const n=Object.keys(v.clips).length,folder=TCF[v.ty],ks=Object.keys(v.clips).sort(),tt=(v.cts||v.ts||"").replace("T","_").replace(/:/g,"-"),k0=ks.filter(x=>x<=tt).pop()||ks[0],      // the minute holding the trigger, not the first one (an event starts ten minutes earlier)
        k0t=Math.max(1,Math.min(58,Math.round((Date.parse((v.cts||v.ts||"").replace("T"," "))-Date.parse((k0||"").replace("_"," ").replace(/(\d\d)-(\d\d)-(\d\d)$/,"$1:$2:$3")))/1000)||2));
      const have=(k0&&v.clips[k0])||[],cam=v.ty==="sentry"&&have.includes(EVCAM[v.cam])?EVCAM[v.cam]:have.includes("front")?"front":"";      // the camera that saw it (Sentry), at the moment it was detected
      return `<button class="evc big" data-ty="${v.ty}" data-id="${v.id}">${k0?stThumb(k0,k0t,v.play&&cam?`/footage/${v.play}/${folder}/${v.id}/${k0}-${cam}.mp4`:"",k0t):'<div class="noimg"></div>'}${bd}<b>${hms(v.ts)}</b><span>${esc(reason(v.reason))}${v.ty==="sentry"&&cam?" · "+esc(CAMN[cam]):""}</span><span>${esc([v.city,v.street].filter(Boolean).join(" · "))}</span><span>${n?n+" min of video":"no video"}</span>${held(v)}${evLinks(v)}</button>`};
    $("#tb").innerHTML=(days.length?"":'<div class="empty">Nothing here for these dates.</div>')+days.slice(0,60).map(d=>`<div class="day">${dayName(d)}<small>${byDay[d].length} clip${byDay[d].length>1?"s":""}</small></div><div class="evs">${byDay[d].map(card).join("")}</div>`).join("")+(days.length>60?'<div class="sub">Showing the newest 60 days. Narrow the dates to see older ones.</div>':"");
    lazyThumbs($("#tb"));$$("#tb .evc").forEach(b=>b.onclick=async()=>{const ty=b.dataset.ty,sel=b.dataset.id;$$("#tb .evc").forEach(x=>x.classList.toggle("on",x===b));$("#spl").scrollIntoView({block:"nearest"});
      const it=data[ty].find(v=>v.id===sel);
      if(ty!=="recent"){
        if(it.play)return ty==="saved"&&it.t?savedDrive($("#spl"),it,places):sentryPlayer($("#spl"),it,it.play,TCF[ty]);
        const what=it.reason?reason(it.reason):ty==="saved"?"Saved clip":"Sentry event";
        return stillViewer($("#spl"),{stamps:Object.keys(it.clips).sort(),roles:it.roles,loc:it.loc,trig:Date.parse(it.cts||it.ts),head:`${esc(what)} on ${dayName(it.ts.slice(0,10))} at ${hms(it.ts)}${it.city?" · "+esc(it.city):""}`})}
      $("#spl").innerHTML='<div class="sub">Reading…</div>';
      try{const rr=await tcGet("clips/run/"+encodeURIComponent(sel)),stamps=Object.keys(rr.clips).sort(),head=`Continuous recording on ${dayName(it.ts.slice(0,10))}, ${hms(it.ts).slice(0,5)} to ${hms(it.end).slice(0,5)}, ${it.n} minute${it.n>1?"s":""}.`;
        if(!it.playable)return stillViewer($("#spl"),{stamps,roles:it.roles,loc:rr.loc,head});
        const cams={};stamps.forEach(x=>{cams[x]=rr.play[x]?rr.clips[x]:[]});      // a minute on no connected drive shows its box with the drives that hold it
        clipPlayer($("#spl"),{stamps,cams,loc:rr.loc,url:(s,k)=>`/footage/${rr.play[s]}/RecentClips/${s}-${k}.mp4`,head:head+" The next minute plays on its own."+(it.playable<it.n?` ${it.n-it.playable} of the minutes are on no connected drive.`:"")})}
      catch(e){$("#spl").innerHTML=errBox(e.message,"Could not read these clips")}})};
  $("#q").oninput=e=>{TCS.q=e.target.value;draw()};chips();load();
}

// ---------- one drive rebuilt from the video: map, telemetry and the stitched footage, moving together
// the colours every route map uses (settings app.map.speed_colors / autopilot_colors): speed as a gradient, and who was driving
const APN={1:"FSD",2:"Autosteer",3:"TACC"},APD={FSD:"#3b6cf6",Autosteer:"#8b5cf6",TACC:"#14b8a6",Manual:"#94a3b8"};
const apCol=k=>(cfg("map.autopilot_colors")||{})[k]||APD[k],APC=new Proxy({},{get:(_,a)=>apCol(APN[a]||"Manual")});
const spdStops=()=>cfg("map.speed_colors")||[[0,"#0000FF"],[60,"#FFFF00"],[120,"#800080"]],spdTop=()=>spdStops()[spdStops().length-1][0];
const spdOver=()=>cfg("map.speed_over_color")||"#000000";
const spdExpr=()=>["interpolate",["linear"],["get","v"],...spdStops().flat(),spdTop()+0.5,spdOver()];
const apExpr=()=>["match",["get","ap"],1,apCol("FSD"),2,apCol("Autosteer"),3,apCol("TACC"),apCol("Manual")];
const spdLegend=()=>`<span style="display:inline-flex;width:150px;height:8px;border-radius:4px;overflow:hidden;vertical-align:middle"><span style="flex:0 0 87%;background:linear-gradient(90deg,${spdStops().map(([a,c])=>c+" "+(100*a/spdTop())+"%").join(",")})"></span><span style="flex:1;background:${spdOver()}"></span></span> 0 → ${spdTop()}+ km/h`;
const apLegend=()=>["FSD","Autosteer","TACC","Manual"].map(k=>`<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${apCol(k)};vertical-align:middle"></span> ${k}`).join("  ");
const spdAt=v=>{const st=spdStops();if((v||0)>spdTop())return spdOver();const t=Math.max(0,v||0);let i=0;while(i<st.length-2&&t>=st[i+1][0])i++;const a=st[i],b=st[i+1],f=(t-a[0])/((b[0]-a[0])||1),h=c=>[1,3,5].map(k=>parseInt(c.substr(k,2),16)),A=h(a[1]),B=h(b[1]);return "#"+A.map((x,k)=>Math.round(x+(B[k]-x)*f).toString(16).padStart(2,"0")).join("")};
// One Sentry event in full: the same layout as a drive (map on top, then the six cameras); no telemetry, the camera that detected it has a red border
// A Saved clip on the TeslaCam page: the map and route, the telemetry and the video together (side by side on a wide screen), over as much time after the moment it was
// saved as before it, with a red line on the telemetry and a red mark on the map at that moment (the horn, a tap on the screen). Without telemetry for that time the
// clips alone play, as before.
async function savedDrive(host,it,places){
  const stamps=Object.keys(it.clips).sort(),stMs=x=>Date.parse(x.slice(0,10)+"T"+x.slice(11).replace(/-/g,":")),trig=Date.parse(it.cts||it.ts),span=Math.max(300,Math.round((trig-stMs(stamps[0]))/1000)+60),what=it.reason?reason(it.reason):"Saved clip";
  host.innerHTML=`<h2 style="margin:14px 0 4px">${esc(what)} · ${dayName(it.ts.slice(0,10))} at ${hms(it.ts)}${it.city?" · "+esc(it.city):""}${it.street?" · "+esc(it.street):""}</h2><div id="evd"></div>`;
  const fetchSeg=async b=>{const r=await fetch("/api/teslacam/auto/window?t0="+Math.round(it.t-span)+"&t1="+Math.round(it.t+span)+"&budget="+(b==null?20:b));if(!r.ok)throw new Error((await r.json().catch(()=>({}))).error||("HTTP "+r.status));return r.json()};
  try{await teslaDriveBody($("#evd"),"A",fetchSeg,places,{heading:false,side:true,trig,events:[{u:it.t,label:what}]})}
  catch(e){const h=$("#evd");if(h&&host.contains(h)){h.innerHTML="";sentryPlayer(h,it,it.play,"SavedClips")}}
}
async function pageTeslaCamSentry(role,ev){
  loading();main.innerHTML=crumbs("#/teslacam","TeslaCam")+'<div id="tdh"></div>';const host=$("#tdh");
  try{
    // the Drives list does not know which drive an event is on: look on the one asked for, then on the others (the kept lists answer when a drive is away)
    let e=null;for(const r of [role].concat(["A","J","U"].filter(x=>x!==role))){try{const x=await tcGet("teslacam/"+r+"/sentry"),f=(x.events||[]).find(v=>v.id===ev);if(f){e=f;role=r;break}}catch(_){}}
    const pl=await api("places").catch(()=>({saved:[]})),places=pl.saved||[];
    if(!e){      // not on a connected drive: the kept list knows it, and its stills are kept
      const all=await tcGet("clips"),k=(all.sentry||[]).find(v=>v.id===ev)||(all.saved||[]).find(v=>v.id===ev);
      if(!k){host.innerHTML=errBox("This event is not in the footage log.","Sentry event not found");return}
      host.innerHTML=`<h1><i class="hal big"></i> ${k.reason?esc(reason(k.reason)):"Event"} · ${dayName(k.ts.slice(0,10))} · ${hms(k.ts).slice(0,5)}</h1><div class="sub">${esc([k.city,k.street].filter(Boolean).join(" · "))}</div><div class="card" style="margin-top:12px" id="dvid"></div>`;
      return stillViewer($("#dvid"),{stamps:Object.keys(k.clips).sort(),roles:k.roles,loc:k.loc,trig:Date.parse(k.cts||k.ts),head:""})}
    const stamps=Object.keys(e.clips),at=nameAt(places,[e.lat,e.lon])||[e.city,e.street].filter(Boolean).join(" · "),hasPos=e.lat!=null&&e.lon!=null&&Math.abs(+e.lat)>1;
    host.innerHTML=`<h1><i class="hal big"></i> Sentry · ${esc(e.reason?reason(e.reason):"event")} · ${dayName(e.ts.slice(0,10))} · ${hms(e.ts).slice(0,5)}</h1><div class="sub">${esc(at||"Location not recorded")}</div>
      <div class="tiles">${tile(hms(e.ts),"detected")}${tile(esc(e.reason?reason(e.reason):"-"),"reason")}${tile(n0(stamps.length)+" min","of clips")}</div>
      ${hasPos?`<div class="card" style="padding:10px"><div class="bar" style="margin:0 0 8px"><span class="sub">Where the car was parked</span><span style="margin-left:auto;display:flex;gap:6px;align-items:center"><span class="sub">Map</span>${MSZ.map(([k,t])=>`<button class="chip" data-ms="${k}">${t}</button>`).join("")}</span></div><div class="map big" id="dmap"></div></div>`:'<div class="card"><div class="sub">No position was recorded for this event, so there is no map.</div></div>'}
      <div class="card" style="margin-top:12px"><div id="dvid"></div></div>`;
    if(hasPos&&window.maplibregl){const dark=matchMedia("(prefers-color-scheme: dark)").matches,m=newMap({container:"dmap",style:MAPSTYLE(dark),center:[+e.lon,+e.lat],zoom:16,attributionControl:{compact:true}}),pos=[+e.lon,+e.lat];
      mapNav(m,()=>m.easeTo({center:pos,zoom:16}),"Back to the car");const el=document.createElement("i");el.className="hal big";new maplibregl.Marker({element:el}).setLngLat(pos).addTo(m);
      const setMs=k=>{lsSet("tc.mapsz.s",k);$$("[data-ms]",host).forEach(b=>b.classList.toggle("on",b.dataset.ms===k));$("#dmap").style.height=(MSZ.find(x=>x[0]===k)||MSZ[1])[2];m.resize();m.easeTo({center:pos,zoom:16,duration:200})};
      $$("[data-ms]",host).forEach(b=>b.onclick=()=>setMs(b.dataset.ms));setMs(lsGet("tc.mapsz.s","m"))}
    clipPlayer($("#dvid"),{stamps,cams:e.clips,loc:e.loc,trig:Date.parse(e.cts||e.ts),view:"all",hot:EVCAM[e.cam]||"front",url:(s,c)=>`/footage/${role}/SentryClips/${e.id}/${s}-${c}.mp4`,
      head:`${stamps.length} one-minute clip${stamps.length>1?"s":""}; the next plays on its own. It plays from the start; press Detection to jump to the moment the car detected something. The camera that saw it has a red border.`})
  }catch(err){host.innerHTML=errBox(err.message,"Could not open this Sentry event")}
}
// the minutes around one instant (a Saved clip's trigger) when no Tessie drive covers it: map, telemetry and video of whichever connected drive holds them
async function pageTeslaCamAt(t){
  loading();const pl=await api("places").catch(()=>({saved:[]}));
  main.innerHTML=crumbs("#/teslacam","TeslaCam")+'<div id="tdh"></div>';
  const fetchSeg=async b=>{const r=await fetch("/api/teslacam/auto/window?t0="+(t-90)+"&t1="+(t+150)+"&budget="+(b==null?20:b));if(!r.ok)throw new Error((await r.json().catch(()=>({}))).error||("HTTP "+r.status));return r.json()};
  try{await teslaDriveBody($("#tdh"),"A",fetchSeg,pl.saved||[],{})}catch(e){const h=$("#tdh");if(h)h.innerHTML=errBox(e.message,"Could not open this moment")}
}
async function pageTeslaCamDrive(role,id){
  loading();const pl=await api("places").catch(()=>({saved:[]}));
  main.innerHTML=(role==="auto"?crumbs("#/driveslist","Drives (new)"):crumbs("#/teslacam","TeslaCam"))+'<div id="tdh"></div>';
  if(role==="auto"){                // opened from the Drives list: the server says which connected drive holds it
    try{const r=await fetch("/api/teslacam/auto/seg/"+encodeURIComponent(id)+"?budget=0"),j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||("HTTP "+r.status));role=j.role||"A"}
    catch(e){$("#tdh").innerHTML=errBox(e.message,"Could not open this drive");return}}
  const fetchSeg=async b=>{const r=await fetch("/api/teslacam/"+role+"/seg/"+encodeURIComponent(id)+(b!=null?"?budget="+b:""));if(!r.ok)throw new Error((await r.json().catch(()=>({}))).error||("HTTP "+r.status));return r.json()};
  try{await teslaDriveBody($("#tdh"),role,fetchSeg,pl.saved||[],{})}catch(e){const h=$("#tdh");if(h)h.innerHTML=errBox(e.message,"Could not rebuild this drive")}
}
// The map, telemetry chart and stitched six-camera video of one drive, moving together. `fetchSeg(budget)` returns the server's segment / window answer;
// it is asked again while minutes are still being read. opts.heading=false leaves the title to the caller (the Footage tab of a Tessie drive).
// the share of the distance driven on FSD / Autosteer / TACC, from the dashcam seconds that say which mode the car was in (Tessie-filled seconds carry no mode);
// cover = the part of the distance those seconds make up
function selfDrive(s){if(!s||!s.v)return null;let all=0,known=0,f=0,a=0;
  for(let i=0;i<s.v.length;i++){const v=s.v[i];if(v==null||!(v>0))continue;all+=v;const p=s.ap?s.ap[i]:null;if(p==null)continue;known+=v;if(p===1)f+=v;else if(p===2||p===3)a+=v}
  return known?{fsd:f/known,aut:a/known,cover:all?known/all:1}:null}
// where the route's seconds came from: the dashcam (has a mode) or Tessie's points filled between them
function dataSources(s){if(!s||!s.lat)return null;let c=0,t=0;for(let i=0;i<s.lat.length;i++){if(s.lat[i]==null)continue;if(s.ap&&s.ap[i]!=null)c++;else t++}const n=c+t;return n?{cam:c/n,tess:t/n}:null}
async function teslaDriveBody(host,role,fetchSeg,places,opts){
  let d=await fetchSeg(0);if(d&&d.role)role=d.role;       // the drive the footage is on (a drive page asks for "auto")
  let player=null,map=null,chart=null,done=false,evShown=false,pendM=null,pendL=null;
  const stop=()=>{done=true};addEventListener("hashchange",stop,{once:true});
  const it=()=>d.item,S=()=>d.series,live=()=>!done&&$("#tdb")&&host.contains($("#tdb"));
  host.innerHTML=`<div id="tdb"${opts.side?' class="side"':""}><h1 id="dh" ${opts.heading===false&&!opts.tessie?"hidden":""}></h1><div class="sub" id="dsub"></div><div id="dpend"></div><div class="tiles" id="dtl"></div>
    <div id="dmapc" class="card" style="padding:10px"><div class="bar" style="margin:0 0 8px"><button class="chip on" data-cm="v">Speed</button><button class="chip" data-cm="ap">Autopilot / FSD</button><span class="sub" id="dleg"></span><span style="margin-left:auto;display:flex;gap:6px;align-items:center"><span class="sub">Map</span>${MSZ.map(([k,t])=>`<button class="chip" data-ms="${k}">${t}</button>`).join("")}</span></div><div class="map big" id="dmap"></div><div class="sub" id="dsrc" style="margin-top:6px"></div></div>
    <div id="drevw"></div><div id="drj"></div>
    <div id="dchc" class="card" style="margin-top:12px;padding:10px"><div class="bar" style="margin:0 0 6px"><b>Telemetry</b><span class="sub" id="dtip" style="min-height:18px;flex:1"></span><span style="display:flex;gap:6px;align-items:center">${TSZ.map(([k,t])=>`<button class="chip" data-ts="${k}">${t}</button>`).join("")}</span></div><canvas id="dch" style="width:100%;display:block"></canvas></div>
    <div class="card dvc" style="margin-top:12px"><div id="dvid"></div></div><div id="drv"></div><div id="dev"></div></div>`;
  const fill=()=>{if(!live())return;const x=it(),m=Math.max(1,Math.round((x.t1-x.t0)/60)),a=nameAt(places,x.p0),b=nameAt(places,x.p1);
    const T=opts.tessie,sd=selfDrive(S()),src=dataSources(S());
    $("#dh").textContent=T?driveTitle(T):(x.kind==="drive"?"Drive":x.kind==="stopped"?"Stopped, recording":"Recording")+" · "+dayName(x.start.slice(0,10))+" · "+clockHM(x.start)+" – "+clockHM(x.end);
    $("#dsub").innerHTML=(T?dayName(T.day)+" · "+T.hm+" – "+addMin(T.hm,T.min)+""+(T.pkey1?' · <a href="#/place/'+encodeURIComponent(T.pkey1)+'">where it ended</a>':"")
      :(a||b&&opts.heading!==false?esc(a||"?")+" → "+esc(b||"?"):""))+(d.connected===false?" · The drive with these clips is not connected: the map and telemetry are the saved copy, and there is no video.":"");
    const sdt=sd?tile(Math.round((sd.fsd+sd.aut)*100)+"%","self-driving · FSD "+Math.round(sd.fsd*100)+"%"+(sd.aut>=.005?" · Autosteer/TACC "+Math.round(sd.aut*100)+"%":"")+(sd.cover<.9?" · dashcam covers "+Math.round(sd.cover*100)+"% of the distance":"")):"";
    $("#dtl").innerHTML=tile(dur(T?T.min:m),"time")+(T?tile(n0(T.km,1)+" km","distance")+tile(n0(T.kwh,1)+" kWh","energy used")+tile(T.km?EF(T.whkm||T.kwh*1000/T.km)+" "+EFU():"–","efficiency")+tile(n0(T.b0)+"% → "+n0(T.b1)+"%","battery")+tile(n0(T.avg)+" / "+n0(T.max)+" km/h","average / top speed")+sdt
      :x.kind==="drive"?tile(n0(x.km,1)+" km","distance")+tile(n0(x.vmax)+" km/h","top speed")+sdt:"")+tile(n0(d.minutes.length),`clips · ${x.recent||0} Recent, ${x.saved||0} Saved`);
    $("#dsrc").innerHTML=src&&src.tess>0?`Route and speed: ${Math.round(src.cam*100)}% from TeslaCam (a point every second), ${Math.round(src.tess*100)}% from Tessie (a point every ~30 s)`:"";
    $("#dpend").innerHTML=d.pending>0?`<div class="card ixb"><div>Reading the telemetry from the video (first time only): ${n0(d.pending)} of ${n0(d.minutes.length)} minutes still to read…</div></div>`:""};
  const pts=()=>{const s=S(),o=[];for(let i=0;i<s.v.length;i++)if(s.lat[i]!=null&&Math.abs(s.lat[i])>1)o.push(i);return o};
  const clk=i=>lclock(it().t0+i,it().lo);                              // the local clock text of second i of the drive (display only)
  // second i of the drive (UTC) -> the minute of footage that holds it and the second inside it; a hole in the footage goes to the next minute
  const seekIdx=i=>{if(!player)return;const ms=d.minutes.filter(m=>m.cams&&m.cams.length);let b=null;for(const m of ms){if(m.off<=i)b=m;else break}if(!b)b=ms[0];if(b)player.seekAt(b.stamp,Math.max(0,Math.min(59,i-b.off)))};
  const cursor=sec=>{const i=Math.max(0,Math.min(S().v.length-1,Math.round(sec)));if(map)map.cursor(i);if(chart)chart.cursor(i)};
  const events=()=>{if(opts.rvtab||evShown||!d.events||!d.events.length||!live())return;evShown=true;
    $("#dev").innerHTML=`<h2>Saved clips and Sentry events in this drive</h2><div class="sub">Click one to jump the video there.</div><div class="evs">${d.events.map(v=>`<button class="evc" data-u="${v.t}">${v.thumb?`<img loading="lazy" src="/footage/${role}/${v.folder}/${v.id}/thumb.png" onerror="this.style.visibility='hidden'">`:'<div class="noimg"></div>'}<b>${hms(v.ts)}</b><span>${esc(v.folder==="SavedClips"?"Saved":"Sentry")} · ${esc(reason(v.reason))}</span><span>${esc(v.city||"")}</span></button>`).join("")}</div>`;
    $$("#dev .evc").forEach(b=>b.onclick=()=>{if(player){seekIdx(Math.round(+b.dataset.u-it().t0));$("#dvid").scrollIntoView({block:"nearest"})}})};
  RVAPI={cursor:i=>cursor(i),seek:i=>seekIdx(i),series:()=>S(),t0:()=>it().t0,clk:i=>clk(i),focus:f=>{if(chart&&chart.focus)chart.focus(f)},lanes:l=>{if(chart&&chart.lanes)chart.lanes(l);else pendL=l},zoom:(a,b)=>{if(chart&&chart.view)chart.view(a,b)},limit:(a,b)=>{if(chart&&chart.limit)chart.limit(a,b)},map:(a,b,p)=>{if(map&&map.section){map.section(a,b);map.pins(p||[])}else pendM=[a,b,p]},live:()=>live(),saved:()=>{const r=[];d.minutes.filter(m=>m.src==="saved").forEach(m=>{const a=it().t0+m.off,b=a+60,l=r[r.length-1];if(l&&a-l[1]<=2)l[1]=b;else r.push([a,b])});return r}};
  const build=()=>{if(!live())return;fill();const s=S(),idx=pts();
    if(RVOV)RVOV();
    if(!idx.length){$("#dmapc").hidden=$("#dchc").hidden=true}else{$("#dmapc").hidden=$("#dchc").hidden=false;
      if(!map)map=driveMap($("#dmap"),s,idx,seekIdx,$("#dleg"));else map.update(s,idx);if(pendM&&map.section){map.section(pendM[0],pendM[1]);map.pins(pendM[2]||[]);pendM=null}
      if(!chart){chart=telemChart($("#dch"),$("#dtip"),s,seekIdx,clk);if(pendL&&chart.lanes){chart.lanes(pendL);pendL=null}}else chart.update(s);
      const em=(opts.events||[]).map(e=>({i:Math.round(e.u-it().t0),label:e.label}));chart.events&&chart.events(em);map.events&&map.events(em)}
    const playable=d.minutes.filter(m=>m.cams&&m.cams.length);
    if(!player&&playable.length){const mm=playable,cams={},meta={};mm.forEach(m=>{cams[m.stamp]=m.cams;meta[m.stamp]=m});
      player=clipPlayer($("#dvid"),{stamps:mm.map(m=>m.stamp),cams,view:"all",trig:opts.trig,
        url:(st,c)=>meta[st].src==="saved"?`/footage/${role}/SavedClips/${meta[st].ref}/${st}-${c}.mp4`:`/footage/${role}/RecentClips/${st}-${c}.mp4`,
        loc:Object.fromEntries(mm.map(m=>[m.stamp,m.loc])),onAt:(st,sec)=>cursor(meta[st].off+sec),head:`${it().kind==="drive"?"Click the map or the chart to jump there; the dot and the line follow the video.":"The car was not moving, so there is no map."} Each minute plays on to the next, Recent and Saved alike.`})}
    else if(!player&&!playable.length&&d.minutes.length){const mm=d.minutes,cams={};mm.forEach(m=>{cams[m.stamp]=[]});
      player=clipPlayer($("#dvid"),{stamps:mm.map(m=>m.stamp),cams,view:"all",url:()=>"",loc:Object.fromEntries(mm.map(m=>[m.stamp,m.loc])),head:"The video of this drive is on no connected drive. Pick a minute to see which drives hold it."})}
    else if(!playable.length)$("#dvid").innerHTML='<div class="sub">No video for this time on a connected drive.</div>';
    events()};
  // map size and telemetry size / hide, remembered
  const setMs=k=>{lsSet("tc.mapsz",k);$$("[data-ms]",host).forEach(b=>b.classList.toggle("on",b.dataset.ms===k));$("#dmap").style.height=(MSZ.find(x=>x[0]===k)||MSZ[2])[2];if(map&&map.resize){map.resize();map.refit(250)}};
  const setTs=k=>{lsSet("tc.telsz",k);$$("[data-ts]",host).forEach(b=>b.classList.toggle("on",b.dataset.ts===k));const c=$("#dch");c.style.display=k==="h"?"none":"block";{const hh=(TSZ.find(x=>x[0]===k)||TSZ[1])[2];c.style.height=opts.rvtab&&hh!=="0"?"calc("+hh+" + 112px)":hh}dispatchEvent(new Event("resize"))};
  $$("[data-ms]",host).forEach(b=>b.onclick=()=>setMs(b.dataset.ms));$$("[data-ts]",host).forEach(b=>b.onclick=()=>setTs(b.dataset.ts));
  setMs(lsGet("tc.mapsz","l"));setTs(lsGet("tc.telsz","n"));
  $$("[data-cm]",host).forEach(b=>b.onclick=()=>{$$("[data-cm]",host).forEach(x=>x.classList.toggle("on",x===b));if(map)map.cursor&&map.mode(b.dataset.cm)});
  // ---- Review: the signs read from the video and the speed changes, drawn on the chart and listed
  let rv=null,steps=[],rvUrl=null;
  const idxOf=g=>Math.round(g.u-it().t0);                              // second of the drive a sign was seen at (both UTC)
  const rvMarks=()=>{if(!chart||!chart.marks)return;const sg=(rv?rv.signs:[]).map(g=>Object.assign({i:idxOf(g)},g));chart.marks({signs:sg,steps})};
  const rvDraw=()=>{if(!live()||!opts.review)return;const sg=rv?rv.signs:[],S0=S(),job=rv?rv.job:{},mn=rv?rv.minutes:0,sc=rv?rv.scanned:0,run=job.running;const ms=rv&&rv.missing?rv.missing:{py:[],cmd:[],hint:[]},need=ms.py.length+ms.cmd.length>0;
    steps=S0.v.length&&d.pending===0?speedSteps(S0.v):[];
    $("#drj").innerHTML=`<div class="card ixb" style="margin-top:12px"><div style="flex:1;min-width:240px"><b>Signs</b> · ${rv?(sc>=mn&&mn>0?`read in all ${n0(mn)} minutes of footage · ${n0(sg.length)} found`:sc?`read in ${n0(sc)} of ${n0(mn)} minutes · ${n0(sg.length)} found`:mn?`not read yet for this drive (${n0(mn)} minutes of footage)`:"no footage on a connected drive for this time"):"…"}${run?` · reading ${n0(job.done)} of ${n0(job.total)} minutes…`:""}${job.error&&!need?` · <span style="color:var(--bad)">${esc(job.error)}</span>`:""}
      <div class="sub" style="margin-top:4px">Round red-ringed speed-limit signs are found in a still every second while the car goes ${cfg("review.min_kmh")} km/h or more; numbers are read by machine, so check each against its picture.</div>${need?`<div class="sub" style="margin-top:6px;color:var(--bad)">This computer is missing ${esc(ms.py.concat(ms.cmd).join(", "))}. In Terminal run, then restart the app:<br><code>${ms.hint.map(esc).join("</code><br><code>")}</code></div>`:""}</div>
      ${run?'<button class="btn" id="rvx">Stop</button>':rv&&rv.connected&&mn&&!need?(sc>=mn?'<button class="btn" id="rvr">Read again</button>':'<button class="btn pri" id="rvs">Read the signs</button>'):""}</div>`;
    const go=a=>async()=>{rv=await (await fetch("/api/review",{method:"POST",body:JSON.stringify({role,t0:opts.review.t0,t1:opts.review.t1,lo:opts.review.lo,action:a})})).json();rvDraw();rvPoll()};
    if($("#rvs"))$("#rvs").onclick=go("start");if($("#rvr"))$("#rvr").onclick=go("redo");if($("#rvx"))$("#rvx").onclick=go("stop");
    rvMarks();
    const T=i=>clk(i);
    const near=x=>sg.filter(g=>g.side==="L").map(g=>({g,dt:idxOf(g)-x.i})).filter(o=>o.dt>=-30&&o.dt<=10).sort((a,b)=>Math.abs(a.dt)-Math.abs(b.dt))[0];
    $("#drv").innerHTML=(sg.length?`<h2>Signs read (${n0(sg.length)})</h2><div class="sub">Click one to jump the video to it. Dim ones were seen in a single still only.</div><div class="evs">${sg.slice(0,120).map(g=>{const i=idxOf(g),v=S0.v[i];return `<button class="evc ${g.n<2?"faded":""}" data-u="${g.u}">${g.crop?`<img loading="lazy" src="/review/${esc(g.crop)}" onerror="this.style.visibility='hidden'">`:'<div class="noimg"></div>'}<b>${esc(g.text)}${g.side==="R"?" (right side)":""}</b><span>${hms(g.t)}${v!=null?` · car ${n0(v)} km/h`:""}${S0.ap[i]?` · ${APN[S0.ap[i]]||""}`:""}</span><span>seen ${g.n}× · sure ${n0(g.conf)}%</span></button>`}).join("")}</div>${sg.length>120?`<div class="sub">The first 120 of ${n0(sg.length)} are shown.</div>`:""}`:"")
;
    $$("#drv .evc").forEach(b=>b.onclick=()=>{if(player){seekIdx(Math.round(+b.dataset.u-it().t0)-3);$("#dvid").scrollIntoView({block:"nearest"})}});
    $$("#drv .trow").forEach(b=>b.onclick=()=>{if(player){seekIdx(+b.dataset.i-6);$("#dvid").scrollIntoView({block:"nearest"})}})};
  const rvGet=async()=>{const r=await fetch("/api/review?role="+role+"&t0="+opts.review.t0+"&t1="+opts.review.t1+"&lo="+opts.review.lo);if(r.ok)rv=await r.json()};
  const rvPoll=async()=>{while(live()&&rv&&rv.job.running){await new Promise(r=>setTimeout(r,2000));if(!live())break;try{await rvGet()}catch(e){break}rvDraw()}};
  build();
  if(opts.review){rvGet().then(()=>{rvDraw();rvPoll()}).catch(()=>{})}
  while(d.pending>0&&live()){try{d=await fetchSeg()}catch(e){break}if(!live())break;build()}
  fill();if(opts.review&&rv)rvDraw();
}
function driveMap(el,s,idx,onPick,leg){
  if(!window.maplibregl){el.innerHTML='<div class="empty">Map library not loaded (offline?).</div>';return{update(){},cursor(){},mode(){},section(){},pins(){},events(){},refit(){}}}
  const dark=matchMedia("(prefers-color-scheme: dark)").matches,m=newMap({container:el,style:MAPSTYLE(dark),center:[idx.length?s.lon[idx[0]]:151,idx.length?s.lat[idx[0]]:-33],zoom:11,attributionControl:{compact:true}});
  const fitBtn=mapNav(m,()=>fit(),"Show the whole route");
  let ready=false,mode="v",S=s,I=idx,first=true,latest=null,SEC=null;      // SEC: the section Review is looking at: its own button (inward arrows) and the size buttons fit that; the outward arrows show the whole route
  const SPD=spdExpr(),AP=apExpr();
  // the line is drawn through the points smoothed over five seconds (the GPS wobbles a metre or three from one second to the next); the points themselves, the cursor and the clicks stay as recorded
  const smooth=()=>{const P=I.map(i=>[S.lon[i],S.lat[i]]),W=[1,2,3,2,1],Q=P.map(p=>p.slice());for(let k=2;k<I.length-2;k++){if(I[k+2]-I[k-2]>8)continue;let x=0,y=0,w=0;for(let j=-2;j<=2;j++){x+=P[k+j][0]*W[j+2];y+=P[k+j][1]*W[j+2];w+=W[j+2]}Q[k]=[x/w,y/w]}return Q};
  const feats=()=>{const f=[],Q=smooth();for(let k=1;k<I.length;k++){const a=I[k-1],b=I[k];if(b-a>5)continue;f.push({type:"Feature",geometry:{type:"LineString",coordinates:[Q[k-1],Q[k]]},properties:{v:S.v[b]||0,ap:S.ap[b]||0,i:b}})}return{type:"FeatureCollection",features:f}};
  const legend=()=>{leg.innerHTML=mode==="v"?spdLegend():apLegend()};
  const paint=()=>{if(ready){m.setPaintProperty("rt","line-color",mode==="v"?SPD:AP);legend()}};
  const draw=()=>{if(!ready)return;m.getSource("rt").setData(feats());
    if(I.length){m.getSource("st").setData({type:"FeatureCollection",features:[{type:"Feature",properties:{c:"#16a34a"},geometry:{type:"Point",coordinates:[S.lon[I[0]],S.lat[I[0]]]}},{type:"Feature",properties:{c:"#dc2626"},geometry:{type:"Point",coordinates:[S.lon[I[I.length-1]],S.lat[I[I.length-1]]]}}]});
      if(first){first=false;fit(0)}}};
  const fit=dur=>{if(!ready||!I.length)return;const b=new maplibregl.LngLatBounds();I.forEach(i=>b.extend([S.lon[i],S.lat[i]]));m.fitBounds(b,{padding:40,maxZoom:16,duration:dur==null?600:dur})};
  m.on("load",()=>{ready=true;m.addSource("rt",{type:"geojson",tolerance:0,data:{type:"FeatureCollection",features:[]}});m.addSource("st",{type:"geojson",data:{type:"FeatureCollection",features:[]}});m.addSource("cu",{type:"geojson",data:{type:"FeatureCollection",features:[]}});
    m.addLayer({id:"rt-case",type:"line",source:"rt",layout:{"line-cap":"round","line-join":"round"},paint:{"line-color":dark?"#000":"#fff","line-width":["interpolate",["linear"],["zoom"],3,8,8,7.5,16,9]}});
    m.addLayer({id:"rt",type:"line",source:"rt",layout:{"line-cap":"round","line-join":"round"},paint:{"line-color":SPD,"line-width":["interpolate",["linear"],["zoom"],3,5,8,4.5,16,6]}});
    m.addLayer({id:"st",type:"circle",source:"st",paint:{"circle-radius":6,"circle-color":["get","c"],"circle-stroke-width":2,"circle-stroke-color":"#fff"}});
    m.addLayer({id:"cu",type:"circle",source:"cu",paint:{"circle-radius":8,"circle-color":"#f59e0b","circle-stroke-width":3,"circle-stroke-color":"#fff"}});
    m.on("click","rt",e=>onPick(e.features[0].properties.i));m.on("mouseenter","rt",()=>m.getCanvas().style.cursor="pointer");m.on("mouseleave","rt",()=>m.getCanvas().style.cursor="");
    draw();paint()});
  // Review: the map goes to the section being looked at, and the speed / warning / road-work signs seen there stand beside the road (only while zoomed in)
  const PINZ=13.5,mk=[];
  const okp=j=>j>=0&&j<S.lat.length&&S.lat[j]!=null&&Math.abs(S.lat[j])>1;
  const near=i=>{i=Math.max(0,Math.min(S.lat.length-1,i));for(let d=0;d<=6;d++){if(okp(i-d))return i-d;if(okp(i+d))return i+d}return -1};
  const showPins=()=>{const on=m.getZoom()>=PINZ;mk.forEach(k=>{k.getElement().style.display=on?"":"none"})};
  m.on("zoom",showPins);
  const secFit=dur=>{if(!SEC)return;const bd=new maplibregl.LngLatBounds();let n=0;for(let i=Math.max(0,Math.floor(SEC[0]));i<=Math.min(S.lat.length-1,Math.ceil(SEC[1]));i++)if(okp(i)){bd.extend([S.lon[i],S.lat[i]]);n++}if(!n)return;first=false;m.fitBounds(bd,{padding:70,maxZoom:17,duration:dur==null?500:dur})};
  const secBtn=document.createElement("button");secBtn.type="button";secBtn.className="maplibregl-ctrl-icon";secBtn.hidden=true;secBtn.title="Show the section being reviewed";secBtn.setAttribute("aria-label",secBtn.title);
  secBtn.innerHTML='<svg viewBox="0 0 16 16" width="16" height="16" style="display:block;margin:6.5px auto" fill="none" stroke="#333" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M2 2l4 4M6 3v3H3M14 2l-4 4M10 3v3h3M2 14l4-4M3 10h3v3M14 14l-4-4M13 10h-3v3"/></svg>';
  secBtn.onclick=()=>secFit(500);fitBtn.parentNode.appendChild(secBtn);
  const section=(a,b)=>{SEC=[a,b];secBtn.hidden=false;secFit(500)};
  const pins=list=>{mk.splice(0).forEach(k=>k.remove());
    list.forEach(p=>{const i=near(p.i);if(i<0)return;const a=near(i-3),b=near(i+3);let h=null;if(a>=0&&b>=0&&a!==b){const dy=(S.lat[b]-S.lat[a])*110540,dx=(S.lon[b]-S.lon[a])*111320*Math.cos(S.lat[i]*Math.PI/180);if(Math.hypot(dx,dy)>=5)h=Math.atan2(dx,dy)}
      const side=p.side==="R"?1:p.side==="L"?-1:0,d=side&&h!=null?15:0,br=(h||0)+side*Math.PI/2,la=S.lat[i]+d*Math.cos(br)/110540,lo=S.lon[i]+d*Math.sin(br)/(111320*Math.cos(S.lat[i]*Math.PI/180));
      const el=document.createElement("div"),sp=p.cat==="speed";el.innerHTML=`<div class="mpin ${sp?"sp":p.cat==="roadwork"?"wn rw":"wn"}${p.conflict?" cf":""}">${sp?esc(p.text):"<span>!</span>"}</div>`;
      el.title=(sp?"Speed limit "+p.text:p.cat==="roadwork"?"Road work sign":"Warning sign")+(p.osm!=null?" · the map says "+p.osm:"")+(p.conflict?" · differs from the map":"")+(p.temp?" · probably temporary":"");
      mk.push(new maplibregl.Marker({element:el}).setLngLat([lo,la]).addTo(m))});showPins()};
  const ek=[];      // red markers where Saved clips were saved: events [{i, label}] (i = second of the drive)
  const events=list=>{ek.splice(0).forEach(k=>k.remove());
    (list||[]).forEach(e=>{const i=near(e.i);if(i<0)return;const el=document.createElement("div");el.innerHTML='<div class="mpin ev"><span>!</span></div>';el.title=e.label||"Event";ek.push(new maplibregl.Marker({element:el}).setLngLat([S.lon[i],S.lat[i]]).addTo(m))})};
  return{section,pins,events,refit:dur=>SEC?secFit(dur):fit(dur),update(s2,i2){S=s2;I=i2;draw()},fit,resize(){m.resize()},mode(x){mode=x;paint()},cursor(i){if(!ready)return;let j=i;while(j>=0&&(S.lat[j]==null||Math.abs(S.lat[j])<1))j--;if(j<0)return;m.getSource("cu").setData({type:"FeatureCollection",features:[{type:"Feature",properties:{},geometry:{type:"Point",coordinates:[S.lon[j],S.lat[j]]}}]})}};
}
// speed, pedals and steering against time, with the autopilot state as a strip; hover reads it out, click or drag moves the video
function telemChart(cv,tip,s,onPick,t0){          // t0: i -> "HH:MM:SS" on the local clock (or a base time in ms, shown on this computer's clock)
  const lab=typeof t0==="function"?t0:i=>new Date(t0+i*1000).toTimeString().slice(0,8);
  // va/vb: the part of the drive shown (vb null = all of it); A/B the same, clamped, in seconds
  let S=s,cur=null,hov=null,F=null,W=0,H=0,M={signs:[],steps:[]},EV=[],ver=0,cache=null,ck="",va=0,vb=null,A=0,B=1,sel=null,selMode=false,LIM=null,LN=null;const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim()||"#888";
  const L=62,R=10,lanes=[{k:"v",t:"km/h",y:34,h:118},{k:"p",t:"pedals",y:164,h:46},{k:"s",t:"steering",y:218,h:40}];
  const norm=()=>{const N=S.v.length-1;if(N<1){A=0;B=1;return}if(vb==null){A=LIM?LIM.a:0;B=LIM?LIM.b:N}else{A=Math.max(0,Math.min(va,N-1));B=Math.max(A+1,Math.min(vb,N))}};
  const X=i=>L+(W-L-R)*(i-A)/Math.max(1,B-A),I=x=>Math.max(0,Math.min(S.v.length-1,Math.round(A+(x-L)/(W-L-R)*(B-A))));
  const paintBase=(c,dpr)=>{c.setTransform(dpr,0,0,dpr,0,0);c.clearRect(0,0,W,H);
    const ink=css("--mut"),line=css("--line"),acc=css("--acc"),bad=css("--bad"),ok=css("--charge"),n=S.v.length;if(!n)return;
    const i0=Math.max(0,Math.floor(A)-1),i1=Math.min(n-1,Math.ceil(B)+1),sw=Math.max(1,(W-L-R)/(B-A+1)+.5),clipped=f=>{c.save();c.beginPath();c.rect(L,0,W-L-R,H);c.clip();f();c.restore()};
    const ff=getComputedStyle(document.body).fontFamily||"system-ui",F="11px "+ff;c.font=F;c.fillStyle=ink;c.strokeStyle=line;c.lineWidth=1;
    const vm=Math.max(120,Math.ceil(Math.max(...S.v.filter(x=>x!=null))/20)*20),vy=v=>lanes[0].y+lanes[0].h*(1-v/vm);
    for(let g=0;g<=vm;g+=40){c.beginPath();c.moveTo(L,vy(g));c.lineTo(W-R,vy(g));c.stroke();c.fillText(g,8,vy(g)+4)}
    // driving-mode strip on the same time scale as the chart: FSD / Autosteer / TACC / Manual in the colours of the map legend, with its key above it
    clipped(()=>{for(let i=i0;i<=i1;i++){if(S.v[i]==null)continue;const a=S.ap[i];c.fillStyle=apCol(a>0&&a<4?APN[a]:"Manual");c.fillRect(X(i),lanes[0].y-12,sw,8)}})
    c.fillStyle=ink;c.textBaseline="middle";c.fillText("FSD / AP",8,lanes[0].y-8);
    let kx=L;["FSD","Autosteer","TACC","Manual"].forEach(k=>{c.fillStyle=apCol(k);c.beginPath();c.arc(kx+4,9,4,0,7);c.fill();c.fillStyle=ink;c.fillText(k,kx+12,10);kx+=12+c.measureText(k).width+14});c.textBaseline="alphabetic";
    // speed
    const seg=(arr,y,col,fill,f)=>{c.save();c.beginPath();c.rect(L,0,W-L-R,H);c.clip();c.beginPath();let on=false,last=-9;for(let i=i0;i<=i1;i++){const v=arr[i];if(v==null){if(i-last>3)on=false;continue}const x=X(i),yy=f(v);if(!on){c.moveTo(x,yy);on=true}else c.lineTo(x,yy);last=i}c.strokeStyle=col;c.lineWidth=1.6;c.stroke();c.restore()};
    seg(S.v,0,acc,0,vy);
    // the limit signs read (a red ring at the number, level with it) and the last limit seen as a dashed line; speed changes as triangles on the floor of the lane
    const L2=M.signs.filter(g=>g.side==="L").map(g=>({i:g.i,v:+g.text})).sort((a,b)=>a.i-b.i);
    c.save();c.beginPath();c.rect(L,0,W-L-R,H);c.clip();c.setLineDash([5,4]);c.strokeStyle="#f59e0b";c.lineWidth=1.2;L2.forEach((g,k)=>{const e=k+1<L2.length?L2[k+1].i:n-1;c.beginPath();const yy=Math.max(lanes[0].y+6,vy(g.v));c.moveTo(X(g.i),yy);c.lineTo(X(e),yy);c.stroke()});c.restore();
    M.signs.forEach(g=>{if(g.i<0||g.i>=n||g.i<A-12||g.i>B+12)return;const x=X(g.i),y=Math.max(lanes[0].y+6,vy(+g.text));c.beginPath();c.arc(x,y,9,0,7);c.fillStyle="#fff";c.fill();c.lineWidth=2.2;c.strokeStyle=g.side==="R"?"#94a3b8":"#dc2626";c.stroke();c.fillStyle="#111";c.font="bold 8.5px system-ui";c.textAlign="center";c.fillText(g.text,x,y+3);c.textAlign="start"});
    c.font=F;
    M.steps.forEach(x=>{if(x.i<A-6||x.i>B+6)return;const px=X(x.i),py=lanes[0].y+lanes[0].h;c.fillStyle=x.d<0?"#dc2626":"#16a34a";c.beginPath();c.moveTo(px,py-9);c.lineTo(px-5,py);c.lineTo(px+5,py);c.fill()});
    // the moments of Saved clips (a horn, a tap on the screen): a red line down every lane, named at the foot of the speed lane
    EV.forEach(e=>{if(e.i<A-1||e.i>B+1)return;const x=X(e.i),y1=lanes[2].y+lanes[2].h;c.save();c.strokeStyle="#dc2626";c.lineWidth=2;c.beginPath();c.moveTo(x,lanes[0].y-14);c.lineTo(x,y1);c.stroke();
      if(e.label){c.font="bold 11px "+ff;const w=c.measureText(e.label).width+10,lx=Math.min(x+4,W-R-w);c.fillStyle="#dc2626";c.beginPath();c.roundRect?c.roundRect(lx,lanes[0].y+lanes[0].h-20,w,16,3):c.rect(lx,lanes[0].y+lanes[0].h-20,w,16);c.fill();c.fillStyle="#fff";c.textBaseline="middle";c.fillText(e.label,lx+5,lanes[0].y+lanes[0].h-12)}c.restore()});
    c.font=F;c.textBaseline="alphabetic";
    // brake marks over the speed lane
    clipped(()=>{c.fillStyle=bad;c.globalAlpha=.18;for(let i=i0;i<=i1;i++)if(S.br[i])c.fillRect(X(i),lanes[0].y,sw,lanes[0].h);c.globalAlpha=1});
    // pedals
    const P=lanes[1];c.fillStyle=ink;c.fillText("accel. %",8,P.y+10);c.strokeStyle=line;c.beginPath();c.moveTo(L,P.y+P.h);c.lineTo(W-R,P.y+P.h);c.stroke();seg(S.accel,0,ok,0,v=>P.y+P.h*(1-Math.min(100,v)/100));
    // steering and blinkers
    const T=lanes[2],sm=Math.max(90,Math.ceil(Math.max(...S.steer.filter(x=>x!=null).map(Math.abs))/90)*90);c.fillStyle=ink;c.fillText("steering °",8,T.y+10);c.strokeStyle=line;c.beginPath();c.moveTo(L,T.y+T.h/2);c.lineTo(W-R,T.y+T.h/2);c.stroke();seg(S.steer,0,acc,0,v=>T.y+T.h/2-(v/sm)*(T.h/2));
    clipped(()=>{c.fillStyle="#f59e0b";for(let i=i0;i<=i1;i++)if(S.blk[i])c.fillRect(X(i),T.y+T.h-4,sw,4)});c.fillStyle="#f59e0b";c.fillRect(8,T.y+T.h-9,8,4);c.fillStyle=ink;c.fillText("indicator",20,T.y+T.h-4);
    // review: one row for each kind of sign read in the stills (and traffic in our lane), a chip from the first to the last still it was seen in
    if(LN){const y0=lanes[2].y+lanes[2].h+10,names=["Limit","Warn","Blue","Green","White","Traffic"];c.textBaseline="middle";
      names.forEach((nm,k)=>{const y=y0+k*15+7;c.strokeStyle=line;c.lineWidth=1;c.beginPath();c.moveTo(L,y);c.lineTo(W-R,y);c.stroke();c.fillStyle=ink;c.fillText(nm,8,y)});
      clipped(()=>LN.forEach(q=>{const y=y0+q.k*15+7,x0=X(q.a),x1=X(q.b),w=Math.max(q.txt?30:16,x1-x0),x=(x0+x1)/2-w/2;c.fillStyle=q.col;c.beginPath();c.roundRect?c.roundRect(x,y-6,w,12,3):c.rect(x,y-6,w,12);c.fill();
        if(q.hot){c.strokeStyle="#111";c.lineWidth=1.5;c.stroke()}else if(q.col==="#f4f4f5"){c.strokeStyle=ink;c.lineWidth=1;c.stroke()}
        if(q.txt){c.fillStyle=q.col==="#f4f4f5"||q.col==="#d4a72c"?"#111":"#fff";c.textAlign="center";c.font="bold 10px system-ui";c.fillText(q.txt,x+w/2,y+.5);c.font=F;c.textAlign="start"}}));c.textBaseline="alphabetic"}
    // clock axis
    c.fillStyle=ink;{const lad=[1,2,5,10,15,30,60,120,300,600,900,1800,3600,7200],want=Math.max(2,Math.floor((W-L-R)/(B-A>=60?80:100))),step=lad.find(x=>x>=(B-A)/want)||7200;
      for(let i=Math.ceil(A/step)*step;i<=B;i+=step){const t=lab(i);c.fillText(step<60?t.slice(0,8):t.slice(0,5),Math.max(L-12,Math.min(W-R-48,X(i)-(step<60?24:12))),H-4)}}
  };
  const draw=()=>{const dpr=devicePixelRatio||1;W=cv.clientWidth;H=cv.clientHeight;if(!W||!H)return;const pw=Math.round(W*dpr),ph=Math.round(H*dpr);if(cv.width!==pw||cv.height!==ph){cv.width=pw;cv.height=ph}
    const key=[W,H,dpr,ver].join();if(!cache||ck!==key){cache=document.createElement("canvas");cache.width=pw;cache.height=ph;paintBase(cache.getContext("2d"),dpr);ck=key}
    const c=cv.getContext("2d");c.setTransform(1,0,0,1,0,0);c.clearRect(0,0,pw,ph);c.drawImage(cache,0,0);c.setTransform(dpr,0,0,dpr,0,0);
    if(F){c.save();c.beginPath();c.rect(L,0,W-L-R,H);c.clip();const fc=F.col||css("--acc");        // review: the event as a band, a tick for each still, the picked one bold
      if(F.a!=null){const x0=X(F.a),x1=X(F.b);c.fillStyle=fc;c.globalAlpha=.12;c.fillRect(x0,12,Math.max(3,x1-x0),H-30);c.globalAlpha=1}
      (F.ticks||[]).forEach((i,k)=>{const x=X(i),h=k===F.hot;c.strokeStyle=c.fillStyle=h?"#f59e0b":fc;c.globalAlpha=h?1:.5;c.lineWidth=h?2.5:1;c.beginPath();c.moveTo(x,12);c.lineTo(x,H-18);c.stroke();c.beginPath();c.arc(x,12,h?4.5:2.5,0,7);c.fill()});
      c.restore();c.globalAlpha=1}
    const mark=(i,col)=>{c.strokeStyle=col;c.lineWidth=1.5;c.beginPath();c.moveTo(X(i),12);c.lineTo(X(i),H-18);c.stroke()};
    if(hov!=null&&hov>=A&&hov<=B)mark(hov,"#888");if(cur!=null&&cur>=A&&cur<=B)mark(cur,"#f59e0b");
    if(sel&&sel.a!==sel.b){const x0=X(Math.min(sel.a,sel.b)),x1=X(Math.max(sel.a,sel.b));c.fillStyle=css("--acc");c.globalAlpha=.18;c.fillRect(x0,12,x1-x0,H-30);c.globalAlpha=1;c.strokeStyle=css("--acc");c.lineWidth=1;c.strokeRect(x0,12,x1-x0,H-30)}};
  const read=i=>{const t=lab(i);if(S.v[i]==null)return t+" · no telemetry";return `${t} · ${n0(S.v[i])} km/h · accelerator ${n0(S.accel[i])}%${S.br[i]?" · braking":""} · steering ${n0(S.steer[i])}°${S.blk[i]?" · indicator":""}${APN[S.ap[i]]?" · "+APN[S.ap[i]]:""}`};
  // ---- zoom: + / − / Full, Select (drag across the chart; Shift-drag does the same), ◀ ▶ to move along, ⌘/Ctrl + wheel or pinch around the pointer, sideways scroll to move
  const bar=cv.parentNode.querySelector(".bar"),zm=document.createElement("span");zm.style.cssText="display:flex;gap:6px;align-items:center";
  zm.innerHTML='<span class="sub" data-z="lab"></span><button class="chip" data-z="L" title="Move back along the drive">\u25C0</button><button class="chip" data-z="R" title="Move forward along the drive">\u25B6</button><button class="chip" data-z="out" title="Zoom out">\u2212</button><button class="chip" data-z="in" title="Zoom in">+</button><button class="chip" data-z="sel" title="Drag across the chart to zoom into that part (Shift-drag does the same)">Select</button><button class="chip" data-z="full" title="The whole drive">Full</button>';
  if(bar)bar.insertBefore(zm,bar.lastElementChild);
  const zb=k=>zm.querySelector('[data-z="'+k+'"]');
  const zoomed=()=>vb!=null,ui=()=>{const w=B-A,N=Math.max(1,S.v.length-1);zb("lab").textContent=zoomed()?lab(Math.round(A))+" \u2013 "+lab(Math.round(B))+" \u00b7 "+(N/w).toFixed(1)+"\u00d7":"";
    zb("L").disabled=zb("R").disabled=zb("out").disabled=zb("full").disabled=!zoomed();zb("in").disabled=w<=12;zb("sel").classList.toggle("on",selMode);cv.style.cursor=selMode?"crosshair":""};
  const setView=(a,b)=>{const N=S.v.length-1;
    if(LIM){const full=LIM.b-LIM.a,w=Math.max(Math.min(10,full),Math.min(b-a,full));a=Math.max(LIM.a,Math.min(a,LIM.b-w));if(w>=full-1){va=0;vb=null}else{va=a;vb=a+w}norm();ver++;ui();draw();return}
    const w=Math.max(10,b-a);if(w>=N-1){va=0;vb=null}else{a=Math.max(0,Math.min(a,N-w));va=a;vb=a+w}norm();ver++;ui();draw()};
  const zoomAt=(c,f)=>setView(c-(c-A)*f,c-(c-A)*f+(B-A)*f);
  const mid=()=>cur!=null&&cur>=A&&cur<=B?cur:(A+B)/2;
  zb("in").onclick=()=>zoomAt(mid(),.5);zb("out").onclick=()=>zoomAt(mid(),2);zb("full").onclick=()=>setView(0,1e9);
  zb("L").onclick=()=>setView(A-(B-A)/2,B-(B-A)/2);zb("R").onclick=()=>setView(A+(B-A)/2,B+(B-A)/2);
  zb("sel").onclick=()=>{selMode=!selMode;ui()};
  cv.onmousemove=e=>{const r=cv.getBoundingClientRect();hov=I(e.clientX-r.left);tip.textContent=read(hov);if(sel)sel.b=hov;draw();if(e.buttons&1&&!sel)onPick(hov)};
  cv.onmouseleave=()=>{hov=null;tip.textContent=cur!=null?read(cur):"";draw()};
  cv.onmousedown=e=>{const r=cv.getBoundingClientRect(),i=I(e.clientX-r.left);if(selMode||e.shiftKey){sel={a:i,b:i};e.preventDefault();return}onPick(i)};
  addEventListener("mouseup",()=>{if(!sel)return;const a=Math.min(sel.a,sel.b),b=Math.max(sel.a,sel.b),px=X(b)-X(a);sel=null;selMode=false;if(px>=8&&b-a>=3)setView(a,b);else{ui();draw()}});     // a drag of a few pixels is a slip, not a selection
  cv.addEventListener("wheel",e=>{const r=cv.getBoundingClientRect();
    if(e.ctrlKey||e.metaKey){e.preventDefault();zoomAt(A+(e.clientX-r.left-L)/(W-L-R)*(B-A),e.deltaY<0?.8:1.25)}
    else if(zoomed()&&Math.abs(e.deltaX)>Math.abs(e.deltaY)){e.preventDefault();const d=(B-A)*e.deltaX/(W-L-R);setView(A+d,B+d)}},{passive:false});
  norm();ui();addEventListener("resize",draw);draw();
  return{update(s2){S=s2;ver++;norm();ui();draw()},marks(m){M=m;ver++;draw()},events(l){EV=l||[];ver++;draw()},lanes(l){LN=l;ver++;draw()},focus(f){F=f;draw()},view(a,b){setView(a,b)},limit(a,b){const N=S.v.length-1;LIM=a==null?null:{a:Math.max(0,Math.min(a,N-1)),b:Math.max(1,Math.min(b,N))};va=0;vb=null;norm();ver++;ui();draw()},cursor(i){cur=i;if(zoomed()&&(i<A||i>B)){const w=B-A;setView(i-w*.2,i+w*.8)}if(hov==null)tip.textContent=read(i);draw()}};
}
// lasting changes of speed: the median of the 6 s before against the 6 s after, 10 km/h or more, both while moving; one per 20 s (the biggest wins)
function speedSteps(v,minD=10){
  const med=a=>{a=a.filter(x=>x!=null).sort((x,y)=>x-y);return a.length>=3?a[a.length>>1]:null},c=[];
  for(let i=8;i<v.length-8;i++){const a=med(v.slice(i-8,i-2)),b=med(v.slice(i+3,i+9));if(a==null||b==null||a<20||b<20)continue;if(Math.abs(b-a)>=minD)c.push({i,pre:a,post:b,d:b-a})}
  c.sort((x,y)=>Math.abs(y.d)-Math.abs(x.d));const out=[];for(const x of c)if(!out.some(o=>Math.abs(o.i-x.i)<20))out.push(x);return out.sort((x,y)=>x.i-y.i);
}

// ---------- Queue
function pageQueue(){
  const q=qget();
  main.innerHTML=`<h1>Footage queue</h1><div class="sub">Copy, delete and save-as-clip actions added on the drive pages. Preview shows what would happen; nothing changes until you run it.</div>
   ${q.length?`<div class="card q"><ul>${q.map((a,i)=>`<li><span class="t ${a.type}">${a.type}</span> ${esc(a.label)} · ${a.items.length} minute(s)${a.dest?" → "+esc(a.dest):""} <button class="btn" data-rm="${i}">remove</button></li>`).join("")}</ul>
   <div class="bar"><button class="btn" id="pv">Preview (changes nothing)</button><button class="btn bad" id="rn">Run for real</button><button class="btn" id="cl">Clear</button></div><pre class="out" id="out" hidden></pre></div>`:'<div class="empty">The queue is empty. Open a drive, select footage minutes and choose copy / delete / save.</div>'}`;
  if(!q.length)return;
  $$("[data-rm]").forEach(b=>b.onclick=()=>{const n=qget();n.splice(+b.dataset.rm,1);localStorage.setItem(QK,JSON.stringify(n));pageQueue();badge()});
  $("#cl").onclick=()=>{localStorage.setItem(QK,"[]");pageQueue();badge()};
  const run=async apply=>{const o=$("#out");o.hidden=false;o.textContent=apply?"Running…":"Checking…";
    try{const r=await fetch("/api/queue/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({actions:qget(),apply,purge:qget().some(a=>a.type==="delete")})}),j=await r.json();o.textContent=j.output||j.error||"(no output)";
      if(apply&&j.ok){localStorage.setItem(QK,"[]");o.textContent+="\n\nDone. Queue cleared.";badge()}}catch(e){o.textContent="Could not reach the app: "+e}};
  $("#pv").onclick=()=>run(false);$("#rn").onclick=()=>{if(confirm("Run these actions for real? Deletes are flagged and then purged from the Archive."))run(true)};
}
function badge(){const n=qget().length,b=$("#qn");b.hidden=!n;b.textContent=n}

// ---------- router

// ---------- Software: Tesla software versions from Teslascope (lib/app_software.py): the versions a region has, when a feature reached it, what a jump from one version to another brings
let SWS={region:"ANZ",car:"",tab:"versions",q:"",a:"",b:"",since:"",fsd:""};
try{Object.assign(SWS,JSON.parse(localStorage.getItem("sw.state")||"{}"))}catch(e){}
const swSave=()=>{try{localStorage.setItem("sw.state",JSON.stringify(SWS))}catch(e){}};
let SWPOLL=null;
const swQ=(extra)=>{const[m,h]=(SWS.car||"|").split("|");return "region="+encodeURIComponent(SWS.region)+(m?"&model="+encodeURIComponent(m):"")+(h?"&hw="+encodeURIComponent(h):"")+(extra||"")};
const swCountry=c=>{try{return new Intl.DisplayNames(["en"],{type:"region"}).of(String(c).toUpperCase())+" ("+String(c).toUpperCase()+")"}catch(e){return String(c).toUpperCase()}};
const swKey=v=>String(v).split(".").map(x=>x.padStart(5,"0")).join(".");
const swDate=iso=>iso?new Date(iso).toLocaleDateString("en-AU",{day:"numeric",month:"short",year:"numeric"}):"–";
const swFsd=(c,hw)=>(c||[]).map(f=>`<span class="pill fsd" title="The FSD (Supervised) version this software includes${f.hw?" on "+esc(f.hw):""}">FSD ${/^\d+(\.\d+)*$/.test(f.v)?"v":""}${esc(f.v)}${f.hw&&!hw?" · "+esc(f.hw):""}</span>`).join(" ");
const swMine=m=>m?'<span class="pill me" title="The software your car is on">Your car</span>':"";
const swDiff=g=>g.map(x=>{const t=esc(x.text).replace(/\n/g,"<br>");return x.op==="add"?`<b class="add">${t}</b>`:x.op==="del"?`<s class="del">${t}</s>`:t}).join("");
const swChanged=d=>!!d&&d.some(x=>x.op!=="same");
// the text of a note: when it differs from the text it had just before in the chain, the new words are bold and the dropped words struck through
const swBody=it=>swChanged(it.diff)?`<div class="sub" style="margin-bottom:4px">Compared with the text on ${esc(it.prev_tag)}: <b>bold</b> is new, <s>struck through</s> was removed.</div>${swDiff(it.diff)}${it.chain&&it.chain.length>2?`<details style="margin-top:6px"><summary class="sub">Each change on the way</summary>${it.chain.map(c=>`<div class="sub" style="margin:6px 0 2px"><b>${esc(c.tag)}</b></div>${c.diff?swDiff(c.diff):"<div>The text at the start.</div>"}`).join("")}</details>`:""}`:esc(it.text||it.description||"").replace(/\n/g,"<br>");
// where a note is listed: car-type and country lozenges (the chosen car and the chosen region's countries first and in the accent colour)
let SWRC=[];
// the page opens on your car (config software.my_car) until a car has been chosen by hand
function swDefaultCar(my){if(!SWS.carSet&&my&&my.model){SWS.car=my.model+"|"+(my.hw||"");SWS.carSet=1;swSave()}}
function swScope(it){
  const sc=it.scope;if(!sc)return"";
  const [m0,h0]=(SWS.car||"|").split("|"),mm=m0||"",hh=h0||"";
  const isMine=c=>mm?(c.model===mm&&(!hh||!c.hw||c.hw===hh)):false;
  const cars=sc.all_cars?[]:sc.cars.slice().sort((a,b)=>isMine(b)-isMine(a));
  const MAXC=3,MAXN=8;
  const carPills=sc.all_cars?'<span class="pill" title="Listed for every car type">All cars</span>':cars.slice(0,MAXC).map(c=>`<span class="pill ${isMine(c)?"in":""}" title="${isMine(c)?"Your car":"Car type"}">${esc(c.label)}</span>`).join("")+(cars.length>MAXC?`<span class="pill dim" title="${esc(cars.slice(MAXC).map(c=>c.label).join(", "))}">+${cars.length-MAXC} car types</span>`:"");
  const rc=new Set(SWRC),cs=sc.all_countries?[]:sc.countries.slice().sort((a,b)=>(rc.has(b)-rc.has(a))||a.localeCompare(b));
  const cl=c=>`<span class="pill ${rc.has(c)?"in":"dim"}" title="${esc(swCountry(c))}">${esc(c.toUpperCase())}</span>`;
  const ctPills=sc.all_countries?'<span class="pill" title="Listed in every country Teslascope reports">All countries</span>':cs.slice(0,MAXN).map(cl).join("")+(cs.length>MAXN?`<span class="pill dim" title="${esc(cs.slice(MAXN).map(swCountry).join(", "))}">+${cs.length-MAXN} countries</span>`:"");
  return `<div class="swl">${carPills}<span class="swsep"></span>${ctPills}</div>`}
function swNotes(items,empty){return items.length?items.map(it=>`<details class="swn"><summary><b>${esc(it.title)}</b> <span class="sub">${it.first_tag&&it.first_tag!==it.tag?"first listed "+esc(it.first_tag)+" · ":""}listed on ${esc(it.tag)}${it.was_tag?" · was "+esc(it.was_title)+" on "+esc(it.was_tag):swChanged(it.diff)?" · text changed since "+esc(it.prev_tag):""}</span>${swScope(it)}</summary><div class="swt">${swBody(it)}${it.versions?`<div class="sub" style="margin-top:6px">On ${it.versions.map(esc).join(", ")}${it.reaches_b?". Cars were seen going from one of these to the new version.":"."}</div>`:""}</div></details>`).join(""):`<div class="sub">${empty}</div>`}
// the fetch from Teslascope, with a progress bar and what it is doing, in words
function swSync(host,info,after){
  clearTimeout(SWPOLL);let was=false;
  const paint=j=>{if(!document.body.contains(host))return;
    if(j&&j.running){const pct=j.total?Math.round(100*j.done/j.total):0;
      host.innerHTML=`<div class="card ixb"><div style="flex:1;min-width:0"><b id="swh">${j.total?"Fetching version "+n0(Math.min(j.done+1,j.total))+" of "+n0(j.total)+(j.what?": "+esc(j.what):""):"Asking Teslascope for its list of versions…"}</b><div style="height:6px;border-radius:3px;background:var(--line,#ddd);margin:8px 0;overflow:hidden"><div id="swp" style="height:100%;width:${pct}%;background:var(--accent,#3b6cf6);transition:width .3s"></div></div></div><button class="btn" id="swx">Stop</button></div>`;
      $("#swx",host).onclick=async()=>{await fetch("/api/software/sync/stop",{method:"POST"});poll()};return}
    let h="";
    if(j&&j.error)h+=errBox(j.error,"The fetch stopped");
    else if(j&&j.ended&&was){const r=j.result||{};h+=`<div class="sub" style="margin-bottom:8px">${r.refused?"Teslascope refused the request. Nothing was changed; try again later.":r.list_ok===false?"Teslascope's list could not be read; the one held from last time was used.":"Fetched "+n0(r.asked||0)+" version"+((r.asked||0)===1?"":"s")+(r.failed?", "+n0(r.failed)+" could not be fetched":"")+(r.left?"; "+n0(r.left)+" more are not held yet":"")+"."}</div>`}
    const left=info&&info.left;
    h+=`<div class="bar"><button class="btn" id="swg">${left?"Fetch versions":"Check for new versions"}</button><span class="sub">${left?n0(left)+" version"+(left===1?" is":"s are")+" not held yet. Fetching asks Teslascope about one a second.":"Looks for new versions and for ones whose notes changed."}</span></div>`;
    host.innerHTML=h;$("#swg",host).onclick=async()=>{$("#swg",host).disabled=true;was=true;let r;try{r=await (await fetch("/api/software/sync",{method:"POST",body:"{}"})).json()}catch(e){r={ok:false,error:"Could not reach the app."}}if(r.ok===false){paint({error:r.error});return}poll()}};
  const poll=async()=>{clearTimeout(SWPOLL);let j;try{j=await (await fetch("/api/software/sync")).json()}catch(e){return}
    if(!document.body.contains(host))return;
    if(j.running){was=true;if(!$("#swh",host))paint(j);else{const pct=j.total?Math.round(100*j.done/j.total):0;$("#swh",host).textContent=j.total?"Fetching version "+n0(Math.min(j.done+1,j.total))+" of "+n0(j.total)+(j.what?": "+j.what:""):"Asking Teslascope for its list of versions…";$("#swp",host).style.width=pct+"%"}SWPOLL=setTimeout(poll,1200);return}
    if(was&&j.ended){bust("software");paint(j);if(after&&!j.error)after();was=false;return}
    paint(j.ended&&was?j:null)};
  paint(null);poll()}
// the car's own journey: each version it was on, with its FSD, since when, and a link that compares one step with the next
function swJourneyBar(o,compare){
  const J=o.journey||[];if(!J.length)return"";
  const when=j=>j.between&&j.between[0]!==j.between[1]?"between "+swDate(j.between[0])+" and "+swDate(j.between[1]):"from "+swDate(j.from);
  return `<div class="card swj" style="margin:10px 0"><b>Your car</b>${J.map((j,i)=>`${i?(compare&&J[i-1].held&&j.held?`<div><button class="chip nw" data-jc="${esc(J[i-1].version)}>${esc(j.version)}" title="What this update brought: compare ${esc(J[i-1].version)} with ${esc(j.version)}">⇄ compare</button></div>`:'<div class="sub">↓</div>'):""}<div class="swjv"><a href="#/software/${encodeURIComponent(j.version)}"><b>${esc(j.version)}</b></a> ${swFsd(j.fsd_chip,"")} <span class="sub" title="${esc(j.note||"")}">${when(j)}${j.current?" · now":""}</span></div>`).join("")}${o.staged?`<div class="sub">↓ update on the way: <a href="#/software/${encodeURIComponent(o.staged)}">${esc(o.staged)}</a></div>`:""}</div>`}
const swPick=()=>`<span class="sub"><b>${esc(SWS.a||"–")}</b> → <b>${esc(SWS.b||"–")}</b></span> <button class="chip" id="swgo">Compare</button>`;
// versions over time: one lane per version (newest on top), a bar from the day it was first seen to the last day it still got installs, and a line for each upgrade step cars were seen taking
function swChart(host,nodes,edges,mine,journey){
  if(!nodes.length){host.innerHTML='<div class="empty">No versions match.</div>';return}
  const DAY=864e5,J=(journey||[]).filter(j=>nodes.some(n=>n.version===j.version)),jt=J.map(j=>Date.parse(j.between&&j.between[0]||j.from)).filter(x=>!isNaN(x)),t0=Math.min(...nodes.map(n=>n.t0),...jt),t1=Math.max(...nodes.map(n=>n.t1),Date.now()),W=Math.max(host.clientWidth||0,820),L=118,R=120,T=30,RH=22,H=T+nodes.length*RH+10;
  const X=t=>L+(t-t0)/(t1-t0||1)*(W-L-R),row=new Map(nodes.map((n,i)=>[n.version,i])),Y=v=>T+row.get(v)*RH+RH/2;
  const byV=new Map(nodes.map(n=>[n.version,n])),ticks=[];for(let d=new Date(t0);d.getTime()<=t1;d=new Date(d.getFullYear(),d.getMonth()+1,1)){const m=new Date(d.getFullYear(),d.getMonth(),1);if(m.getTime()>=t0-DAY*31)ticks.push(m)}
  const lines=edges.filter(e=>byV.has(e.src)&&byV.has(e.dst)&&e.installs>=3).map(e=>{const a=byV.get(e.src),b=byV.get(e.dst),x2=X(b.t0);let x1=Math.min(X(a.t1),x2-6);const y1=Y(e.src),y2=Y(e.dst),xm=(x1+x2)/2;
    return `<path class="swe" data-s="${esc(e.src)}" data-d="${esc(e.dst)}" d="M${x1.toFixed(1)} ${y1} C${xm.toFixed(1)} ${y1} ${xm.toFixed(1)} ${y2} ${x2.toFixed(1)} ${y2}" fill="none" style="stroke:var(--mut);stroke-opacity:.28;stroke-width:${(.6+Math.log10(e.installs+1)*.9).toFixed(1)}"><title>${esc(e.src)} → ${esc(e.dst)}: ${n0(e.installs)} cars</title></path>`}).join("");
  const bars=nodes.map(n=>{const x=X(n.t0),w=Math.max(6,X(n.t1)-x),y=Y(n.version),op=Math.max(.35,Math.min(1,.35+.65*(n.region_installs||0)/30));
    return `<g class="swb" data-v="${esc(n.version)}" style="cursor:pointer"><text x="${L-8}" y="${y+4}" text-anchor="end" style="fill:var(--ink);font-size:12px;font-weight:${n.version===mine?700:400}">${esc(n.version)}</text>
      <rect x="${x.toFixed(1)}" y="${y-7}" width="${w.toFixed(1)}" height="14" rx="4" style="fill:${n.fsd.length?"var(--acc)":"var(--mut)"};fill-opacity:${op};${n.version===mine?"stroke:var(--ink);stroke-width:1.5":""}"/>
      ${n.fsd.length?`<text x="${(x+w+6).toFixed(1)}" y="${y+4}" style="fill:var(--mut);font-size:11px">FSD ${/^\d+(\.\d+)*$/.test(n.fsd[0].v)?"v":""}${esc(n.fsd[0].v)}</text>`:""}</g>`}).join("");
  let lastX=-99;
  const axis=ticks.map(m=>{const x=X(Math.max(m.getTime(),t0)),show=x-lastX>=56;if(show)lastX=x;return `<line x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${T-6}" y2="${H-8}" style="stroke:var(--line)"/><text x="${(x+4).toFixed(1)}" y="${T-12}" style="fill:var(--mut);font-size:11px">${show?MON[m.getMonth()]+" "+m.getFullYear():""}</text>`}).join("");
  // the car's own journey: the time it was on each version as an outlined span in that version's lane (a light band where only a window is known), joined from one to the next
  const jx=t=>X(Date.parse(t)),jl=J.map((j,i)=>{const y=Y(j.version),xa=jx(j.from),xb=j.to?jx(j.to):X(Date.now()),w0=j.between&&j.between[0]!==j.between[1]?jx(j.between[0]):null;
    const nx=J[i+1],link=nx?`<path d="M${xb.toFixed(1)} ${y} C${(xb+14).toFixed(1)} ${y} ${(xb+14).toFixed(1)} ${Y(nx.version)} ${jx(nx.from).toFixed(1)} ${Y(nx.version)}" fill="none" style="stroke:var(--ink);stroke-width:2.2"/>`:"";
    return `${w0!=null?`<rect x="${w0.toFixed(1)}" y="${y-11}" width="${Math.max(3,xa-w0).toFixed(1)}" height="22" rx="3" style="fill:var(--ink);fill-opacity:.16"><title>The car got ${esc(j.version)} between ${swDate(j.between[0])} and ${swDate(j.between[1])}</title></rect>`:""}<rect x="${xa.toFixed(1)}" y="${y-10}" width="${Math.max(4,xb-xa).toFixed(1)}" height="20" rx="5" fill="none" style="stroke:var(--ink);stroke-width:2"><title>Your car on ${esc(j.version)}: ${swDate(j.from)} to ${j.to?swDate(j.to):"now"}</title></rect>${link}`}).join("");
  host.innerHTML=`<div class="sub" style="margin-bottom:6px"><b style="color:var(--ink)">▭</b> your car on that version &nbsp; <span class="pill fsd">FSD</span> includes an FSD version &nbsp; <b style="color:var(--mut)">▬</b> no FSD &nbsp; faded: few cars in this region &nbsp; lines: cars seen going from one version to the next (thicker = more cars). Dates are when Teslascope saw the version on any car in any region; the region and car choose which versions and steps appear.</div>
   <div style="overflow-x:auto"><svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" style="display:block">${axis}${lines}${bars}${jl}</svg></div>`;
  $$(".swb",host).forEach(g=>{const n=byV.get(g.dataset.v);g.onclick=()=>location.hash="#/software/"+encodeURIComponent(n.version);
    g.onmousemove=ev=>{showTip(ev,`<b>${esc(n.version)}</b>${n.fsd.length?" · FSD "+esc(n.fsd.map(f=>f.v).join(", ")):""}<br>First seen ${swDate(n.first)}<br>Last installs ${n.last?dayName(n.last):"–"}<br>${n0(n.region_installs)} cars in ${esc(SWS.region)}, ${n0(n.installs)} in all regions`);$$(".swe",host).forEach(p=>{p.style.strokeOpacity=p.dataset.s===n.version||p.dataset.d===n.version?".8":".08"})};
    g.onmouseleave=()=>{hideTip();$$(".swe",host).forEach(p=>p.style.strokeOpacity=".28")}})}
async function pageSoftware(arg){
  if(arg)return pageSoftwareOne(decodeURIComponent(arg));
  loading();const o=await fetchJ("software?"+swQ());
  if(o.empty){main.innerHTML=`<h1>Software</h1><div class="sub">Tesla software versions, their release notes and who has them, from Teslascope. None are held yet.</div><div id="sws" style="margin-top:10px"></div>`;swSync($("#sws"),{left:1},()=>{bust("software");pageSoftware()});return}
  if(o.error){main.innerHTML=`<h1>Software</h1>`+errBox(o.error,"Could not read the versions");return}
  if(!o.choices.regions.some(r=>r.name===SWS.region)&&!o.choices.countries.some(c=>c.country===SWS.region)){SWS.region=o.choices.regions[0].name;swSave();return pageSoftware()}      // a remembered name that is no longer a region (it was "Down Under")
  const ch=o.choices,vs=o.versions,keys=vs.map(v=>v.version);swDefaultCar(ch.my_car&&ch.models.some(m=>m.model===ch.my_car.model&&(m.hw||"")===(ch.my_car.hw||""))?ch.my_car:null);{const rg=ch.regions.find(r=>r.name===SWS.region);SWRC=rg?rg.countries:[SWS.region]}
  if(!SWS.a||!keys.includes(SWS.a))SWS.a=keys[Math.min(keys.length-1,Math.max(0,keys.indexOf(o.mine)+3))]||keys[keys.length-1]||"";
  if(!SWS.b||!keys.includes(SWS.b))SWS.b=o.mine&&keys.includes(o.mine)?o.mine:keys[0]||"";
  const carOpts=`<option value="">All cars</option>`+ch.models.map(m=>`<option value="${esc(m.model+"|"+(m.hw||""))}">${esc(m.label)}${ch.my_car&&m.model===ch.my_car.model&&(m.hw||"")===(ch.my_car.hw||"")?" (my car)":""}</option>`).join("");
  main.innerHTML=`<h1>Software</h1><div class="sub">Tesla software versions from Teslascope: ${n0(o.held.n)} held, from ${esc(o.held.oldest||"–")} to ${esc(o.held.newest||"–")}.${o.mine?" Your car is on "+esc(o.mine)+".":""} Car counts are cars Teslascope has seen, so they are a sample, not every car.</div>
   <div id="sws" style="margin-top:8px"></div>
   <div class="bar"><select id="swr"><optgroup label="Regions">${ch.regions.map(r=>`<option value="${esc(r.name)}">${esc(r.name)} (${r.countries.length})</option>`).join("")}</optgroup><optgroup label="Countries">${ch.countries.map(c=>`<option value="${esc(c.country)}">${esc(swCountry(c.country))}</option>`).join("")}</optgroup></select><select id="swc">${carOpts}</select>
    <button class="chip ${SWS.tab==="versions"?"on":""}" data-t="versions">Versions</button><button class="chip ${SWS.tab==="timeline"?"on":""}" data-t="timeline">Timeline</button><button class="chip ${SWS.tab==="feature"?"on":""}" data-t="feature">When a feature arrived</button><button class="chip ${SWS.tab==="compare"?"on":""}" data-t="compare">Compare two versions</button></div>
   <div class="bar" id="swfl"><label class="sub">First seen from <input type="date" id="swd" value="${esc(SWS.since)}"></label><button class="chip ${SWS.fsd===""?"on":""}" data-f="">Any FSD</button><button class="chip ${SWS.fsd==="yes"?"on":""}" data-f="yes">With FSD</button><button class="chip ${SWS.fsd==="no"?"on":""}" data-f="no">Without FSD</button><span class="sub" id="swsh"></span></div>
   <div id="swb"></div>`;
  $("#swr").value=SWS.region;$("#swc").value=SWS.car;
  $("#swr").onchange=e=>{SWS.region=e.target.value;swSave();bust("software");pageSoftware()};$("#swc").onchange=e=>{SWS.car=e.target.value;SWS.carSet=1;swSave();pageSoftware()};
  $$("[data-t]").forEach(b=>b.onclick=()=>{SWS.tab=b.dataset.t;swSave();$$("[data-t]").forEach(x=>x.classList.toggle("on",x===b));body()});
  swSync($("#sws"),o,()=>{bust("software");pageSoftware()});
  const carName=(SWS.car?(ch.models.find(m=>m.model+"|"+(m.hw||"")===SWS.car)||{}).label:"")||"any car";
  const vf=()=>vs.filter(v=>(!SWS.since||(v.first||"").slice(0,10)>=SWS.since)&&(!SWS.fsd||(SWS.fsd==="yes")===(v.fsd.length>0)));
  $("#swd").onchange=e=>{SWS.since=e.target.value;swSave();body()};
  $$("[data-f]").forEach(b=>b.onclick=()=>{SWS.fsd=b.dataset.f;swSave();$$("[data-f]").forEach(x=>x.classList.toggle("on",x===b));body()});
  const body=()=>{const host=$("#swb"),vl=vf(),pick=SWS.tab==="versions"||SWS.tab==="timeline";
    $("#swfl").style.display=pick?"":"none";
    $("#swsh").textContent=pick?"Showing only what "+carName+" in "+SWS.region+" can install: "+n0(vl.length)+" of "+n0(vs.length)+" versions.":"";
    if(SWS.tab==="timeline"){
      host.innerHTML='<div class="sub">Loading…</div>';
      fetchJ("software/timeline?"+swQ()).then(t=>{if(!document.body.contains(host)||SWS.tab!=="timeline")return;
        const mk=v=>({version:v.version,first:v.first,t0:Date.parse(v.first),last:t.last[v.version]||null,t1:Date.parse((t.last[v.version]||(v.first||"").slice(0,10))+"T23:59:59Z"),fsd:v.fsd,region_installs:v.region_installs,installs:v.installs});
        const have=new Set(vl.map(v=>v.version)),inJ=t.journey.filter(j=>!SWS.since||!j.to||j.to.slice(0,10)>=SWS.since);
        const nodes=vl.concat(inJ.filter(j=>!have.has(j.version)&&t.extra[j.version]).map(j=>t.extra[j.version])).map(mk).filter(n=>!isNaN(n.t0)).sort((a,b)=>b.t0-a.t0);
        host.innerHTML=swJourneyBar(o,true)+'<div id="swch"></div>';swChart($("#swch"),nodes,t.edges,o.mine,inJ);
        $$("[data-jc]",host).forEach(b=>b.onclick=()=>{const[a,z]=b.dataset.jc.split(">");SWS.a=a;SWS.b=z;SWS.tab="compare";swSave();$$("[data-t]").forEach(x=>x.classList.toggle("on",x.dataset.t==="compare"));body()})});return}
    if(SWS.tab==="versions"){
      host.innerHTML=`${swJourneyBar(o,true)}<div class="tiles">${tile(n0(vl.length),"versions for "+esc(SWS.region))}${tile(n0(vl.filter(v=>v.fsd.length).length),"include FSD")}${tile(vl[0]?esc(vl[0].version):"–","newest")}</div><div class="bar" id="swpick">${swPick()}</div><div id="swt"></div>`;
      table($("#swt"),[{k:"vk",t:"Version",l:1,f:r=>`<b>${esc(r.version)}</b> ${swMine(r.mine)}`},{k:"fsdk",t:"FSD",l:1,f:r=>swFsd(r.fsd,(SWS.car.split("|")[1]||""))||'<span class="sub">–</span>'},{k:"first",t:"First seen",f:r=>swDate(r.first)},{k:"region_installs",t:"Cars in "+esc(SWS.region),f:r=>n0(r.region_installs)},{k:"installs",t:"Cars, all regions",f:r=>n0(r.installs)},{k:"cmp",t:"Compare",l:1,f:r=>`<a href="#" class="pill" data-cf="${esc(r.version)}" title="Compare from this version">From</a> <a href="#" class="pill" data-ct="${esc(r.version)}" title="Compare to this version">To</a>`},{k:"via",t:"Known from",l:1,f:r=>r.has_notes?(r.region_installs?"cars and notes":"notes"):"cars"}],
        vl.map(v=>Object.assign({vk:swKey(v.version),fsdk:v.fsd.length?v.fsd[0].v:""},v)),r=>location.hash="#/software/"+encodeURIComponent(r.version),"vk");
    }else if(SWS.tab==="feature"){
      host.innerHTML=`<div class="bar"><input type="search" id="swq" placeholder="Search release notes, for example welcome animation" value="${esc(SWS.q)}" style="min-width:320px"></div><div id="swf"></div>`;
      let tm=null;const go=async()=>{const q=SWS.q.trim();if(q.length<3){$("#swf").innerHTML='<div class="sub">Type at least three letters of a feature name.</div>';return}
        const r=await fetchJ("software/first?region="+encodeURIComponent(SWS.region)+"&q="+encodeURIComponent(q));if(!document.body.contains($("#swf")))return;
        if(!r.rows.length){$("#swf").innerHTML=`<div class="empty">No release note for ${esc(SWS.region)} mentions that.</div>`;return}
        const by=new Map();r.rows.forEach(x=>{if(!by.has(x.note_key))by.set(x.note_key,[]);by.get(x.note_key).push(x)});
        $("#swf").innerHTML=[...by.values()].map(g=>`<div class="card" style="margin-bottom:10px"><h2 style="margin-top:0">${esc(g[g.length-1].title)}</h2><div class="sub">First reached ${esc(SWS.region)} on ${esc(g[0].first_tag||g[0].first_page)}, seen ${swDate(g[0].first_seen)}.</div>
          <table class="kv"><thead><tr><th class="l">Change</th><th class="l">Listed on</th><th class="l">First seen</th><th class="l">Countries</th><th class="l">Listed again on</th></tr></thead><tbody>${g.map(x=>`<tr><td class="l">${x.change==="new"?"Added":"Text changed"}</td><td class="l"><a href="#/software/${encodeURIComponent(x.first_tag||x.first_page)}">${esc(x.first_tag||x.first_page)}</a></td><td class="l">${swDate(x.first_seen)}</td><td class="l">${esc((x.countries||[]).join(", ").toUpperCase())}</td><td class="l">${(x.repeated_in||[]).slice(0,6).map(esc).join(", ")}${(x.repeated_in||[]).length>6?" +"+((x.repeated_in||[]).length-6):""}</td></tr><tr><td colspan="5" class="l sub">${swChanged(x.diff)?"Compared with "+esc(x.prev_tag)+": <b>bold</b> is new, <s>struck through</s> was removed.<br>"+swDiff(x.diff):esc((x.description||"").slice(0,400)).replace(/\n/g,"<br>")}</td></tr>`).join("")}</tbody></table></div>`).join("")};
      $("#swq").oninput=e=>{SWS.q=e.target.value;swSave();clearTimeout(tm);tm=setTimeout(go,300)};go();
    }else{
      host.innerHTML=`<div class="bar"><span>From</span><input id="swa" list="swl" value="${esc(SWS.a)}" size="14" autocomplete="off" placeholder="2026.8.200"><span>to</span><input id="swb2" list="swl" value="${esc(SWS.b)}" size="14" autocomplete="off" placeholder="2026.38.5.5"><button class="chip" id="swsw" title="Swap the two versions">⇄</button></div><datalist id="swl">${o.all_versions.map(k=>`<option value="${esc(k)}"></option>`).join("")}</datalist>${swJourneyBar(o,true)}<div id="swcmp"></div>`;
      const run=async()=>{SWS.a=$("#swa").value.trim();SWS.b=$("#swb2").value.trim();swSave();const box=$("#swcmp");
        const bad=[SWS.a,SWS.b].filter(v=>!o.all_versions.includes(v));
        if(bad.length){box.innerHTML=`<div class="sub">${bad.map(esc).join(" and ")} ${bad.length>1?"are":"is"} not held. Fetch more versions above, or pick one from the list.</div>`;return}
        if(swKey(SWS.a)>=swKey(SWS.b)){box.innerHTML='<div class="sub">Pick an older version first and a newer one second.</div>';return}
        box.innerHTML='<div class="sub">Comparing…</div>';const r=await fetchJ("software/compare?a="+encodeURIComponent(SWS.a)+"&b="+encodeURIComponent(SWS.b)+"&"+swQ());if(!document.body.contains(box))return;
        if(r.error){box.innerHTML=errBox(r.error,"Could not compare");return}
        SWRC=r.countries||SWRC;const nb=r.between_region.length;
        box.innerHTML=`<div class="sub">${esc(SWS.region)} had ${n0(nb)} version${nb===1?"":"s"} between these two (${n0(r.between.length)} held in all regions).${r.a_page_lists_nothing_here?" No release notes for "+esc(SWS.region)+" were listed on "+esc(r.a)+" itself, so what it had is taken from the notes listed on the versions before it.":""}</div>
         <h2>Added by ${esc(r.b)} <small class="sub">${n0(r.listed_new.length)}</small></h2>${swNotes(r.listed_new,"Nothing new is listed on "+esc(r.b)+".")}
         <h2>Text changed <small class="sub">${n0(r.listed_revised.length)}</small></h2>${swNotes(r.listed_revised,"No feature was reworded.")}
         <h2>Installed on the way, not in the notes of ${esc(r.b)} <small class="sub">${n0(r.skipped.length)}</small></h2><div class="sub" style="margin-bottom:6px">Listed on a version between the two for ${esc(SWS.region)}; ${esc(r.b)}'s own notes do not repeat them. Cars that went through those versions have them.</div>${swNotes(r.skipped,"Nothing was listed only on a version in between.")}
         <h2>Listed again on ${esc(r.b)} <small class="sub">${n0(r.listed_again.length)}</small></h2><div class="sub" style="margin-bottom:6px">Already listed on ${esc(r.a)} with the same text.</div>${swNotes(r.listed_again,"None.")}
         ${(()=>{const car=r.others.filter(x=>x.region_cars),elsewhere=r.others.filter(x=>!x.region_cars);return `<h2>Listed for ${esc(SWS.region)}, but for other car types <small class="sub">${n0(car.length)}</small></h2>${swNotes(car,"None.")}<h2>Only listed in other regions <small class="sub">${n0(elsewhere.length)}</small></h2>${swNotes(elsewhere,"None.")}`})()}`};
      $("#swa").onchange=run;$("#swb2").onchange=run;$("#swsw").onclick=()=>{const a=$("#swa").value;$("#swa").value=$("#swb2").value;$("#swb2").value=a;run()};run()}
    $$("[data-jc]",host).forEach(b=>b.onclick=()=>{const[a,z]=b.dataset.jc.split(">");SWS.a=a;SWS.b=z;SWS.tab="compare";swSave();$$("[data-t]").forEach(x=>x.classList.toggle("on",x.dataset.t==="compare"));body()});
    $$("[data-cf],[data-ct]",host).forEach(a=>a.onclick=e=>{e.preventDefault();e.stopPropagation();if(a.dataset.cf)SWS.a=a.dataset.cf;else SWS.b=a.dataset.ct;swSave();pickRefresh()});
    const goCompare=()=>{SWS.tab="compare";swSave();$$("[data-t]").forEach(x=>x.classList.toggle("on",x.dataset.t==="compare"));body()};
    function pickRefresh(){const x=$("#swpick",host);if(!x)return;x.innerHTML=swPick();$("#swgo",x).onclick=goCompare}pickRefresh();
  };
  body()}
async function pageSoftwareOne(v){
  loading();const d=await fetchJ("software/version?v="+encodeURIComponent(v)+"&"+swQ());
  if(d.error){main.innerHTML=crumbs("#/software","Software")+`<h1>Software ${esc(v)}</h1>`+errBox(d.error,"Could not read this version");return}
  SWRC=d.region_countries||[];swDefaultCar(d.my_car);const u=d.uptake,hw=(SWS.car.split("|")[1]||"");
  main.innerHTML=crumbs("#/software","Software")+`<h1>Software ${esc(v)} ${swFsd(u.fsd,hw)}</h1><div class="sub">First seen ${swDate(u.first_spotted)}. ${esc(SWS.region)}${SWS.car?", "+esc((SWS.car.split("|")[0])):""}.</div>
   <div class="bar"><button class="chip" id="cmpf">Compare from this version</button><button class="chip" id="cmpt">Compare to this version</button></div>
   <div class="tiles">${tile(n0(u.installs),"cars seen on it")}${tile(u.region_installs==null?"–":n0(u.region_installs),"in "+esc(SWS.region))}${tile(n0(u.pending),"waiting for it")}${tile(n0(d.features.length),"features listed up to it")}</div>
   <div class="card" style="margin-top:12px"><h2 style="margin-top:0">Features for ${esc(SWS.region)}</h2><div class="sub" style="margin-bottom:6px">The newest text of each feature listed on this version or any before it. Those not on this version's own page were listed earlier.</div>
    ${swNotes(d.features.map(f=>Object.assign({},f,{tag:f.tag,text:f.description,was_tag:null,first_tag:f.listed_on_page?null:f.seen_on})),"No release notes are held for this version.")}</div>
   <div class="chr2" style="margin-top:12px"><div class="card"><h2 style="margin-top:0">By country</h2><table class="kv"><tbody>${u.countries.slice(0,20).map(c=>`<tr><td class="l">${esc(String(c.country||"?").toUpperCase())}</td><td>${n0(c.installs)}</td></tr>`).join("")}</tbody></table></div>
    <div class="card"><h2 style="margin-top:0">By car</h2><table class="kv"><tbody>${u.models.map(m=>`<tr><td class="l">${esc(m.label)}</td><td>${n0(m.installs)}</td></tr>`).join("")}</tbody></table></div>
    <div class="card"><h2 style="margin-top:0">Cars came from</h2><table class="kv"><tbody>${u.from.length?u.from.slice(0,12).map(c=>`<tr ${c.region?"":'class="sub"'}><td class="l"><a href="#/software/${encodeURIComponent(c.version)}">${esc(c.version)}</a>${c.region?"":" <span class='sub'>other regions</span>"}</td><td>${n0(c.installs)}</td></tr>`).join(""):'<tr><td class="l sub">Not known.</td></tr>'}</tbody></table></div>
    <div class="card"><h2 style="margin-top:0">Installs per day</h2><table class="kv"><tbody>${u.daily.slice(0,14).map(c=>`<tr><td class="l">${esc(dayName(c.day))}</td><td>${n0(c.installs)}</td></tr>`).join("")||'<tr><td class="l sub">Not known.</td></tr>'}</tbody></table></div></div>`;
  const go=k=>()=>{SWS[k]=v;SWS.tab="compare";swSave();location.hash="#/software"};$("#cmpf").onclick=go("a");$("#cmpt").onclick=go("b")}

const ROUTES={activity:pageActivity,drives:pageDrives,driveslist:pageDriveList,charges:pageCharges,battery:pageBattery,health:pageHealth,analytics:pageAnalytics,teslacam:pageTeslaCam,queue:pageQueue,flips:pageDrives,review:pageDrives,dashcam:pageDrives,parked:pageParked,idles:pageParked,places:pagePlaces,alerts:pageAlerts,software:pageSoftware};
async function route(){
  navTrack();hideTip();if(location.hash.indexOf("#/charge/")!==0){try{sessionStorage.removeItem("chargeSet")}catch(e){}}if(TLOFF){TLOFF();TLOFF=null}const h=(location.hash||"#/activity").replace(/^#\//,""),[p,arg,arg2]=h.split("/").map(decodeURIComponent);
  // the side bar item lit is the list a detail page was reached from (a drive opened from Drives (new) lights Drives (new)); a section's own page (Software, Battery, ...) always lights itself, whatever the path that led to it
  const own=({drive:"drives",charge:"charges",idle:"parked",idles:"parked",dashcam:"drives",flips:"drives",review:"drives",place:"places"})[p]||p,from=NAV.length?(NAV[0].h||"").replace(/^#\//,"").split("/")[0]:"",lit=own!==p&&ROOTS[from]&&ROOTS[from]!==ROOTS[own]&&NAV.length>1?(({idles:"parked"})[from]||from):own;
  $$("#nav>a").forEach(a=>a.classList.toggle("on",a.dataset.p===lit));
  badge();if(!QUIET)main.scrollTop=0;
  try{
    if(p==="drive")await pageDrive(arg,arg2);else if(p==="charge")await pageCharge(arg,arg2);else if(p==="idle"||p==="parked"&&arg)await pageParkedOne(arg);else if(p==="place")await pagePlace(arg);else await (ROUTES[p]||pageActivity)(arg,arg2);
  }catch(e){main.innerHTML=errBox(e.stack||e.message,"Something went wrong")}
}
async function boot(){
  addEventListener("hashchange",route);
  $$("#nav>a").forEach(a=>a.addEventListener("click",()=>{if(a.getAttribute("href")!==location.hash)NAVRESET=true}));
  try{SET=await (await fetch("/api/settings")).json()}catch(e){}
  try{const s=await api("summary");$("#veh").textContent=s.vehicle;$("#vsub").textContent=n0(s.km)+" km logged"}catch(e){}
  // the corner: one line a drive, a dot and its name. Green = connected, amber = something is reading it, red = not connected; ⏏ only while connected (and not just ejected)
  window.EJD={};window.KBUSY=[];const NM={A:"Archive",J:"JOWUA",U:"Tesla USB"};
  const st=async()=>{try{const s=await (await fetch("/api/status")).json();if(window.UI0==null)window.UI0=s.ui;
    const up=r=>s.roots[r]&&!(EJD[r]&&Date.now()-EJD[r]<20000);
    $("#status").innerHTML=["A","J","U"].filter(r=>r==="A"||up(r)).map(r=>`<div><span class="dot ${!up(r)?"off":KBUSY.includes(r)?"busy":"on"}" data-r="${r}" title="${!up(r)?"Not connected":KBUSY.includes(r)?"Being read":"Connected"}"></span>${NM[r]}${up(r)?`<button class="eject" data-ej="${r}" title="Eject ${NM[r]}" aria-label="Eject ${NM[r]}">⏏</button>`:""}</div>`).join("")+(window.EJMSG&&Date.now()<window.EJMSG.until?`<div class="${window.EJMSG.ok?"":"upd"}" style="font-size:12px">${esc(window.EJMSG.text)}</div>`:"")+`${s.stale?'<div class="upd">The app was updated. Quit and reopen it to load the new version.</div>':""}${s.ui!==window.UI0?'<div class="upd">The page was updated. <a href="#" onclick="location.reload();return false">Reload it</a> to load the new version.</div>':""}`;
    $$("#status [data-ej]").forEach(b=>b.onclick=async()=>{b.disabled=true;const r=await ejectDrive(b.dataset.ej);if(r.ok)EJD[b.dataset.ej]=Date.now();window.EJMSG={text:r.text,ok:r.ok,until:Date.now()+15000};st()})}catch(e){}};
  // amber while the Keeper (or a button) is reading a drive: asked often, and only the dots are touched
  const kp=async()=>{try{const k=await (await fetch("/api/keeper")).json();window.KBUSY=k.working?(Object.keys(k.drives||{}).length?Object.keys(k.drives):k.roles||[]):[];
    $$("#status .dot[data-r]").forEach(d=>{if(d.classList.contains("off"))return;const b=KBUSY.includes(d.dataset.r);d.classList.toggle("busy",b);d.classList.toggle("on",!b);d.title=b?"Being read":"Connected"})}catch(e){}};
  st();setInterval(st,30000);kp();setInterval(kp,4000);route();
}
addEventListener("keydown",e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==="r"){e.preventDefault();location.reload()}});
$("#rl").onclick=()=>location.reload();
boot();
