"""settings.py - the "software" block of config.json (region groups, labels), with these defaults when a key is missing.

    "software": {
        "regions": {"ANZ": ["au", "nz"], ...},       a named group of country codes (ISO 3166 alpha-2, lower case); a country Teslascope reports that is in no group
                                                    is put in a group called "Other" so it is never lost, and every country can also be chosen by itself
        "model_labels": {"model3": "Model 3", ...},         Teslascope's car_type id before the "_" -> the name shown
        "hardware_labels": {"TeslaAP4": "AI4", ...},        the id after the "_" -> the name shown
        "settle_days": 14,                                  a version nobody has taken for this long, with none waiting, is not asked about again
        "refetch_hours": 6,                                 a version still moving is asked about at most this often
        "pace_seconds": 1.0,                                pause between requests to Teslascope
        "car_check_minutes": 30                             how often the app asks Tessie which software the car is on (0 = never; the drives sync asks too)
    }
"""
DEFAULTS = {
    "regions": {
        "ANZ": ["au", "nz"],
        "North America": ["us", "ca", "mx", "pr"],
        "Latin America": ["ar", "bo", "br", "cl", "co", "cr", "do", "ec", "gt", "pa", "pe", "py", "sv", "uy", "ve"],
        "United Kingdom and Ireland": ["gb", "ie"],
        "Europe": ["al", "ad", "at", "ba", "be", "bg", "by", "ch", "cy", "cz", "de", "dk", "ee", "es", "fi", "fr", "gr", "hr", "hu", "is", "it", "li", "lt", "lu", "lv", "mc",
                   "md", "me", "mk", "mt", "nl", "no", "pl", "pt", "ro", "rs", "se", "si", "sk", "sm", "tr", "ua", "xk"],
        "Middle East and Africa": ["ae", "bh", "eg", "il", "jo", "kw", "lb", "ma", "om", "qa", "sa", "tn", "za"],
        "Asia Pacific": ["bd", "cn", "hk", "id", "in", "jp", "kh", "kr", "lk", "mo", "mn", "my", "ph", "sg", "th", "tw", "vn"],
    },
    "model_labels": {
        "model3": "Model 3", "modely": "Model Y", "cybertruck": "Cybertruck",
        "models": "Model S (MonoCam)", "models2": "Model S", "modelx": "Model X",
        "lychee": "Model S (Refresh)", "tamarind": "Model X (Refresh)",
    },
    "hardware_labels": {"TeslaAP4": "AI4", "TeslaAP3": "AI3", "ParkerPascal2_5": "HW2.5", "MonoCam": "MonoCam"},
    "settle_days": 14,
    "refetch_hours": 6,
    "pace_seconds": 1.0,
    "car_check_minutes": 30,
}


def load(cfg=None):
    """-> the settings: config.json's "software" block over DEFAULTS. cfg: an already-loaded config dict, or None to read config.json."""
    if cfg is None:
        try:
            from lib.tessie_api_common import load_config
            cfg = load_config()
        except Exception:
            cfg = {}
    mine = (cfg or {}).get("software") if isinstance(cfg, dict) else None
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    if isinstance(mine, dict):
        for k, v in mine.items():
            if isinstance(out.get(k), dict) and isinstance(v, dict):
                out[k].update(v)
            elif k in out:
                out[k] = v
    return out
