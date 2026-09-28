#!/usr/bin/env python3
"""
pv_base.py - REQ-127: BasePvProvider, the interface a solar-monitoring
provider implementation must supply, so the local PV history cache
(lib/pv_history_db.py) and any report built on it never hardcodes one
monitoring vendor's API. Mirrors providers_power/power_base.py's own
"one active provider at a time, resolved by a factory" shape
deliberately (see that file's docstring) - a household has one PV
monitoring account active at a time, same as one power retailer.

Why this exists at all: the user has PVOutput (rate-limited to 50
days/call regardless of tier - REQ-124) AND, as of REQ-125/127, a
direct SolarEdge Monitoring API key - a genuinely better source (real
SelfConsumption meter, 15-minute granularity, 1-year-per-call daily
cap). The user asked for this to be "flexible to move to different
system in future" rather than hardcoding SolarEdge - e.g. a future
Fronius/Enphase/Growatt system, or simply a different SolarEdge site -
so every PV-specific API call lives in a provider subclass, and every
caller goes through get_pv_provider() (providers_pv/__init__.py)
instead of importing lib/solaredge_common.py directly.

Five real per-interval fields, common to any grid-tied PV+consumption
setup (confirmed real on SolarEdge's own energyDetails response -
Production, Consumption, FeedIn, Purchased, SelfConsumption): a future
provider that can't report all five (e.g. no consumption CT clamp
installed) simply returns None for the ones it can't supply, exactly
like lib/solaredge_common.py's fetch_energy_details_range() already
does for a day with a null meter value.
"""

PV_FIELDS = ("production_kwh", "consumption_kwh", "feedin_kwh", "purchased_kwh", "self_consumption_kwh")


class BasePvProvider:
    """A PV provider's own `name` (lowercase, e.g. "solaredge") is both
    its registry key (providers_pv/__init__.py's get_pv_provider()) and
    the config.json `pv.provider` value that selects it."""

    name = "base"
    display_name = "Base"

 # Real per-call day caps this provider's API enforces, for the two
 # granularities every provider must support - a subclass sets these
 # to whatever it confirms live (see pv_solaredge.py's REQ-125/127
 # comments for how SolarEdge's own two limits were confirmed).
    max_daily_days_per_call = None
    max_intraday_days_per_call = None
    intraday_interval_minutes = None

    def resolve_credentials(self, config):
        """(api_key_or_similar, account_or_site_id) from config.json,
        or (None, None) if unset - a caller decides how to report a
        missing credential, this just resolves it."""
        raise NotImplementedError

    def install_date(self, api_key, account_id):
        """The provider's own record of when this system started
        reporting data (e.g. SolarEdge's site `details.installationDate`)
        - a real fact from the provider, not a guess, distinct from
        config.json's own solar.install_date (REQ-121), which is the
        CONTRACT/commissioning date a human recorded by hand. The two
        should agree but aren't guaranteed to - a caller that cares
        compares them rather than assuming."""
        raise NotImplementedError

    def fetch_daily_range(self, date_from, date_to, api_key, account_id):
        """[{"day": date_obj, **PV_FIELDS}, ...] - one row per day.
        Returns (rows, error_or_None); chunks internally at this
        provider's own max_daily_days_per_call."""
        raise NotImplementedError

    def fetch_intraday_range(self, date_from, date_to, api_key, account_id):
        """[{"timestamp": datetime_obj, **PV_FIELDS}, ...] - one row
        per this provider's intraday_interval_minutes (e.g. SolarEdge:
        15). Returns (rows, error_or_None); chunks internally at this
        provider's own max_intraday_days_per_call (typically much
        smaller than the daily cap - confirmed real on SolarEdge: one
        MONTH per call for 15-minute data, one YEAR for daily)."""
        raise NotImplementedError
