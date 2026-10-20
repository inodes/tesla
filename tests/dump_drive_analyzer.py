#!/usr/bin/env python3
"""
dump_drive_analyzer.py - fingerprint what DriveAnalyzer produces, to prove a restructuring changed nothing.

NAME
    dump_drive_analyzer.py - fingerprints of the drive consolidation and matching

SYNOPSIS
    dump_drive_analyzer.py TOOLS_DIR SCRATCH_DATA_DIR OUT.json old|new

DESCRIPTION
    Runs the drive consolidation (load_drives), the dashcam deep-dive load and the matching on a SCRATCH copy of the data (it writes
    drives_master.csv there, so never point it at the real data) using the code in TOOLS_DIR, and writes short fingerprints to OUT.json.
    Run it once on the earlier code (style "old": analyse_drives.DriveAnalyzer) and once on the new (style "new": lib.drives.analyzer),
    each on its own identical copy of the data, and compare the two files. Paths are ignored in the fingerprints.

EXAMPLES
    ./dump_drive_analyzer.py ~/ref/Tools ~/scratch/a old.json old
    ./dump_drive_analyzer.py ~/repos/tesla/Tools ~/scratch/b new.json new
"""
import sys,os,json,hashlib,glob
tools,scratch,out,style=sys.argv[1:5]
sys.path.insert(0,tools)
os.chdir(tools)
if style=="old":
    from analyse_drives import DriveAnalyzer
else:
    from lib.drives.analyzer import DriveAnalyzer
import io,contextlib
res={}
buf=io.StringIO()
with contextlib.redirect_stdout(buf):
    a=DriveAnalyzer(tessie_dir=scratch)
    a._sync_disabled=True
    drives=a.load_drives()
res["n_drives"]=len(drives)
def h(o): return hashlib.sha256(json.dumps(o,sort_keys=True,default=str).replace(scratch,"<S>").encode()).hexdigest()[:16]
res["drives_hash"]=h(drives)
with contextlib.redirect_stdout(buf):
    det=a.load_detailed_drives()
res["detailed_hash"]=h(det) if det is not None else None
res["detailed_n"]=len(det) if hasattr(det,"__len__") else None
m=[]
with contextlib.redirect_stdout(buf):
    for d in drives[-40:]:
        try: m.append(a.match_detailed_drive(d))
        except Exception as e: m.append("ERR "+type(e).__name__)
res["match_hash"]=h(m)
# files written by the consolidation
files={}
for f in sorted(glob.glob(scratch+"/drives/*.csv")+glob.glob(scratch+"/drives/*.json")):
    files[os.path.basename(f)]=hashlib.sha256(open(f,"rb").read()).hexdigest()[:16]
res["files"]=files
json.dump(res,open(out,"w"),indent=1,sort_keys=True)
print(json.dumps(res,sort_keys=True)[:600])
print("stdout lines swallowed:",len(buf.getvalue().splitlines()))
