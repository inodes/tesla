"""drive_charts.py - the stacked telemetry charts of a drive page (REQ-195), in the spirit of Tessie's drive page.

One panel per metric (speed, elevation, power, battery, range efficiency, temperatures), each with headline numbers,
all on ONE shared time axis. Hovering any panel draws a cursor through all of them, moves the marker on the route map
and shows every value at that moment; clicking jumps the dashcam footage (footage_ui.py) to that moment, and while the
footage plays the same cursor follows it (D.cursor(t) / D.seekFootage(t) are the two hooks between the modules).
DATA.pts rows: [t, lat, lon, speed, power, elevation, battery%, range_km, inside_C, outside_C, odometer_km].
"""

CSS = r"""
.cp{margin:0 0 6px}.cp .ph{display:flex;gap:18px;flex-wrap:wrap;align-items:baseline;margin:6px 0 2px}.cp .ph h3{margin:0;font-size:13px;font-weight:600;color:var(--ink)}
.cp .ph span{color:var(--mut);font-size:12px}.cp .ph span b{color:var(--ink);font-size:14px;font-weight:600;margin-left:4px}
.cp svg{width:100%;height:auto;display:block}.cp svg .cl{stroke:var(--ink);opacity:.45}
.trip{display:grid;gap:8px}.tp{display:flex;gap:12px;align-items:flex-start}.tp .dot{flex:none;width:12px;height:12px;border-radius:50%;margin-top:5px}
.tp .w{color:var(--mut);font-size:12px}.tp .w i{font-style:normal;margin-left:8px}.tp b{display:block;font-size:15px}.tp .ad{color:var(--mut);font-size:12px}
"""

JS = r"""
(function(){const root=$("#charts");if(!root||!D.pts.length)return;
const W=1060,L=46,R=46,pw=W-L-R,P=D.pts,t0=T[0],t1=T[N-1]||1,X=t=>L+(t-t0)/(t1-t0||1)*pw;
const col=i=>P.map(p=>p[i]),num=a=>a.filter(x=>x!=null),mx=a=>{a=num(a);return a.length?Math.max(...a):null},mn=a=>{a=num(a);return a.length?Math.min(...a):null},
 av=a=>{a=num(a);return a.length?a.reduce((s,x)=>s+x,0)/a.length:null},fx=(x,d=0)=>x==null?"-":(+x).toFixed(d);
const first=a=>{for(const x of a)if(x!=null)return x;return null},last=a=>{for(let i=a.length-1;i>=0;i--)if(a[i]!=null)return a[i];return null};
const odo=col(10),rg=col(7),dist=(last(odo)!=null&&first(odo)!=null)?last(odo)-first(odo):null;
// 100 % efficiency reference: rated range falls 1 km per km driven
const ref=(first(rg)!=null&&first(odo)!=null)?P.map(p=>p[10]==null?null:first(rg)-(p[10]-first(odo))):null;
const used=first(rg)!=null?first(rg)-last(rg):null;
const defs=[
 {k:"speed",t:"Speed",i:3,c:"var(--acc)",fill:1,u:"km/h",min0:1,stats:[["Average",fx(av(col(3)))+" km/h"],["Max",fx(mx(col(3)))+" km/h"]]},
 {k:"el",t:"Elevation",i:5,c:"var(--mut)",fill:1,u:"m",stats:[["Total",(last(col(5))!=null?fx(last(col(5))-first(col(5))):"-")+" m"],["Peak",fx(mx(col(5)))+" m"]]},
 {k:"pw",t:"Power",i:4,c:"#a855f7",u:"kW",zero:1,stats:[["Peak draw",fx(mx(col(4)))+" kW"],["Peak regen",fx(mn(col(4)))+" kW"]]},
 {k:"bat",t:"Battery",i:6,c:"var(--ok)",fill:1,u:"%",fixed:[0,100],stats:[["Start → end",fx(first(col(6)))+" → "+fx(last(col(6)))+" %"]]},
 {k:"rg",t:"Range efficiency",i:7,c:"var(--acc)",u:"km",ref:ref,stats:[["Range used",used!=null?fx(used,1)+" km":"-"],["Efficiency",used>0&&dist!=null?fx(dist/used*100)+" %":"-"]],note:ref?"dotted = 100 % efficiency":""},
 {k:"tmp",t:"Temperature",i:8,c:"var(--warn)",u:"°C",i2:9,c2:"var(--mut)",stats:[["Inside avg",fx(av(col(8)),1)+" °C"],["Outside avg",fx(av(col(9)),1)+" °C"]],note:"orange = inside, grey = outside"}
].filter(d=>num(col(d.i)).length>1||(d.i2&&num(col(d.i2)).length>1));
const nt=6,hh=118,pad=10,H=hh+pad+22;
function path(a,Y){let d="",gap=1;for(let k=0;k<N;k++){if(a[k]==null){gap=1;continue}d+=(gap?"M":"L")+X(T[k]).toFixed(1)+","+Y(a[k]).toFixed(1);gap=0}return d}
let html="";
defs.forEach(d=>{const a=col(d.i),b=d.i2?col(d.i2):[],all=num(a).concat(num(b),d.ref?num(d.ref):[]);
 let lo=Math.min(...all),hi=Math.max(...all);if(d.fixed){lo=d.fixed[0];hi=d.fixed[1]}if(d.min0)lo=Math.min(0,lo);if(d.zero){lo=Math.min(0,lo);hi=Math.max(0,hi)}
 if(hi-lo<1){hi+=.5;lo-=.5}const stp=(hi-lo)/2,dec=hi-lo<8?1:0,Y=v=>pad+hh-(v-lo)/(hi-lo)*hh;d.Y=Y;
 let o=`<g class="grid">`;for(let q=0;q<3;q++){const v=lo+stp*q;o+=`<line x1="${L}" x2="${W-R}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${L-6}" y="${Y(v)+4}" text-anchor="end">${fx(v,dec)}</text>`}o+=`</g>`;
 if(d.zero&&lo<0)o+=`<line x1="${L}" x2="${W-R}" y1="${Y(0)}" y2="${Y(0)}" stroke="var(--mut)" opacity=".5"/>`;
 if(d.fill){const pp=path(a,Y);if(pp)o+=`<path d="${pp}L${X(T[N-1]).toFixed(1)},${pad+hh}L${X(T[0]).toFixed(1)},${pad+hh}Z" fill="${d.c}" opacity=".13" stroke="none"/>`}
 if(d.ref)o+=`<path d="${path(d.ref,Y)}" fill="none" stroke="var(--mut)" stroke-width="1.4" stroke-dasharray="3 4"/>`;
 o+=`<path d="${path(a,Y)}" fill="none" stroke="${d.c}" stroke-width="2" stroke-linejoin="round"/>`;
 if(d.i2)o+=`<path d="${path(b,Y)}" fill="none" stroke="${d.c2}" stroke-width="2" stroke-linejoin="round"/>`;
 (D.flips||[]).forEach(e=>{if(e.t>=t0&&e.t<=t1)o+=`<line x1="${X(e.t)}" x2="${X(e.t)}" y1="${pad}" y2="${pad+hh}" stroke="${e.kind==="110->100"?"#d4332b":"#0a6cff"}" stroke-dasharray="4 3" opacity=".6"/>`});
 (D.saved||[]).forEach(e=>{if(e.t>=t0&&e.t<=t1)o+=`<line x1="${X(e.t)}" x2="${X(e.t)}" y1="${pad}" y2="${pad+hh}" stroke="#a855f7" stroke-dasharray="2 3" opacity=".6"/>`});
 for(let i=0;i<=nt;i++){const t=t0+(t1-t0)*i/nt;o+=`<text x="${X(t)}" y="${H-5}" text-anchor="middle">${fmtT(t)}</text>`}
 o+=`<line class="cl" y1="${pad}" y2="${pad+hh}" style="display:none"/>`;
 html+=`<div class="cp"><div class="ph"><h3>${d.t} <span>${d.u}</span></h3>${d.stats.map(s=>`<span>${s[0]}<b>${s[1]}</b></span>`).join("")}${d.note?`<span>${d.note}</span>`:""}</div><svg class="pn" viewBox="0 0 ${W} ${H}">${o}</svg></div>`});
root.innerHTML=`<h2>Telemetry</h2>${html}<div class="legend">${(D.flips&&D.flips.length)?`<span><i style="background:#d4332b;height:8px;width:8px;border-radius:50%"></i>110→100</span><span><i style="background:#0a6cff;height:8px;width:8px;border-radius:50%"></i>100→110</span>`:""}<span><i style="background:#a855f7;height:8px;width:8px;border-radius:50%"></i>saved clip</span>${D.foot?`<span>click any chart to jump the dashcam footage to that moment</span>`:""}</div>`;
const tip=$("#tip"),lines=()=>root.querySelectorAll(".cl");
const idx=t=>{let lo=0,hi=N-1;while(lo<hi){const m=(lo+hi)>>1;if(T[m]<t)lo=m+1;else hi=m}return lo>0&&Math.abs(T[lo-1]-t)<Math.abs(T[lo]-t)?lo-1:lo};
D.cursor=t=>{if(t<t0||t>t1){D.cursorOff();return -1}const i=idx(t),x=X(T[i]);lines().forEach(l=>{l.setAttribute("x1",x);l.setAttribute("x2",x);l.style.display=""});
 if(D.mapCursor&&D.ok&&D.ok[i])D.mapCursor(i);return i};
D.cursorOff=()=>{lines().forEach(l=>l.style.display="none");if(D.mapCursorOff)D.mapCursorOff()};
const tm=ev=>{const sv=ev.target.closest&&ev.target.closest("svg.pn");if(!sv)return null;const r=sv.getBoundingClientRect(),x=(ev.clientX-r.left)/r.width*W;return Math.max(t0,Math.min(t1,t0+(x-L)/pw*(t1-t0)))};
root.addEventListener("mousemove",ev=>{const t=tm(ev);if(t==null)return;const i=D.cursor(t),p=P[i];if(i<0)return;
 tip.style.display="block";tip.style.left=Math.min(ev.clientX+14,innerWidth-200)+"px";tip.style.top=(ev.clientY+14)+"px";
 tip.innerHTML=`<b>${fmtT(p[0])}</b><br>Speed ${f0(p[3])} km/h<br>Power ${p[4]==null?"-":p[4]} kW<br>Elevation ${f0(p[5])} m<br>Battery ${f0(p[6])} %`+(p[7]!=null?`<br>Range ${fx(p[7],1)} km`:"")+(p[8]!=null?`<br>Inside ${fx(p[8],1)} °C`:"")+(p[9]!=null?`<br>Outside ${fx(p[9],1)} °C`:"")});
root.addEventListener("mouseleave",()=>{tip.style.display="none";D.cursorOff()});
root.addEventListener("click",ev=>{const t=tm(ev);if(t!=null&&D.seekFootage)D.seekFootage(t)});
})();
"""
