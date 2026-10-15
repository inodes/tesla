#!/usr/bin/env python3
"""
shading_model.py - REQ-129: quantifies real-world shading loss (the new
double-storey duplex to the west, built "within the last 2 years" per
the user) by comparing actual SolarEdge intraday production (REQ-127's
pv_intraday cache - full 15-minute history) against a pvlib clear-sky
model of what an UNOBSTRUCTED array at this exact site/geometry should
produce, in a sun-geometry-defined afternoon window where a western
obstruction could plausibly matter (low sun, western sky) - not a
fixed clock-time window, so the same physical window is compared
across seasons.

Method (deliberately simple, and its assumptions are stated so the
user can judge how much to trust it - this is NOT a calibrated
irradiance model, it's a same-day self-calibrated comparison):
  1. For each day, compute a per-day scale factor between real
     production and modeled clear-sky POA over a late-MORNING
     reference window (10am-1pm) - this both converts modeled
     irradiance units into this system's real kWh units AND soaks up
     that day's own cloud cover, panel temperature derating, etc, all
     the ordinary reasons real production undershoots an idealized
     clear-sky model even with zero obstruction.
  2. Only trust days where that morning window's own interval-level
     ratios are low-variance (coefficient of variation <= 0.20) - a
     proxy for "this morning was genuinely clear", since an inconsistent
     ratio means clouds were moving through and the scale factor isn't
     trustworthy for extrapolating into the afternoon.
  3. Apply that SAME day's scale factor to the modeled POA in a
     GEOMETRY-defined afternoon window (sun azimuth >=240deg - i.e.
     west of due south - AND elevation between 5-30deg - low enough a
     nearby 2-storey structure could plausibly block it, high enough
     to still be a meaningful production period) to get "what this
     specific day's clear-sky-calibrated model expects" for that
     window, then compares it to what was ACTUALLY produced. The gap
     (residual_kwh) is production lost to something the morning-based
     model didn't already explain - obstruction is the leading
     candidate for the WEST window specifically, since it's the only
     thing here that should have a "before 2024" vs "after 2024"
     regime, not weather.
  4. Comparing the SAME calendar month across years controls for the
     one thing this simple model can't itself control - the very real
     seasonal difference in low-sun-angle atmospheric losses that
     exists every year regardless of the duplex (this is why the raw
     residual_pct time series alone still LOOKS strongly seasonal even
     in years before the duplex existed - see AGENTS.local.md REQ-129).

Known limitations, stated plainly rather than glossed over:
  - No real local irradiance/turbidity data - pvlib's Ineichen model
    with its bundled Linke turbidity climatology is a reasonable
    clear-sky approximation, not a measurement, so residual_kwh's
    ABSOLUTE value is not to be trusted as "kWh definitely lost to the
    duplex" - only the BEFORE/AFTER (or month-matched before/after)
    DELTA is meaningful, since whatever systematic model bias exists
    should be common to both periods.
  - SolarEdge's per-panel/optimizer data isn't available on this API
    key (confirmed access-denied, see REQ-125/127 history), so this
    works on whole-SITE production only - it cannot separate the
    duplex's effect on the rear-row panels from the small, separate,
    constant front-tree shading the user also mentioned (which,
    because it's constant over the whole history rather than a 2024
    step-change, should NOT show up in a before/after comparison the
    way the duplex does - but it does mean this model's baseline,
    pre-duplex residual is not "zero shading", it's "whatever the tree
    already cost, plus ordinary model error").
  - "Clear morning" is a proxy from THAT day's own morning shape, not
    a true irradiance measurement - some cloudy-afternoon-only days
    will still leak into the result as noise. This is why the
    month-matched comparison (many days, both directions of noise
    averaging out) is the trustworthy output, not any single day.
"""

import os
import sys

import numpy as np
import pandas as pd
import pvlib

TZ = "Australia/Sydney"

MORNING_HOURS = (10, 13)          # calibration reference window (local clock hours)
WEST_LOW_AZIMUTH_MIN = 240         # pvlib convention: 0=N,90=E,180=S,270=W
WEST_LOW_ELEVATION_RANGE = (5, 30)  # degrees


def panel_groups_from_config(solar_config=None):
    """[(tilt_deg, surface_azimuth_deg, panel_count), ...] from
    Solar/solar_system.json's panel_groups (REQ-127/128, moved out of
    config.json's old 'solar' block by REQ-150 - see lib/
    solar_system_config.py), converting the stored
    'azimuth_deg_from_true_north' (positive=east, negative=west) into
    pvlib's clockwise-from-north surface_azimuth convention.
    solar_config defaults to load_solar_system_config() if not given."""
    if solar_config is None:
        from lib.solar_system_config import load_solar_system_config
        solar_config = load_solar_system_config()
    groups = []
    for g in solar_config.get("panel_groups", []):
        tilt = g.get("tilt_deg")
        offset = g.get("azimuth_deg_from_true_north", 0) or 0
        count = g.get("panel_count", 0)
        if tilt is None or not count:
            continue
        surface_azimuth = (360 + offset) % 360
        groups.append((tilt, surface_azimuth, count))
    return groups


def build_clearsky_poa_series(lat, lon, groups, start, end, freq="15min"):
    """DataFrame indexed by local (Australia/Sydney) timestamp with
    modeled_poa (panel-count-weighted clear-sky plane-of-array
    irradiance, W/m^2, averaged per-panel across every group) plus the
    raw sun_azimuth/sun_elevation pvlib already computed, reused below
    to define the geometry-based afternoon window."""
    times = pd.date_range(start, end, freq=freq, tz=TZ, inclusive="left")
    solpos = pvlib.solarposition.get_solarposition(times, lat, lon)
    linke = pvlib.clearsky.lookup_linke_turbidity(times, lat, lon)
    airmass = pvlib.atmosphere.get_relative_airmass(solpos["apparent_zenith"])
    cs = pvlib.clearsky.ineichen(solpos["apparent_zenith"], airmass, linke)

    total_panels = sum(c for _, _, c in groups)
    weighted_poa = pd.Series(0.0, index=times)
    for tilt, surface_azimuth, count in groups:
        poa = pvlib.irradiance.get_total_irradiance(
            surface_tilt=tilt, surface_azimuth=surface_azimuth,
            solar_zenith=solpos["apparent_zenith"], solar_azimuth=solpos["azimuth"],
            dni=cs["dni"], ghi=cs["ghi"], dhi=cs["dhi"],
        )["poa_global"].fillna(0)
        weighted_poa += poa * count
    weighted_poa /= total_panels

    return pd.DataFrame({
        "modeled_poa": weighted_poa,
        "sun_azimuth": solpos["azimuth"],
        "sun_elevation": solpos["apparent_elevation"],
    })


def compute_daily_residuals(clearsky_df, actual_kwh_series):
    """actual_kwh_series: a Series of production_kwh indexed the same
    way as clearsky_df (Australia/Sydney tz-aware 15-min timestamps).
    Returns one row per usable ('clear morning') day - see module
    docstring for the method and its limitations."""
    df = clearsky_df.join(actual_kwh_series.rename("production_kwh"), how="left")
    df["production_kwh"] = df["production_kwh"].fillna(0.0)
    df["date"] = df.index.date

    results = []
    for day, day_df in df.groupby("date"):
        morn = day_df[(day_df.index.hour >= MORNING_HOURS[0]) & (day_df.index.hour < MORNING_HOURS[1])]
        aft = day_df[
            (day_df["sun_azimuth"] >= WEST_LOW_AZIMUTH_MIN)
            & (day_df["sun_elevation"] >= WEST_LOW_ELEVATION_RANGE[0])
            & (day_df["sun_elevation"] <= WEST_LOW_ELEVATION_RANGE[1])
        ]
        if morn["modeled_poa"].sum() <= 0 or len(morn) < 8 or len(aft) < 2:
            continue
        ratios = (morn["production_kwh"] / morn["modeled_poa"].replace(0, np.nan)).dropna()
        if len(ratios) < 6:
            continue
        mean_r, std_r = ratios.mean(), ratios.std()
        if mean_r <= 0 or np.isnan(std_r):
            continue
        cov = std_r / mean_r
        calibration = morn["production_kwh"].sum() / morn["modeled_poa"].sum()
        if calibration <= 0 or cov > 0.20:
            continue
        aft_modeled_kwh = calibration * aft["modeled_poa"].sum()
        aft_actual_kwh = aft["production_kwh"].sum()
        residual_kwh = aft_modeled_kwh - aft_actual_kwh
        results.append({
            "date": pd.Timestamp(day), "calibration": calibration, "cov": cov,
            "n_aft_intervals": len(aft),
            "aft_modeled_kwh": aft_modeled_kwh, "aft_actual_kwh": aft_actual_kwh,
            "residual_kwh": residual_kwh,
            "residual_pct": residual_kwh / aft_modeled_kwh if aft_modeled_kwh > 0 else np.nan,
        })
    return pd.DataFrame(results)
