"""drive_enrich_ui.py - the extra telemetry panels of a drive page whose points were filled in from the dashcam (drive_fuse.enrich).

A drive page (drive_report.py) draws Tessie's own points. When "Enrich with TeslaCam metadata" is ticked, tesla_app.fuse_into() swaps DATA.pts for
the finer rows of drive_fuse.enrich() - 16 columns, the last five being [autopilot mode, accelerator %, steering deg, indicator, brake] - and adds
js() below, which draws four more panels under the existing ones on the same time axis, with the same cursor and tooltip:

  FSD / AP     a strip: Manual / FSD / Autosteer / TACC in the map colours (config.json app.map.autopilot_colors), with the share of time in each
  Accelerator  0-100 %
  Steering     degrees, left negative, symmetric about zero
  Indicator    a strip, with the number of times it was switched on

Rows without a camera reading (the start and end that the footage missed, a hole in it) leave a gap. Without the extra columns nothing is drawn,
so a page showing pure Tessie data is untouched.
"""
import json

import app_config as CFG

MODES = [("Manual", 0), ("FSD", 1), ("Autosteer", 2), ("TACC", 3)]


def colours():
    c = CFG.get("map.autopilot_colors") or {}
    d = {"FSD": "#3b6cf6", "Autosteer": "#8b5cf6", "TACC": "#14b8a6", "Manual": "#94a3b8"}
    return [str(c.get(n) or d[n]) for n, _ in MODES]


JS = r"""
(function(){const root=$("#charts");if(!root||!D.pts.length||D.pts[0].length<16)return;
const W=1060,L=46,R=46,pw=W-L-R,P=D.pts,t0=T[0],t1=T[N-1]||1,X=t=>L+(t-t0)/(t1-t0||1)*pw,pad=10,nt=6;
const MC=/*MC*/,MN=["Manual","FSD","Autosteer","TACC"],GAP=3;
const has=i=>P[i][11]!=null;
const dur=i=>i+1<N?Math.min(T[i+1]-T[i],GAP):1;
const axis=H=>{let o="";for(let i=0;i<=nt;i++){const t=t0+(t1-t0)*i/nt;o+=`<text x="${X(t).toFixed(1)}" y="${H-5}" text-anchor="middle">${fmtT(t)}</text>`}return o};
const marks=h=>{let o="";(D.flips||[]).forEach(e=>{if(e.t>=t0&&e.t<=t1)o+=`<line x1="${X(e.t)}" x2="${X(e.t)}" y1="${pad}" y2="${pad+h}" stroke="${e.kind==="110->100"?"#d4332b":"#0a6cff"}" stroke-dasharray="4 3" opacity=".6"/>`});
 (D.saved||[]).forEach(e=>{if(e.t>=t0&&e.t<=t1)o+=`<line x1="${X(e.t)}" x2="${X(e.t)}" y1="${pad}" y2="${pad+h}" stroke="#a855f7" stroke-dasharray="2 3" opacity=".6"/>`});return o};
const panel=(title,unit,stats,h,body)=>{const H=h+pad+22;
 return `<div class="cp"><div class="ph"><h3>${title} <span>${unit}</span></h3>${stats.map(s=>`<span>${s[0]}<b>${s[1]}</b></span>`).join("")}</div><svg class="pn" viewBox="0 0 ${W} ${H}">${body}${marks(h)}${axis(H)}<line class="cl" y1="${pad}" y2="${pad+h}" style="display:none"/></svg></div>`};
const grid=(h,lo,hi,fmt,mid)=>{let o=`<g class="grid">`;const vs=mid==null?[lo,(lo+hi)/2,hi]:[lo,mid,hi];vs.forEach(v=>{const y=pad+h-(v-lo)/(hi-lo)*h;o+=`<line x1="${L}" x2="${W-R}" y1="${y}" y2="${y}"/><text x="${L-6}" y="${y+4}" text-anchor="end">${fmt(v)}</text>`});return o+`</g>`};
const line=(k,Y,fill,h)=>{let d="",gap=1,a=null,b=null;for(let i=0;i<N;i++){const v=P[i][k];if(!has(i)||v==null){gap=1;continue}const x=X(T[i]).toFixed(1);d+=(gap?"M":"L")+x+","+Y(v).toFixed(1);if(a==null)a=x;b=x;gap=0}return d};
// a strip: runs of equal value merged, a gap where there is no reading
const strip=(val,colour,h)=>{let o="",run=null;const flush=()=>{if(run){o+=`<rect x="${X(run.a).toFixed(1)}" y="${pad}" width="${Math.max(.6,X(run.b)-X(run.a)).toFixed(1)}" height="${h}" fill="${run.c}"/>`;run=null}};
 for(let i=0;i<N;i++){if(!has(i)){flush();continue}const c=colour(val(i));if(!c){flush();continue}const a=T[i],b=a+dur(i);if(run&&run.c===c&&a-run.b<=GAP)run.b=b;else{flush();run={a:a,b:b,c:c}}}flush();return o};
let html="";
{let cov=0,first=null,last=null;for(let i=0;i<N;i++)if(has(i)){cov+=dur(i);if(first==null)first=i;last=i}
 const tot=t1-t0,mm=s=>Math.floor(s/60)+" min "+String(Math.round(s%60)).padStart(2,"0")+" s";
 if(first!=null&&tot>0&&cov<tot*.9)html+=`<p style="margin:6px 0 10px;font-size:12px;opacity:.75">Dashcam footage covers ${mm(cov)} of this ${mm(tot)} drive (${fmtT(T[first])}\u2013${fmtT(T[last])}); the rest shows Tessie's own points, about one every 30 s.</p>`}
// FSD / AP
{const tot=[0,0,0,0];let all=0;for(let i=0;i<N;i++)if(has(i)){const m=P[i][11];if(m>=0&&m<4)tot[m]+=dur(i);all+=dur(i)}
 const pc=m=>all?Math.round(100*tot[m]/all)+" %":"-";
 const h=34,body=`<rect x="${L}" y="${pad}" width="${pw}" height="${h}" fill="var(--bd)" opacity=".25"/>`+strip(i=>P[i][11],m=>MC[m]||null,h);
 html+=panel("FSD / AP","mode",[["FSD",pc(1)],["Autosteer",pc(2)],["TACC",pc(3)],["Manual",pc(0)]],h,body)+
  `<div class="legend" style="margin:-2px 0 8px">${[1,2,3,0].map(m=>`<span><i style="background:${MC[m]}"></i>${MN[m]}</span>`).join("")}</div>`}
// accelerator
{const h=118,Y=v=>pad+h-Math.max(0,Math.min(100,v))/100*h,d=line(12,Y);let mx=0,sm=0,n=0;for(let i=0;i<N;i++)if(has(i)&&P[i][12]!=null){mx=Math.max(mx,P[i][12]);sm+=P[i][12];n++}
 html+=panel("Accelerator","%",[["Average",n?Math.round(sm/n)+" %":"-"],["Max",Math.round(mx)+" %"]],h,grid(h,0,100,v=>v.toFixed(0))+(d?`<path d="${d}" fill="none" stroke="var(--ok)" stroke-width="1.6" stroke-linejoin="round"/>`:""))}
// steering
{const h=118;let m=0;for(let i=0;i<N;i++)if(has(i)&&P[i][13]!=null)m=Math.max(m,Math.abs(P[i][13]));const sp=Math.max(90,Math.ceil(m/90)*90),Y=v=>pad+h/2-v/sp*h/2,d=line(13,Y);
 html+=panel("Steering","° (left −, right +)",[["Max left",Math.round(Math.min(0,...P.map(p=>p[13]==null?0:p[13])))+"°"],["Max right",Math.round(Math.max(0,...P.map(p=>p[13]==null?0:p[13])))+"°"]],h,
  grid(h,-sp,sp,v=>v.toFixed(0),0)+(d?`<path d="${d}" fill="none" stroke="var(--acc)" stroke-width="1.6" stroke-linejoin="round"/>`:""))}
// indicator
{let n=0,prev=false,pt=null;for(let i=0;i<N;i++){if(!has(i)){prev=false;continue}const on=!!P[i][14];if(on&&!(prev&&pt!=null&&T[i]-pt<=GAP))n++;prev=on;pt=T[i]}
 const h=24,body=`<rect x="${L}" y="${pad}" width="${pw}" height="${h}" fill="var(--bd)" opacity=".25"/>`+strip(i=>P[i][14],v=>v?"#f59e0b":null,h);
 html+=panel("Indicator","on",[["Times switched on",String(n)]],h,body)}
const lg=root.querySelector(".legend");if(lg)lg.insertAdjacentHTML("beforebegin",html);else root.insertAdjacentHTML("beforeend",html);
const tip=$("#tip"),idx=t=>{let lo=0,hi=N-1;while(lo<hi){const m=(lo+hi)>>1;if(T[m]<t)lo=m+1;else hi=m}return lo>0&&Math.abs(T[lo-1]-t)<Math.abs(T[lo]-t)?lo-1:lo};
root.addEventListener("mousemove",ev=>{const sv=ev.target.closest&&ev.target.closest("svg.pn");if(!sv||tip.style.display!=="block")return;
 const r=sv.getBoundingClientRect(),x=(ev.clientX-r.left)/r.width*W,t=Math.max(t0,Math.min(t1,t0+(x-L)/pw*(t1-t0))),i=idx(t),p=P[i];
 if(p[11]==null)return;
 tip.innerHTML+=`<br>${MN[p[11]]||"-"}${p[12]!=null?`<br>Accelerator ${Math.round(p[12])} %`:""}${p[13]!=null?`<br>Steering ${Math.round(p[13])}°`:""}${p[14]?"<br>Indicator on":""}${p[15]?"<br>Braking":""}`});
})();
"""


def js():
    return JS.replace("/*MC*/", json.dumps(colours()))
