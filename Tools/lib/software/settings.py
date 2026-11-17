"""settings.py - the "software" block of config.json (region groups, labels), with these defaults when a key is missing.

    "software": {
        "regions": {"ANZ": ["au", "nz"], ...},       a named group of country codes (ISO 3166 alpha-2, lower case); a country Teslascope reports that is in no group
                                                    is put in a group called "Other" so it is never lost, and every country can also be chosen by itself
        "model_labels": {"model3": "Model 3", ...},         Teslascope's car_type id before the "_" -> the name shown
        "hardware_labels": {"TeslaAP4": "AI4", ...},        the id after the "_" -> the name shown
        "fsd_ai4_from": 13,                                 FSD from this major version on needs an AI4 computer (earlier FSD, v12, also ran on AI3); FSD Lite is for AI3
        "fsd_hw_share_pct": 5,                              a version the notes say a computer cannot take stays on its list when at least this share of its installs were that computer
        "settle_days": 14,                                  a version nobody has taken for this long, with none waiting, is not asked about again
        "refetch_hours": 6,                                 a version still moving is asked about at most this often
        "pace_seconds": 1.0,                                pause between requests to Teslascope
        "my_car": {"model": "modely", "hw": "TeslaAP4"},    Teslascope's car type of your car (Model Y L is "modely"): the page opens on it
        "fsd_countries": {"us": {"hw4": true, "hw3": "lite"}, "nl": {"hw4": true, "hw3": false}, ...}
                                                            the countries where FSD (Supervised) is approved (a tick on notanfsdtracker.com), with what each has: hw4 true = FSD
                                                            (Supervised) on an AI4 car ("Yes"), hw3 "lite" = FSD v14 Lite on an AI3 car (anything but "No" / "-"), false = none.
                                                            FSD is shown for a region only from cars and notes of countries that have it for that computer: a car Teslascope
                                                            puts in any other country (2 cars of Ukraine on 2026.38.5.11) is ignored for FSD. A list of codes means hw4 only.
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
    # FSD (Supervised) approved, from the ticks of notanfsdtracker.com (11 Nov 2026): hw4 = its HW4 column is "Yes", hw3 = "lite" when its HW3 column is not "No" / "-" (v14 Lite).
    # Latvia is approved but its HW4 column is "-" (rollout not started), so it has no FSD yet. Add a country here when it gets a tick, or set "fsd_countries" in config.json.
    "fsd_countries": dict(
        {c: {"hw4": True, "hw3": False} for c in ("mx", "pr", "cn", "au", "nz", "nl", "lt", "ee", "dk", "be", "si", "cz", "hr", "sk", "fi")},
        us={"hw4": True, "hw3": "lite"}, ca={"hw4": True, "hw3": "lite"}, kr={"hw4": True, "hw3": "lite"}, lv={"hw4": False, "hw3": False}),
    "fsd_ai4_from": 13,
    "fsd_hw_share_pct": 5,
    "settle_days": 14,
    "refetch_hours": 6,
    "pace_seconds": 1.0,
    "car_check_minutes": 30,
    "my_car": {"model": "modely", "hw": "TeslaAP4"},
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
