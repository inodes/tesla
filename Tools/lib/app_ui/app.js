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
const cache={};
const api=async p=>{if(cache[p])return cache[p];const r=await fetch("/api/"+p);if(!r.ok)throw new Error(p+": "+r.status);return cache[p]=await r.json()};
const main=$("#main");
const place=x=>x.place||x.addr||"Unknown place";
const driveTitle=d=>(d.frm||"?")+" → "+(d.to||"?");
// An error you can copy: the text is selectable and a button puts it on the clipboard.
const errBox=(msg,lead)=>`<div class="errbox"><div>${lead||""}</div><pre>${esc(msg)}</pre><button class="btn" data-copy>Copy error</button></div>`;
document.addEventListener("click",e=>{const b=e.target.closest("[data-copy]");if(!b)return;const pre=b.parentNode.querySelector("pre"),sel=()=>{const r=document.createRange();r.selectNodeContents(pre);const s=getSelection();s.removeAllRanges();s.addRange(r);b.textContent="Selected - press Cmd+C"};
  (navigator.clipboard?navigator.clipboard.writeText(pre.textContent):Promise.reject()).then(()=>{b.textContent="Copied"},sel)});
// Settings come from the "app" block of config.json (see lib/app_config.py), loaded once at start.
let SET={};const cfg=k=>k.split(".").reduce((o,x)=>o&&o[x],SET),REPM=()=>Math.round(cfg("drives.repark_max_km")*1000)+" m",MAPSTYLE=dark=>cfg("map.tiles_base")+cfg(dark?"map.style_dark":"map.style_light");
const loading=()=>main.innerHTML='<div class="empty">Loading…</div>';
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
  b.innerHTML='<span style="display:block;text-align:center;font-size:18px;line-height:29px">⤢</span>';b.onclick=reset;nav._container.appendChild(b)}
function miniMap(el,lat,lon,col){
  if(lat==null||lon==null){el.outerHTML='<div class="sub">No location.</div>';return}
  if(!window.maplibregl){el.innerHTML='<div class="empty">Map library not loaded (offline?).</div>';return}
  const dark=matchMedia("(prefers-color-scheme: dark)").matches;
  const m=newMap({container:el,style:MAPSTYLE(dark),center:[lon,lat],zoom:14.5,attributionControl:{compact:true}});
  mapNav(m,()=>m.flyTo({center:[lon,lat],zoom:14.5}),"Back to the pin");
  new maplibregl.Marker({color:col||"#3b6cf6"}).setLngLat([lon,lat]).addTo(m);
}
function tile(b,s){return `<div class="tile"><b>${b}</b><span>${s}</span></div>`}
function crumbs(h,label){return `<div class="crumb"><a href="${h}">← ${label}</a></div>`}

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
  if(x.k==="drive"){ti=driveTitle(x);st=`${dur(x.min)} · ${n0(x.whkm)} Wh/km · ${n0(x.b0)}→${n0(x.b1)}%${x.saved?" · "+x.saved+" saved clip":""}${x.foot?" · dashcam "+x.foot+"%":""}${alb}`;nums=`<span>${n0(x.km,1)}<small>km</small></span><span>${n0(x.kwh,1)}<small>kWh used</small></span>`}
  else if(x.k==="charge"){ti=place(x);st=`${dur(x.min)} · ${n0(x.b0)}→${n0(x.b1)}% · ${x.kind==="supercharger"?"Supercharger":x.kind==="dc"?"DC fast":"AC"}${x.parts&&x.parts.length>1?" · "+x.parts.length+" parts":""}${alb}`;nums=`<span>+${n0(x.kwh,1)}<small>kWh</small></span><span>${money(x.cost)}<small>cost</small></span>`}
  else{ti="Parked · "+place(x);st=`${dur(x.min)} · ${n0(x.b0)}→${n0(x.b1)}%${x.sentry>0.01?" · Sentry "+Math.round(x.sentry*100)+"%":""}${x.climate>0.01?" · Climate "+Math.round(x.climate*100)+"%":""}${alb}`;nums=`<span>${n0(x.kwh,2)}<small>kWh used</small></span>`}
  return `<a class="row ${x.k==="idle"?"faded":""}" ${href?`href="${href}"`:""} ${x.k==="drive"&&!x.page?'title="No dashcam / deep-dive page for this drive - shows the summary"':""}><span class="t">${x.hm} – ${end}</span><span class="k ${x.k}"></span><div style="min-width:0"><div class="ti">${esc(ti)}</div><div class="st">${esc(st)}</div></div><div class="nums">${nums}</div></a>`;
}

// ---------- generic sortable table
function table(host,cols,rows,onRow,def){
  let sk=def||cols[0].k,dir=-1;
  const cmp=(x,y)=>typeof x==="string"&&typeof y==="string"?x.localeCompare(y):x<y?-1:x>y?1:0,byDay=sk0=>sk0==="t0"&&rows.length&&rows[0].day!==undefined;
  const draw=()=>{const rs=[...rows].sort((a,b)=>{const x=a[sk],y=b[sk];if(x==null||y==null)return (x==null)-(y==null);return dir*cmp(x,y)});
    host.innerHTML=`<table><thead><tr>${cols.map(c=>`<th class="${c.l?"l":""}" data-k="${c.k}">${c.t}${sk===c.k?(dir<0?" ▾":" ▴"):""}</th>`).join("")}</tr></thead><tbody>${rs.slice(0,500).map((r,i)=>`<tr data-i="${rows.indexOf(r)}">${cols.map(c=>`<td class="${c.l?"l":""}">${c.f?c.f(r):esc(r[c.k])}</td>`).join("")}</tr>`).join("")}</tbody></table>${rs.length>500?`<div class="sub" style="padding:8px">Showing the first 500 of ${rs.length}. Narrow the filter.</div>`:""}`;
    $$("th",host).forEach(th=>th.onclick=()=>{if(sk===th.dataset.k)dir=-dir;else{sk=th.dataset.k;dir=-1}draw()});
    $$("tbody tr",host).forEach(tr=>tr.onclick=()=>onRow(rows[+tr.dataset.i]))};
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
  // minutes whose signs have been read (Review tab): a drive counts as read when nearly every minute of footage in it was
  const scn=await fetch("/api/review/scanned").then(r=>r.json()).catch(()=>({})),scT=Object.keys(scn).map(Number).sort((a,b)=>a-b);   // UTC epoch seconds of each minute read
  const signsRead=r=>{if(r.foot==null||!scT.length)return null;const t0=r.t0,n=scT.filter(t=>t>=t0-30&&t<t0+r.min*60).length,want=Math.max(1,Math.round(r.min*r.foot/100));return n?Math.min(100,Math.round(100*n/want)):null};
  // a drive "ends at a charge" when a charge starts within 30 min after it, "leaves a charge" when one ended within 30 min before it
  const W=cfg("drives.charge_window_minutes")*60;
  all.forEach(d=>{d.toCh=chg.find(c=>c.t0>=d.t1-300&&c.t0<=d.t1+W)||null;d.fromCh=chg.find(c=>c.t1<=d.t0+300&&c.t1>=d.t0-W)||null});
  const CHF={all:()=>true,to:x=>x.toCh,from:x=>x.fromCh,any:x=>x.toCh||x.fromCh,none:x=>!x.toCh&&!x.fromCh,low:x=>x.b1!=null&&x.b1<=cfg("drives.low_battery_pct")},
   EXF={all:()=>true,foot:x=>x.foot,fsd:x=>x.ap>0,al:x=>x.al>0,hwy:x=>x.km>=cfg("drives.long_highway_km")&&x.max>=cfg("drives.long_highway_fast_kmh")};
  const draw=()=>{const q=DP.q.toLowerCase(),rs=all.filter(x=>inRange(x,DP.r)&&(!q||driveTitle(x).toLowerCase().includes(q))&&CHF[DP.ch](x)&&EXF[DP.ex](x)&&x.km>=DP.mk&&(x.min?x.km/(x.min/60):0)>=DP.ma&&(DP.rp==="both"||(DP.rp==="repark")===!!x.repark)),
    km=rs.reduce((a,x)=>a+x.km,0),kwh=rs.reduce((a,x)=>a+(x.kwh||0),0),ap=rs.reduce((a,x)=>a+(x.ap||0),0),mn=rs.reduce((a,x)=>a+(x.min||0),0);
    $("#tl").innerHTML=tile(n0(rs.length),"drives")+tile(n0(km)+" km","distance")+tile(km?n0(kwh*1000/km)+" Wh/km":"–","average efficiency")+tile(km?n0(100*ap/km)+"%":"–","self-driving")+tile(dur(mn),"time driving")+tile(n0(Math.max(0,...rs.map(x=>x.max||0)))+" km/h","top speed");
    table($("#tb"),[{k:"t0",t:"Date",l:1,f:r=>`${r.day} ${r.hm}`},{k:"frm",t:"Trip",l:1,f:r=>esc(driveTitle(r))+(r.repark?` <span class="pill" title="Under ${REPM()}: the car was only moved a little">re-park</span>`:"")+(r.toCh?` <span class="pill ok" title="A charge started right after this drive">to charge</span>`:"")+(r.fromCh?` <span class="pill" title="A charge ended just before this drive">from charge</span>`:"")},{k:"km",t:"km",f:r=>n0(r.km,1)},{k:"min",t:"Time",f:r=>dur(r.min)},{k:"whkm",t:"Wh/km",f:r=>n0(r.whkm)},{k:"max",t:"Max",f:r=>n0(r.max)},{k:"ap",t:"Self-driving",f:r=>r.km?n0(100*r.ap/r.km)+"%":"–"},{k:"b0",t:"Battery",f:r=>`${n0(r.b0)}→${n0(r.b1)}%`},{k:"foot",t:"Dashcam",f:r=>r.foot==null?"–":r.foot+"%"},{k:"sr",t:"Signs read",f:r=>{const p=signsRead(r);return p==null?"–":p+"%"}}],rs,r=>{location.hash="#/drive/"+r.id},"t0")};
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
let RVAPI=null;      // set by teslaDriveBody: what the review viewer may do to the page (cursor, video, chart)
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
  const plain=e=>{if(e.kind==="intervention")return `${e.from_mode} → ${e.to_mode}`+((e.flags||[]).length?" · "+e.flags.map(f=>f==="brake"?"braking":f==="steering"?"steering":"accelerator").join(", "):e.at_stop?"":" · no pedal / wheel input")+(e.at_stop?" · stopped":"")+(e.off_s==null?" · FSD did not come back":" · FSD off for "+n0(e.off_s)+" s");
    if(e.kind==="accelerator")return `Accelerator held ${n0(e.dur_s)} s, peak ${n0(e.peak)}%`+(e.ended_in_off?" · FSD switched off right after":"");
    return `Speed change`+(e.pressed?" · accelerator pressed":" · no pedal input")};
  const info=(e,k)=>{if(!RVAPI||!geo(e))return"";const s=RVAPI.series(),i=stillI(e,k),rel=e.shots[k];
    return `${RVAPI.clk(i)} · ${rel?(rel>0?"+":"")+n0(rel)+" s":"the moment"}${s.v[i]!=null?" · "+n0(s.v[i])+" km/h":""}${s.accel[i]?" · accelerator "+n0(s.accel[i])+"%":""}${s.br[i]?" · braking":""}${APN[s.ap[i]]?" · "+APN[s.ap[i]]:" · manual"}`};
  // the strip across the drive: where the events of this tab are; click one to go to it
  const ovDraw=()=>{const cv=$("#rvo"),R=curRes(),L=list();if(!cv||!R)return;const dpr=devicePixelRatio||1,W=cv.clientWidth,H=cv.clientHeight;if(!W)return;cv.width=Math.round(W*dpr);cv.height=Math.round(H*dpr);
    const c=cv.getContext("2d");c.setTransform(dpr,0,0,dpr,0,0);const st=getComputedStyle(document.documentElement),css=n=>st.getPropertyValue(n).trim()||"#888";
    const t0=R.t0!=null?R.t0:Math.min(...R.events.map(e=>e.t)),t1=Math.max(R.t1!=null?R.t1:t0+1,t0+1),X=t=>8+(W-16)*(t-t0)/(t1-t0);
    c.strokeStyle=css("--line");c.lineWidth=3;c.lineCap="round";c.beginPath();c.moveTo(8,H/2);c.lineTo(W-8,H/2);c.stroke();
    OV=L.map((e,k)=>({k,x:X(e.t)}));
    L.forEach((e,k)=>{const x=X(e.t),on=k===ei;c.fillStyle=KCOL[e.kind];c.beginPath();c.arc(x,H/2,on?7:4,0,7);c.fill();if(on){c.strokeStyle=css("--fg")||"#000";c.lineWidth=2;c.beginPath();c.arc(x,H/2,10,0,7);c.stroke()}})};
  // the strip of this event: speed, FSD, accelerator, the signs, a tick for every still (the picked one bold)
  const miniDraw=()=>{const m=MC;if(!m||!RVAPI)return;const cv=m.cv,e=m.e,s=RVAPI.series(),dpr=devicePixelRatio||1,W=cv.clientWidth,H=cv.clientHeight;if(!W||!H)return;
    if(cv.width!==Math.round(W*dpr)||cv.height!==Math.round(H*dpr)){cv.width=Math.round(W*dpr);cv.height=Math.round(H*dpr)}
    const c=cv.getContext("2d");c.setTransform(dpr,0,0,dpr,0,0);c.clearRect(0,0,W,H);
    const st=getComputedStyle(document.documentElement),css=n=>st.getPropertyValue(n).trim()||"#888",ink=css("--mut"),line=css("--line"),acc=css("--acc"),ok=css("--charge"),bad=css("--bad");
    const a=m.a,b=m.b,L=32,R=8,T=14,B=18,X=i=>L+(W-L-R)*(i-a)/Math.max(1,b-a),i0=Math.max(0,Math.floor(a)),i1=Math.min(s.v.length-1,Math.ceil(b)),sw=Math.max(1,X(i0+1)-X(i0));
    let vm=40;for(let i=i0;i<=i1;i++)if(s.v[i]!=null&&s.v[i]>vm)vm=s.v[i];vm=Math.ceil(vm/20)*20;const Y=v=>T+(H-T-B)*(1-v/vm);
    c.fillStyle=KCOL[e.kind];c.globalAlpha=.14;c.fillRect(X(ea(e)),T,Math.max(3,X(eb(e))-X(ea(e))),H-T-B);c.globalAlpha=1;
    c.font="11px system-ui";c.fillStyle=ink;c.strokeStyle=line;c.lineWidth=1;for(let v=0;v<=vm;v+=vm/2){c.beginPath();c.moveTo(L,Y(v));c.lineTo(W-R,Y(v));c.stroke();c.fillText(Math.round(v),3,Y(v)+4)}
    for(let i=i0;i<=i1;i++){if(s.v[i]==null)continue;const q=s.ap[i];c.fillStyle=apCol(q>0&&q<4?APN[q]:"Manual");c.fillRect(X(i),1,sw,7)}
    c.fillStyle=ok;c.globalAlpha=.4;for(let i=i0;i<=i1;i++)if(s.accel[i]){const h=(H-T-B)*.4*Math.min(100,s.accel[i])/100;c.fillRect(X(i),H-B-h,sw,h)}c.globalAlpha=1;
    c.fillStyle=bad;c.globalAlpha=.5;for(let i=i0;i<=i1;i++)if(s.br[i])c.fillRect(X(i),H-B-3,sw,3);c.globalAlpha=1;
    c.beginPath();let on=false;for(let i=i0;i<=i1;i++){const v=s.v[i];if(v==null){on=false;continue}const x=X(i),y=Y(v);on?c.lineTo(x,y):c.moveTo(x,y);on=true}c.strokeStyle=acc;c.lineWidth=2;c.stroke();
    (e.signs||[]).forEach(g=>{const i=base(e)+(g.t-e.t);if(i<a||i>b)return;const x=X(i),y=Math.max(T+8,Y(+g.text||0));c.beginPath();c.arc(x,y,9,0,7);c.fillStyle="#fff";c.fill();c.lineWidth=2;c.strokeStyle=g.side==="R"?"#94a3b8":"#dc2626";c.stroke();c.fillStyle="#111";c.font="bold 8.5px system-ui";c.textAlign="center";c.fillText(g.text,x,y+3);c.textAlign="start"});
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
    if(geo(e)){const bg=$("#rvbig");if(bg)bg.setAttribute("style",sprite(e,k));const cp=$("#rvcap");if(cp)cp.textContent=info(e,k)}
    miniDraw();if(RVAPI&&geo(e))RVAPI.focus({a:ea(e),b:eb(e),ticks:e.shots.map((x,j)=>stillI(e,j)),hot:k,col:KCOL[e.kind]})};
  const pick=(k,seek)=>{const e=list()[ei];if(!e||!geo(e))return;si=Math.max(0,Math.min(e.shots.length-1,k));hv=null;hot();
    if(RVAPI){RVAPI.cursor(stillI(e,si));if(seek)RVAPI.seek(stillI(e,si)-1)}if(lb)lbShow()};
  const arrive=e=>{if(!RVAPI||!e)return;const g=geo(e),t=g?e.shots.map((x,j)=>stillI(e,j)):[];       // the chart under the map shows only this event's section (Review: nothing else), the map dot goes to it
    RVAPI.focus({a:ea(e),b:eb(e),ticks:t,hot:si,col:KCOL[e.kind]});RVAPI.limit(Math.min(ea(e),t.length?t[0]:ea(e))-10,Math.max(eb(e),t.length?t[t.length-1]:eb(e))+10);RVAPI.cursor(g?stillI(e,si):ea(e));RVAPI.seek((g?stillI(e,si):ea(e))-1)};      // the video below goes to the event too, not on playing from where it was
  const go=n=>{const L=list();if(!L.length)return;ei=Math.max(0,Math.min(L.length-1,n));const e=L[ei];si=geo(e)?k0(e):0;hv=null;viewer(true);arrive(e)};
  // the big picture
  const lbShow=()=>{const e=list()[ei],o=document.getElementById("rvlb");if(!e||!o||!geo(e)){return}
    o.querySelector(".rvlbi").setAttribute("style",sprite(e,si)+";width:min(92vw,"+(e.tile.w*2.2)+"px,calc(78vh*"+e.tile.w+"/"+e.tile.h+"))");o.querySelector(".rvlbc").textContent=e.id+" · "+info(e,si)+" · "+(si+1)+" of "+e.shots.length};
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
        <div style="margin:8px 0 2px">${dd.h}</div><div>${dd.tags}</div>`;
      if(e.look_error)h+=`<div class="sub" style="color:var(--bad)">${esc(e.look_error)}</div>`;
      if(g){
        h+=`<div class="rvmain"><div><div id="rvbig" class="rvimg" style="${sprite(e,si)}" title="Click to enlarge"></div><div class="sub" id="rvcap" style="margin-top:4px"></div><div class="sub" id="rvst"></div></div>
          <div><canvas id="rvc" class="rvcv"></canvas><div class="sgs">${(e.signs||[]).map(x=>`<span class="sgn" title="seen ${x.n} time(s), ${x.conf}% sure">${x.f?`<img src="/review/${encodeURIComponent(x.f)}" alt="">`:""}<b>${esc(x.text)}</b> <span class="sub">${hm(x.t)}</span></span>`).join("")}</div>
          <div class="sub" style="margin-top:4px">${(e.signs||[]).length?"Signs read in these stills (check each against its picture).":"No signs read in the stills."}</div></div></div>
          <div class="rvfilm">${e.shots.map((x,k)=>`<button class="rvc" data-k="${k}" style="${sprite(e,k)}" title="${x>0?"+":""}${n0(x)} s"></button>`).join("")}</div>
          <div class="sub" style="margin-top:6px">Click a still to light it up on the map, the chart and the video. ← → step through the stills, Shift + ← → through the events.</div>`}
      else if(e.sheet)h+=`<div style="margin-top:8px"><a href="/revev/${encodeURIComponent(R.stamp)}/${encodeURIComponent(e.sheet)}" target="_blank"><img class="rvsheet" src="/revev/${encodeURIComponent(R.stamp)}/${encodeURIComponent(e.sheet)}"></a><div class="sub">This review was run before the stills could be picked one by one. Run it again for that.</div></div>`;
      else h+=`<div class="sub" style="margin-top:8px">${R.stills?"No stills for this event.":"No stills: the Archive was not connected when this review ran."}</div>`;
      h+="</div>"}
    box.innerHTML=h+'<div class="card" style="margin-top:12px;padding:4px 0"><div id="rvtb"></div></div>';
    // every event of the drive (the same ones the tabs and the speed filter leave in) as a table: click a row to open it above
    {const rows=L.map((x,k)=>({k,id:x.id,t:x.t,kind:KNAME[x.kind]||x.kind,what:plain(x),from:x.v,to:x.kind==="intervention"?null:x.v1,sec:x.kind==="intervention"?(x.off_s==null?null:x.off_s):x.dur_s,n:(x.signs||[]).length}));
      if(rows.length)table($("#rvtb",box),[{k:"t",t:"Time",l:1,f:r=>(r.k===ei?"▶ ":"")+hm(r.t)},{k:"id",t:"#",l:1,f:r=>`<b>${esc(r.id)}</b>`},{k:"kind",t:"Event",l:1},{k:"what",t:"What happened",l:1,f:r=>esc(r.what)},
        {k:"from",t:"From",f:r=>r.from==null?"–":n0(r.from)+" km/h"},{k:"to",t:"To",f:r=>r.to==null?"–":n0(r.to)+" km/h"},{k:"sec",t:"Seconds",f:r=>r.sec==null?"–":n0(r.sec)},{k:"n",t:"Signs",f:r=>r.n?n0(r.n):"–"}],rows,r=>go(r.k),"t")}
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
    if(run)h+=`<div class="sub" style="margin-top:8px">${esc(job.stage||"")}${job.total?" · "+n0(job.done)+" of "+n0(job.total):""}…</div>`;
    if(job.error)h+=`<div class="sub" style="margin-top:8px;color:var(--bad);user-select:text">${esc(job.error)}</div>`;
    if(ms.py.length+ms.cmd.length)h+=`<div class="sub" style="margin-top:8px">Stills and sign reading need ${esc(ms.py.concat(ms.cmd).join(", "))} installed. <code>${esc((ms.hint||[]).join(" ; "))}</code></div>`;
    else if(!S.connected&&!(R&&R.stills))h+=`<div class="sub" style="margin-top:8px">The Archive is not connected: events can be found from telemetry already read, but no stills.</div>`;
    if(R){const c=R.counts||{};
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
async function pageDrive(stamp,tab){
  const act=await api("activity"),d=act.find(x=>x.k==="drive"&&x.id===stamp);
  const als=await alertsFor("drive",stamp);
  if(d&&!d.page){driveLite(d,als);return}
  if(tab==="flips")tab="review";tab=["footage","review"].includes(tab)?tab:"overview";
  const tabs=[["overview","Overview"],["footage","Footage"+(d&&(d.foot||d.saved)?"":"")],["review","Review"]];
  main.innerHTML=crumbs("#/drives","Drives")+alertsCard(als,"drive")+(d?`<div class="bar" id="dtabs">${tabs.map(([k,t])=>`<a class="chip ${k===tab?"on":""}" href="#/drive/${encodeURIComponent(stamp)}/${k}">${t}</a>`).join("")}</div>`:"")+'<div id="dtab"></div>';
  wireAlerts(main);const box=$("#dtab");
  if(tab==="overview"||!d){
    // "Enrich with TeslaCam metadata": the page draws the route from the dashcam telemetry (a point a second) where it covers the drive, instead of Tessie's one every ~30 s
    const pref=()=>{try{return localStorage.getItem("drv.enrich")!=="0"}catch(e){return true}},src=on=>`/data/drives/deepdive/${encodeURIComponent(stamp)}/drive.html?enrich=${on?1:0}`;
    box.innerHTML=`<div id="fuseb" style="display:flex;gap:12px;align-items:baseline;flex-wrap:wrap;margin:0 0 8px"><label id="enrl" style="display:flex;gap:6px;align-items:center;opacity:.5"><input type="checkbox" id="enr" disabled> Enrich with TeslaCam metadata</label><span class="sub" id="enrn">checking…</span></div><iframe class="drive" id="fr"></iframe>`;
    const note=(i,on)=>on?`Route drawn from the dashcam telemetry (a point every second) for ${i.cam_pct}% of this drive. Tessie's own points (every ${n0(i.tessie_every_s)} s) fill ${i.start_gap_s>5?"the first "+i.start_gap_s+" s":"the start"}${i.end_gap_s>5?" and the last "+i.end_gap_s+" s":""} and any hole in the footage.`:"Route drawn from Tessie's own points (one every "+n0(i.tessie_every_s)+" s).";
    const load=on=>{const f=$("#fr");if(f)f.src=src(on)};
    // greyed out only while no telemetry is kept for the drive (the Scan on the TeslaCam page reads what is missing); the video itself need not exist any more
    api("drivefuse?id="+encodeURIComponent(stamp)+"&summary=1").then(r=>{const i=r.info,c=$("#enr");if(!c||!i||!i.cam_n)throw 0;
      const on=pref();c.disabled=false;c.checked=on;$("#enrl").style.opacity="";$("#enrn").textContent=note(i,on);load(on);
      c.onchange=()=>{try{localStorage.setItem("drv.enrich",c.checked?"1":"0")}catch(e){}$("#enrn").textContent=note(i,c.checked);load(c.checked)}})
      .catch(()=>{const l=$("#enrl"),n=$("#enrn");if(l){l.title="No TeslaCam telemetry has been read for this drive";l.style.opacity=".5"}if(n)n.textContent="No TeslaCam telemetry is kept for this drive yet. Scan the connected drives on the TeslaCam page to read it.";load(false)});
    fitFrame($("#fr"),doc=>{$$("a",doc).forEach(a=>{if(/^(\.\.\/)+index\.html$/.test(a.getAttribute("href")||"")){a.parentNode.style.display="none"}});
      // the footage and the flips have their own tabs (the clips are watched in the Footage tab)
      const f=doc.getElementById("foot");if(f)f.style.display="none";hideSection(doc,"FSD speed changes");hideSection(doc,"Saved clips")});
  }else{
    const t0=d.t0,t1=d.t1||d.t0+d.min*60,lo=Math.round((Date.parse(d.day+"T"+d.hm+":00Z")/1000-d.t0)/900)*900,pl=await api("places").catch(()=>({saved:[]}));
    const rv=tab==="review";
    box.innerHTML=(rv?'<div id="drr"></div>':'')+'<div id="tdh"></div>'+(rv?'':'<details class="card" id="mg" style="margin-top:14px"><summary><b>Copy, save as a clip, or delete clips</b></summary><div id="mgb" style="margin-top:10px"></div></details>');
    const fetchSeg=async b=>{const r=await fetch("/api/teslacam/A/window?t0="+t0+"&t1="+t1+"&lo="+lo+"&budget="+(b==null?20:b));
      if(!r.ok){const e=(await r.json().catch(()=>({}))).error||("HTTP "+r.status);const x=new Error(e);x.none=r.status===404;throw x}return r.json()};
    if(!rv)$("#mg").ontoggle=()=>{if(!$("#mg").open||$("#mgb").innerHTML)return;$("#mgb").innerHTML=`<iframe class="drive" id="mf" src="/data/drives/deepdive/${encodeURIComponent(stamp)}/drive.html"></iframe>`;
      fitFrame($("#mf"),doc=>{const w=doc.querySelector(".wrap");if(!w)return;if(!doc.getElementById("foot")){$("#mgb").innerHTML='<div class="sub">No clip tools for this drive: the page was built without footage. Run <code>./drive_report.py</code> with the Archive connected.</div>';return}
        [...w.children].forEach(c=>{if(c.id!=="foot")c.style.display="none"})})};
    let bodyOn=false;
    const body=async()=>{
    try{await teslaDriveBody($("#tdh"),"A",fetchSeg,pl.saved||[],{heading:false,review:rv?{t0,t1,lo}:null})}
    catch(e){const h=$("#tdh");if(h)h.innerHTML=e.none?'<div class="card"><b>No dashcam footage or telemetry for this drive.</b><div class="sub" style="margin-top:6px">Nothing recorded on the Archive covers these times, and nothing was read from it before. If the Archive is not connected, connect it and open this tab again.</div></div>':errBox(e.message,"Could not rebuild this drive from the video")}};
    // Review: nothing but the Review card until the drive has been reviewed; then the map, chart, video and the events
    if(rv)driveReviewPanel($("#drr"),stamp,lo,has=>{if(has&&!bodyOn){bodyOn=true;body()}});else body();
  }
}

// A drive Tessie recorded that has no per-drive file (so no dashcam / deep-dive page): the summary from the drives list.
function driveLite(d,als){
  main.innerHTML=crumbs("#/drives","Drives")+`<h1>${esc(driveTitle(d))}</h1><div class="sub">${dayName(d.day)} · ${d.hm} – ${addMin(d.hm,d.min)} · ${dur(d.min)} · ${n0(d.km,1)} km</div>
   <div class="tiles">${tile(n0(d.km,1)+" km","distance")}${tile(n0(d.kwh,1)+" kWh","energy used")}${tile(d.km?n0(d.whkm||d.kwh*1000/d.km)+" Wh/km":"–","efficiency")}${tile(n0(d.b0)+"% → "+n0(d.b1)+"%","battery")}${tile(n0(d.avg)+" / "+n0(d.max)+" km/h","average / top speed")}${tile(d.km?n0(100*(d.ap||0)/d.km)+"%":"–","self-driving")}</div>
   ${alertsCard(als,"drive")}<div class="card"><h2 style="margin-top:0">Where</h2><div class="map" id="mp"></div><div class="sub" style="margin-top:8px">From ${esc(d.frm||"?")} to ${esc(d.to||"?")}${d.pkey1?' · <a href="#/place/'+encodeURIComponent(d.pkey1)+'">where it ended</a>':""}</div></div>
   <div class="sub" style="margin-top:12px">Tessie has this drive but no per-drive file (route, dashcam footage) was saved for it, so only the summary is shown. Run <code>./sync_api_tessie.py</code> then <code>./drive_report.py</code> to build the full page if the data exists.</div>`;
  wireAlerts(main);miniMap($("#mp"),d.lat,d.lon,"#3b6cf6");
}

// ---------- Charges: the charge book (analyse_charging.py's reconcile - invoices, plug-ins, Tessie sync) (REQ-207)
let CP={r:{p:"all"},kind:"all",q:"",inv:"all",tab:"charges",open:{},sk:"t0",dir:-1};
const cmpv=(x,y)=>typeof x==="string"&&typeof y==="string"?x.localeCompare(y):x<y?-1:x>y?1:0;
const getBook=async()=>{try{return await (await fetch("/api/chargebook")).json()}catch(e){return{status:"error",error:String(e),book:null}}};
const SYNC_T={synced:["In Tessie","ok","Tessie already holds the invoice cost for every record of this charge."],pending:["Not in Tessie","warn","Tessie's cost differs from the invoice. To send the invoice cost run ./analyse_charging.py --sync-tessie-costs --apply (this app never changes Tessie)."],partial:["Part in Tessie","warn","Some records already hold the invoice cost, others do not. Run ./analyse_charging.py --sync-tessie-costs --apply to send the rest."]};
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
      let h=`<tr class="chr" data-id="${p.id}"><td class="l">${multi?`<span class="tg" data-tg="${p.id}" title="Show the ${p.parts.length} Tessie records">${op?"▾":"▸"}</span> `:""}${p.day} ${p.hm}${multi?` <span class="pill">${p.parts.length} parts</span>`:""}</td><td class="l">${whereLink({pkey:p.pkey,place:p.where,addr:p.addr})}</td><td>${kindPill(p.kind)}</td><td>${dur(p.min)}</td><td>${n0(p.b0)}→${n0(p.b1)}%</td><td>${p.peak==null?"–":n0(p.peak)}</td><td>${p.kmh==null?"–":n0(p.kmh)}</td><td>${p.disp==null?"–":n0(p.disp,1)}</td><td>${n0(p.kwh,1)}</td><td>${p.eff==null?"–":n0(p.eff)+"%"}</td><td>${p.rate==null?"–":money(p.rate)}</td><td>${money(p.cost)}</td><td>${p.invoice?esc(p.invoice.number||"yes"):p.home?'<span class="sub">–</span>':'<span class="pill warn" title="No invoice matched this charge">none</span>'}</td><td>${syncPill(p.sync)}</td></tr>`;
      if(multi&&op)h+=p.parts.map(x=>`<tr class="chs" data-id="${x.id}"><td class="l" style="padding-left:30px">↳ ${x.day} ${x.hm}</td><td class="l sub">part of the charge above</td><td></td><td>${dur(x.min)}</td><td>${n0(x.b0)}→${n0(x.b1)}%</td><td>${x.peak==null?"–":n0(x.peak)}</td><td>${x.kmh==null?"–":n0(x.kmh)}</td><td>${x.disp==null?"–":n0(x.disp,1)}</td><td>${n0(x.kwh,1)}</td><td></td><td>${x.irate==null&&x.trate==null?"–":money(x.irate!=null?x.irate:x.trate)}</td><td>${money(x.icost!=null?x.icost:x.tcost)}</td><td class="sub">${p.invoice?"same invoice":""}</td><td>${syncPill(x.sync)}</td></tr>`).join("");
      return h};
    $("#tb").innerHTML=`<table><thead><tr>${COLS.map(c=>`<th class="${c[2]?"l":""}" data-k="${c[0]}">${c[1]}${CP.sk===c[0]?(CP.dir<0?" ▾":" ▴"):""}</th>`).join("")}</tr></thead><tbody>${rs.map(row).join("")}</tbody></table>${rs.length?"":'<div class="empty">No charges here.</div>'}`;
    $$("#tb th").forEach(th=>th.onclick=()=>{const k=th.dataset.k;if(CP.sk===k)CP.dir=-CP.dir;else{CP.sk=k;CP.dir=k==="t0"||typeof P[0][k]==="number"?-1:1}draw()});
    $$("#tb tr.chr,#tb tr.chs").forEach(tr=>tr.onclick=ev=>{const tg=ev.target.closest("[data-tg]");if(tg){CP.open[tg.dataset.tg]=!CP.open[tg.dataset.tg];draw();return}if(ev.target.closest("a"))return;location.hash="#/charge/"+tr.dataset.id});
  };
  const invoices=()=>{
    const I=B.invoices.filter(i=>inRange({day:i.date},CP.r)&&(!CP.q||((i.location||"")+" "+(i.network||"")+" "+(i.number||"")).toLowerCase().includes(CP.q.toLowerCase())));
    const tot=I.reduce((a,i)=>a+(i.total||0),0),kw=I.reduce((a,i)=>a+(i.kwh||0),0),un=I.filter(i=>!i.plug).length;
    $("#tl").innerHTML=tile(n0(I.length),"invoices")+tile(money(tot),"invoiced")+tile(n0(kw,1)+" kWh","energy invoiced")+tile(kw?money(tot/kw)+"/kWh":"–","average price")+tile(money(gstOf(tot)),"GST included")+tile(un?String(un):"none",un?"invoices with no matching charge":"unmatched invoices");
    table($("#tb"),[{k:"date",t:"Date",l:1},{k:"network",t:"Network",l:1,f:i=>esc(i.network||"")},{k:"location",t:"Location",l:1,f:i=>esc((i.location||"").split(",").slice(0,2).join(","))},{k:"number",t:"Invoice",l:1,f:i=>esc(i.number||"")},{k:"kwh",t:"kWh",f:i=>n0(i.kwh,2)},{k:"rate",t:"$/kWh",f:i=>i.rate==null?"–":money(i.rate)},{k:"gst",t:"GST",f:i=>money(gstOf(i.total))},{k:"total",t:"Total",f:i=>money(i.total)},{k:"plug",t:"Charge",f:i=>i.plug?`<a href="#/charge/${i.plug}" onclick="event.stopPropagation()">open</a>`:'<span class="pill warn">no match</span>'},{k:"file",t:"File",f:i=>i.file?`<a href="/api/invoice/${encodeURIComponent(i.file)}" target="_blank" onclick="event.stopPropagation()" title="${esc(i.file)}">open</a>`:""}],I,i=>{if(i.plug)location.hash="#/charge/"+i.plug},"date");
  };
  const show=()=>{$$("[data-tab]").forEach(c=>c.classList.toggle("on",c.dataset.tab===CP.tab));$("#kd").style.display=$("#iv").style.display=CP.tab==="charges"?"":"none";CP.tab==="charges"?draw():invoices()};
  main.innerHTML=`<h1>Charges</h1><div class="sub">Every charge reconciled with its invoice, as in <code>./analyse_charging.py</code>. A plug-in that Tessie recorded as several sessions is one charge with its records as parts.${B0.status==="building"?" · refreshing in the background…":""}</div>
   <div class="bar"><button class="chip" data-tab="charges">Charges</button><button class="chip" data-tab="invoices">Invoices (${B.invoices.length})</button><span id="rg"></span><select id="kd"><option value="all">All types</option><option value="ac">AC / home</option><option value="dc">DC fast</option><option value="supercharger">Supercharger</option></select><select id="iv"><option value="all">Any invoice state</option><option value="with">With an invoice</option><option value="without">Public, no invoice</option><option value="unsynced">Not yet in Tessie</option></select><input type="search" id="q" placeholder="Search a place or invoice…" value="${esc(CP.q)}"></div><div class="tiles" id="tl"></div><div id="tb" style="overflow-x:auto"></div>`;
  $("#kd").value=CP.kind;$("#iv").value=CP.inv;rangePicker($("#rg"),CP.r,daysOf(P),show);$("#kd").onchange=e=>{CP.kind=e.target.value;show()};$("#iv").onchange=e=>{CP.inv=e.target.value;show()};$("#q").oninput=e=>{CP.q=e.target.value;show()};
  $$("[data-tab]").forEach(c=>c.onclick=()=>{CP.tab=c.dataset.tab;show()});show();
  if(B0.status==="building")bookPoll("#/charges",pageCharges,true);
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
async function pageCharge(id,pid){
  loading();const [B0,D]=await Promise.all([bookWithRaw(),fetch("/api/chargebook/"+encodeURIComponent(id)+(pid?"?part="+encodeURIComponent(pid):"")).then(r=>r.ok?r.json():null).catch(()=>null)]);
  if(!B0.book){bookWait(B0,"Charge",()=>pageCharge(id,pid));return}
  if(!D||!D.plug){return pageChargeBasic(id)}
  const p=D.plug,part=D.part&&p.parts.find(x=>x.id===D.part),v=part||p,multi=p.parts.length>1,raw=B0.book.plugs.find(z=>z.id===p.id)||p;
  const where=raw.where||p.place,end=addMin(v.hm,part?part.min:p.span),inv=p.invoice,cur=D.curves&&!D.curves.error?D.curves:null;
  const eff=v.disp&&v.kwh?100*v.kwh/v.disp:null,costv=part?(part.icost!=null?part.icost:part.tcost):p.cost,ratev=part?(part.irate!=null?part.irate:part.trate):p.rate,minv=part?part.min:p.min;
  const links=multi?p.parts.map((x,i)=>`<a class="chip ${part&&part.id===x.id?"on":""}" href="#/charge/${p.id}/${x.id}" style="text-decoration:none">Part ${i+1} · ${x.hm}</a>`).join(" "):"";
  const amps=D.segments[0]&&D.segments[0].amps_src==="pack";
  main.innerHTML=(part?crumbs("#/charge/"+p.id,"the whole charge ("+p.parts.length+" parts)"):crumbs("#/charges","Charges"))+`<h1>${whereLink({pkey:raw.pkey,place:where,addr:p.addr})}${part?` <span class="sub" style="font-size:15px">· part ${p.parts.indexOf(part)+1} of ${p.parts.length}</span>`:""}</h1>
   <div class="sub">${dayName(v.day)} · ${v.hm} – ${end} · ${dur(part?part.min:p.span)}${multi&&!part?" plugged in":""} · ${esc(p.network||(p.kind==="supercharger"?"Supercharger":p.kind==="dc"?"DC fast charger":"AC charging"))} · ${esc(String(p.status||"").replace(/[^\x20-\x7e]/g,"").trim())}${raw.pkey?' · <a href="#/place/'+encodeURIComponent(raw.pkey)+'">what is around here</a>':""}</div>
   ${multi?`<div class="bar"><a class="chip ${part?"":"on"}" href="#/charge/${p.id}" style="text-decoration:none">Whole charge</a>${links}<span class="sub">Tessie recorded this plug-in as ${p.parts.length} sessions; it is one charge with one invoice.</span></div>`:""}
   ${await alertsHere(p)}
   <div class="chgrid"><div class="chl">
     <div class="card"><h2 style="margin-top:0">Charger power and battery</h2><div class="sub" style="margin-bottom:6px">${chartSub(p,part,cur)}</div><div id="tc"></div></div>
     <div class="card"><h2 style="margin-top:0">Pack temperature, current and voltage</h2><div id="tv"></div></div>
     <div class="card"><h2 style="margin-top:0">Power against state of charge</h2>${cur?`<div class="sub" style="margin-bottom:6px"><div class="cs">${[cur.hardware&&cur.tier?esc(cur.hardware)+" · "+esc(cur.tier)+(cur.cap?" · "+n0(cur.cap)+" kW cap":"")+(cur.stalls?" · "+cur.stalls+" stalls":""):"",esc(cur.caption||"")].filter(Boolean).map(x=>`<div class="cs-r">${x}</div>`).join("")}</div></div>`:""}<div id="cc"></div></div>
     <div class="card"><h2 style="margin-top:0">Pack voltage and current, with history</h2><div id="pc"></div></div>
     <div class="card"><h2 style="margin-top:0">Where</h2><div class="map" id="mp"></div><div class="sub" style="margin-top:8px">${esc(p.addr||"")}</div></div>
   </div><div class="chr2">
     <div class="tiles tiles2">${tile("+"+n0(v.kwh,1)+" kWh","into the battery")}${tile(n0(v.b0)+"% → "+n0(v.b1)+"%","battery")}${tile(money(costv),(ratev!=null?money(ratev)+"/kWh · ":"")+(inv&&!part?"invoice total":part&&v.icost!=null?"invoice share":"Tessie cost"))}${tile(v.disp!=null?n0(v.disp,1)+" kWh":"–","from the charger"+(inv&&!part?" (invoice)":""))}${tile(eff?n0(eff,1)+"%":"–","efficiency")}${tile(v.peak?n0(v.peak)+" kW":"–","peak"+(minv&&v.kwh?" · avg "+n0(v.kwh*60/minv)+" kW":""))}${tile(v.kmh?n0(v.kmh)+" km/h":"–","range recovered per hour")}${tile(syncPillText(part?part.sync:p.sync),"in Tessie")}</div>
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
  if(inv){h+=`<table class="kv"><tbody>${kv("Invoice",esc(inv.number||""))}${kv("Date",esc(inv.date||""))}${kv("Network",esc(inv.network||""))}${kv("Location",esc(inv.location||""))}${kv("Energy invoiced",inv.kwh!=null?n0(inv.kwh,2)+" kWh":"")}${kv("Rate",inv.rate!=null?money(inv.rate)+"/kWh"+(inv.gross_rate!=null&&inv.gross_rate!==inv.rate?" (gross "+money(inv.gross_rate)+")":""):"")}${kv("GST included",money(gstOf(inv.total)))}${kv("Total",`<b>${money(inv.total)}</b>`)}${inv.file?kv("File",`<a href="/api/invoice/${encodeURIComponent(inv.file)}" target="_blank">${esc(inv.file)}</a>`):""}</tbody></table>`}
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
   ${!part&&p.expected_rate!=null?kv("Expected tariff",money(p.expected_rate)+"/kWh"+(p.schedule?" · "+esc(p.schedule):""),"from the charger's published rates at that time"):""}${!part&&p.theory!=null&&p.theory>0?kv("Theoretical cost",money(p.theory),"expected rate × kWh"+(p.icost!=null?" · invoice "+(p.icost-p.theory>=0?"+":"-")+money(Math.abs(p.icost-p.theory))+" vs theory":"")):""}${!part&&p.gst?kv("GST included (calculated)",money(p.gst),""):""}</tbody></table>`;
}
function telCard(p,part){
  const parts=part?[part]:p.parts,kv=(a,b)=>b==null||b===""?"":`<tr><td>${a}</td><td>${b}</td></tr>`;
  return parts.map(x=>{const t=x.tel,head=multi1(p)?`<div class="sub" style="margin:8px 0 2px"><b>Part ${p.parts.indexOf(x)+1} · ${x.hm}</b></div>`:"";
    return head+(t?`<table class="kv"><tbody>${kv("Samples",n0(t.samples)+" over "+dur(t.min))}${kv("State of charge",n0(t.soc0)+"% → "+n0(t.soc1)+"%")}${kv("Power",(t.peak==null?"–":"peak "+n0(t.peak,1))+" kW · "+(t.avg==null?"–":"avg "+n0(t.avg,1))+" kW")}${kv("Pack temperature",t.t_start==null?"":n0(t.t_start,1)+" → "+n0(t.t_end,1)+" °C (max "+n0(t.t_max,1)+")")}${kv("Battery heater",esc(t.heater||""))}${kv("Outside / inside",t.outside==null?"":n0(t.outside,1)+" / "+(t.inside==null?"–":n0(t.inside,1))+" °C")}${kv("Energy at the charger",t.int_disp==null?"":n0(t.int_disp,2)+" kWh (integrated)")}${kv("Energy into the pack",t.int_pack==null?"":n0(t.int_pack,2)+" kWh (integrated)")}${kv("Car's own energy gauge",t.d_energy==null?"":n0(t.d_energy,2)+" kWh")}${kv("File",esc(t.file||""))}</tbody></table>`:'<div class="sub">No telemetry file for this record.</div>')}).join("");
}

// the plain page, kept for a charge that is not in the book (e.g. the book could not be built)
async function pageChargeBasic(id){
  loading();const d=await api("charge/"+id),c=d.charge;
  main.innerHTML=crumbs("#/charges","Charges")+`<h1>${whereLink(c)}</h1><div class="sub">${dayName(c.day)} · ${c.hm} – ${addMin(c.hm,c.min)} · ${dur(c.min)} · ${c.kind==="supercharger"?"Supercharger":c.kind==="dc"?"DC fast charger":"AC charging"}${c.pkey?' · <a href="#/place/'+encodeURIComponent(c.pkey)+'">what is around here</a>':""}</div>
   <div class="tiles">${tile("+"+n0(c.kwh,1)+" kWh","energy added")}${tile(n0(c.b0)+"% → "+n0(c.b1)+"%","battery")}${tile(money(c.cost),c.rate!=null?money(c.rate)+"/kWh":"cost")}${tile(c.used!=null?n0(c.used,1)+" kWh":"–","energy drawn")}${tile(c.used&&c.kwh?n0(100*c.kwh/c.used)+"%":"–","charging efficiency")}${tile(c.min?n0(c.kwh*60/c.min,1)+" kW":"–","average power")}</div>
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
    $("#tl").innerHTML=tile(n0(rs.length),"saved Sentry events")+tile(n0(rs.filter(v=>idleOf(v)).length),"inside a recorded parked period")+tile(sn.connected?"Connected":"Not connected","Archive drive");
    $("#tb").innerHTML=(sn.connected?'<div id="spl"></div>':'<div class="empty">The Archive drive is not connected.</div>')+Object.keys(byDay).sort().reverse().slice(0,60).map(d=>`<div class="day">${dayName(d)}<small>${byDay[d].length} event${byDay[d].length>1?"s":""}</small></div><div class="evs">${byDay[d].map(v=>`<button class="evc" data-id="${v.id}"><img loading="lazy" src="/footage/A/SentryClips/${v.id}/thumb.png" onerror="this.style.visibility='hidden'"><b>${hms(v.ts)}</b><span>${esc(reason(v.reason))}</span><span>${esc(v.city||"")}</span>${idleOf(v)?`<a href="#/parked/${idleOf(v).id}" onclick="event.stopPropagation()">parked period</a>`:""}</button>`).join("")}</div>`).join("");
    $$(".evc").forEach(b=>b.onclick=()=>{$$(".evc").forEach(x=>x.classList.toggle("on",x===b));sentryPlayer($("#spl"),evs.find(v=>v.id===b.dataset.id));$("#spl").scrollIntoView({block:"nearest"})})};
  const draw=()=>{if(IP.tab==="events")return drawEvents();const q=IP.q.toLowerCase(),rs=all.filter(x=>inRange(x,IP.r)&&x.min>=IP.min&&(IP.sen==="all"||x.nev>0)&&(!q||place(x).toLowerCase().includes(q))),kwh=rs.reduce((a,x)=>a+(x.kwh||0),0),mn=rs.reduce((a,x)=>a+x.min,0);
    $("#tl").innerHTML=tile(n0(rs.length),"parked periods")+tile(dur(mn),"time parked")+tile(n0(kwh,1)+" kWh","energy used while parked")+tile(mn?n0(kwh*1000/(mn/60))+" Wh/h":"–","average drain")+tile(n0(rs.reduce((a,x)=>a+x.nev,0)),"saved Sentry events here")+tile(sn.connected?n0(evs.length)+" ("+matched.size+" placed)":"Archive not connected","Sentry events on the Archive");
    table($("#tb"),[{k:"t0",t:"Date",l:1,f:r=>`${r.day} ${r.hm}`},{k:"place",t:"Where",l:1,f:whereLink},{k:"min",t:"Parked",f:r=>dur(r.min)},{k:"b0",t:"Battery",f:r=>`${n0(r.b0)}→${n0(r.b1)}%`},{k:"kwh",t:"kWh",f:r=>n0(r.kwh,2)},{k:"dr",t:"Wh/h",f:r=>r.min?n0(r.kwh*1000/(r.min/60)):"–"},{k:"nev",t:"Sentry clips",f:r=>r.nev?`<b>${r.nev}</b> ▶`:"–"},{k:"sentry",t:"In Sentry",f:r=>r.sentry>0.01?Math.round(r.sentry*100)+"%":"–"},{k:"climate",t:"Climate",f:r=>r.climate>0.01?Math.round(r.climate*100)+"%":"–"}],rs.map(r=>Object.assign({dr:r.min?r.kwh/(r.min/60):0},r)),r=>location.hash="#/parked/"+r.id,"t0")};
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
  if(!sn.connected){box.innerHTML='<div class="sub">The Archive drive is not connected, so saved Sentry clips cannot be shown.</div>';return}
  if(!evs.length){box.innerHTML='<div class="sub">No saved Sentry events during this parked period.</div>';return}
  box.innerHTML=`<div class="sub">${evs.length} saved event${evs.length>1?"s":""}. Pick one to watch.</div><div class="evs">${evs.map((v,i)=>`<button class="evc" data-i="${i}"><img loading="lazy" src="/footage/A/SentryClips/${v.id}/thumb.png" onerror="this.style.visibility='hidden'"><b>${hms(v.ts)}</b><span>${esc(reason(v.reason))}${v.cam?" · camera "+esc(v.cam):""}</span></button>`).join("")}</div>`;
  const pick=i=>{$$(".evc").forEach(b=>b.classList.toggle("on",+b.dataset.i===i));sentryPlayer($("#spl"),evs[i])};
  $$(".evc").forEach(b=>b.onclick=()=>pick(+b.dataset.i));pick(0);
}
// ---------- Clip player (Sentry, Saved and Recent clips): all six cameras laid out like a drive page, or one camera big
// event.json "camera" -> the camera that saw it (checked against the thumbnails and the clips: 0 and 1 front, 5 left repeater, 6 right repeater, 7 back; others unknown -> all six)
const EVCAM={"0":"front","1":"front","5":"left_repeater","6":"right_repeater","7":"back"};
const MSZ=[["s","Small","260px"],["m","Medium","420px"],["l","Large","640px"],["f","Full","calc(100vh - 120px)"]],TSZ=[["h","Hide","0"],["n","Normal","284px"]];
const CORDER=["left_pillar","front","right_pillar","left_repeater","back","right_repeater"];
const lsGet=(k,d)=>{try{return localStorage.getItem(k)||d}catch(e){return d}},lsSet=(k,v)=>{try{localStorage.setItem(k,v)}catch(e){}};
// o = {head: html line above the video, stamps: [minute, ...], cams: {minute: [camera, ...]}, url: (minute, camera) => url, trig: epoch ms the event happened (start a little before it)}
function clipPlayer(host,o){
  const stamps=o.stamps;if(!stamps.length){host.innerHTML='<div class="sub">This has no video clips.</div>';return}
  // a clip's name is the car's clock (stMs: only used to place things that are on that same clock, like a Sentry trigger); what is SHOWN is the local clock (o.loc: stamp -> local text)
  const stMs=s=>Date.parse(s.slice(0,10)+"T"+s.slice(11).replace(/-/g,":")),LOC=s=>(o.loc&&o.loc[s])||s.slice(0,10)+"T"+s.slice(11).replace(/-/g,":"),clock=s=>LOC(s).slice(11,19),shownAt=(s,t)=>new Date(Date.parse(LOC(s)+"Z")+t*1000).toISOString().substr(11,8),fmt=t=>Math.floor(t/60)+":"+String(Math.floor(t%60)).padStart(2,"0");
  let si=0,seek=0,view=o.view||lsGet("tc.view","all"),rate=lsGet("tc.rate","1"),vs=[],mv=null;
  if(o.trig){si=stamps.reduce((a,s,i)=>stMs(s)<=o.trig?i:a,0);seek=Math.max(0,(o.trig-stMs(stamps[si]))/1000)}
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
    if(mv.readyState>=1)ready();else mv.onloadedmetadata=()=>{ready();vs.forEach(x=>x.play().catch(()=>{}))};
    mv.onplay=()=>pl.textContent="❚❚ Pause";mv.onpause=()=>{if(!mv.ended)pl.textContent="▶ Play"};
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
  const prep=()=>{if(nx||si>=stamps.length-1)return;const s2=stamps[si+1],have=o.cams[s2]||[],m={};
    $$("#vids figure[data-c]").forEach(f=>{const c=f.dataset.c;if(have.includes(c)){const v=document.createElement("video");v.muted=true;v.playsInline=true;v.preload="auto";v.src=o.url(s2,c);v.load();m[c]=v}});
    nx={i:si+1,m}};
  const swap=()=>{const m=nx.m;nx=null;vs.forEach(drop);
    $$("#vids figure[data-c]").forEach(f=>{const nv=m[f.dataset.c],old=f.querySelector("video");if(nv&&old)f.replaceChild(nv,old)});
    if($("#stp"))$("#stp").value=si;
    vs=$$("#vids video");mv=vs.find(v=>v.parentNode.dataset.c==="front")||vs[0];wire();vs.forEach(x=>{x.playbackRate=+rate;x.play().catch(()=>{})})};
  const vids=()=>{dropAll();
    const s=stamps[si],have=o.cams[s]||[],all=view==="all",show=all?CORDER:[have.includes(view)?view:have.includes("front")?"front":have[0]];
    const box=$("#vids");box.className=all?"vgrid":"vone";
    box.innerHTML=show.map(c=>have.includes(c)?`<figure data-c="${c}" class="${o.hot===c?"hot":""}" ${all?'title="Click to see this camera large"':""}><video muted playsinline preload="auto" src="${o.url(s,c)}"></video><figcaption>${CAMN[c]}${o.hot===c?" · detected here":""}</figcaption></figure>`:`<figure class="none"><div>no file</div><figcaption>${CAMN[c]}</figcaption></figure>`).join("");
    vs=$$("#vids video");mv=vs.find(v=>v.parentNode.dataset.c==="front")||vs[0];if(!mv){return}
    if(all)$$("#vids figure[data-c]").forEach(f=>f.onclick=()=>{view=f.dataset.c;seek=mv.currentTime;shell()});
    wire()};
  // nothing plays while the page is hidden (a hidden tab still decodes six streams otherwise)
  const hide=()=>{if(!document.contains(host)){document.removeEventListener("visibilitychange",hide);dropAll();return}if(document.hidden&&mv&&!mv.paused){offp=true;vs.forEach(x=>x.pause())}else if(!document.hidden&&offp){offp=false;vs.forEach(x=>x.play().catch(()=>{}))}};
  document.addEventListener("visibilitychange",hide);
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
function sentryPlayer(host,ev,role="A",folder="SentryClips"){
  const stamps=Object.keys(ev.clips),t=ev.ts,what=ev.reason?reason(ev.reason):folder==="SavedClips"?"Saved clip":"Sentry event";
  clipPlayer(host,{stamps,cams:ev.clips,loc:ev.loc,trig:Date.parse(ev.cts||t),view:EVCAM[ev.cam]||"all",url:(s,c)=>`/footage/${role}/${folder}/${ev.id}/${s}-${c}.mp4`,
    head:`${esc(what)} on ${dayName(t.slice(0,10))} at ${hms(t)}${ev.city?" · "+esc(ev.city):""}${ev.street&&folder==="SavedClips"?" · "+esc(ev.street):""}. One-minute clips, ${stamps.length} in this event; the next clip plays on its own. It opens at the moment it happened${EVCAM[ev.cam]?", on the "+CAMN[EVCAM[ev.cam]].toLowerCase()+" camera, which is the one that saw it":""}.`});
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
  let p;if(key==="@new"){let pre={};try{pre=JSON.parse(sessionStorage.getItem("placePrefill")||"{}")}catch(e){}p=Object.assign({kind:"spot",key,name:"New place",lat:null,lon:null,radius:150,keywords:[],notes:"",visits:[],nvisits:0},pre,{radius:pre.radius_m||150,name:pre.name||"New place"})}
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
    <label class="w">Name<input id="f_name" value="${esc(p.kind==="spot"?(pre.name||""):p.name)}" placeholder="e.g. Corner Shop Chargers"></label>
    <label>Latitude<input id="f_lat" value="${p.lat==null?"":p.lat}"></label><label>Longitude<input id="f_lon" value="${p.lon==null?"":p.lon}"></label>
    <label>Radius (m)<input id="f_rad" value="${p.kind==="spot"&&pre.radius_m?pre.radius_m:(p.radius||150)}"></label>${p.kind==="spot"&&pre.radnote?`<div class="sub" style="align-self:end">${esc(pre.radnote)}</div>`:""}
    <label class="w">Keywords (addresses or names to match, comma separated)<input id="f_kw" value="${esc(kw)}"></label>
    <label class="w">Notes<input id="f_notes" value="${esc(p.notes||"")}"></label></div>
    <div class="bar"><button class="btn pri" id="sv">${p.kind==="saved"?"Save changes":"Save place"}</button>${p.kind==="saved"?'<button class="btn bad" id="dl">Delete</button>':""}<span class="sub" id="msg"></span></div>
    <div class="sub">Saved places are written to places.json (a dated backup is kept first). The sync scripts use the same file.</div>`;
    $("#sv").onclick=async()=>{const m=$("#msg");m.textContent="Saving…";const body={old_name:p.kind==="saved"?p.name:"",name:$("#f_name").value,lat:$("#f_lat").value,lon:$("#f_lon").value,radius_m:$("#f_rad").value,keywords:$("#f_kw").value,notes:$("#f_notes").value};
      try{const j=await (await fetch("/api/places/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})).json();
        if(j.ok){Object.keys(cache).forEach(k=>delete cache[k]);sessionStorage.removeItem("placePrefill");location.hash="#/place/"+encodeURIComponent(j.key);route()}else m.textContent="Not saved: "+j.error}catch(e){m.textContent="Could not reach the app: "+e}};
    if($("#dl"))$("#dl").onclick=async()=>{if(!confirm("Delete the saved place '"+p.name+"'? Past drives keep their addresses."))return;
      const j=await (await fetch("/api/places/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:p.name})})).json();
      if(j.ok){Object.keys(cache).forEach(k=>delete cache[k]);location.hash="#/places"}else $("#msg").textContent="Not deleted: "+j.error}}
  else ed.innerHTML=`<h2 style="margin-top:0">About this ${p.kind==="charger"?"charger":"Supercharger"}</h2><div class="sub">${p.kind==="charger"?"From the charger registry (sync_charger_all.sh). Use Sync chargers on the Places page to refresh it.":"The name is the one used on tesla.com."}</div><table class="kv">${p.kind==="charger"?`<tr><td>Network</td><td>${esc(p.network||"–")}</td></tr><tr><td>Connectors</td><td>${esc(p.conn||"–")}</td></tr><tr><td>Access</td><td>${esc(p.access||"–")}</td></tr>`:""}<tr><td>Stalls</td><td>${n0(p.stalls)}</td></tr><tr><td>Max power</td><td>${n0(p.kw)} kW${p.tier?" ("+esc(p.tier)+")":""}</td></tr><tr><td>Hours</td><td>${esc(p.hours||"–")}</td></tr><tr><td>Address</td><td>${esc(p.addr||"–")}</td></tr></table>${p.notes?`<div class="sub" style="margin-top:8px;white-space:pre-line">${esc(p.notes)}</div>`:""}`;
  $("#vs").innerHTML=vis.length?vis.filter(v=>!(v.k==="repark"&&v.in_stay)).map(v=>{const href=v.k==="charge"?"#/charge/"+v.id:v.k==="idle"?"#/parked/"+v.id:v.k==="stay"?"":v.id?"#/drive/"+v.id:"";
    return `<a class="row" ${href?`href="${href}"`:""}><span class="t">${v.day} ${v.hm}</span><span class="k ${v.k==="stay"?"idle":v.k==="repark"?"drive":v.k}"></span><div style="min-width:0"><div class="ti">${v.k==="charge"?"Charge":v.k==="idle"||v.k==="stay"?"Parked":v.k==="repark"?"Car moved a few metres":"Arrived"}</div><div class="st">${dur(v.min)}${v.k==="stay"&&v.reparks?" · "+v.reparks+" re-park"+(v.reparks>1?"s":""):""}${v.frm&&v.k==="drive"?" · from "+esc(v.frm):""}${v.parts&&v.parts.length>1?" · "+v.parts.length+" parts (Tessie recorded "+v.parts.length+" sessions)":""}</div></div><div class="nums">${v.k==="charge"?`<span>+${n0(v.kwh,1)}<small>kWh</small></span><span>${money(v.cost)}<small>cost</small></span>`:v.k==="drive"?`<span>${n0(v.km,1)}<small>km</small></span>`:v.k==="repark"?`<span>${n0(v.km*1000)}<small>m</small></span>`:v.k==="stay"?`<span>${v.reparks||0}<small>re-parks</small></span>`:`<span>${n0(v.kwh,2)}<small>kWh</small></span>`}</div></a>`+(v.parts&&v.parts.length>1?v.parts.map((x,i)=>`<a class="row nest" href="#/charge/${x.id}"><span class="t">${x.day} ${x.hm}</span><span class="k charge"></span><div style="min-width:0"><div class="st">↳ part ${i+1} · ${dur(x.min)} · ${n0(x.b0)}→${n0(x.b1)}%</div></div><div class="nums"><span>+${n0(x.kwh,1)}<small>kWh</small></span><span>${money(x.cost)}<small>cost</small></span></div></a>`).join(""):"")}).join(""):'<div class="empty">No recorded visits.</div>';
  if(p.lat==null){$("#osm").innerHTML='<div class="sub">Needs a location.</div>';return}
  osmBlock($("#osm"),p);
}
async function osmBlock(host,p){
  let R=OSMG[p.key+"r"]||300,q="";
  const load=async refresh=>{host.querySelector("#ol")&&(host.querySelector("#ol").innerHTML='<div class="sub">Asking OpenStreetMap…</div>');
    try{const r=await fetch(`/api/place-osm?key=${encodeURIComponent(p.key)}&lat=${p.lat}&lon=${p.lon}&r=${R}${refresh?"&refresh=1":""}`),j=await r.json();OSMG[p.key]=j;paint()}catch(e){host.innerHTML='<div class="sub">Could not load: '+esc(e)+'</div>'}};
  const paint=()=>{const j=OSMG[p.key]||{items:[]},its=j.items.filter(i=>!q||(i.name+" "+i.group+" "+i.category).toLowerCase().includes(q.toLowerCase())),by={};its.forEach(i=>(by[i.group]=by[i.group]||[]).push(i));
    host.innerHTML=`<div class="bar"><select id="orad">${[100,300,500,1000].map(v=>`<option value="${v}" ${v===R?"selected":""}>within ${v} m</option>`).join("")}</select><input type="search" id="oq" placeholder="Filter these…" value="${esc(q)}"><button class="btn" id="orf">Refresh from OpenStreetMap</button><span class="sub">${its.length} places · ${esc(j.source||"")}${j.fetched?" · "+esc(String(j.fetched).slice(0,10)):""}</span></div>
     <div id="ol">${its.length?Object.keys(by).sort().map(g=>`<div class="og"><h3>${esc(by[g][0].emoji||"")} ${esc(g)} <span>${by[g].length}</span></h3>${by[g].map(i=>`<div class="oi"><span class="on">${esc(i.name)}</span><span class="sub">${esc(i.category&&i.category!==g?i.category:"")}</span><span class="od">${i.dist_m==null?"":n0(i.dist_m)+" m"}</span>${i.lat!=null?`<a href="https://www.openstreetmap.org/?mlat=${i.lat}&mlon=${i.lon}#map=19/${i.lat}/${i.lon}" target="_blank">map</a><a href="#" data-sv="${esc(i.name)}|${i.lat}|${i.lon}|${esc(i.addr||"")}">save as place</a>`:""}</div>`).join("")}</div>`).join("")
      :(j.error?errBox(j.error,"OpenStreetMap did not answer. This is the server being busy, not an empty street: press Refresh from OpenStreetMap to try again."):'<div class="sub">Nothing found within this radius. Try a wider radius or Refresh.</div>')}</div>`;
    $("#orad").onchange=e=>{R=+e.target.value;OSMG[p.key+"r"]=R;load(false)};$("#oq").oninput=e=>{q=e.target.value;const pos=e.target.selectionStart;paint();const o=$("#oq");o.focus();o.setSelectionRange(pos,pos)};$("#orf").onclick=()=>load(true);
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
async function pageHealth(){
  loading();const b=await api("battery"),r=b.readings,sp=b.spec.battery||{},orig=sp.original_capacity_kwh,last=r[r.length-1],lastOdo=[...r].reverse().find(x=>x.odo!=null);
  const pts=r.filter(x=>x.odo!=null).map(x=>({odo:x.odo,cap:x.kwh,rng:x.range,t:x.t})).sort((a,c)=>a.odo-c.odo),rp=pts.filter(p=>p.rng!=null);
  const rec=r.slice(-7),avg=rec.length?rec.reduce((a,x)=>a+x.kwh,0)/rec.length:null;
  main.innerHTML=`<h1>Battery health</h1><div class="sub">From Tessie: usable capacity and rated range against odometer, ${r.length} readings (${pts.length} with an odometer). The dashed line is the least-squares trend; single readings wobble by about ±1 kWh.</div>
   <div class="tiles">${tile(last?n0(last.kwh,1)+" kWh":"–","latest capacity"+(last?" ("+last.t.slice(0,10)+")":""))}${tile(orig?n0(orig,1)+" kWh":"–","when new (reference)")}${tile(avg&&orig?n0(100*avg/orig,1)+"%":"–","capacity vs new (last 7 readings)")}${tile(last?n0(last.range)+" km":"–","max rated range")}${tile(lastOdo?n0(lastOdo.odo)+" km":"–","odometer")}${tile(sp.chemistry||"–","chemistry")}</div>
   <div class="card"><h2 style="margin-top:0">Usable capacity vs odometer</h2><div class="sub" id="s1"></div><div id="c1"></div></div>
   <div class="card" style="margin-top:14px"><h2 style="margin-top:0">Max rated range vs odometer</h2><div class="sub" id="s2"></div><div id="c2"></div></div>`;
  const a1=scatter($("#c1"),pts.map(p=>Object.assign({},p)),"cap","kWh","#3b82f6",2),a2=scatter($("#c2"),rp,"rng","km","#16a34a",0);
  if(a1)$("#s1").textContent=a1;if(a2)$("#s2").textContent=a2;
}

// ---------- Battery (Tessie's Battery page: where the charge level is now and how it moved; health is its own page)
let BP={r:{p:"30"}};
async function pageBattery(){
  loading();const act=await api("activity"),b=await api("battery"),hr=b.readings,lastH=hr[hr.length-1];
  const T1=x=>x.t1||x.t0+(x.min||0)*60,seg=act.filter(x=>x.b0!=null&&x.b1!=null).map(x=>({x,k:x.k,t0:x.t0,t1:Math.max(T1(x),x.t0),b0:x.b0,b1:x.b1})).sort((a,c)=>a.t0-c.t0);
  if(!seg.length){main.innerHTML='<h1>Battery</h1><div class="empty">No battery levels in the drives, charges or parked periods yet.</div>';return}
  const now=seg.reduce((a,s)=>s.t1>a.t1?s:a,seg[0]),cap=lastH?lastH.kwh:null,rng=lastH?lastH.range:null,d30=Date.now()/1000-30*86400;
  const id=act.filter(x=>x.k==="idle"&&x.t0>d30&&x.min>=30),hrs=id.reduce((a,x)=>a+x.min/60,0),drain=hrs?id.reduce((a,x)=>a+(x.kwh||0),0)*1000/hrs:null,pdrop=hrs?id.reduce((a,x)=>a+Math.max(0,(x.b0||0)-(x.b1||0)),0)/(hrs/24):null;
  const ch=act.filter(x=>x.k==="charge"&&x.b1!=null).slice(0,20).map(x=>x.b1).sort((a,c)=>a-c),lim=ch.length?ch[Math.floor(ch.length/2)]:null,lc=act.find(x=>x.k==="charge"&&(x.kwh||0)>=0.5);
  const lo=seg.filter(s=>s.t0>d30).reduce((a,s)=>Math.min(a,s.b0,s.b1),100);
  main.innerHTML=`<h1>Battery</h1><div class="sub">Charge level over time from your drives, charges and parked periods. As at ${new Date(now.t1*1000).toLocaleString("en-AU",{dateStyle:"medium",timeStyle:"short"})} (the end of the latest period), so it is the last known level, not live. Capacity trend and range over the years are on <a href="#/health">Battery health</a>.</div>
   <div class="tiles">${tile(n0(now.b1)+"%","battery level")}${tile(rng?n0(rng*now.b1/100)+" km":"–","rated range (estimated)")}${tile(cap?n0(cap*now.b1/100,1)+" kWh":"–","energy in the pack (estimated)")}${tile(drain!=null?n0(drain)+" Wh/h":"–","parked drain, last 30 days")}${tile(pdrop!=null?n0(pdrop,1)+"% / day":"–","parked loss, last 30 days")}${tile(lim!=null?n0(lim)+"%":"–","usual charge target (median of last 20)")}${tile(lc?n0(lc.b0)+"% → "+n0(lc.b1)+"%":"–","last charge"+(lc?" ("+lc.day+")":""))}${tile(lo<100?n0(lo)+"%":"–","lowest level, last 30 days")}</div>
   <div class="bar"><span id="rg"></span><span class="sub"><b style="color:#16a34a">━</b> charging &nbsp; <b style="color:#3b82f6">━</b> driving &nbsp; <b style="color:#94a3b8">━</b> parked</span></div>
   <div class="card"><div id="bc"></div></div>`;
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
}

// ---------- Analytics
let AM="km";
const METRICS={km:["Distance","km",0],kwh:["Energy used driving","kWh",0],whkm:["Efficiency","Wh/km",0],ap_pct:["Self-driving","% of distance",0],added:["Energy charged","kWh",0],cost:["Charging cost","$",0],cost_km:["Cost per 100 km","$",2],drives:["Drives","",0],idle_kwh:["Parked drain","kWh",1],max:["Top speed","km/h",0]};
async function pageAnalytics(){
  loading();const m=await api("analytics"),rows=m.slice(-36);
  const draw=()=>{const [t,u,dec]=METRICS[AM],vals=rows.map(x=>x[AM]),mx=Math.max(...vals.map(v=>v||0),1e-9);
    $("#bars").innerHTML=`<div class="bars">${rows.map(x=>`<div style="height:${Math.max(2,100*(x[AM]||0)/mx)}%" data-m="${x.month}"><span>${x.month.slice(5)}${x.month.endsWith("-01")?"<br>"+x.month.slice(0,4):""}</span></div>`).join("")}</div><div style="height:30px"></div>`;
    $$("#bars .bars div").forEach(b=>{const x=rows.find(r=>r.month===b.dataset.m);b.onmousemove=ev=>showTip(ev,`<b>${x.month}</b><br>${t}: ${x[AM]==null?"–":n0(x[AM],dec)+" "+u}`);b.onmouseleave=hideTip});
    $$(".chip").forEach(c=>c.classList.toggle("on",c.dataset.m===AM))};
  const tot=f=>m.reduce((a,x)=>a+(x[f]||0),0),km=tot("km");
  main.innerHTML=`<h1>Analytics</h1><div class="sub">By month, from the drive, charge and idle records.</div>
   <div class="tiles">${tile(n0(km)+" km","total distance")}${tile(n0(tot("drives")),"drives")}${tile(km?n0(tot("kwh")*1000/km)+" Wh/km":"–","lifetime efficiency")}${tile(n0(tot("added"))+" kWh","charged")}${tile(money(tot("cost")),"charging cost")}${tile(km?n0(100*tot("ap")/km)+"%":"–","self-driving")}</div>
   <div class="bar">${Object.entries(METRICS).map(([k,v])=>`<button class="chip" data-m="${k}">${v[0]}</button>`).join("")}</div><div class="card" id="bars"></div><h2>Month by month</h2><div id="tb"></div>`;
  $$(".chip").forEach(c=>c.onclick=()=>{AM=c.dataset.m;draw()});draw();
  table($("#tb"),[{k:"month",t:"Month",l:1},{k:"drives",t:"Drives"},{k:"km",t:"km",f:r=>n0(r.km)},{k:"whkm",t:"Wh/km",f:r=>n0(r.whkm)},{k:"ap_pct",t:"Self-driving",f:r=>r.ap_pct==null?"–":n0(r.ap_pct)+"%"},{k:"charges",t:"Charges"},{k:"added",t:"kWh added",f:r=>n0(r.added)},{k:"home",t:"Home kWh",f:r=>n0(r.home)},{k:"super",t:"Fast kWh",f:r=>n0(r.super)},{k:"cost",t:"Cost",f:r=>money(r.cost)},{k:"cost_km",t:"$/100 km",f:r=>r.cost_km==null?"–":money(r.cost_km)},{k:"idle_kwh",t:"Parked kWh",f:r=>n0(r.idle_kwh,1)}],m,()=>{},"month");
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
const VT=window.IntersectionObserver?new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){const v=e.target;VT.unobserve(v);v.src=v.dataset.src+"#t=2"}}),{rootMargin:"300px"}):null;
const vthumb=url=>`<video class="vt" muted playsinline preload="metadata" data-src="${esc(url)}" onerror="this.outerHTML='<div class=\\'noimg\\'></div>'"></video>`;
const lazyThumbs=host=>$$("video.vt",host).forEach(v=>{if(VT)VT.observe(v);else v.src=v.dataset.src+"#t=2"});
// ---------- TeslaCam: the clips on any connected drive, and one continuous timeline built from the files alone
let TCS={role:null,kind:"timeline",q:"",r:{p:"all"},src:{sentry:1,saved:1,recent:1}},TCPOLL=null,TLOFF=null;
const tcGet=async p=>{const r=await fetch("/api/"+p);if(!r.ok)throw new Error(p+": "+r.status);return r.json()};
const tcPost=async(p,b)=>(await fetch("/api/"+p,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)})).json();
const TCF={sentry:"SentryClips",saved:"SavedClips"},TCK={timeline:"Timeline",clips:"Clips"},TCSRC={sentry:"Sentry",saved:"Saved",recent:"Recent"};
const isoMs=t=>Date.parse(t),mins=(a,b)=>Math.round((isoMs(b)-isoMs(a))/60000);
const clockHM=t=>t.slice(11,16);
// the saved place (or nothing) a position is inside
function nameAt(places,p){if(!p||p[0]==null)return "";let best="",bd=1e9;for(const x of places||[]){if(x.lat==null)continue;const d=hav(p[0],p[1],x.lat,x.lon);if(d<=Math.max(x.radius||100,60)&&d<bd){bd=d;best=x.name}}return best}
// ---------- the TeslaCam timeline: for each day a map on the left, the day's drives, Sentry events and parked spells (tick boxes) on the right
// Ticking a row shows it on that day's map: a drive as its route (speed gradient or who was driving), a Sentry event as a red dot, a parked spell as a 🅿️.
// A drive row still opens the drive when clicked; a Sentry row plays its clips. A drive shorter than the re-park limits (settings app.drives) counts as parked.
const TLR={},TLF={},TLS={on:{},mode:"v"};let TLQ=Promise.resolve();
function tlClass(v){
  const rp=v.kind==="drive"&&v.km<cfg("drives.repark_max_km")&&(v.t1-v.t0)/60<cfg("drives.repark_max_minutes");
  return v.kind==="drive"&&!rp?"drive":v.kind==="sentry"?"sentry":(v.kind==="parked"||v.kind==="stopped"||rp)?"parked":v.kind==="recording"?"rec":null}
// where a parked spell was: its own position, else where the drive before it ended (or the next one began)
function tlPos(items){
  const o=items.slice().sort((a,b)=>a.t0-b.t0);
  o.forEach((v,i)=>{let p=v.p0&&v.p0[0]!=null?v.p0:v.p1&&v.p1[0]!=null?v.p1:null;
    for(let k=i-1;!p&&k>=0&&i-k<4;k--)if(o[k].p1&&o[k].p1[0]!=null)p=o[k].p1;
    for(let k=i+1;!p&&k<o.length&&k-i<4;k++)if(o[k].p0&&o[k].p0[0]!=null)p=o[k].p0;
    v._pos=p})}
async function tlRoute(role,id){
  if(TLR[id]!==undefined)return TLR[id];
  try{const d=await tcGet("teslacam/"+role+"/seg/"+encodeURIComponent(id)+"?budget=0");TLR[id]=d&&d.series?{S:d.series,start:d.item.start,u0:d.item.t0,lo:d.item.lo}:null;if(d&&d.pending)tlFill(role,id,d.pending)}catch(e){TLR[id]=null}
  return TLR[id]}
// minutes of a drive not read yet leave holes in its line: read them in the background, one drive at a time, and tell the maps after each pass
function tlFill(role,id,left){
  if(TLF[id])return;TLF[id]=1;
  TLQ=TLQ.then(async()=>{for(let k=0;k<40&&left>0;k++){try{const d=await tcGet("teslacam/"+role+"/seg/"+encodeURIComponent(id)+"?budget=15");if(!d||!d.series)break;TLR[id]={S:d.series,start:d.item.start,u0:d.item.t0,lo:d.item.lo};dispatchEvent(new CustomEvent("tlroute",{detail:id}));if(!d.pending||d.pending>=left)break;left=d.pending}catch(e){break}}})}
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
    marks.forEach(k=>{if(!k.pos)return;const e=document.createElement("div");e.className="mk "+k.type;e.title=k.title;if(k.type==="parked")e.textContent="🅿️";e.onclick=()=>hooks.pick&&hooks.pick(k.id);
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
  const dd=(a,b)=>Math.round((Date.parse(b+"T00:00:00Z")-Date.parse(a+"T00:00:00Z"))/864e5),plus=(d,n)=>new Date(Date.parse(d+"T00:00:00Z")+n*864e5).toISOString().slice(0,10);
  // an event that crosses midnight is listed on every day it touches (whole, with a note), and is one and the same tick on all of them
  rs.forEach(v=>{v._d0=v.start.slice(0,10);v._d1=v._c==="sentry"||!v.end||v.end<v.start?v._d0:v.end.slice(0,10);for(let n=0;n<=Math.min(dd(v._d0,v._d1),6);n++){const d=plus(v._d0,n);(byDay[d]=byDay[d]||[]).push(v)}});
  if(TLS.dayDesc===undefined)TLS.dayDesc=cfg("teslacam.days_newest_first")!==false;if(TLS.rowDesc===undefined)TLS.rowDesc=cfg("teslacam.rows_newest_first")!==false;
  const allDays=Object.keys(byDay).filter(d=>!o.dayOk||o.dayOk(d)).sort().reverse(),cur=TLS.day&&allDays.includes(TLS.day)?TLS.day:allDays[0],days=cur?[cur]:[],role=o.role,places=o.places;TLS.day=cur;
  // one day at a time: a picker (newest day first by default) with arrows to the earlier / later day
  const asc=allDays.slice().reverse(),ci=asc.indexOf(cur),opts=(TLS.dayDesc?allDays:asc).map(d=>`<option value="${d}" ${d===cur?"selected":""}>${dayName(d)} (${byDay[d].length})</option>`).join("");
  const key=(v,d)=>d>v._d0?"00:00:00":hms(v._c==="sentry"?(v.trig||v.start):v.start),rowsOf=d=>byDay[d].slice().sort((a,b)=>{const x=key(a,d),y=key(b,d);return(x<y?-1:x>y?1:a.t0-b.t0)*(TLS.rowDesc?-1:1)});
  rs.forEach(v=>{if(TLS.on[v.id]===undefined)TLS.on[v.id]=v._c==="drive"});
  const place=v=>nameAt(places,v.p0)||nameAt(places,v.p1)||nameAt(places,v._pos);
  const tm=v=>clockHM(v.start)+" – "+clockHM(v.end),mins=v=>Math.max(1,Math.round((v.t1-v.t0)/60));
  // where things were: a saved place first; else what Tessie's own record of the drive says; else (Sentry) the city and street; a drive's ends are where the stopped spells next to it were
  const TS=(o.tess||[]).map(x=>({s:x.t0,e:x.t0+x.min*60,frm:x.frm||"",to:x.to||""})).sort((a,b)=>a.s-b.s),WIN=20*60;       // UTC seconds
  const tOver=v=>{const s0=v.t0,e0=v.t1;let b=null,bo=0;for(const t of TS){const ov=Math.min(e0,t.e)-Math.max(s0,t.s);if(ov>bo){bo=ov;b=t}}return b};
  const stayAt=v=>{const n=nameAt(places,v.p0)||nameAt(places,v.p1)||nameAt(places,v._pos);if(n)return n;
    const cs=[v.city,v.street].filter(Boolean).join(" · ");if(v._c==="sentry"&&cs)return cs;
    const s0=v.t0,e0=v.t1,a=TS.filter(t=>t.to&&Math.abs(t.e-s0)<=WIN).sort((x,y)=>Math.abs(x.e-s0)-Math.abs(y.e-s0))[0];if(a)return a.to;
    const z=TS.filter(t=>t.frm&&Math.abs(t.s-e0)<=WIN).sort((x,y)=>Math.abs(x.s-e0)-Math.abs(y.s-e0))[0];return z?z.frm:cs};
  const chron=rs.slice().sort((a,b)=>a.t0-b.t0),stays=chron.filter(v=>v._c==="parked"||v._c==="sentry");
  const around=(v,end)=>{const t=end?v.t1:v.t0;let best=null,bd=WIN;for(const k of stays){const d=end?Math.abs(k.t0-t):Math.abs(k.t1-t);if(d<bd){const n=stayAt(k);if(n){bd=d;best=n}}}return best||""};
  const ends=v=>{const t=tOver(v);return[nameAt(places,v.p0)||(t&&t.frm)||around(v,false)||"Unknown",nameAt(places,v.p1)||(t&&t.to)||around(v,true)||"Unknown"]};
  const ico=c=>c==="drive"?"🚗":c==="parked"?"🅿️":c==="sentry"?'<i class="hal" title="Sentry"></i>':"🎬";
  const row=(v,d)=>{const c=v._c,id=esc(v.id),canTick=c==="drive"||!!v._pos,chk=c==="rec"?"<span></span>":`<label class="ck"><input type="checkbox" data-id="${id}" ${TLS.on[v.id]?"checked":""} ${canTick?"":"disabled"} title="${canTick?"Show on the map":"No position known"}"></label>`;
    const when=c==="sentry"?hms(v.trig||v.start).slice(0,5):tm(v),e2=c==="drive"?ends(v):null;
    const where=c==="drive"?`${esc(e2[0])} <span class="ar">→</span> ${esc(e2[1])}`:c==="rec"?`Recording · ${v.n} clip${v.n>1?"s":""} <span class="sub">telemetry not read yet</span>`:esc(stayAt(v)||"Unknown location");
    const go=c==="drive"?`#/teslacam/${role}/${encodeURIComponent(v.id)}`:c==="sentry"?`#/teslacam/${role}/sentry-${encodeURIComponent(v.ev)}`:c==="rec"?`#/teslacam/${role}/${encodeURIComponent(v.id)}`:"";
    return `<div class="tr ${c}" data-id="${id}">${chk}<span class="tm">${when}</span><span class="ti">${ico(c)}</span><span class="tw">${where}</span><span class="td">${dur(mins(v))}</span><span class="to">${go?`<button class="btn sm" data-go="${go}">${c==="rec"?"Read":"Open"} ▸</button>`:""}</span></div>`};
  host.innerHTML=days.length?`<div class="bar" style="margin:0 0 4px"><button class="chip ${TLS.mode==="v"?"on":""}" data-tm="v">Speed</button><button class="chip ${TLS.mode==="ap"?"on":""}" data-tm="ap">Autopilot / FSD</button><button class="chip" data-so="days" style="margin-left:10px">${TLS.dayDesc?"Days: newest first":"Days: oldest first"}</button><button class="chip" data-so="rows">${TLS.rowDesc?"Within a day: 23:59 → 00:00":"Within a day: 00:00 → 23:59"}</button><span class="sub" id="tlleg"></span><span class="sub" style="margin-left:auto;cursor:help" title="Tick a row to show it on the map. Click a drive to open it, a Sentry event to watch it. Hover a route for the moment under the pointer; click it to open the drive. Short drives under the re-park limits count as parked. An event that runs past midnight is shown whole on both days, and ticking it ticks it on both. Click a row to find it on the map; Open shows a drive or Sentry event in full.">ⓘ how this works</span></div>`
    +days.map(d=>`<div class="dpick"><button class="chip" data-nav="${ci>0?asc[ci-1]:""}" ${ci>0?"":"disabled"} title="Earlier day">‹</button><select id="dsel">${opts}</select><button class="chip" data-nav="${ci<asc.length-1?asc[ci+1]:""}" ${ci<asc.length-1?"":"disabled"} title="Later day">›</button><small>${byDay[d].length} item${byDay[d].length>1?"s":""}</small><label class="dall"><input type="checkbox" data-day="${d}"> show all</label></div><div class="tday" data-day="${d}"><div class="tl2"><div class="tr trh"><span></span><span>Time</span><span></span><span>Where</span><span class="td">Duration</span><span></span></div>${rowsOf(d).map(v=>row(v,d)).join("")}</div><div class="tmap"></div></div>`).join("")
    :'<div class="empty">Nothing here for these dates.</div>';
  if(!days.length)return;
  const leg=()=>{$("#tlleg").innerHTML=TLS.mode==="v"?spdLegend():apLegend()};leg();
  const maps={},order=[];
  const dayItems=d=>byDay[d];
  const populate=async d=>{const mp=maps[d];if(!mp)return;const tok=mp.tok=(mp.tok||0)+1,on=dayItems(d).filter(v=>TLS.on[v.id]&&(v._c==="drive"||v._pos));
    const rt=(await Promise.all(on.filter(v=>v._c==="drive").map(async v=>{const r=await tlRoute(role,v.id);return r?{id:v.id,S:r.S,start:r.start,u0:r.u0,lo:r.lo}:null}))).filter(Boolean);
    if(mp.tok!==tok||!maps[d])return;
    mp.m.set(rt,on.filter(v=>v._c!=="drive").map(v=>({id:v.id,type:v._c,pos:v._pos,title:v._c==="sentry"?"Sentry · "+reason(v.reason)+" · "+hms(v.trig||v.start):"Parked "+tm(v)})))};
  const build=d=>{if(maps[d])return;if(!window.maplibregl)return;if(order.length>=6){const old=order.shift();if(maps[old]){maps[old].m.destroy();delete maps[old];const e=$(`.tday[data-day="${old}"] .tmap`,host);if(e)e.innerHTML=""}}
    const el=$(`.tday[data-day="${d}"] .tmap`,host);maps[d]={m:dayMap(el,role,{open:id=>{location.hash="#/teslacam/"+role+"/"+encodeURIComponent(id)},pick:id=>{const c=$(`input[data-id="${CSS.escape(id)}"]`,host);if(c)c.scrollIntoView({block:"center"})}})};order.push(d);populate(d)};
  const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting)build(e.target.parentNode.dataset.day)}),{rootMargin:"300px"});
  $$(".tmap",host).forEach(e=>io.observe(e));
  const setOn=(ids,val)=>{const ds=new Set();ids.forEach(id=>{TLS.on[id]=val;$$(`input[data-id="${CSS.escape(id)}"]`,host).forEach(x=>{if(!x.disabled)x.checked=val;ds.add(x.closest(".tday").dataset.day)})});ds.forEach(populate);syncAll()};
  $$("input[data-id]",host).forEach(c=>c.onchange=()=>setOn([c.dataset.id],c.checked));
  const onRoute=e=>{if(!TLS.on[e.detail])return;new Set($$(`input[data-id="${CSS.escape(e.detail)}"]`,host).map(x=>x.closest(".tday").dataset.day)).forEach(populate)};addEventListener("tlroute",onRoute);
  $("#dsel",host).onchange=e=>{TLS.day=e.target.value;o.redraw&&o.redraw()};
  $$("[data-nav]",host).forEach(b=>b.onclick=()=>{if(b.dataset.nav){TLS.day=b.dataset.nav;o.redraw&&o.redraw()}});
  // the day's list and map are as tall as what is left of the window below them
  const fitH=()=>{const e=$(".tday",host);if(!e)return;e.style.setProperty("--tdh",Math.max(380,innerHeight-(e.getBoundingClientRect().top+scrollY)-16)+"px")};fitH();addEventListener("resize",fitH);
  $$("[data-so]",host).forEach(b=>b.onclick=()=>{if(b.dataset.so==="days")TLS.dayDesc=!TLS.dayDesc;else TLS.rowDesc=!TLS.rowDesc;o.redraw&&o.redraw()});
  const syncAll=()=>$$("input[data-day]",host).forEach(a=>{const bx=$$(`.tday[data-day="${a.dataset.day}"] input[data-id]:not(:disabled)`,host);a.checked=bx.length>0&&bx.every(x=>x.checked)});syncAll();
  $$("input[data-day]",host).forEach(a=>a.onchange=()=>setOn($$(`.tday[data-day="${a.dataset.day}"] input[data-id]:not(:disabled)`,host).map(x=>x.dataset.id),a.checked));
  $$("[data-tm]",host).forEach(b=>b.onclick=()=>{TLS.mode=b.dataset.tm;$$("[data-tm]",host).forEach(x=>x.classList.toggle("on",x===b));leg();Object.values(maps).forEach(x=>x.m.mode(TLS.mode))});
  $$("[data-go]",host).forEach(b=>b.onclick=e=>{e.stopPropagation();location.hash=b.dataset.go});
  // clicking a row ticks it and brings it up on the map
  $$(".tr[data-id]",host).forEach(r=>r.onclick=async e=>{if(e.target.closest("input,button,a,label"))return;const v=rs.find(x=>x.id===r.dataset.id);if(!v||!(v._c==="drive"||v._pos))return;
    $$(".tr.sel",host).forEach(x=>x.classList.remove("sel"));r.classList.add("sel");const c=$("input[data-id]",r);if(c&&!c.checked){c.checked=true;TLS.on[v.id]=true;syncAll()}
    await populate(cur);const mp=maps[cur];if(mp)mp.m.focus(v.id)});
  return()=>{removeEventListener("tlroute",onRoute);removeEventListener("resize",fitH);io.disconnect();Object.values(maps).forEach(x=>x.m.destroy())}}

function routeSvg(pts,w=64,h=40){if(!pts||pts.length<2)return "";const la=pts.map(p=>p[0]),lo=pts.map(p=>p[1]),a=Math.min(...la),b=Math.max(...la),c=Math.min(...lo),d=Math.max(...lo),k=Math.cos((a+b)/2*Math.PI/180),sx=(d-c)*k||1e-6,sy=(b-a)||1e-6,s=Math.min((w-6)/sx,(h-6)/sy),ox=(w-sx*s)/2,oy=(h-sy*s)/2;
  return `<svg class="rt" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round" points="${pts.map(p=>((p[1]-c)*k*s+ox).toFixed(1)+","+(h-((p[0]-a)*s+oy)).toFixed(1)).join(" ")}"/></svg>`}
// ---------- the drive scan: footage on the drives you pick against the telemetry already kept; reads what is missing (lib/teslacam_scan.py, ./teslacam_scan.py)
let SCN={roles:{A:1},rep:null,timer:null,err:""};
function scanPanel(box,drives){
  const picked=()=>drives.filter(d=>SCN.roles[d.role]).map(d=>d.role),alive=()=>document.body.contains(box);
  const get=async()=>{SCN.err="";try{SCN.rep=await tcGet("scan?roles="+picked().join(","))}catch(e){SCN.err=e.message}};
  const paint=()=>{if(!alive())return;const rep=SCN.rep,job=rep?rep.job:null,run=!!(job&&job.running),left=rep?rep.drives.reduce((a,d)=>a+d.to_read,0):0;
    let h=`<div class="sub" style="margin:8px 0">Compares the footage on the drives you pick with the telemetry already kept (speed, GPS, steering, pedals, autopilot) and reads what is missing. The kept telemetry stays when the video is deleted, so those drives can still be drawn from it. A drive is only read when you pick it.</div>
      <div class="bar" style="margin:0 0 8px">${drives.map(d=>`<label class="chip ${SCN.roles[d.role]?"on":""}" style="cursor:pointer"><input type="checkbox" data-sr="${d.role}" ${SCN.roles[d.role]?"checked":""} ${run?"disabled":""} style="margin-right:6px">${esc(d.name)}</label>`).join("")}<button class="btn ${rep?"":"pri"}" id="scg" ${run||!picked().length?"disabled":""}>${rep?"Scan again":"Scan"}</button></div>`;
    if(SCN.err)h+=`<div class="sub" style="color:var(--bad);user-select:text">${esc(SCN.err)}</div>`;
    if(rep){
      h+=`<table><thead><tr><th class="l">Drive</th><th>Minutes of video</th><th>Telemetry read</th><th>Still to read</th><th>Also on another picked drive</th><th class="l">Dates</th></tr></thead><tbody>${rep.drives.map(d=>`<tr><td class="l"><b>${esc(d.name)}</b>${d.no_front?`<div class="sub">${n0(d.no_front)} without a front clip: cannot be read</div>`:""}</td><td>${n0(d.minutes)}<div class="sub">${n0(d.recent)} Recent · ${n0(d.saved)} Saved</div></td><td>${n0(d.read)}</td><td>${d.to_read?`<b>${n0(d.to_read)}</b>`:"0"}</td><td>${n0(d.elsewhere)}</td><td class="l">${d.first?(d.first===d.last?dayName(d.first):dayName(d.first)+" to "+dayName(d.last)):""}</td></tr>`).join("")}</tbody></table>
        <div class="sub" style="margin-top:8px">Telemetry kept: ${n0(rep.kept)} minutes${rep.kept_orphans?`, ${n0(rep.kept_orphans)} of them with no video on the picked drives (kept anyway)`:""}.</div>`;
      if(run)h+=`<div class="card ixb" style="margin-top:8px"><div>Reading ${n0(job.done)} of ${n0(job.total)}${job.current?", "+esc(job.current.replace("_"," ")):""}…</div><button class="btn" id="scx">Stop</button></div>`;
      else if(left)h+=`<div class="card ixb" style="margin-top:8px"><div><b>${n0(left)}</b> minutes of video have no telemetry yet.${job&&job.error?` <span style="color:var(--bad);user-select:text">${esc(job.error)}</span>`:""}</div><button class="btn pri" id="scr">Read ${n0(left)} minutes</button></div>`;
      else h+=`<div class="sub" style="margin-top:8px">Every minute with a front clip on the picked drives has its telemetry.${job&&job.done?` Read ${n0(job.done)} just now.`:""}</div>`}
    box.innerHTML=h;
    $$("[data-sr]",box).forEach(c=>c.onchange=()=>{SCN.roles[c.dataset.sr]=c.checked?1:0;SCN.rep=null;paint()});
    const g=$("#scg",box),r=$("#scr",box),x=$("#scx",box);
    if(g)g.onclick=async()=>{g.disabled=true;g.textContent="Scanning…";await get();paint()};
    if(r)r.onclick=async()=>{await tcPost("scan",{action:"start",roles:picked()});await get();paint();poll()};
    if(x)x.onclick=async()=>{await tcPost("scan",{action:"stop"});await get();paint()}};
  const poll=()=>{clearTimeout(SCN.timer);if(!alive())return;SCN.timer=setTimeout(async()=>{try{const j=await tcGet("scan/status");if(SCN.rep)SCN.rep.job=j;
      if(j.running){const l=$("#scn .ixb div");if(l)l.textContent="Reading "+n0(j.done)+" of "+n0(j.total)+(j.current?", "+j.current.replace("_"," "):"")+"…";poll()}else{await get();paint()}}catch(e){}},2000)};
  paint();if(SCN.rep&&SCN.rep.job&&SCN.rep.job.running)poll();
}
let FGS={roles:{A:1},log:null,st:null,timer:null,err:""};
function footagePanel(box,drives){
  const picked=()=>drives.filter(d=>FGS.roles[d.role]).map(d=>d.role),alive=()=>document.body.contains(box),NM=r=>(drives.find(d=>d.role===r)||{name:({A:"Archive",J:"JOWUA",U:"Tesla USB"})[r]||r}).name;
  const gb=b=>b>=1073741824?(b/1073741824).toFixed(1)+" GB":Math.round(b/1048576)+" MB";
  const loadLog=async()=>{FGS.err="";try{FGS.log=await tcGet("footage/log")}catch(e){FGS.err=e.message}};
  const loadSt=async()=>{FGS.err="";try{FGS.st=await tcGet("footage/stills?roles="+picked().join(","))}catch(e){FGS.err=e.message}};
  const paint=()=>{if(!alive())return;const lg=FGS.log,st=FGS.st,job=(lg&&lg.job)||(st&&st.job)||null,run=!!(job&&job.running);
    let h=`<div class="sub" style="margin:8px 0">A log of every TeslaCam video file (which drive has it, how big, when it was deleted), and a still for every second of the front camera, kept after the video is gone. Reviews show those stills one by one. A drive is only read when you pick it.</div>
      <div class="bar" style="margin:0 0 8px">${drives.map(d=>`<label class="chip ${FGS.roles[d.role]?"on":""}" style="cursor:pointer"><input type="checkbox" data-fr="${d.role}" ${FGS.roles[d.role]?"checked":""} ${run?"disabled":""} style="margin-right:6px">${esc(d.name)}</label>`).join("")}
      <button class="btn pri" id="fgl1" ${run||!picked().length?"disabled":""}>Update the log</button><button class="btn" id="fgs1" ${run||!picked().length?"disabled":""}>Check the stills</button></div>`;
    if(FGS.err)h+=`<div class="sub" style="color:var(--bad);user-select:text">${esc(FGS.err)}</div>`;
    if(run)h+=`<div class="card ixb" style="margin-top:8px"><div>${job.kind==="stills"?`Making stills: ${n0(job.done)} of ${n0(job.total)} minutes${job.current?", "+esc(job.current.replace("_"," ")):""}…`:`Listing ${esc(NM(job.current)||"")}… ${n0(job.done)} of ${n0(job.total)} drives`}</div><button class="btn" id="fgx">Stop</button></div>`;
    else if(job&&job.error)h+=`<div class="sub" style="color:var(--bad);user-select:text">${esc(job.error)}</div>`;
    else if(job&&job.note)h+=`<div class="sub" style="user-select:text">${esc(job.note)}</div>`;
    if(lg&&lg.scans&&Object.keys(lg.scans).length){const t=lg.total;
      h+=`<h3 style="margin:12px 0 4px">The log</h3><table><thead><tr><th class="l">Drive</th><th>Files listed</th><th>Size</th><th>Gone</th><th class="l">Last listed (UTC)</th></tr></thead><tbody>${Object.keys(lg.drives).sort().map(r=>{const d=lg.drives[r],s=lg.scans[r]||{};return `<tr><td class="l"><b>${esc(NM(r))}</b></td><td>${n0(d.listed)}</td><td>${gb(d.listed_bytes)}</td><td>${n0(d.gone)}</td><td class="l">${esc((s.at||"never").replace("T"," ").replace("Z",""))}</td></tr>`}).join("")}</tbody></table>
        <div class="sub" style="margin-top:6px">${n0(t.files)} files ever logged (${gb(t.bytes)}). <b>${n0(t.deleted)}</b> deleted from every drive (${gb(t.deleted_bytes)}), their stills and telemetry kept. <b>${n0(t.only_secondary)}</b> only on a drive other than the Archive (${gb(t.only_secondary_bytes)}), not imported yet. <b>${n0(t.purgeable)}</b> copies also on the Archive can be purged (${gb(t.purgeable_bytes)}); nothing is deleted from here.</div>`}
    else if(lg)h+=`<div class="sub" style="margin-top:8px">The log is empty. Update it from a drive.</div>`;
    if(st){const left=st.to_make||0;
      h+=`<h3 style="margin:12px 0 4px">The stills</h3><table><thead><tr><th class="l">Drive</th><th>Front clips</th><th>Stills kept</th><th>To make</th><th>Also on another picked drive</th><th class="l">Dates</th></tr></thead><tbody>${st.drives.map(d=>`<tr><td class="l"><b>${esc(NM(d.role))}</b></td><td>${n0(d.clips)}</td><td>${n0(d.done)}</td><td>${d.to_make?`<b>${n0(d.to_make)}</b>`:"0"}</td><td>${n0(d.elsewhere)}</td><td class="l">${d.first?(d.first===d.last?dayName(d.first):dayName(d.first)+" to "+dayName(d.last)):""}</td></tr>`).join("")}</tbody></table>
        <div class="sub" style="margin-top:6px">Stills kept: ${n0(st.kept)} minutes, including video that is gone.</div>`;
      if(!run&&left)h+=`<div class="card ixb" style="margin-top:8px"><div><b>${n0(left)}</b> minutes of front video have no stills yet (about ${gb(left*750000)}).${st.ffmpeg?"":` <span style="color:var(--bad)">ffmpeg is not installed.</span>`}</div><button class="btn pri" id="fgm" ${st.ffmpeg?"":"disabled"}>Make ${n0(left)} minutes of stills</button></div>`;
      else if(!run)h+=`<div class="sub" style="margin-top:8px">Every front clip on the picked drives has its stills.</div>`}
    box.innerHTML=h;
    $$("[data-fr]",box).forEach(c=>c.onchange=()=>{FGS.roles[c.dataset.fr]=c.checked?1:0;FGS.st=null;paint()});
    const b1=$("#fgl1",box),b2=$("#fgs1",box),b3=$("#fgm",box),bx=$("#fgx",box);
    if(b1)b1.onclick=async()=>{await tcPost("footage",{action:"log",roles:picked()});await loadLog();paint();poll()};
    if(b2)b2.onclick=async()=>{b2.disabled=true;b2.textContent="Checking…";await loadSt();paint()};
    if(b3)b3.onclick=async()=>{await tcPost("footage",{action:"stills",roles:picked()});await loadLog();paint();poll()};
    if(bx)bx.onclick=async()=>{await tcPost("footage",{action:"stop"});await loadLog();paint()}};
  const poll=()=>{clearTimeout(FGS.timer);if(!alive())return;FGS.timer=setTimeout(async()=>{try{const j=await tcGet("footage/status");
      if(j.running){const l=$("#fgl .ixb div");if(l)l.textContent=j.kind==="stills"?"Making stills: "+n0(j.done)+" of "+n0(j.total)+" minutes"+(j.current?", "+j.current.replace("_"," "):"")+"…":"Listing drives… "+n0(j.done)+" of "+n0(j.total);poll()}
      else{await loadLog();if(FGS.st)await loadSt();paint()}}catch(e){}},2000)};
  loadLog().then(()=>{paint();if(FGS.log&&FGS.log.job&&FGS.log.job.running)poll()});
}
async function pageTeslaCam(role,id){
  if(role&&id)return /^sentry-/.test(id)?pageTeslaCamSentry(role,id.slice(7)):pageTeslaCamDrive(role,id);
  loading();const [dr,act,pl]=await Promise.all([tcGet("teslacam"),api("activity"),api("places").catch(()=>({saved:[]}))]),drives=dr.drives,places=(pl.saved||[]);
  const head='<h1>TeslaCam</h1><div class="sub">The clips on any connected TeslaCam drive. A drive is recognised by what is on it, not by its name, and nothing on it is changed. A drive is only read once you pick it.</div>';
  if(!drives.length){main.innerHTML=head+'<div class="empty">No TeslaCam drive is connected.</div>';return}
  if(!drives.some(d=>d.role===TCS.role))TCS.role=(drives.find(d=>d.role==="A")||drives[0]).role;
  const trips=act.filter(x=>x.k==="drive"&&!x.repark).map(x=>({id:x.id,t0:x.t0,t1:x.t0+x.min*60,ti:driveTitle(x)}));
  const tripOf=v=>{const a=v.t0!=null?v.t0:v.t,b=v.t1!=null?v.t1:(v.t||0)+60;return trips.find(d=>d.t0<b&&d.t1>a)};
  main.innerHTML=head+`<details class="card" id="scn" style="margin-bottom:12px"><summary><b>Scan the connected drives for telemetry</b></summary><div id="scb"></div></details><details class="card" id="fgl" style="margin-bottom:12px"><summary><b>Footage log and one-second stills</b></summary><div id="fgb"></div></details><div class="bar" id="tcd"></div><div class="sub" id="tcn"></div><div class="bar" id="tck"></div><div class="bar" id="tsr"></div><div class="bar"><span id="rg"></span><input type="search" id="q" placeholder="Search…" value="${esc(TCS.q)}"></div><div id="ixb"></div><div class="tiles" id="tl"></div><div id="spl"></div><div id="tb"></div>`;
  let data=null,sel=null,token=0,sentryList=null;
  const chips=()=>{$("#tcd").innerHTML=drives.map(d=>`<button class="chip ${d.role===TCS.role?"on":""}" data-r="${d.role}"><span class="dot on" style="margin-right:6px"></span>${esc(d.name)}</button>`).join("");
    $("#tck").innerHTML=Object.keys(TCK).map(k=>`<button class="chip ${k===TCS.kind?"on":""}" data-k="${k}">${TCK[k]}</button>`).join("");
    $("#tsr").innerHTML=TCS.kind==="clips"?'<span class="sub" style="margin:0 4px 0 0">Show:</span>'+Object.keys(TCSRC).map(k=>`<button class="chip ${TCS.src[k]?"on":""}" data-s="${k}">${TCSRC[k]}${data&&data[k]?" · "+data[k].length:""}</button>`).join("")+'<span class="sub" style="margin-left:6px">Pick one, two or all three: they are listed together, newest first.</span>':"";
    $$("#tsr [data-s]").forEach(b=>b.onclick=()=>{const on=Object.keys(TCSRC).filter(k=>TCS.src[k]);if(TCS.src[b.dataset.s]&&on.length<2)return;TCS.src[b.dataset.s]=TCS.src[b.dataset.s]?0:1;$("#spl").innerHTML="";chips();draw()});
    $("#tcn").textContent=TCS.role==="A"?"":"Not the Archive: its clips are read only as you open them. Stop playback and leave this page before ejecting it.";
    $$("#tcd [data-r]").forEach(b=>b.onclick=()=>{TCS.role=b.dataset.r;$("#spl").innerHTML="";sentryList=null;chips();load()});
    $$("#tck [data-k]").forEach(b=>b.onclick=()=>{TCS.kind=b.dataset.k;$("#spl").innerHTML="";chips();load()})};
  const load=async(quiet)=>{const my=++token;if(!quiet){data=null;$("#tl").innerHTML="";$("#ixb").innerHTML="";$("#tb").innerHTML='<div class="empty">Reading the drive…</div>'}
    try{if(TCS.kind==="clips"){const r=await Promise.all(["sentry","saved","recent"].map(k=>tcGet("teslacam/"+TCS.role+"/"+k)));if(my!==token)return;
        data={connected:r.every(x=>x.connected),sentry:r[0].events||[],saved:r[1].events||[],recent:r[2].runs||[]}}
      else{const d=await tcGet("teslacam/"+TCS.role+"/"+TCS.kind);if(my!==token)return;data=d}}catch(e){$("#tb").innerHTML=errBox(e.message,"Could not read the drive");return}
    const list=TCS.kind==="clips"?[].concat(data.sentry,data.saved,data.recent):data.items;if(!quiet)rangePicker($("#rg"),TCS.r,daysOf((list||[]).map(v=>({day:(v.ts||v.start).slice(0,10)}))),draw);chips();draw()};
  const ixBar=()=>{if(TCS.kind!=="timeline"||!data||!data.connected){$("#ixb").innerHTML="";return}
    const ix=data.index||{},run=ix.running,left=data.minutes-data.read;
    $("#ixb").innerHTML=`<div class="card ixb"><div><b>${n0(data.read)}</b> of ${n0(data.minutes)} minutes of Recent and Saved video read for telemetry (speed, GPS, steering, pedals, autopilot).${run?` Reading now: ${n0(ix.done)} of ${n0(ix.total)}, ${esc((ix.current||"").replace("_"," ").replace(/-/g,(m,i)=>i>12?":":"-"))}.`:left>0?` Each clip is read once, about a second each; opening a drive reads just that drive.`:" All read."}${ix.error?errBox(ix.error,"Reading stopped"):""}</div>
      ${run?'<button class="btn" id="ixs">Stop</button>':left>0?'<button class="btn pri" id="ixg">Read all of it</button>':""}</div>`;
    if($("#ixg"))$("#ixg").onclick=async()=>{await tcPost("teslacam/index",{role:TCS.role,action:"start"});poll()};
    if($("#ixs"))$("#ixs").onclick=async()=>{await tcPost("teslacam/index",{role:TCS.role,action:"stop"})}};
  const poll=()=>{if(TCPOLL)clearInterval(TCPOLL);TCPOLL=setInterval(async()=>{if(!$("#ixb")){clearInterval(TCPOLL);TCPOLL=null;return}
    try{const ix=await tcGet("teslacam/"+TCS.role+"/index");if(data)data.index=ix;if(TCS.kind==="timeline")ixBar();if(!ix.running){clearInterval(TCPOLL);TCPOLL=null;load(true)}else if(ix.done%40===0&&TCS.kind==="timeline")load(true)}catch(e){}},3000)};
  const itemRow=v=>{const t0=clockHM(v.start),t1=clockHM(v.end),m=Math.max(1,Math.round((v.t1-v.t0)/60));
    if(v.kind==="sentry")return `<button class="trow sen" data-id="${esc(v.ev)}"><span class="tm">${hms(v.trig||v.start)}</span><span class="tk">Sentry</span><span class="tt"><b>${esc(reason(v.reason))}</b> · ${esc([v.city,v.street].filter(Boolean).join(" · "))}</span><span class="ts">${m} min parked, watched</span></button>`;
    if(v.kind==="parked"||v.kind==="gap"){const w=nameAt(places,v.p1)||nameAt(places,v.p0);return `<div class="trow gap"><span class="tm">${t0}</span><span class="tk">${v.kind==="parked"?"Parked":"No footage"}</span><span class="tt">${v.kind==="parked"?esc(w||"same place before and after"):(v.km!=null?"the car moved "+n0(v.km,1)+" km":"nothing recorded")}</span><span class="ts">${dur(m)}</span></div>`}
    const a=nameAt(places,v.p0),b=nameAt(places,v.p1),ti=a||b?`${esc(a||"?")} → ${esc(b||"?")}`:"",src=(v.recent?"Recent":"")+(v.recent&&v.saved?" + ":"")+(v.saved?"Saved":"");
    if(v.kind==="drive")return `<a class="trow drv" href="#/teslacam/${TCS.role}/${esc(v.id)}"><span class="tm">${t0}</span><span class="tk">Drive</span><span class="tt">${routeSvg(v.thumb)}<span><b>${n0(v.km,1)} km</b> · max ${n0(v.vmax)} km/h${v.fsd?` · FSD ${Math.round(v.fsd*100)}%`:""}${v.aut?` · Autosteer/TACC ${Math.round(v.aut*100)}%`:""}<small>${ti}</small></span></span><span class="ts">${dur(m)} · ${src}</span></a>`;
    return `<a class="trow ${v.kind==="stopped"?"stp":"rec"}" href="#/teslacam/${TCS.role}/${esc(v.id)}"><span class="tm">${t0}</span><span class="tk">${v.kind==="stopped"?"Stopped":"Recording"}</span><span class="tt">${v.kind==="stopped"?"recording, but not moving":"telemetry not read yet"}<small>${v.n} clip${v.n>1?"s":""}, ${src}</small></span><span class="ts">${dur(m)}</span></a>`};
  const drawTimeline=()=>{if(TLOFF){TLOFF();TLOFF=null}const q=TCS.q.toLowerCase(),rs=data.items.filter(v=>(inRange({day:v.start.slice(0,10)},TCS.r)||inRange({day:(v.end||v.start).slice(0,10)},TCS.r))&&(!q||(v.kind+" "+(v.city||"")+" "+(v.street||"")+" "+reason(v.reason)+" "+nameAt(places,v.p0)+" "+nameAt(places,v.p1)).toLowerCase().includes(q)));
    rs.forEach(v=>v._c=tlClass(v));const dr=rs.filter(v=>v._c==="drive");
    $("#tl").innerHTML=tile(n0(dr.length),"drives with telemetry")+tile(n0(dr.reduce((a,v)=>a+v.km,0))+" km","driven (from the video)")+tile(n0(rs.filter(v=>v._c==="sentry").length),"Sentry events")+tile(n0(rs.filter(v=>v._c==="parked").length),"parked spells")+tile(n0(rs.filter(v=>v._c==="rec").length),"recordings not read yet");
    $("#tb").innerHTML='<div id="tlh"></div>';
    TLOFF=tlDays($("#tlh"),rs,{role:TCS.role,places,tess:act.filter(x=>x.k==="drive"),dayOk:d=>inRange({day:d},TCS.r),redraw:drawTimeline,onSentry:async ev=>{$("#spl").scrollIntoView({block:"nearest"});
      try{if(!sentryList)sentryList=(await tcGet("teslacam/"+TCS.role+"/sentry")).events;sentryPlayer($("#spl"),sentryList.find(v=>v.id===ev),TCS.role,"SentryClips")}catch(e){$("#spl").innerHTML=errBox(e.message,"Could not read these clips")}}})||null};
  const draw=()=>{if(!data)return;if(TCS.kind==="clips"?!data.sentry:!data.items&&data.connected!==false){return}if(!data.connected){$("#tb").innerHTML='<div class="empty">This drive is not connected.</div>';$("#ixb").innerHTML="";return}
    if(TCS.kind==="timeline"){ixBar();return drawTimeline()}$("#ixb").innerHTML="";
    const q=TCS.q.toLowerCase(),role=TCS.role,srcs=Object.keys(TCSRC).filter(k=>TCS.src[k]);
    const all=[].concat(...srcs.map(k=>data[k].map(v=>Object.assign({ty:k},v)))).filter(v=>inRange({day:v.ts.slice(0,10)},TCS.r)&&(!q||(v.ty==="recent"?((tripOf(v)||{}).ti||""):(v.city+" "+v.street+" "+reason(v.reason))).toLowerCase().includes(q)||TCSRC[v.ty].toLowerCase().includes(q))).sort((a,b)=>a.ts<b.ts?1:-1),byDay={};
    all.forEach(v=>(byDay[v.ts.slice(0,10)]=byDay[v.ts.slice(0,10)]||[]).push(v));
    const days=Object.keys(byDay).sort().reverse(),cnt=k=>all.filter(v=>v.ty===k).length,mins=all.reduce((a,v)=>a+(v.ty==="recent"?v.n:Object.keys(v.clips).length),0);
    $("#tl").innerHTML=srcs.map(k=>tile(n0(cnt(k)),k==="recent"?"continuous recordings":TCSRC[k]+" events")).join("")+tile(n0(mins),"minutes of video");
    const card=v=>{const bd=`<span class="srcb ${v.ty}">${TCSRC[v.ty]}</span>`;
      if(v.ty==="recent"){const t=tripOf(v);return `<button class="evc run" data-ty="recent" data-id="${esc(v.id)}">${vthumb("/footage/"+role+"/RecentClips/"+v.id+"-front.mp4")}${bd}<b>${hms(v.ts).slice(0,5)} – ${hms(v.end).slice(0,5)}</b><span>${v.n} minute${v.n>1?"s":""}${v.full<v.n?" · "+(v.n-v.full)+" with fewer than 6 cameras":""}</span>${t?`<span>${esc(t.ti)}</span><a href="#/drive/${t.id}" onclick="event.stopPropagation()">open the drive</a>`:""}<a href="#/teslacam/${role}/${esc(v.id)}" onclick="event.stopPropagation()">map and telemetry</a></button>`}
      const n=Object.keys(v.clips).length,folder=TCF[v.ty],k0=Object.keys(v.clips)[0];
      return `<button class="evc ${n?"":"faded"}" data-ty="${v.ty}" data-id="${v.id}">${v.thumb?`<img loading="lazy" src="/footage/${role}/${folder}/${v.id}/thumb.png" onerror="this.style.visibility='hidden'">`:k0&&v.clips[k0]&&v.clips[k0].includes("front")?vthumb(`/footage/${role}/${folder}/${v.id}/${k0}-front.mp4`):'<div class="noimg"></div>'}${bd}<b>${hms(v.ts)}</b><span>${esc(reason(v.reason))}</span><span>${esc([v.city,v.street].filter(Boolean).join(" · "))}</span><span>${n?n+" min of video":"no video"}</span></button>`};
    $("#tb").innerHTML=(days.length?"":'<div class="empty">Nothing here for these dates.</div>')+days.slice(0,60).map(d=>`<div class="day">${dayName(d)}<small>${byDay[d].length} clip${byDay[d].length>1?"s":""}</small></div><div class="evs">${byDay[d].map(card).join("")}</div>`).join("")+(days.length>60?'<div class="sub">Showing the newest 60 days. Narrow the dates to see older ones.</div>':"");
    lazyThumbs($("#tb"));$$("#tb .evc").forEach(b=>b.onclick=async()=>{const ty=b.dataset.ty,sel=b.dataset.id;$$("#tb .evc").forEach(x=>x.classList.toggle("on",x===b));$("#spl").scrollIntoView({block:"nearest"});
      if(ty!=="recent")return sentryPlayer($("#spl"),data[ty].find(v=>v.id===sel),role,TCF[ty]);
      const run=data.recent.find(v=>v.id===sel);$("#spl").innerHTML='<div class="sub">Reading…</div>';
      try{const rr=await tcGet("teslacam/"+role+"/recent/"+encodeURIComponent(sel)),c=rr.clips,stamps=Object.keys(c);
        clipPlayer($("#spl"),{stamps,cams:c,loc:rr.loc,url:(s,k)=>`/footage/${role}/RecentClips/${s}-${k}.mp4`,head:`Continuous recording on ${dayName(run.ts.slice(0,10))}, ${hms(run.ts).slice(0,5)} to ${hms(run.end).slice(0,5)}, ${run.n} minute${run.n>1?"s":""}. The next minute plays on its own.`})}
      catch(e){$("#spl").innerHTML=errBox(e.message,"Could not read these clips")}})};
  $("#q").oninput=e=>{TCS.q=e.target.value;draw()};chips();load();$("#scn").ontoggle=()=>{if($("#scn").open&&!$("#scb").innerHTML)scanPanel($("#scb"),drives)};$("#fgl").ontoggle=()=>{if($("#fgl").open&&!$("#fgb").innerHTML)footagePanel($("#fgb"),drives)};
}

// ---------- one drive rebuilt from the video: map, telemetry and the stitched footage, moving together
// the colours every route map uses (settings app.map.speed_colors / autopilot_colors): speed as a gradient, and who was driving
const APN={1:"FSD",2:"Autosteer",3:"TACC"},APD={FSD:"#3b6cf6",Autosteer:"#8b5cf6",TACC:"#14b8a6",Manual:"#94a3b8"};
const apCol=k=>(cfg("map.autopilot_colors")||{})[k]||APD[k],APC=new Proxy({},{get:(_,a)=>apCol(APN[a]||"Manual")});
const spdStops=()=>cfg("map.speed_colors")||[[0,"#0000FF"],[60,"#FFFF00"],[120,"#800080"]],spdTop=()=>spdStops()[spdStops().length-1][0];
const spdOver=()=>cfg("map.speed_over_color")||"#000000";
const spdExpr=()=>["interpolate",["linear"],["get","v"],...spdStops().flat(),spdTop()+0.5,spdOver()];
const apExpr=()=>["match",["get","ap"],1,apCol("FSD"),2,apCol("Autosteer"),3,apCol("TACC"),apCol("Manual")];
const spdLegend=()=>`<span style="background:linear-gradient(90deg,${spdStops().map(([a,c])=>c+" "+(100*a/spdTop())+"%").join(",")});display:inline-block;width:140px;height:8px;border-radius:4px;vertical-align:middle"></span> 0 → ${spdTop()} km/h <span style="background:${spdOver()};display:inline-block;width:10px;height:10px;border-radius:50%;vertical-align:middle;border:1px solid #888"></span> faster`;
const apLegend=()=>["FSD","Autosteer","TACC","Manual"].map(k=>`<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${apCol(k)};vertical-align:middle"></span> ${k}`).join("  ");
const spdAt=v=>{const st=spdStops();if((v||0)>spdTop())return spdOver();const t=Math.max(0,v||0);let i=0;while(i<st.length-2&&t>=st[i+1][0])i++;const a=st[i],b=st[i+1],f=(t-a[0])/((b[0]-a[0])||1),h=c=>[1,3,5].map(k=>parseInt(c.substr(k,2),16)),A=h(a[1]),B=h(b[1]);return "#"+A.map((x,k)=>Math.round(x+(B[k]-x)*f).toString(16).padStart(2,"0")).join("")};
// One Sentry event in full: the same layout as a drive (map on top, then the six cameras); no telemetry, the camera that detected it has a red border
async function pageTeslaCamSentry(role,ev){
  loading();main.innerHTML=crumbs("#/teslacam","TeslaCam")+'<div id="tdh"></div>';const host=$("#tdh");
  try{
    const [lr,pl]=await Promise.all([tcGet("teslacam/"+role+"/sentry"),api("places").catch(()=>({saved:[]}))]),e=lr.events.find(v=>v.id===ev),places=pl.saved||[];
    if(!e){host.innerHTML=errBox("This event is not on a connected drive.","Sentry event not found");return}
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
      head:`${stamps.length} one-minute clip${stamps.length>1?"s":""}; the next plays on its own. It opens at the moment the car detected something, and the camera that saw it has a red border.`})
  }catch(err){host.innerHTML=errBox(err.message,"Could not open this Sentry event")}
}
async function pageTeslaCamDrive(role,id){
  loading();const pl=await api("places").catch(()=>({saved:[]}));
  main.innerHTML=crumbs("#/teslacam","TeslaCam")+'<div id="tdh"></div>';
  const fetchSeg=async b=>{const r=await fetch("/api/teslacam/"+role+"/seg/"+encodeURIComponent(id)+(b!=null?"?budget="+b:""));if(!r.ok)throw new Error((await r.json().catch(()=>({}))).error||("HTTP "+r.status));return r.json()};
  try{await teslaDriveBody($("#tdh"),role,fetchSeg,pl.saved||[],{})}catch(e){const h=$("#tdh");if(h)h.innerHTML=errBox(e.message,"Could not rebuild this drive")}
}
// The map, telemetry chart and stitched six-camera video of one drive, moving together. `fetchSeg(budget)` returns the server's segment / window answer;
// it is asked again while minutes are still being read. opts.heading=false leaves the title to the caller (the Footage tab of a Tessie drive).
async function teslaDriveBody(host,role,fetchSeg,places,opts){
  let d=await fetchSeg(0),player=null,map=null,chart=null,done=false,evShown=false;
  const stop=()=>{done=true};addEventListener("hashchange",stop,{once:true});
  const it=()=>d.item,S=()=>d.series,live=()=>!done&&$("#tdb")&&host.contains($("#tdb"));
  host.innerHTML=`<div id="tdb"><h1 id="dh" ${opts.heading===false?"hidden":""}></h1><div class="sub" id="dsub"></div><div id="dpend"></div><div class="tiles" id="dtl"></div>
    <div id="dmapc" class="card" style="padding:10px"><div class="bar" style="margin:0 0 8px"><button class="chip on" data-cm="v">Speed</button><button class="chip" data-cm="ap">Autopilot / FSD</button><span class="sub" id="dleg"></span><span style="margin-left:auto;display:flex;gap:6px;align-items:center"><span class="sub">Map</span>${MSZ.map(([k,t])=>`<button class="chip" data-ms="${k}">${t}</button>`).join("")}</span></div><div class="map big" id="dmap"></div></div>
    <div id="drevw"></div><div id="drj"></div>
    <div id="dchc" class="card" style="margin-top:12px;padding:10px"><div class="bar" style="margin:0 0 6px"><b>Telemetry</b><span class="sub" id="dtip" style="min-height:18px;flex:1"></span><span style="display:flex;gap:6px;align-items:center">${TSZ.map(([k,t])=>`<button class="chip" data-ts="${k}">${t}</button>`).join("")}</span></div><canvas id="dch" style="width:100%;display:block"></canvas></div>
    <div class="card" style="margin-top:12px"><div id="dvid"></div></div><div id="drv"></div><div id="dev"></div></div>`;
  const fill=()=>{if(!live())return;const x=it(),m=Math.max(1,Math.round((x.t1-x.t0)/60)),a=nameAt(places,x.p0),b=nameAt(places,x.p1);
    $("#dh").textContent=(x.kind==="drive"?"Drive":x.kind==="stopped"?"Stopped, recording":"Recording")+" · "+dayName(x.start.slice(0,10))+" · "+clockHM(x.start)+" – "+clockHM(x.end);
    $("#dsub").textContent=(a||b&&opts.heading!==false?(a||"?")+" → "+(b||"?")+" · ":"")+(d.connected===false?"The drive these clips are on is not connected: the map and telemetry come from the copy kept when they were read, and there is no video. ":"")+"Rebuilt from the dashcam video alone (Recent and Saved clips stitched). Telemetry is only written while the car moves.";
    $("#dtl").innerHTML=tile(dur(m),"time")+(x.kind==="drive"?tile(n0(x.km,1)+" km","distance")+tile(n0(x.vmax)+" km/h","top speed")+tile(Math.round((x.fsd+x.aut)*100)+"%","on autopilot / FSD"):"")+tile(n0(d.minutes.length),`clips · ${x.recent||0} Recent, ${x.saved||0} Saved`);
    $("#dpend").innerHTML=d.pending>0?`<div class="card ixb"><div>Reading the telemetry from the video (first time only): ${n0(d.pending)} of ${n0(d.minutes.length)} minutes still to read…</div></div>`:""};
  const pts=()=>{const s=S(),o=[];for(let i=0;i<s.v.length;i++)if(s.lat[i]!=null&&Math.abs(s.lat[i])>1)o.push(i);return o};
  const clk=i=>lclock(it().t0+i,it().lo);                              // the local clock text of second i of the drive (display only)
  // second i of the drive (UTC) -> the minute of footage that holds it and the second inside it; a hole in the footage goes to the next minute
  const seekIdx=i=>{if(!player)return;const ms=d.minutes.filter(m=>m.cams&&m.cams.length);let b=null;for(const m of ms){if(m.off<=i)b=m;else break}if(!b)b=ms[0];if(b)player.seekAt(b.stamp,Math.max(0,Math.min(59,i-b.off)))};
  const cursor=sec=>{const i=Math.max(0,Math.min(S().v.length-1,Math.round(sec)));if(map)map.cursor(i);if(chart)chart.cursor(i)};
  const events=()=>{if(evShown||!d.events||!d.events.length||!live())return;evShown=true;
    $("#dev").innerHTML=`<h2>Saved clips and Sentry events in this drive</h2><div class="sub">Click one to jump the video there.</div><div class="evs">${d.events.map(v=>`<button class="evc" data-u="${v.t}">${v.thumb?`<img loading="lazy" src="/footage/${role}/${v.folder}/${v.id}/thumb.png" onerror="this.style.visibility='hidden'">`:'<div class="noimg"></div>'}<b>${hms(v.ts)}</b><span>${esc(v.folder==="SavedClips"?"Saved":"Sentry")} · ${esc(reason(v.reason))}</span><span>${esc(v.city||"")}</span></button>`).join("")}</div>`;
    $$("#dev .evc").forEach(b=>b.onclick=()=>{if(player){seekIdx(Math.round(+b.dataset.u-it().t0));$("#dvid").scrollIntoView({block:"nearest"})}})};
  RVAPI={cursor:i=>cursor(i),seek:i=>seekIdx(i),series:()=>S(),t0:()=>it().t0,clk:i=>clk(i),focus:f=>{if(chart&&chart.focus)chart.focus(f)},zoom:(a,b)=>{if(chart&&chart.view)chart.view(a,b)},limit:(a,b)=>{if(chart&&chart.limit)chart.limit(a,b)},live:()=>live()};
  const build=()=>{if(!live())return;fill();const s=S(),idx=pts();
    if(!idx.length){$("#dmapc").hidden=$("#dchc").hidden=true}else{$("#dmapc").hidden=$("#dchc").hidden=false;
      if(!map)map=driveMap($("#dmap"),s,idx,seekIdx,$("#dleg"));else map.update(s,idx);
      if(!chart)chart=telemChart($("#dch"),$("#dtip"),s,seekIdx,clk);else chart.update(s)}
    const playable=d.minutes.filter(m=>m.cams&&m.cams.length);
    if(!player&&playable.length){const mm=playable,cams={},meta={};mm.forEach(m=>{cams[m.stamp]=m.cams;meta[m.stamp]=m});
      player=clipPlayer($("#dvid"),{stamps:mm.map(m=>m.stamp),cams,view:"all",
        url:(st,c)=>meta[st].src==="saved"?`/footage/${role}/SavedClips/${meta[st].ref}/${st}-${c}.mp4`:`/footage/${role}/RecentClips/${st}-${c}.mp4`,
        loc:Object.fromEntries(mm.map(m=>[m.stamp,m.loc])),onAt:(st,sec)=>cursor(meta[st].off+sec),head:`${it().kind==="drive"?"Click the map or the chart to jump there; the dot and the line follow the video.":"The car was not moving, so there is no map."} Each minute plays on to the next, Recent and Saved alike.`})}
    else if(!playable.length)$("#dvid").innerHTML='<div class="sub">No video for this time on a connected drive.</div>';
    events()};
  // map size and telemetry size / hide, remembered
  const setMs=k=>{lsSet("tc.mapsz",k);$$("[data-ms]",host).forEach(b=>b.classList.toggle("on",b.dataset.ms===k));$("#dmap").style.height=(MSZ.find(x=>x[0]===k)||MSZ[2])[2];if(map&&map.resize){map.resize();map.fit(250)}};
  const setTs=k=>{lsSet("tc.telsz",k);$$("[data-ts]",host).forEach(b=>b.classList.toggle("on",b.dataset.ts===k));const c=$("#dch");c.style.display=k==="h"?"none":"block";c.style.height=(TSZ.find(x=>x[0]===k)||TSZ[1])[2];dispatchEvent(new Event("resize"))};
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
  if(!window.maplibregl){el.innerHTML='<div class="empty">Map library not loaded (offline?).</div>';return{update(){},cursor(){},mode(){}}}
  const dark=matchMedia("(prefers-color-scheme: dark)").matches,m=newMap({container:el,style:MAPSTYLE(dark),center:[idx.length?s.lon[idx[0]]:151,idx.length?s.lat[idx[0]]:-33],zoom:11,attributionControl:{compact:true}});
  mapNav(m,()=>fit(),"Show the whole route");
  let ready=false,mode="v",S=s,I=idx,first=true,latest=null;
  const SPD=spdExpr(),AP=apExpr();
  const feats=()=>{const f=[];for(let k=1;k<I.length;k++){const a=I[k-1],b=I[k];if(b-a>5)continue;f.push({type:"Feature",geometry:{type:"LineString",coordinates:[[S.lon[a],S.lat[a]],[S.lon[b],S.lat[b]]]},properties:{v:S.v[b]||0,ap:S.ap[b]||0,i:b}})}return{type:"FeatureCollection",features:f}};
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
  return{update(s2,i2){S=s2;I=i2;draw()},fit,resize(){m.resize()},mode(x){mode=x;paint()},cursor(i){if(!ready)return;let j=i;while(j>=0&&(S.lat[j]==null||Math.abs(S.lat[j])<1))j--;if(j<0)return;m.getSource("cu").setData({type:"FeatureCollection",features:[{type:"Feature",properties:{},geometry:{type:"Point",coordinates:[S.lon[j],S.lat[j]]}}]})}};
}
// speed, pedals and steering against time, with the autopilot state as a strip; hover reads it out, click or drag moves the video
function telemChart(cv,tip,s,onPick,t0){          // t0: i -> "HH:MM:SS" on the local clock (or a base time in ms, shown on this computer's clock)
  const lab=typeof t0==="function"?t0:i=>new Date(t0+i*1000).toTimeString().slice(0,8);
  // va/vb: the part of the drive shown (vb null = all of it); A/B the same, clamped, in seconds
  let S=s,cur=null,hov=null,F=null,W=0,H=0,M={signs:[],steps:[]},ver=0,cache=null,ck="",va=0,vb=null,A=0,B=1,sel=null,selMode=false,LIM=null;const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim()||"#888";
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
    // brake marks over the speed lane
    clipped(()=>{c.fillStyle=bad;c.globalAlpha=.18;for(let i=i0;i<=i1;i++)if(S.br[i])c.fillRect(X(i),lanes[0].y,sw,lanes[0].h);c.globalAlpha=1});
    // pedals
    const P=lanes[1];c.fillStyle=ink;c.fillText("accel. %",8,P.y+10);c.strokeStyle=line;c.beginPath();c.moveTo(L,P.y+P.h);c.lineTo(W-R,P.y+P.h);c.stroke();seg(S.accel,0,ok,0,v=>P.y+P.h*(1-Math.min(100,v)/100));
    // steering and blinkers
    const T=lanes[2],sm=Math.max(90,Math.ceil(Math.max(...S.steer.filter(x=>x!=null).map(Math.abs))/90)*90);c.fillStyle=ink;c.fillText("steering °",8,T.y+10);c.strokeStyle=line;c.beginPath();c.moveTo(L,T.y+T.h/2);c.lineTo(W-R,T.y+T.h/2);c.stroke();seg(S.steer,0,acc,0,v=>T.y+T.h/2-(v/sm)*(T.h/2));
    clipped(()=>{c.fillStyle="#f59e0b";for(let i=i0;i<=i1;i++)if(S.blk[i])c.fillRect(X(i),T.y+T.h-4,sw,4)});c.fillStyle="#f59e0b";c.fillRect(8,T.y+T.h-9,8,4);c.fillStyle=ink;c.fillText("indicator",20,T.y+T.h-4);
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
  return{update(s2){S=s2;ver++;norm();ui();draw()},marks(m){M=m;ver++;draw()},focus(f){F=f;draw()},view(a,b){setView(a,b)},limit(a,b){const N=S.v.length-1;LIM=a==null?null:{a:Math.max(0,Math.min(a,N-1)),b:Math.max(1,Math.min(b,N))};va=0;vb=null;norm();ver++;ui();draw()},cursor(i){cur=i;if(zoomed()&&(i<A||i>B)){const w=B-A;setView(i-w*.2,i+w*.8)}if(hov==null)tip.textContent=read(i);draw()}};
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
const ROUTES={activity:pageActivity,drives:pageDrives,charges:pageCharges,battery:pageBattery,health:pageHealth,analytics:pageAnalytics,teslacam:pageTeslaCam,queue:pageQueue,flips:pageDrives,review:pageDrives,dashcam:pageDrives,parked:pageParked,idles:pageParked,places:pagePlaces,alerts:pageAlerts};
async function route(){
  hideTip();if(TLOFF){TLOFF();TLOFF=null}if(TCPOLL){clearInterval(TCPOLL);TCPOLL=null}const h=(location.hash||"#/activity").replace(/^#\//,""),[p,arg,arg2]=h.split("/").map(decodeURIComponent);
  $$("#nav>a").forEach(a=>a.classList.toggle("on",a.dataset.p===(({drive:"drives",charge:"charges",idle:"parked",idles:"parked",dashcam:"drives",flips:"drives",review:"drives",place:"places"})[p]||p)));
  badge();main.scrollTop=0;
  try{
    if(p==="drive")await pageDrive(arg,arg2);else if(p==="charge")await pageCharge(arg,arg2);else if(p==="idle"||p==="parked"&&arg)await pageParkedOne(arg);else if(p==="place")await pagePlace(arg);else await (ROUTES[p]||pageActivity)(arg,arg2);
  }catch(e){main.innerHTML=errBox(e.stack||e.message,"Something went wrong")}
}
async function boot(){
  addEventListener("hashchange",route);
  try{SET=await (await fetch("/api/settings")).json()}catch(e){}
  try{const s=await api("summary");$("#veh").textContent=s.vehicle;$("#vsub").textContent=n0(s.km)+" km logged"}catch(e){}
  const st=async()=>{try{const s=await (await fetch("/api/status")).json();$("#status").innerHTML=`<span class="dot ${s.roots.A?"on":"off"}"></span>Archive ${s.roots.A?"connected":"not connected"}${s.stale?'<div class="upd">The app was updated. Quit and reopen it to load the new version.</div>':""}`}catch(e){}};
  st();setInterval(st,30000);route();
}
addEventListener("keydown",e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==="r"){e.preventDefault();location.reload()}});
$("#rl").onclick=()=>location.reload();
boot();
