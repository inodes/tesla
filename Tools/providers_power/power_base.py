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

    def config_invoices_directory_path(self):
        """(config_group, config_key) into the nested config.json - where
        this provider's RAW invoice PDFs/usage-export CSVs live, e.g.
        ("directories", "power_invoices") - kept separate from the
        derived data home deliberately, same reasoning as
        resolve_agl_invoices_dir() always had: raw source documents are
        the household's general PDF archive, not Tesla-repo-managed
        derived data. Genuinely optional - see resolve_invoices_dir()."""
        raise NotImplementedError

    def default_data_home_name(self):
        """Fallback directory name (sibling of the Tessie/ data folder)
        when no config.json override is set for either key above."""
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
        """Returns the configured folder, or None if genuinely unset.
        config.json restructure follow-up (2026-09-21, BUG-131): this used
        to fall back to a literal iCloud path duplicated (independently,
        with no shared constant) across this file, sync_invoices.py, and
        lib/invoice_common.py - removed per user request; it's config.json-
        driven only now, and every call site handles a None/missing
        folder gracefully instead of guessing a default."""
        from lib.tessie_api_common import load_config
        cfg = load_config()
        group, key = self.config_invoices_directory_path()
        val = cfg.get(group, {}).get(key)
        if val:
            return os.path.abspath(os.path.expanduser(val))
        return None

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

    def solar_tier_split_kwh(self, cumulative_export_before_kwh, marginal_kwh):
        """(tier1_kwh, tier2_kwh, tier2_rate) - how a SESSION's own
        marginal_kwh of solar export splits across the tier-1/tier-2
        boundary, given cumulative_export_before_kwh already exported
        that same calendar day before this session started. Distinct
        from solar_credit() above (which prices a whole period's total
        export against a fresh days-wide cap) - this is the per-session,
        point-in-time version: the daily cap may already be partly or
        fully used up by earlier export that same day, so a session's
        own kWh can start already past it, straddle it, or sit entirely
        under it. Returns (marginal_kwh, 0.0, None) - i.e. "assume it's
        all tier-1" - for a provider with no tiered feed-in structure at
        all; only override this for one that actually has one (AGL
        does)."""
        return marginal_kwh, 0.0, None

    def demand_charge(self, kw_latched, billing_days, rate_per_kw_day):
        """(ex_gst, incl_gst) for a latched demand peak of `kw_latched`
        kW billed over `billing_days` days."""
        raise NotImplementedError

    def usage_rate_for_window(self, period, local_start, local_end):
        """(blended_rate_ex_gst, breakdown) - the usage rate that applies
        to a session/interval running from local_start to local_end under
        `period`.

        For a flat period (no "tariff_type" key, or anything other than
        "tou") this is just period["usage_rate_per_kwh"] unchanged - every
        historical AGL period, and any future provider/plan with no real
        time-of-use structure, behaves exactly as it always has.

        For a genuine "tou" period (REQ-155, the 2026-09-07 AGL plan
        change), a session can straddle the Peak/Off-peak boundary - it
        may start before 3pm and run past it, for example - so a single
        rate for the whole session would be wrong on either side of that
        boundary. This time-weights the session by wall-clock minute: each
        minute is classified Peak (its month is in period["peak_months"]
        AND its local hour falls in [peak_hour_start, peak_hour_end)) or
        Off-peak, and the returned rate is the duration-weighted blend of
        peak_rate_per_kwh and offpeak_rate_per_kwh. This mirrors the same
        constant-power-draw assumption pv_window_split() already makes for
        solar-fraction blending, so a charge that runs entirely within one
        side of the boundary (the common case) just gets that side's flat
        rate back - the blend only matters for a genuine straddle.

        breakdown is a dict for transparency/debugging: for a flat period,
        {"flat_rate": rate}; for a TOU period, {"peak_fraction",
        "offpeak_fraction", "peak_rate", "offpeak_rate"}."""
        if period.get("tariff_type") != "tou":
            rate = period["usage_rate_per_kwh"]
            return rate, {"flat_rate": rate}

        from datetime import timedelta

        peak_months = set(period["peak_months"])
        peak_start_h = period["peak_hour_start"]
        peak_end_h = period["peak_hour_end"]
        peak_rate = period["peak_rate_per_kwh"]
        offpeak_rate = period["offpeak_rate_per_kwh"]

        def _is_peak(dt):
            return dt.month in peak_months and peak_start_h <= dt.hour < peak_end_h

        total_seconds = (local_end - local_start).total_seconds()
        if total_seconds <= 0:
            is_peak = _is_peak(local_start)
            rate = peak_rate if is_peak else offpeak_rate
            return rate, {
                "peak_fraction": 1.0 if is_peak else 0.0,
                "offpeak_fraction": 0.0 if is_peak else 1.0,
                "peak_rate": peak_rate,
                "offpeak_rate": offpeak_rate,
            }

        step = timedelta(minutes=1)
        peak_seconds = 0.0
        t = local_start
        while t < local_end:
            seg_end = min(t + step, local_end)
            seg_seconds = (seg_end - t).total_seconds()
            if _is_peak(t):
                peak_seconds += seg_seconds
            t = seg_end

        peak_fraction = peak_seconds / total_seconds
        offpeak_fraction = 1.0 - peak_fraction
        blended = peak_fraction * peak_rate + offpeak_fraction * offpeak_rate
        return blended, {
            "peak_fraction": peak_fraction,
            "offpeak_fraction": offpeak_fraction,
            "peak_rate": peak_rate,
            "offpeak_rate": offpeak_rate,
        }


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
    from datetime import date, datetime, timedelta

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

 # REQ-141 (2026-09-25): each period's own effective END date, computed
 # here rather than trusted blindly from whatever "effective_to" string
 # (if any) is sitting in the JSON - every period but the last runs up
 # to the day before the NEXT period's effective_from (by construction,
 # this can never leave a gap or an overlap between adjacent periods);
 # the LAST period (latest effective_from) has no known end yet, so its
 # effective_to is "now" - date.today(), not a fixed stored date, per
 # the user's own framing ("with the latest rates effective_to is
 # always now()") - recomputed fresh every time this file is read, so
 # it never needs a write just because a day passed.
 #
 # This turns rate_for_date() from "the LAST period sorted by
 # effective_from that's <= d" (order-dependent - silently wrong if a
 # later-dated but INCOMPLETE period ever gets inserted between two
 # correct ones, exactly the real bug found in REQ-141: several
 # auto-proposed periods sourced from GAS bills, not electricity ones,
 # sat between real dated electricity periods and silently won every
 # lookup for their date range) into an explicit, self-consistent range
 # check - a period is either the unambiguous match for date `d` or it
 # isn't, regardless of how many other periods exist or what order
 # they were inserted in.
    is_last = len(periods) - 1
    for i, p in enumerate(periods):
        if i < is_last:
            p["_effective_to_date"] = periods[i + 1]["_effective_from_date"] - timedelta(days=1)
 # Persisted human-readable mirror of the computed value above -
 # never the source of truth for lookups (see the comment above), so
 # the real JSON file itself is legible without running code.
            p["effective_to"] = p["_effective_to_date"].isoformat()
        else:
            p["_effective_to_date"] = date.today()
            p["effective_to"] = None  # still current - "now", not a fixed date (see comment above)
    return periods, data.get("plan")
