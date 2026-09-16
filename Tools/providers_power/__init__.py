"""
providers_power - pluggable power-retailer/distributor billing providers.

REQ-070 (2026-09-15): mirrors Tools/providers_chargers/'s BaseChargerProvider
pattern (REQ-063) for household electricity billing rather than EV
charger discovery - but shaped differently, since only ONE power
provider is ever active for a household at a time (unlike chargers,
where many networks are queried together and merged). There's no
"manager" here, just a factory (get_power_provider()) that resolves
config.json's chosen provider - or a caller override - to one provider
instance.

Extracted from what used to be agl_common.py/agl_cost_calculator.py/
agl_demand_calculator.py/agl_usage_sync.py's AGL-specific logic
(power_base.py's BasePowerProvider interface + power_agl.py's
AGLProvider, the first and currently only implementation) so the
generic power_*.py CLI scripts in Tools/ never hardcode one retailer's
billing rules - see power_base.py's own docstring for the full
reasoning, including why the retailer (tariff/solar-credit rules) and
distributor (demand-charge window rules) are kept together in one
provider class for now.
"""

from .power_base import BasePowerProvider, MASTER_FIELDS, load_rate_periods_from_file
from .power_agl import AGLProvider

_PROVIDERS = {
    "agl": AGLProvider,
}


def get_power_provider(name=None):
    """Returns an instance of the active power provider. `name`
    overrides config.json's own "power_provider" key, which defaults to
    "agl" (the only implementation today, and this household's real
    current retailer)."""
    if name is None:
        from lib.tessie_api_common import load_config
        cfg = load_config()
        name = cfg.get("power_provider", "agl")
    name = name.lower()
    try:
        cls = _PROVIDERS[name]
    except KeyError:
        raise ValueError(f"Unknown power provider '{name}' - known providers: {sorted(_PROVIDERS)}")
    return cls()


__all__ = ["BasePowerProvider", "AGLProvider", "get_power_provider", "MASTER_FIELDS", "load_rate_periods_from_file"]
