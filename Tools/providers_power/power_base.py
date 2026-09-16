"""
power_base.py - BasePowerProvider: the interface a power retailer/
distributor implementation must provide, so the generic power_*.py CLI
scripts (Tools/) never hardcode one retailer's billing rules.

Why this is shaped differently from Tools/providers_chargers/'s BaseChargerProvider
(REQ-063): chargers are queried MANY-at-once and merged (every network
that might have a station near you); a household only ever has ONE power
retailer/distributor combination active at a time, so there's no
"manager" that merges providers here - providers_power/__init__.py's
get_power_provider() just resolves config.json's chosen provider (or a
caller override) and returns ONE instance.

Two genuinely different real-world axes are bundled into one provider
class for now: the RETAILER's own tariff/solar-credit rules (what you're
charged per kWh, how solar export is tiered) and the local DISTRIBUTOR's
network tariff (e.g. Ausgrid's EA025 demand charge, in AGL's case) are
independent in reality - switching retailer doesn't change your
distributor, which is tied to your address, not your choice of biller.
They're kept together here because there has only ever been one real
combination to model (AGL + Ausgrid). A future provider whose distributor
genuinely differs should split this into two separate lookups rather than
stretching this one class further - don't build that split speculatively
before there's a second real data point.

REQ-069/AGENTS.md 3.1: this file has no argparse/__main__ - it's a
library module living in Tools/providers_power/ rather than Tools/lib/
because it's specifically an ABSTRACT INTERFACE for interchangeable
power-provider implementations, the same "provider adapter" category
Tools/providers_chargers/'s BaseChargerProvider occupies for chargers - just a
separate package, since the two domains share no real code and
AGENTS.md's own existing description of Tools/providers_chargers/ is written
specifically in terms of charging networks.
"""

import os


MASTER_FIELDS = ["StartDate", "EndDate", "RegisterCode", "Role", "kWh", "SourceFile"]


class BasePowerProvider:
    """A power provider's own `name` (lowercase, e.g. "agl") is both its
    registry key (providers_power/__init__.py's get_power_provider()) and
    the value a real tariff-rates JSON file's own top-level "provider"
    field must match - see load_rate_periods_from_file() below."""

    name = "base"
    display_name = "Base"

    # REQ-071 (2026-09-15): True if this provider's usage-export data
    # requires a manual step to refresh (e.g. AGL: no history API for
    # residential accounts, a CSV downloaded by hand from its "My Usage"
    # portal - see sync_power_usage.py's own module docstring) rather
    # than an automated sync a script can just run. Drives
    # sync_power_usage.py's post-consolidate staleness reminder - a
    # provider whose usage data syncs automatically (like this repo's
    # Evnex/PVOutput integrations already do) never goes stale in the
    # same way, so the default here is False and only a genuinely manual
    # provider overrides it.
    manual_usage_sync = False

    # ------------------------------------------------------------------
    # Paths - config.json-driven, with a generic key checked before a
    # provider-specific legacy key, so an existing agl_directory-style
    # config keeps working unchanged while new configs can use the
    # generic power_directory key regardless of which provider is active.
    # ------------------------------------------------------------------

    def config_directory_keys(self):
        """(generic_key, legacy_key, ...) - config.json keys checked in
        order for this provider's DERIVED data home (usage master,
        tariff rates, demand-charge output)."""
        raise NotImplementedError

    def config_invoices_directory_keys(self):
        """Same idea, for where this provider's RAW invoice PDFs/usage-
        export CSVs live - kept separate from the derived data home
        deliberately, same reasoning as resolve_agl_invoices_dir() always
        had: raw source documents are the household's general PDF
        archive, not Tesla-repo-managed derived data."""
        raise NotImplementedError

    def default_data_home_name(self):
        """Fallback directory name (sibling of the Tessie/ data folder)
        when no config.json override is set for either key above."""
        raise NotImplementedError

    def default_invoices_home_name(self):
        raise NotImplementedError

    def resolve_data_home(self):
        from lib.tessie_api_common import load_config
        cfg = load_config()
        for key in self.config_directory_keys():
            val = cfg.get(key)
            if val:
                return os.path.abspath(os.path.expanduser(val))
        return os.path.expanduser(
            f"~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/{self.default_data_home_name()}"
        )

    def resolve_invoices_dir(self):
        from lib.tessie_api_common import load_config
        cfg = load_config()
        for key in self.config_invoices_directory_keys():
            val = cfg.get(key)
            if val:
                return os.path.abspath(os.path.expanduser(val))
        return os.path.expanduser(
            f"~/Library/Mobile Documents/com~apple~CloudDocs/PDF/{self.default_invoices_home_name()}"
        )

    def usage_master_filename(self):
        """Filename (not path) of this provider's consolidated usage
        master CSV, inside resolve_data_home()/usage/."""
        raise NotImplementedError

    def tariff_rates_filename(self):
        """Filename (not path) of this provider's real, gitignored,
        per-account tariff-rates JSON, inside resolve_data_home()."""
        raise NotImplementedError

    def tariff_rates_example_path(self):
        """Repo-relative path to the committed, sanitized TEMPLATE for
        this provider's tariff-rates JSON (never real data) - shown in
        error messages when the real file is missing."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Usage-export ingestion - retailer-specific manual-export format.
    # ------------------------------------------------------------------

    def is_usage_export_filename(self, filename):
        """True if `filename` (basename only) looks like one of this
        provider's own manually-downloaded usage-export files."""
        raise NotImplementedError

    def parse_datetime(self, s):
        """Parses this provider's own usage-export timestamp text into a
        naive local-time datetime()."""
        raise NotImplementedError

    def register_role(self, register_code):
        """This provider's own register-code -> canonical role mapping
        ("import" / "solar_export" / "controlled_load" / ... - the
        canonical vocabulary the shared usage-master CSV schema uses,
        so every downstream consumer can rely on Role regardless of
        which provider actually produced the row)."""
        raise NotImplementedError

    def load_source_rows(self, csv_path):
        """(rows_dict, skipped_count) for one raw usage-export CSV -
        rows_dict keyed by (StartDate, EndDate, RegisterCode), each
        value shaped to MASTER_FIELDS above."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Tariff / billing math - retailer + distributor specific.
    # ------------------------------------------------------------------

    def load_rate_periods(self):
        """Sorted list of this provider's own dated tariff periods, from
        its real tariff-rates JSON (see tariff_rates_filename()) -
        validates the file's own declared "provider"/"plan" self-
        description against this provider before trusting it (see
        load_rate_periods_from_file() below)."""
        raise NotImplementedError

    def rate_for_date(self, d, periods=None):
        """The tariff period in effect on date `d`."""
        raise NotImplementedError

    def in_demand_window(self, dt):
        """True if the half-hour interval starting at local datetime
        `dt` counts toward this provider's distributor's monthly
        demand-peak calculation (season/weekday/holiday rules)."""
        raise NotImplementedError

    def solar_credit(self, total_kwh, days, tier1_rate):
        """(tier1_credit, tier2_credit) ex-GST, for `total_kwh` of solar
        export over `days` days at the period's own tier-1 rate."""
        raise NotImplementedError

    def demand_charge(self, kw_latched, billing_days, rate_per_kw_day):
        """(ex_gst, incl_gst) for a latched demand peak of `kw_latched`
        kW billed over `billing_days` days."""
        raise NotImplementedError


def load_rate_periods_from_file(rates_path, expected_provider, example_path):
    """Shared JSON-loading + provider/plan self-description check, used
    by every provider's own load_rate_periods() so the validation logic
    (and its error messages) live in exactly one place.

    REQ-070: the real tariff-rates JSON is no longer implicitly "whichever
    provider happens to read it" - it now self-describes itself via two
    required top-level fields, "provider" (e.g. "AGL") and "plan" (e.g.
    "Solar VIP"), checked here against `expected_provider` (case-
    insensitive) so a rates file left over from a retailer you've since
    switched away from is a loud, immediate error instead of silently
    producing wrong bill totals under the wrong provider's code."""
    import json
    from datetime import datetime

    if not os.path.isfile(rates_path):
        raise FileNotFoundError(
            f"{os.path.basename(rates_path)} not found at {rates_path}. This file "
            f"holds real account tariff data and is never committed to the repo "
            f"(see AGENTS.md REQ-034) - copy your own rates file there, starting "
            f"from {example_path}'s schema."
        )
    with open(rates_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    file_provider = data.get("provider")
    if not file_provider:
        raise ValueError(
            f"{rates_path} has no top-level \"provider\" field (REQ-070 - every "
            f"tariff-rates file now self-describes which retailer it's for). "
            f"Add \"provider\": \"{expected_provider.upper()}\" (and \"plan\": "
            f"\"<your plan name>\") at the top level - see {example_path}."
        )
    if file_provider.strip().lower() != expected_provider.strip().lower():
        raise ValueError(
            f"{rates_path} declares provider \"{file_provider}\", but the active "
            f"power provider is \"{expected_provider}\" (config.json's "
            f"\"power_provider\", default \"agl\") - refusing to load a rates file "
            f"for the wrong retailer rather than silently apply the wrong tariff "
            f"rules. Either switch providers back, or replace this file with a "
            f"real {expected_provider.upper()} rates file."
        )

    periods = sorted(data["periods"], key=lambda p: p["effective_from"])
    for p in periods:
        p["_effective_from_date"] = datetime.strptime(p["effective_from"], "%Y-%m-%d").date()
    return periods, data.get("plan")
