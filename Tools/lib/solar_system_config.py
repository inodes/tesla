"""
solar_system_config.py - REQ-150: shared loader for the real solar-
install/panel-geometry data (install_date, net_upfront_cost_aud,
panel_groups, west_obstruction, ...), extracted out of config.json's
own 'solar' block (which had grown from a handful of ROI-calculator
fields, REQ-121, into real physical-geometry/shading documentation,
REQ-127/128/129 - too much for a secrets-bearing settings file) into
its own real data file, Solar/solar_system.json, a sibling of AGL/,
PVOutput/, SolarEdge/ under the active power provider's data home -
never inside config.json and never inside this repo (see AGENTS.md
REQ-034 for the same PII rule every other real per-domain data file
already follows).

There is no providers_solar/ package here, deliberately - per the
convention established at TODO-035/REQ-139, a domain only gets its
own provider package once more than one real interchangeable
implementation exists for it, and there has only ever been one solar
installation to describe.
"""
import json
import os


def resolve_solar_system_path(data_home=None):
    """Path to the real solar_system.json - data_home defaults to the
    general Tesla/Tessie data home (resolve_tessie_data_home(), the
    same root AGL/, PVOutput/, SolarEdge/ all sit under as siblings),
    NOT the active power provider's own resolve_data_home() (which
    points at that provider's own subfolder, e.g. AGL/ - a different,
    narrower thing)."""
    if data_home is None:
        from lib.tessie_api_common import resolve_tessie_data_home
        data_home = resolve_tessie_data_home()
    return os.path.join(data_home, "Solar", "solar_system.json")


def load_solar_system_config(data_home=None):
    """The real solar-install dict (install_date, net_upfront_cost_aud,
    panel_groups, west_obstruction, ...) - raises a clear, actionable
    FileNotFoundError if it hasn't been set up yet, rather than a bare
    KeyError deep inside some caller. See Solar/solar_system.example.
    json in the repo for the full field shape/meaning."""
    path = resolve_solar_system_path(data_home)
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"{path} not found. This file holds real household install data and is never "
            f"committed to the repo (see AGENTS.md REQ-034) - copy Solar/solar_system.example."
            f"json's shape there and fill in your own install_date, net_upfront_cost_aud, "
            f"lifetime_years (see power_roi_report.py) and, optionally, the richer panel_groups/"
            f"west_obstruction geometry fields (see lib/shading_model.py)."
        )
    with open(path) as f:
        return json.load(f)
