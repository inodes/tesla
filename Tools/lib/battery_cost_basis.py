"""lib/battery_cost_basis.py - what a kWh in the battery cost, at any moment, from the car's own charges.

Every charge is known (kWh added, what it cost, the battery level before it) and so is every drive (kWh used). The energy in the battery is
priced by the weighted average of what was paid for it: a charge mixes its kWh, at its real cost, into the energy already in the pack
(battery level before it x the pack's size, at the average so far); a drive uses its energy at the average at that time. Nothing is
assumed: the pack's size is the battery health reading in force at the time (it changes as the battery ages; the charges' own kWh per % only
when there is no health reading at all), and a drive has no price (None) until the energy from before the first charges
is mostly replaced by charges whose cost is known.

    BatteryCostBasis(tessie_dir).rate_at(epoch)  ->  $/kWh in the battery at that moment, or None
    BatteryCostBasis(tessie_dir).drive_cost(epoch, kwh_used)  ->  $ or None

The cost of a charge is the charge book's (invoice or real tariff) when the Charges page has built it, else the cost on the charge record.
Charges the charge book has only from an invoice (Tessie never saw them) have no battery level and are not mixed in; `skipped` counts them.
"""
import bisect
import csv
import json
import os
import statistics


def _f(x):
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def pack_kwh(charges):
    """The pack's usable size, from the charges: kWh added per % of battery gained, over the charges big enough to measure (>= 20 points).
    None when there are none."""
    ests = [c["kwh"] / (c["b1"] - c["b0"]) * 100 for c in charges if c["b1"] - c["b0"] >= 20 and c["kwh"] > 0]
    return statistics.median(ests) if ests else None


UNKNOWN_MAX = 0.10          # no price while more than this share of the pack is energy whose cost is not known (before the first charges, or gaps)


def capacity_series(tessie_dir):
    """[(epoch, usable kWh)] from the battery health readings (battery_health_log.json and any exported history), oldest first; [] when none."""
    try:
        from lib.battery_health import load_battery_health_history
        from lib.timezones import epoch_for_naive_local
        import datetime
        hist = load_battery_health_history(tessie_dir) or []
        out = [(epoch_for_naive_local(datetime.datetime.fromisoformat(h["synced_at"])), float(h["capacity_kwh"])) for h in hist if h.get("capacity_kwh")]
    except Exception:
        return []
    return sorted(out)


def pack_at(series, epoch, fallback=None):
    """The pack's usable kWh at that moment: the latest health reading before it (the first one when it is earlier than all of them); `fallback` with none."""
    if not series:
        return fallback
    i = bisect.bisect_right([t for t, _ in series], epoch) - 1
    return series[max(i, 0)][1]


def build_steps(charges, pack):
    """[(epoch, average $/kWh of the energy in the battery whose cost is known, or None while too much of it is unknown)], one per charge, oldest first.
    `charges` are dicts with t0, kwh, cost, b0 (% before). `pack` is a number
    (kWh) or a function of the charge's epoch. The energy in the pack when the first charge began has no known cost; later charges
    replace it (drives use known and unknown energy alike), so the price appears once it is mostly gone."""
    steps, known_kwh, known_usd, unknown = [], 0.0, 0.0, 0.0
    first = True
    for c in sorted(charges, key=lambda c: c["t0"]):
        if c["kwh"] <= 0 or c["cost"] is None:
            continue
        size = pack(c["t0"]) if callable(pack) else pack
        before = size * c["b0"] / 100.0                    # the pack's energy when this charge began
        if first:
            unknown, first = before, False
        else:
            have = known_kwh + unknown
            if have > 0 and before < have:                  # drives and idling used some: known and unknown energy shrink together
                k = before / have
                known_kwh, known_usd, unknown = known_kwh * k, known_usd * k, unknown * k
            elif before > have:                             # more energy than the charges account for: the rest is of unknown cost
                unknown += before - have
        known_kwh += c["kwh"]
        known_usd += c["cost"]
        total = known_kwh + unknown
        steps.append((c["t0"], known_usd / known_kwh if unknown / total <= UNKNOWN_MAX else None))
    return steps


class BatteryCostBasis:
    def __init__(self, tessie_dir):
        self.dir, self.skipped = tessie_dir, 0
        ch = self._charges()
        self.capacity = capacity_series(tessie_dir)
        derived = pack_kwh(ch)                              # only used with no health reading at all
        self.pack = pack_at(self.capacity, 10 ** 12, derived)           # the latest size
        self.steps = build_steps(ch, lambda t: pack_at(self.capacity, t, derived)) if self.pack else []
        self._t = [t for t, _ in self.steps]

    def _book_costs(self):
        """{charge record id: its cost} from the charge book: a plug-in's cost shared over its records by kWh."""
        try:
            with open(os.path.join(self.dir, "charges", "app_chargebook.json"), encoding="utf-8") as fh:
                book = json.load(fh)
        except (OSError, ValueError, TypeError):
            return {}
        out = {}
        for p in book.get("plugs", []):
            parts = p.get("parts") or []
            if p.get("invoice_only"):
                self.skipped += 1
                continue
            if not parts or p.get("cost") is None:
                continue
            tot = sum(_f(x.get("kwh")) or 0 for x in parts)
            for x in parts:
                share = (_f(x.get("kwh")) or 0) / tot if tot else 1.0 / len(parts)
                out[str(x.get("id"))] = float(p["cost"]) * share
        return out

    def _charges(self):
        try:
            with open(os.path.join(self.dir, "charges", "charges_master.csv"), newline="", encoding="utf-8-sig") as fh:
                rows = list(csv.DictReader(fh))
        except OSError:
            return []
        book = self._book_costs()
        out = []
        for r in rows:
            t0, kwh = _f(r.get("Started At (UTC)")), _f(r.get("Energy Added (kWh)"))
            b0, b1 = _f(r.get("Starting Battery (%)")), _f(r.get("Ending Battery (%)"))
            if t0 is None or kwh is None or b0 is None or b1 is None:
                continue
            cost = book.get(str(int(t0)))
            if cost is None:
                cost = _f(r.get("Cost"))
            out.append(dict(t0=int(t0), kwh=kwh, cost=cost, b0=b0, b1=b1))
        return out

    def rate_at(self, epoch):
        """$/kWh in the battery at that moment (the average after the latest charge before it); None before the first charge."""
        i = bisect.bisect_right(self._t, epoch) - 1
        return self.steps[i][1] if i >= 0 else None

    def drive_cost(self, epoch, kwh_used):
        r = self.rate_at(epoch)
        return None if r is None or kwh_used is None or kwh_used <= 0 else kwh_used * r


_KEPT = {}


def for_dir(tessie_dir):
    """The basis for a data folder, kept until its charge files change."""
    keys = []
    for f in ("charges/charges_master.csv", "charges/app_chargebook.json"):
        try:
            keys.append(os.stat(os.path.join(tessie_dir, f)).st_mtime_ns)
        except OSError:
            keys.append(0)
    hit = _KEPT.get(tessie_dir)
    if hit and hit[0] == keys:
        return hit[1]
    b = BatteryCostBasis(tessie_dir)
    _KEPT[tessie_dir] = (keys, b)
    return b
