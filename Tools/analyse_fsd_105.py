#!/usr/bin/env python3
"""FSD / speed-plateau finder (REQ-190).

Finds every stretch of at least 3 consecutive samples at about 105 km/h (the
"hurry on a perceived 100" plateau) in the drive deep-dive telemetry and, for
each, what the car did on every OTHER pass of the same road (same or opposite
direction). Sections whose other passes ran at 110/115 are the candidates where
FSD's map or sign data may have under-read a 110 zone as 100.

Tessie records speed as whole mph converted to km/h, so the useful values are
101-102 (standard in a 100), 104-106 (hurry in a 100, the "105"), 112
(standard in a 110) and 114-115 (hurry in a 110).

Writes <data>/drives/analysis/fsd_105_sections.csv and fsd_105_cross_passes.csv.
Also writes fsd_highway_stretches.csv (every highway stretch since --since, default 2026-10-01)
and fsd_highway_map.html (route map coloured by speed band, with the 105 sections).
Usage: analyse_fsd_105.py [--data-dir DIR] [--since YYYY-MM-DD]
"""
import csv,sys,glob,os,datetime,math,collections,json
CLS={101:("100","std"),102:("100","std"),104:("100","hurry"),105:("100","hurry"),112:("110","std"),111:("110","std"),114:("110","hurry"),115:("110","hurry")}
def load(f):
    out=[]
    for r in csv.DictReader(open(f,encoding="utf-8")):
        try:
            sp=float(r["Speed (km/h)"]); lat=float(r["Latitude"]); lon=float(r["Longitude"]); od=float(r["Odometer (km)"])
        except: continue
        if r["Shift State"]!="D": continue
        t=datetime.datetime.strptime(r[next(k for k in r if k.startswith("Timestamp"))],"%Y-%m-%d %H:%M:%S")
        out.append((t,sp,lat,lon,od))
    return out
def runs(rows):
    res=[]
    cur=None
    for t,sp,lat,lon,od in rows:
        c=CLS.get(int(round(sp)))
        key=c if c else ("other",)
        if cur and cur["key"]==key and (t-cur["t1"]).total_seconds()<=120:
            cur.update(t1=t,lat1=lat,lon1=lon,od1=od,n=cur["n"]+1)
        else:
            if cur: res.append(cur)
            cur=dict(key=key,t0=t,t1=t,lat0=lat,lon0=lon,lat1=lat,lon1=lon,od0=od,od1=od,n=1,sp0=sp)
    if cur: res.append(cur)
    return res

def band(sp):
    s=int(round(sp))
    if 99<=s<=102: return "100s"      # standard in a 100 (63-64 mph)
    if 103<=s<=107: return "105h"      # hurry in a 100 / the 105 plateau (65-66 mph)
    if 110<=s<=113: return "110s"      # standard in a 110 (70 mph)
    if 114<=s<=116: return "115h"      # hurry in a 110 (71-72 mph)
    return "x"
def hav(a,b,c,d):
    R=6371.0;p=math.pi/180
    x=math.sin((c-a)*p/2)**2+math.cos(a*p)*math.cos(c*p)*math.sin((d-b)*p/2)**2
    return 2*R*math.asin(math.sqrt(x))
def segs(rows):
    # per-sample bands, absorb a single 'x' sample sandwiched between identical bands
    b=[band(r[1]) for r in rows]
    for i in range(1,len(b)-1):
        if b[i]=="x" and b[i-1]==b[i+1] and b[i-1]!="x" and (rows[i+1][0]-rows[i-1][0]).total_seconds()<=150:
            b[i]=b[i-1]
    out=[];i=0
    while i<len(rows):
        j=i
        while j+1<len(rows) and b[j+1]==b[i] and (rows[j+1][0]-rows[j][0]).total_seconds()<=120: j+=1
        out.append((b[i],i,j)); i=j+1
    return out,b


_DATA = os.path.expanduser(os.environ.get("TESLA_DATA_DIR", "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla"))
if "--data-dir" in sys.argv:
    _DATA = os.path.expanduser(sys.argv[sys.argv.index("--data-dir") + 1])
DEEPDIVE_DIR = os.path.join(_DATA, "drives", "deepdive")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import drive_paths as _dp   # REQ-193: per-drive folders + legacy flat files
OUT_DIR = os.path.join(_DATA, "drives", "analysis")
os.makedirs(OUT_DIR, exist_ok=True)

files=sorted(_dp.list_csvs(DEEPDIVE_DIR))
CELL=0.003
grid=collections.defaultdict(list)   # cell -> (fileidx, lat, lon, band, bearing)
sections=[]
def bearing(a,b,c,d):
    p=math.pi/180
    y=math.sin((d-b)*p)*math.cos(c*p); x=math.cos(a*p)*math.sin(c*p)-math.sin(a*p)*math.cos(c*p)*math.cos((d-b)*p)
    return (math.degrees(math.atan2(y,x))+360)%360
for fi,f in enumerate(files):
    rows=load(f)
    if len(rows)<5: continue
    sg,b=segs(rows)
    # moving samples only
    for i,(t,sp,lat,lon,od) in enumerate(rows):
        if sp<60: continue
        j=min(i+1,len(rows)-1); k=max(i-1,0)
        brg=bearing(rows[k][2],rows[k][3],rows[j][2],rows[j][3])
        grid[(round(lat/CELL),round(lon/CELL))].append((fi,lat,lon,b[i],brg,rows[i][0]))
    for bd,i,j in sg:
        if bd=="105h" and j-i+1>=3:
            sections.append((fi,rows,i,j))
def neighbours(lat,lon,fi,brg):
    c=(round(lat/CELL),round(lon/CELL)); out=[]
    for dx in (-1,0,1):
        for dy in (-1,0,1):
            for e in grid.get((c[0]+dx,c[1]+dy),()):
                if e[0]==fi: continue
                if hav(lat,lon,e[1],e[2])>0.25: continue
                d=abs((e[4]-brg+180)%360-180)
                out.append((e,"same" if d<60 else ("opp" if d>120 else "x")))
    return out
res=[]
for fi,rows,i,j in sections:
    cnt=collections.Counter(); passes=collections.defaultdict(set)
    for q in range(i,j+1):
        t,sp,lat,lon,od=rows[q]
        brg=bearing(rows[max(q-1,0)][2],rows[max(q-1,0)][3],rows[min(q+1,len(rows)-1)][2],rows[min(q+1,len(rows)-1)][3])
        seen=set()
        for e,dirn in neighbours(lat,lon,fi,brg):
            if dirn=="x": continue
            key=(e[0],dirn)
            if key in seen: continue   # one vote per other drive per sample
            seen.add(key); cnt[(dirn,e[3])]+=1; passes[dirn].add(e[0])
    a=rows[i];z=rows[j]
    res.append(dict(file=os.path.basename(files[fi]),start=f"{a[0]:%Y-%m-%d %H:%M}",km=round(z[4]-a[4],1),
        lat0=a[2],lon0=a[3],lat1=z[2],lon1=z[3],
        same_passes=len(passes["same"]),opp_passes=len(passes["opp"]),
        same_bands=dict(sorted((k[1],v) for k,v in cnt.items() if k[0]=="same")),
        opp_bands=dict(sorted((k[1],v) for k,v in cnt.items() if k[0]=="opp"))))
out=OUT_DIR+"/fsd_105_cross_passes.csv"
with open(out,"w",newline="") as fh:
    w=csv.DictWriter(fh,fieldnames=list(res[0])); w.writeheader(); w.writerows(res)
print(len(res),"sections written to",out)

files_all = sorted(_dp.list_csvs(DEEPDIVE_DIR))
_rows105 = []
for _fi, _rows, _i, _j in sections:
    _a, _z = _rows[_i], _rows[_j]
    _rows105.append(dict(file=os.path.basename(files[_fi]), start=f"{_a[0]:%Y-%m-%d %H:%M:%S}", end=f"{_z[0]:%H:%M:%S}",
        samples=_j - _i + 1, km=round(_z[4] - _a[4], 1), mins=round((_z[0] - _a[0]).total_seconds() / 60, 1),
        lat0=_a[2], lon0=_a[3], lat1=_z[2], lon1=_z[3]))
with open(OUT_DIR + "/fsd_105_sections.csv", "w", newline="") as _fh:
    _w = csv.DictWriter(_fh, fieldnames=list(_rows105[0])); _w.writeheader(); _w.writerows(_rows105)
print(len(res), "sections of >=3 samples at 103-107 km/h;", round(sum(r["km"] for r in res)), "km")


# ---- highway stretches + map ----
SINCE=sys.argv[sys.argv.index("--since")+1] if "--since" in sys.argv else "2026-10-01"
files_hwy=[f for f in sorted(_dp.list_csvs(DEEPDIVE_DIR)) if os.path.basename(f)[15:25]>=SINCE]
geo=json.load(open(os.path.join(_DATA,"geo_cache.json")))
pts=[]
for k,v in geo.items():
    try: la,lo=map(float,k.split(","))
    except: continue
    if isinstance(v,dict) and v.get("suburb"): pts.append((la,lo,v["suburb"]))
def label(lat,lon):
    best=None
    for la,lo,s in pts:
        if abs(la-lat)>0.15 or abs(lo-lon)>0.15: continue
        d=hav(lat,lon,la,lo)
        if best is None or d<best[0]: best=(d,s)
    return best[1] if best and best[0]<12 else f"{lat:.2f},{lon:.2f}"
out=[]
for f in files_hwy:
    rows=load(f)
    if len(rows)<5: continue
    i=0
    while i<len(rows):
        if rows[i][1]<85: i+=1; continue
        j=i
        while j+1<len(rows) and rows[j+1][1]>=80 and (rows[j+1][0]-rows[j][0]).total_seconds()<=150: j+=1
        km=rows[j][4]-rows[i][4]
        if km>=5:
            c=collections.Counter(band(r[1]) for r in rows[i:j+1])
            n=sum(c.values())
            seq=[("100" if band(r[1]) in("100s","105h") else "110" if band(r[1]) in("110s","115h") else "?") for r in rows[i:j+1]]
            sw=sum(1 for a,b in zip(seq,seq[1:]) if a!=b and "?" not in (a,b))
            sp=[r[1] for r in rows[i:j+1]]
            out.append(dict(date=f"{rows[i][0]:%Y-%m-%d}",t0=f"{rows[i][0]:%H:%M}",t1=f"{rows[j][0]:%H:%M}",km=round(km,1),
                frm=label(rows[i][2],rows[i][3]),to=label(rows[j][2],rows[j][3]),
                avg=round(sum(sp)/len(sp),1),
                p100s=round(100*c["100s"]/n),p105h=round(100*c["105h"]/n),p110s=round(100*c["110s"]/n),p115h=round(100*c["115h"]/n),
                other=round(100*c["x"]/n),switches=sw,n=n,
                lat0=rows[i][2],lon0=rows[i][3],lat1=rows[j][2],lon1=rows[j][3],file=os.path.basename(f)))
        i=j+1
p=OUT_DIR+"/fsd_highway_stretches.csv"
with open(p,"w",newline="") as fh:
    w=csv.DictWriter(fh,fieldnames=list(out[0])); w.writeheader(); w.writerows(out)
print(len(out),"stretches,",round(sum(o["km"] for o in out)),"km")
for o in out:
    print(o["date"][5:],o["t0"],f"{o['km']:6.1f}km avg{o['avg']:5.1f}",f"100s{o['p100s']:3d} 105h{o['p105h']:3d} 110s{o['p110s']:3d} 115h{o['p115h']:3d} x{o['other']:3d} sw{o['switches']:2d}",o["frm"][:18],">",o["to"][:18])


# ---- map ----
BC={"100s":0,"105h":1,"110s":2,"115h":3,"x":4}
samples=[];drives=[];file_to_i={}
for fi,f in enumerate(files_hwy):
    rows=load(f)
    if len(rows)<5: continue
    d=dict(i=len(drives),name=os.path.basename(f)[15:31],date=f"{rows[0][0]:%a %d %b}",t0=f"{rows[0][0]:%H:%M}")
    drives.append(d); file_to_i[os.path.basename(f)]=d["i"]
    for (t,sp,lat,lon,od) in rows:
        if sp<5: continue
        samples.append([round(lat,5),round(lon,5),round(sp,1),round(od,1),int(t.timestamp()),d["i"],BC[band(sp)]])
# geo suburbs near the route
geo=json.load(open(os.path.join(_DATA,"geo_cache.json")))
cells={(round(s[0]/0.2),round(s[1]/0.2)) for s in samples}
subs=[]
for k,v in geo.items():
    try: la,lo=map(float,k.split(","))
    except: continue
    if isinstance(v,dict) and v.get("suburb") and any((round(la/0.2)+a,round(lo/0.2)+b) in cells for a in(-1,0,1) for b in(-1,0,1)):
        subs.append([round(la,3),round(lo,3),v["suburb"]])
cross={ (r["file"][15:31] if False else r["file"],r["start"]):r for r in csv.DictReader(open(OUT_DIR+"/fsd_105_cross_passes.csv"))}
sec_times={}
for r in csv.DictReader(open(OUT_DIR+"/fsd_105_sections.csv")):
    t0=datetime.datetime.strptime(r["start"],"%Y-%m-%d %H:%M:%S")
    t1=datetime.datetime.strptime(r["start"][:10]+" "+r["end"],"%Y-%m-%d %H:%M:%S")
    sec_times[(r["file"],r["start"][:16])]=(int(t0.timestamp()),int(t1.timestamp()))
secs=[]
for r in csv.DictReader(open(OUT_DIR+"/fsd_105_cross_passes.csv")):
    if r["start"][:10]<SINCE: continue
    def parse(s):
        import ast
        try: return ast.literal_eval(s)
        except Exception: return {}
    sb=parse(r["same_bands"]);ob=parse(r["opp_bands"])
    hi=sum(v for d in (sb,ob) for k,v in d.items() if k in("110s","115h")); lo=sum(v for d in (sb,ob) for k,v in d.items() if k in("100s","105h"))
    verdict="no other pass" if hi+lo==0 else "others at 110" if hi>=2*lo else "others at 100/105" if lo>=2*hi else "mixed"
    ix=[]
    sx=sec_times.get((r["file"],r["start"][:16]))
    di=file_to_i.get(r["file"])
    if sx and di is not None:
        ix=[k for k,sm in enumerate(samples) if sm[5]==di and sx[0]<=sm[4]<=sx[1]]
    secs.append(dict(start=r["start"],km=float(r["km"]),lat=float(r["lat0"]),lon=float(r["lon0"]),lat1=float(r["lat1"]),lon1=float(r["lon1"]),v=verdict,hi=hi,lo=lo,ix=ix))
stretches=list(csv.DictReader(open(OUT_DIR+"/fsd_highway_stretches.csv")))
# The map itself is now built by build_fsd_map.py (adds the OpenStreetMap comparison, flips, shortlist).
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
try:
    import build_fsd_map
    build_fsd_map.build(_DATA,SINCE,print)
except Exception as e:
    print("map not built:",e)
