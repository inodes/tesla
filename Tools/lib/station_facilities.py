#!/usr/bin/env python3
"""
station_facilities.py - Persisted Station Facilities Registry & Quality Scoring Engine
====================================================================================
Maintains a local JSON cache of verified nearby amenities, food, restrooms, and shops
around EV charging stations (primarily Superchargers), with a 0-10 quality scoring
algorithm that allows drivers to instantly see which chargers have good vs bad facilities.

Stored in Tessie data home (station_facilities.json) for instant offline pullup.
"""

import os
import json
import time
from datetime import datetime, timezone

try:
    from tessie_api_common import resolve_tessie_data_home
except ImportError:
    try:
        from lib.tessie_api_common import resolve_tessie_data_home
    except ImportError:
        try:
            from Tools.lib.tessie_api_common import resolve_tessie_data_home
        except ImportError:
            def resolve_tessie_data_home():
                return os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie")


def compute_facility_score(pois: list) -> dict:
    """
    Computes an objective, EV-driver focused facility quality score (0.0 to 10.0)
    and categorical rating based on nearby amenities.

    Key considerations for EV drivers during a 15-45 minute charging session:
    1. 🚻 Restrooms & Services (Essential): up to 3.0 pts
    2. 🍔 Dining & Food Options: up to 2.5 pts
    3. ☕ Cafes & Coffee: up to 2.0 pts
    4. 🛒 Supermarkets & Groceries: up to 1.5 pts
    5. 🛍️ Retail & Leisure: up to 1.0 pts

    Closer venues (<= 100m) receive full weight; 100m-250m receive 0.8x weight;
    venues > 250m receive 0.5x weight.
    """
    # Exclude charging station self-matches
    filtered_pois = [
        p for p in (pois or [])
        if not (p.get("category", "").lower().startswith("charging") or "supercharger" in p.get("name", "").lower() or p.get("name", "").lower().startswith("tesla super"))
    ]
    pois = filtered_pois
    if not pois:
        return {
            "score": 0.0,
            "rating": "Sparse",
            "tier_color": "🔴",
            "badge": "🔴 Sparse (< 3.0/10) · No amenities detected",
            "categories": {},
            "chips": "—",
            "total_count": 0,
            "doorstep_count": 0,
            "doorstep_100m": [],
            "immediate_doorstep": [],
            "closest_poi": None,
        }

    # Group POIs by category group
    grouped = {}
    for p in pois:
        grp = p.get("group", "Other Facilities")
        if grp not in grouped:
            grouped[grp] = []
        grouped[grp].append(p)

    def group_pts(grp_name, max_pts, per_item_base):
        items = grouped.get(grp_name, [])
        if not items:
            return 0.0
        tot = 0.0
        for item in items:
            dist = item.get("dist_m", 200.0)
            weight = 1.0 if dist <= 100 else (0.8 if dist <= 250 else 0.5)
            tot += per_item_base * weight
        return min(max_pts, tot)

    pts_restroom = group_pts("Amenities & Services", 3.0, 1.5)
    pts_dining = group_pts("Dining & Fast Food", 2.5, 1.0)
    pts_coffee = group_pts("Cafes & Coffee", 2.0, 1.0)
    pts_groceries = group_pts("Supermarkets & Groceries", 1.5, 1.0)
    pts_retail = group_pts("Retail & Shopping", 0.7, 0.4)
    pts_leisure = group_pts("Leisure & Entertainment", 0.3, 0.3)

    raw_score = pts_restroom + pts_dining + pts_coffee + pts_groceries + pts_retail + pts_leisure
    score = round(min(10.0, raw_score), 1)

    # Determine Rating Tier
    if score >= 8.0:
        rating = "Excellent"
        tier_color = "🟢"
    elif score >= 5.5:
        rating = "Good"
        tier_color = "🟡"
    elif score >= 3.0:
        rating = "Basic"
        tier_color = "🟠"
    else:
        rating = "Sparse"
        tier_color = "🔴"

    # Emoji summary chips for available categories
    chips_list = []
    if "Amenities & Services" in grouped:
        chips_list.append("🚻")
    if "Cafes & Coffee" in grouped:
        chips_list.append("☕")
    if "Dining & Fast Food" in grouped:
        chips_list.append("🍔")
    if "Supermarkets & Groceries" in grouped:
        chips_list.append("🛒")
    if "Retail & Shopping" in grouped:
        chips_list.append("🛍️")
    if "Lodging & Hotels" in grouped:
        chips_list.append("🏨")
    if "Leisure & Entertainment" in grouped:
        chips_list.append("🎯")

    chips_str = "".join(chips_list) if chips_list else "—"
    badge = f"{tier_color} {rating} ({score:.1f}/10) · {chips_str}"

    cat_summary = {}
    for grp_name, items in grouped.items():
        cat_summary[grp_name] = [f"{it['name']} ({it['dist_m']:.0f}m)" for it in items[:6]]

    # Immediate Doorstep amenities (<= 100m, ~1-2 min walk right by the stalls)
    doorstep_pois = [p for p in pois if p.get("dist_m", 999.0) <= 100.0]
    doorstep_summary = [f"{p['name']} ({p.get('dist_m', 0):.0f}m)" for p in doorstep_pois]
    immediate_doorstep = [
        {
            "name": p["name"],
            "category": p.get("category", "amenity"),
            "group": p.get("group", "Other Facilities"),
            "emoji": p.get("emoji", "📍"),
            "dist_m": p.get("dist_m", 0.0)
        }
        for p in doorstep_pois
    ]
    closest_poi = pois[0] if pois else None

    return {
        "score": score,
        "rating": rating,
        "tier_color": tier_color,
        "badge": badge,
        "categories": cat_summary,
        "chips": chips_str,
        "total_count": len(pois),
        "doorstep_count": len(doorstep_pois),
        "doorstep_100m": doorstep_summary,
        "immediate_doorstep": immediate_doorstep,
        "closest_poi": f"{closest_poi['name']} ({closest_poi['dist_m']:.0f}m)" if closest_poi else None,
    }


class FacilitiesRegistry:
    """
    Manages local JSON storage and query resolution for charging station facilities.
    """

    def __init__(self, data_home: str = None):
        self.data_home = data_home or resolve_tessie_data_home()
        tools_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        repo_root = os.path.dirname(tools_dir)
        self.candidate_paths = [
            os.path.join(self.data_home, "station_facilities.json"),
            os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie/station_facilities.json"),
            os.path.join(repo_root, "Tessie", "station_facilities.json")
        ]
        # Remove duplicate paths while preserving order
        seen = set()
        self.candidate_paths = [p for p in self.candidate_paths if not (p in seen or seen.add(p))]
        self.file_path = self.candidate_paths[0]
        self._cache = None

    def load(self) -> dict:
        if self._cache is not None:
            return self._cache
        for p in self.candidate_paths:
            if os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        self._cache = json.load(f)
                        self.file_path = p
                        return self._cache
                except Exception:
                    continue
        self._cache = {}
        return self._cache

    def save(self) -> bool:
        if self._cache is None:
            return False
        for p in self.candidate_paths:
            try:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                tmp = f"{p}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._cache, f, indent=2, ensure_ascii=False)
                os.replace(tmp, p)
                self.file_path = p
                return True
            except Exception:
                continue
        return False

    def _normalize_key(self, key_or_name: str) -> str:
        if not key_or_name:
            return ""
        s = key_or_name.strip().lower()
        for prefix in ("tesla_", "chargefox_", "plugshare_", "evie_", "evx_", "bppulse_", "ampcharge_", "supercharger_"):
            if s.startswith(prefix):
                s = s[len(prefix):]
        import re
        s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
        return s

    def get_station_facilities(self, station_key: str = None, station_name: str = None, lat: float = None, lon: float = None) -> dict:
        """Looks up cached facilities record for a station by key, name, or proximity."""
        data = self.load()
        res = None
        if station_key and station_key in data:
            res = data[station_key]

        if not res:
            norm_key = self._normalize_key(station_key)
            norm_name = self._normalize_key(station_name)

            for k, v in data.items():
                if k == station_key:
                    res = v
                    break
                nk = self._normalize_key(k)
                nv_name = self._normalize_key(v.get("station_name", ""))
                if norm_key and (nk == norm_key or nk in norm_key or norm_key in nk):
                    res = v
                    break
                if norm_name and (nk == norm_name or nv_name == norm_name or norm_name in nv_name or nv_name in norm_name):
                    res = v
                    break

        # Coordinate matching (within ~200m)
        if not res and lat is not None and lon is not None:
            for k, v in data.items():
                v_lat, v_lon = v.get("lat"), v.get("lon")
                if v_lat is not None and v_lon is not None:
                    d_lat = abs(v_lat - lat)
                    d_lon = abs(v_lon - lon)
                    if d_lat < 0.002 and d_lon < 0.002:
                        res = v
                        break

        if not res:
            return None

        # Backwards compatibility: dynamically attach doorstep_100m & immediate_doorstep if missing from older json
        if ("doorstep_100m" not in res or "immediate_doorstep" not in res) and "pois" in res:
            doorstep_pois = [p for p in res["pois"] if p.get("dist_m", 999.0) <= 100.0]
            res["doorstep_100m"] = [f"{p['name']} ({p.get('dist_m', 0):.0f}m)" for p in doorstep_pois]
            res["immediate_doorstep"] = [
                {
                    "name": p["name"],
                    "category": p.get("category", "amenity"),
                    "group": p.get("group", "Other Facilities"),
                    "emoji": p.get("emoji", "📍"),
                    "dist_m": p.get("dist_m", 0.0)
                }
                for p in doorstep_pois
            ]
            res["doorstep_count"] = len(doorstep_pois)
            closest_poi = res["pois"][0] if res["pois"] else None
            res["closest_poi"] = f"{closest_poi['name']} ({closest_poi['dist_m']:.0f}m)" if closest_poi else None
        return res

    def store_station_facilities(self, station_key: str, station_name: str, network: str, lat: float, lon: float, pois: list, radius_m: int = 250, notes: str = "") -> dict:
        """Computes score and stores facility record in JSON registry."""
        self.load()
        score_info = compute_facility_score(pois)
        record = {
            "station_name": station_name,
            "network": network,
            "lat": lat,
            "lon": lon,
            "radius_m": radius_m,
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "score": score_info["score"],
            "rating": score_info["rating"],
            "tier_color": score_info["tier_color"],
            "badge": score_info["badge"],
            "chips": score_info["chips"],
            "total_count": score_info["total_count"],
            "doorstep_count": score_info["doorstep_count"],
            "doorstep_100m": score_info["doorstep_100m"],
            "immediate_doorstep": score_info["immediate_doorstep"],
            "closest_poi": score_info["closest_poi"],
            "categories": score_info["categories"],
            "pois": pois,
            "notes": notes,
        }
        self._cache[station_key] = record
        self.save()
        return record


_FACILITIES_REGISTRY = None

def get_facilities_registry() -> FacilitiesRegistry:
    global _FACILITIES_REGISTRY
    if _FACILITIES_REGISTRY is None:
        _FACILITIES_REGISTRY = FacilitiesRegistry()
    return _FACILITIES_REGISTRY


SUPERMARKET_KEYWORDS = {
    "woolworths", "coles", "aldi", "iga", "harris farm", "costco",
    "foodland", "drakes", "spudshed", "kmart", "target", "big w",
    "supermarket", "grocery", "groceries"
}

def station_has_supermarket(cached_fac: dict, max_dist_m: int = 250) -> bool:
    """True if cached facilities record contains a confirmed supermarket within max_dist_m."""
    if not cached_fac:
        return False
    pois = cached_fac.get("pois", [])
    for p in pois:
        if p.get("dist_m", 999.0) <= max_dist_m:
            if p.get("group") == "Supermarkets & Groceries":
                return True
            cat = (p.get("category") or "").lower()
            name = (p.get("name") or "").lower()
            if any(kw in cat or kw in name for kw in SUPERMARKET_KEYWORDS):
                return True
    return False


import shutil

try:
    from table_formatter import display_len, truncate_display
except ImportError:
    try:
        from lib.table_formatter import display_len, truncate_display
    except ImportError:
        try:
            from Tools.lib.table_formatter import display_len, truncate_display
        except ImportError:
            import re
            _ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\033\[[0-9;]*[a-zA-Z]")
            def display_len(s: str) -> int:
                return len(_ANSI_RE.sub("", str(s)))
            def truncate_display(s: str, max_w: int, ellipsis: str = "…") -> str:
                d = display_len(s)
                if d <= max_w:
                    return s
                return str(s)[:max(1, max_w - 1)] + ellipsis


def format_facilities_summary_card(cached_fac: dict, indent: str = "  ", max_width: int = None) -> list:
    """
    Renders an executive, beautifully formatted terminal block for nearby facilities
    on station inspection and preview cards, strictly constrained to terminal width.
    """
    if not cached_fac:
        return []

    if max_width is None:
        try:
            cols = shutil.get_terminal_size((100, 24)).columns
            max_width = max(60, cols)
        except Exception:
            max_width = 100

    lines = []
    radius_m = cached_fac.get("radius_m", 250)
    badge = cached_fac.get("badge", "")
    header_raw = f"{indent}🏙️  \033[1mNearby Facilities:\033[0m {badge} \033[90m(within {radius_m}m)\033[0m"
    if display_len(header_raw) > max_width:
        header_raw = truncate_display(header_raw, max_width)
    lines.append(header_raw)

    # 1. Immediate Doorstep (<= 100m)
    pois = cached_fac.get("pois", [])
    doorstep = [
        p for p in pois
        if p.get("dist_m", 999.0) <= 100.0
        and not (p.get("category", "").lower().startswith("charging") or "supercharger" in p.get("name", "").lower() or p.get("name", "").lower().startswith("tesla super"))
    ]

    if doorstep:
        lines.append(f"{indent}   🚪 \033[1mImmediate Doorstep (≤ 100m — 1-2 min walk):\033[0m")
        for p in doorstep[:8]:
            emoji = p.get("emoji", "📍")
            name = p.get("name", "Unknown")
            cat = p.get("category", "")
            dist = f"{p.get('dist_m', 0):.0f}m"
            
            prefix = f"{indent}      • {emoji} "
            suffix = f" ({dist})"
            avail = max_width - display_len(prefix) - display_len(suffix)
            
            if cat:
                full_content = f"{name} — {cat}"
                if display_len(full_content) <= avail:
                    line = f"{prefix}\033[36m{name}\033[0m — \033[90m{cat}\033[0m{suffix}"
                else:
                    cat_short = truncate_display(cat, 16)
                    rem_for_name = max(8, avail - display_len(f" — {cat_short}"))
                    name_short = truncate_display(name, rem_for_name)
                    line = f"{prefix}\033[36m{name_short}\033[0m — \033[90m{cat_short}\033[0m{suffix}"
            else:
                name_short = truncate_display(name, avail)
                line = f"{prefix}\033[36m{name_short}\033[0m{suffix}"
            lines.append(line)

        if len(doorstep) > 8:
            lines.append(f"{indent}      \033[90m... and {len(doorstep) - 8} more within 100m\033[0m")
    else:
        closest = None
        for p in pois:
            if not (p.get("category", "").lower().startswith("charging") or "supercharger" in p.get("name", "").lower() or p.get("name", "").lower().startswith("tesla super")):
                closest = p
                break
        if closest:
            emoji = closest.get("emoji", "📍")
            lines.append(f"{indent}   🚪 \033[1mWithin 100m:\033[0m \033[90mNone (closest is {emoji} {closest['name']} at {closest['dist_m']:.0f}m)\033[0m")
        else:
            lines.append(f"{indent}   🚪 \033[1mWithin 100m:\033[0m \033[90mNo amenities detected\033[0m")

    # 2. Detailed Per-Line Category Summary with emojis, counts, and top venues
    groups = {}
    for p in pois:
        if p.get("category", "").lower().startswith("charging") or "supercharger" in p.get("name", "").lower() or p.get("name", "").lower().startswith("tesla super"):
            continue
        grp = p.get("group", "Other Facilities")
        emoji = p.get("emoji", "📍")
        key = (grp, emoji)
        if key not in groups:
            groups[key] = []
        groups[key].append(p)

    if groups:
        lines.append(f"{indent}   📋 \033[1mCategory Summary (within {radius_m}m):\033[0m")
        def group_sort_order(g_key):
            g_name = g_key[0]
            prio = {
                "Supermarkets & Groceries": 1,
                "Dining & Fast Food": 2,
                "Cafes & Coffee": 3,
                "Amenities & Services": 4,
                "Retail & Shopping": 5,
                "Leisure & Entertainment": 6,
                "Lodging & Hotels": 7,
                "Healthcare": 8,
                "Automotive & Parking": 9,
                "Other Facilities": 10
            }
            return prio.get(g_name, 99)

        for (grp_name, grp_emoji), grp_pois in sorted(groups.items(), key=group_sort_order):
            prefix = f"{indent}      • {grp_emoji} \033[1m{grp_name}\033[0m ({len(grp_pois)}): "
            avail = max(15, max_width - display_len(prefix))
            
            # Dynamically pack as many venues as fit within line width
            chosen_venues = []
            for p in grp_pois:
                v_str = f"{p['name']} ({p.get('dist_m', 0):.0f}m)"
                rem_count = len(grp_pois) - (len(chosen_venues) + 1)
                suffix_check = f", +{rem_count} more" if rem_count > 0 else ""
                candidate = ", ".join(chosen_venues + [v_str]) + suffix_check
                if display_len(candidate) <= avail:
                    chosen_venues.append(v_str)
                else:
                    break

            if chosen_venues:
                rem_count = len(grp_pois) - len(chosen_venues)
                if rem_count > 0:
                    venues_str = ", ".join(chosen_venues) + f", +{rem_count} more"
                else:
                    venues_str = ", ".join(chosen_venues)
            else:
                first_v = f"{grp_pois[0]['name']} ({grp_pois[0].get('dist_m', 0):.0f}m)"
                rem_count = len(grp_pois) - 1
                suffix = f", +{rem_count} more" if rem_count > 0 else ""
                first_avail = max(8, avail - display_len(suffix))
                venues_str = truncate_display(first_v, first_avail) + suffix

            lines.append(f"{prefix}\033[90m{venues_str}\033[0m")

    return lines
