"""drive_map_ui.py - the colours of the route map on a drive page (drive_report.py), the same as the app's own maps.

The speed gradient and its legend come from config.json (app.map.speed_colors, see app_config.py), so a drive page and the app's TeslaCam / Footage / Review
maps are always painted alike. patch() brings a page built before this existed up to date when the app serves it (no rebuild needed).
"""
import json

import app_config as CFG

OLD_SC = ('const SC=[[0,"#6b7a99"],[30,"#2f7fd8"],[60,"#18a058"],[90,"#e0b000"],[105,"#ee7a1a"],[112,"#d4332b"]];\n'
          'const sc=v=>{let c=SC[0][1];for(const [a,col] of SC)if(v>=a)c=col;return c};')
OLD_LEGEND = ('<div class="legend"><span><i style="background:#6b7a99"></i>&lt;30</span><span><i style="background:#2f7fd8"></i>30–60</span>'
              '<span><i style="background:#18a058"></i>60–90</span><span><i style="background:#e0b000"></i>90–105</span>'
              '<span><i style="background:#ee7a1a"></i>105–112</span><span><i style="background:#d4332b"></i>112+ km/h</span></div>')


def stops():
    s = CFG.get("map.speed_colors") or [[0, "#22a559"], [120, "#c71f2e"]]
    return [[float(a), str(c)] for a, c in s]


def sc_js():
    """The colour of a speed: the stops interpolated, in 5 km/h steps (so a route is a few dozen pieces, not one per reading)."""
    return ("const SC=%s;const hx=h=>[1,3,5].map(i=>parseInt(h.substr(i,2),16));\n"
            "const sc=v=>{const top=SC[SC.length-1][0];v=Math.max(0,Math.min(top,Math.round((v||0)/5)*5));let i=0;while(i<SC.length-2&&v>=SC[i+1][0])i++;"
            "const a=SC[i],b=SC[i+1],t=(v-a[0])/((b[0]-a[0])||1),A=hx(a[1]),B=hx(b[1]);return \"#\"+A.map((x,k)=>Math.round(x+(B[k]-x)*t).toString(16).padStart(2,\"0\")).join(\"\")};"
            % json.dumps(stops(), separators=(",", ":")))


def legend_html():
    s = stops()
    top = s[-1][0]
    grad = ",".join("%s %.0f%%" % (c, 100 * a / top) for a, c in s)
    return ('<div class="legend"><span style="display:inline-flex;align-items:center;gap:6px"><i style="width:150px;height:8px;border-radius:4px;margin:0;'
            'background:linear-gradient(90deg,%s)"></i>0 → %d km/h</span></div>' % (grad, top))


def patch(h):
    """A page built before the speed gradient: give it the new colours."""
    if OLD_SC in h:
        h = h.replace(OLD_SC, sc_js())
    if OLD_LEGEND in h:
        h = h.replace(OLD_LEGEND, legend_html())
    return h
