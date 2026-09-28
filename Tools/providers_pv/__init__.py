"""
providers_pv - REQ-127: pluggable PV-monitoring providers (SolarEdge
today; PVOutput, or a future different SolarEdge site, could join
later) - mirrors providers_power/__init__.py's own factory shape (see
pv_base.py's docstring for the full reasoning).
"""

from .pv_base import BasePvProvider, PV_FIELDS
from .pv_solaredge import SolarEdgeProvider

_PROVIDERS = {
    "solaredge": SolarEdgeProvider,
}


def get_pv_provider(name=None):
    """Returns an instance of the active PV provider. `name` overrides
    config.json's own `pv.provider` key, which defaults to "solaredge"
    (the user's real current system, and the only implementation
    today - see REQ-127)."""
    if name is None:
        from lib.tessie_api_common import load_config
        cfg = load_config()
        name = cfg.get("pv", {}).get("provider", "solaredge")
    name = name.lower()
    try:
        cls = _PROVIDERS[name]
    except KeyError:
        raise ValueError(f"Unknown PV provider '{name}' - known providers: {sorted(_PROVIDERS)}")
    return cls()


__all__ = ["BasePvProvider", "PV_FIELDS", "SolarEdgeProvider", "get_pv_provider"]
