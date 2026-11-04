# Made-up numbers in Tools/ (audit, 2026-11-02)

Rule (owner): every number shown or used in money, energy or range calculations comes from the owner's own data (charges, invoices, drives,
battery health, tariffs in config), never an assumed constant. Where the data does not exist the code returns `None` and the page or report
says "no price" / "unknown", never `0` and never a guess.

Scope: `Tools/lib`, `Tools/commands`, `Tools/providers` (searched for prices, rates, efficiencies, capacities, fuel prices, masses, tax rates and
fallback defaults). Not touched, by instruction: `Tools/lib/app_ui/app.js`, `app_data.py`, `app_drivelist.py`, `app_teslacam.py`,
`Tools/tesla_app.py` (none of them hold such a number; `app.js` already shows any `error` text the range service returns).
Line numbers are the file as it is after this change. Already fixed before this audit: the drive fuel comparison's flat $0.28/kWh EV
price (now `lib/battery_cost_basis.py`).

New shared helper: `Tools/lib/car_figures.py` - the car's usable kWh and full range from the newest battery health reading, and its Wh/km from
`drives_master.csv`; `None` when the data is not there. Tests: `tests/test_no_made_up_numbers.py` (27 tests, made-up data only).

## 1. Fixed

| file:line | value (before) | purpose | verdict | action |
|---|---|---|---|---|
| `Tools/lib/fuel_common.py` (`get_fuel_price_dated`, 2026-11-04) | nearest recorded day's price, however far away, not dated; an unreadable date got the first row stored | fuel price for a date outside the synced range | real data from another date, shown as that date's | refreshed first, then dated: section 5 (decision) |
| `Tools/lib/fuel_common.py:1269` (`get_fuel_price`) | U91 $1.95, P95 $2.10, P98 $2.20, Diesel $2.05, E10 $1.90, LPG $1.10 /L | last resort when the FuelCheck database has nothing | made up | returns `None`; every caller says "no fuel price data" (`NO_FUEL_PRICE`, `:1495`; see the 2026-11-03 caller check below) |
| `Tools/lib/fuel_common.py:1387` (`vehicle_l_per_100km`), `:1426` | `or 8.5` L/100km | comparison car with no consumption figure | made up | `None`; litres and fuel cost `None` |
| `Tools/lib/fuel_common.py:1524`, `format_drive_fuel_comparison` | `${p:.2f}` of the default above | "Ref Fuel Prices" line and each car's row in the drive view | showed the made-up price | "no fuel price data" / "L/100km unknown" |
| `Tools/lib/charging_analyzer.py:4141` | 8.2 L/100km, "petrol_standard", U91 | `print_summary` comparison car before the registry is read | made up | `None` until the vehicle spec gives it |
| `Tools/lib/charging_analyzer.py:2630` (`_ice_equivalent`) | km = Tesla's rated range added, or battery kWh / 0.15 kWh/km; $1.95/L when no fuel price | "Estimated ICE Equivalent" in `analyse charges` and `--compare-all` | 0.15 and 1.95 made up; rated range is Tesla's rated consumption, not this car's | km = battery kWh / the car's real Wh/km over the drives in the window (`_real_wh_per_km`, `:2615`; all logged drives when the window has none); FuelCheck price per charge date; charges without battery kWh are left out of both sides and counted |
| `Tools/lib/charging_analyzer.py:789` (`format_ice_equivalent`) | `$0.00` printed when nothing could be worked out | summary line | a zero standing in for unknown | "no fuel price data", or "unknown (reason)": no L/100km, no drives to turn kWh into km, no charge with battery kWh, a charge with no date |
| `Tools/lib/charging_analyzer.py:4110` | `0.0` $/kWh | "Average Cost" with no metered kWh | zero standing in for unknown | "unknown (no metered kWh)" |
| `Tools/lib/charging_analyzer.py:4172`, `:4333` | `max(0.0, ...)` | net / annual savings | hid a loss as $0 | signed figure (+/-); "unknown" when fuel price or L/100km missing |
| `Tools/commands/analyse_charging.py:304`, `:97` | `8.5`, `0.0` L/100km | `--list-vehicles`, `--gvg-top20`, `--search-vehicles` tables | made up | "-" / "not published" |
| `Tools/commands/sync_fuel_history.py:86`, `:215` | `$1.95/L`, `8.5` L/100km | `--status` reference prices, `--query` 100 km table | made up | "no fuel price data" / "L/100km unknown" |
| `Tools/lib/abrp_common.py:252` (`car_usable_kwh`) | 85.0 kWh (`DEFAULT_USABLE_KWH`, removed) | arrival battery % on routes | made up | config `abrp.usable_kwh`, else newest battery health reading, else `vehicle_spec.json`; `None` otherwise |
| `Tools/lib/abrp_common.py:268` (`car_consumption_wh_km`) | 171.1 Wh/km (`DEFAULT_CONSUMPTION_WH_KM`, removed); GVG x1.15 and WLTP x1.25 uplifts | same | made up (both the constant and the uplift factors) | config `abrp.consumption_wh_km`, else ABRP's calibrated reference for this car, else Wh/km from `drives_master.csv`; `None` otherwise |
| `Tools/lib/abrp_common.py:399` | 90 % departure battery when Tessie is offline or the setting is unreadable | route plans | made up | `None`, labelled "unknown (Tessie offline)"; `charger_find` says to give `--departure-soc` (`Tools/commands/charger_find.py:489`) |
| `Tools/lib/abrp_common.py:410`, `:479` | arithmetic on the defaults; `origin_soc=90` in the ABRP link | arrival % and the ABRP deep link | made up | arrival `None` when a figure is missing; link sent without `origin_soc` (ABRP uses its own) |
| `Tools/lib/station_report_email.py:35` | 90 %, 171.1 Wh/km, 85 kWh | arrival % in the email / table | made up | `None` (shown as "—") |
| `Tools/commands/charger_find.py:1406` | 165.0 Wh/km, 60.0 kWh | station detail "Projected Arrival SoC" | made up (and different from the 171.1 / 85 pair) | values from `load_abrp_config()`; "unknown (...)" when missing |
| `Tools/commands/charger_find.py:1502` | "$2.00/min at 100% occupancy" | Supercharger idle fee note | fixed text whatever the site's fee | 2 x the site's own idle fee (Tesla's published doubling rule) |
| `Tools/lib/app_reach.py:90` (`car`) | 560 km full range, 85 kWh pack | Range view on the Places map | made up | `car_profile.json`, else newest battery health reading; with neither the picture returns an error ("... not known"), which `app.js` already shows |
| `Tools/lib/osm_roads_graph.py:175` (`kwh_per_100km_fit`) | (11.0 kWh/100km, 0.0010) curve with < 3 speed bands; clamps 6.0 and 0.0004 | kWh basis of the Range view | made up | no bands: `None` (kWh view says the consumption is not known, `:232`); 1-2 bands or a non-physical fit: the distance-weighted average of the measured bands |
| `Tools/lib/car_efficiency.py:11` | 2200 kg (`FALLBACK_VEHICLE_MASS_KG`, removed) | grade-corrected kWh/100 km (feeds `car_profile.json` and the Range view) | made up ("a generic round Model Y estimate") | mass from `--vehicle-mass-kg` or `vehicle_spec.json`; without it the grade-corrected figures are `None` / the table is empty and says why |
| `Tools/commands/find_tesla_chargers.py:1286` | idle $1.00/min, congestion $0.50/min | written into `tesla_chargers.json` when Tesla's pricebook has no fee | made up | `None`; detail view shows "unknown" |
| `Tools/providers/chargers/chargers_evx.py:151` | $0.50 / $0.39 day-night table, $0.20/min idle, also as `tessie_cost_config` | EVX registry; the expected-tariff check costs a matched session with it | made up (the EVX feed has no price) | no price stored; rate shows "-"; expected rate `None` |
| `Tools/providers/chargers/chargers_bppulse.py:215` | $0.65 / $0.69 / $0.72 by power; idle $0.00 | bp pulse registry | made up (the feed has no price) | no price stored; idle `None` |
| `Tools/providers/chargers/chargers_chargefox.py:411` | "$0.54 - $0.60/kWh" for NRMA sites | rate text when the feed has none | made up | nothing shown |
| `Tools/providers/chargers/chargers_base.py:559` | `per_kwh_flat: 0.0` for non-flat stations; idle `or 0.0` | `tessie_cost_config` of every unified record | a 0 that makes an unknown-price station cost $0 in `get_expected_tariff_rate` | `None` |
| `Tools/commands/charger_plugshare_auditor.py:397` | `rate` default 0.39 / 0.50 | expected price text | made up | "-" when the schedule has no rate |
| `Tools/commands/power_roi_report.py:69` | 25-year lifetime when `solar_system.json` has none | lifetime ROI | made up (the template calls the field required) | "Lifetime ROI: unknown (no lifetime_years ...)" |
| `Tools/lib/session_cost.py:533`, `:729` | `GST_MULTIPLIER = 1.10` | home charge cost incl. GST | published 10 %, but the tariff file has its own `gst` | uses the power provider's `gst_multiplier()` (the tariff file's value, 10 % when it gives none) |

## 2. Open: needs the owner's decision (not changed)

| file:line | value | purpose | verdict | why not fixed |
|---|---|---|---|---|
| `Tools/lib/fuel_common.py:48` `DEFAULT_COMPARISON_VEHICLES` (22 cars), also seeded into the fuel DB (`:511`) and `resources/comparison_vehicles.example.json` | `consumption` L/100km per car (e.g. CX-8 7.5 / 10.0 / 6.0) | the ICE side of every comparison | other cars' figures: no owner data can exist. Only the CX-8 also has `adr_consumption` (6.0 / 7.0 / 5.5, `:60`), which differs from its `consumption`, so the `consumption` figures look like unsourced "real-world" uplifts | needs a source per car (greenvehicleguide.gov.au) or the owner's own `comparison_vehicles.json`; changing them changes every savings figure |
| `Tools/lib/fuel_common.py:65` (each car) | `running_costs`: service $0.026-0.065/km, tyres $0.017-0.035/km | "total running cost", matrix "Running/km" | unsourced estimates | same three places hold them (code, DB seed, example JSON); removing only the code copy would not remove them |
| `Tools/lib/fuel_common.py:1376` | urban/highway split by average speed: 35 / 85 km/h, 0.95 | weights urban vs highway L/100km | model assumption (ADR 81/02 cycle speeds) | not a price; replacing it needs a different model; `raw_samples` path already uses real speeds |
| `Tools/lib/fuel_common.py:1325`, `Tools/lib/ev_efficiency.py:53` | 70 km/h urban threshold | same split | model setting | candidate for `config.json` (`app` block), not a data stand-in |
| `Tools/lib/abrp_common.py:43` | `tesla:myl:25:88:dual:ccs`, "Tesla Model Y" | ABRP vehicle model when config and ABRP give none | names one car model (ABRP then uses its own range model) | should come from config / vehicle_spec; not a number in our calculations |
| `Tools/providers/chargers/chargers_bppulse.py:209` | 2 stalls, CCS2, 75 kW when the feed lists none | bp pulse hardware | made up, hardware only | not money/energy/range; listed so it is not forgotten |
| `Tools/providers/chargers/chargers_jolt.py:78` | 50 kW | Jolt hardware power | from the feed's image names, per its comment | hardware only |
| `Tools/providers/chargers/chargers_base.py:560` | `per_minute 0.0`, `per_session 0.0` | `tessie_cost_config` | zeros meaning "none charged", not known | no reader uses them in a cost today |
| `Tools/commands/find_plugshare_chargers.py:951` | `idle_fee_per_min` default 0 | PlugShare detail view | a missing fee shown as "None" (no fee) | PlugShare's own data; not in this change |

## 3. Kept: legitimate constants (considered, no change)

| file:line | value | purpose | verdict |
|---|---|---|---|
| `Tools/lib/charging_analyzer.py:98`, `Tools/providers/power/power_base.py:168` | GST 1.10 / 0.10 when the tariff file gives none | GST on rates | published GST 10 % |
| `Tools/commands/price_history_report.py:101` | GST 1.10 | price history in GST-inclusive terms | published GST 10 % |
| `Tools/lib/charging_analyzer.py:3557` | cost / 11 | GST part of a GST-inclusive amount | arithmetic of 10 % GST |
| `Tools/providers/chargers/chargers_base.py:147` | AU 10 %, NZ 15 %, GB 20 %, DE 19 %, JP 10 %, CA 5 % | tax breakdown of listed charger prices | published tax rates |
| `Tools/providers/chargers/chargers_evx.py:206` | GST 0.10 | EVX tax field | published GST |
| `Tools/providers/chargers/chargers_jolt.py:79` | 7 kWh/day free | Jolt rate text | Jolt's published offer |
| `Tools/lib/car_efficiency.py:11` (`potential_energy_kwh`) | 9.81 m/s², 3.6e6 J/kWh | climb energy | physical constants |
| `Tools/lib/route_common.py:20` | 6371.0 km | haversine | Earth radius |
| `Tools/lib/abrp_common.py:373` | 1.60934 | miles to km | unit conversion |
| `Tools/lib/solaredge_v2.py:268` | Wh / kWh / MWh factors | unit conversion | unit conversion |
| `Tools/lib/fuel_common.py:1089` | 0.4 < price < 4.0 means dollars, x100 to cents | FuelCheck file parsing | unit detection |
| `Tools/lib/charging_analyzer.py:4172` | 365.25 days | annualising | calendar |
| `Tools/commands/power_demand_calculator.py:77`, `Tools/commands/agl_demand_calculator.py:141` | August 2026, 31 days | a labelled projection in the self-test | real month length; dated self-test |
| `Tools/lib/abrp_common.py:226` and on | target arrival 10 %, max charge 85 %, corridor 5 km, departure "live" | route-plan preferences | user settings with defaults (config `abrp.*`), not data stand-ins |
| `Tools/lib/battery_cost_basis.py:37` | 10 % unknown share | when the battery's cost basis is trusted | threshold |
| `Tools/lib/battery_cost_basis.py:30` | kWh per % from the charges | pack size with no health reading | from the owner's charges |
| `Tools/lib/supercharger_pricing.py:108` | $0.03/kWh | Supercharger price regime tolerance | matching tolerance |
| `Tools/lib/charging_analyzer.py:810`, `:3400` | 5 kWh, 5 %, $0.50 | invoice / session matching | matching tolerances (5 kWh is a config setting) |
| `Tools/commands/power_invoice_report.py:57` | $0.60 | bill cross-check rounding | tolerance |
| `Tools/lib/session_cost.py:181`, `Tools/lib/pvoutput_common.py:85`, `Tools/commands/power_solar_report.py:73` | 25 %, 50 %, 0.5 kWh | solar / import conflict flags | thresholds |
| `Tools/commands/price_history_report.py:131` | 30 readings | minimum sample for a daily fuel price | data-quality threshold |
| `Tools/lib/charging_analyzer.py:4196` | 85 % / 75 % | colour of charging efficiency | display thresholds |
| `Tools/lib/car_efficiency.py:7`, `Tools/commands/analyse_car_efficiency.py:119`, `:208` | 5 / 20 km confidence, 0.5 % flat grade, 20-150 kWh plausible pack | efficiency confidence and filters | thresholds / sanity filter |
| `Tools/lib/osm_roads_graph.py:183` | top speed band = low + 10 km/h | midpoint of the open-ended band | small modelling choice, flagged |
| `Tools/lib/app_reach.py:240`, `:218` | 5 % minimum safe charge, Australia map box | gaps picture, default view | display |
| `Tools/lib/charging_analyzer.py:4406` | Tesla's rated range added | km/h of range in the session view | Tesla's own figure, labelled "rated" |

## 4. Not run on real data

Everything above was run in the cloud sandbox with `TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata` and on a made-up data folder
(two charges, two drives, one FuelCheck price): the summary prints "unknown (no FuelCheck price for every charge date)" with no fuel data (since 2026-11-03: "no fuel price data") and
"$46.88 (Mazda CX-8: Diesel ~$2.00/L, 7.5 L/100km; car 160 Wh/km) │ Net Savings: +$31.88" with it (50 kWh / 160 Wh/km = 312.5 km x 7.5 L/100km x $2.00).
Not tested on the owner's charges, drives, battery health, fuel database or charger registries.

## 5. `get_fuel_price` callers and the nearest-day fallback (checked 2026-11-03)

`get_fuel_price` (`Tools/lib/fuel_common.py:1269`) returns `None` when the fuel database holds no price for the fuel and region; it no longer
has a typed-in $/L. Every caller and every figure built on its result, and what each shows with no price:

| caller | figure | with no price |
|---|---|---|
| `fuel_common.calculate_single_vehicle_running_cost` (`:1403`) | `fuel_price_per_l`, `fuel_cost`, `estimated_cost`, `total_running_cost` | all `None` (litres still worked out) |
| `fuel_common.calculate_ice_comparison_costs` (`:1466`) | the above for every comparison car | same |
| `fuel_common.format_drive_fuel_comparison` (`:1524`; `analyse drives` drive detail and the drive browser) | "Ref Fuel Prices", each car's cost and "Saved $" | "Ref Fuel Prices: no fuel price data" (or per fuel when only some are held); row "1.92 L · no fuel price data"; no "Saved" |
| `sync_fuel_history.print_status` (`--status`) | "Latest Reference Prices" | "no fuel price data (run --sync-all)" |
| `sync_fuel_history.query_price` (`--query`) | the date's price and the 100 km table | "no fuel price data (run --sync-all)"; each car "[Diesel] · no fuel price data" |
| `charging_analyzer._ice_equivalent` (`:2643`) via `print_summary` | "Estimated ICE Equivalent", "Net Savings" | "no fuel price data - <car>"; a charge without a date: "unknown (no fuel price for 1 of 2 charges)" |
| `charging_analyzer.print_summary` | "Projected Annual Savings" | "no fuel price data" (`annual_savings_unknown`, `:803`) |
| `charging_analyzer._print_multi_vehicle_comparison_matrix` (`--compare-all`) | Avg Price, Equiv Spend, Net Savings, Annual Savings | "no data" / "unknown", and under the table "No fuel price data for Diesel, U91, ..." |
| `price_history_report.build_fuel_series`, `cost_per_100km_report` | price and cost charts | do not call `get_fuel_price` (own query, gaps over 7 days left empty); unchanged |

With fuel data every figure is the same as before (checked line for line on made-up prices). `get_fuel_price` now also closes its database
connection. Tests: `tests/test_fuel_no_price.py`.

**Nearest-day fallback (not changed).** Order: the day's average, else the month's average, else the nearest held day by
`ABS(julianday(date) - julianday(asked))` with no limit. Checked on made-up rows (2020-03-06 and 2026-05-20, month 2026-04):

| date asked | price returned |
|---|---|
| 2026-05-20 | that day |
| 2026-04-02 | April's monthly average |
| 2030-01-01 | 2026-05-20 (3.6 years away) |
| 2019-01-01 | 2020-03-06 |
| `""`, `None`, `"garbage"` | the first row stored (`julianday` gives NULL, so the order means nothing) |

So a date outside the synced range is priced with the first or last price held, with nothing on screen saying the price is from another
date. A drive with no start time can reach this through the drive browser (`drives/browser.py:739` passes `trip.get("start_dt")`).
`price_history_report` (`MAX_FILL_DAYS = 7`, `:133`) leaves such dates empty instead, so the chart and the cost comparisons disagree there.

Options for the owner (option 5 chosen, refined, 2026-11-04; decision below):

1. Leave as is.
2. Return `None` for a date it cannot read (no limit needed; it removes the "price for no date" case only).
3. A cap in days, set by the owner in `config.json` (e.g. reuse 7 from `price_history_report`, or 31), `None` beyond it.
4. Same calendar month only (the daily and monthly lookups already do that; drop the nearest-day step).
5. Keep the nearest day but return its date too, so callers show "(price from 2026-05-20)".

**Decision (owner, 2026-11-04): option 5, refined.** The nearest-day fallback stays, but it is never silent:

- `get_fuel_price_dated(date, fuel, region, data_dir)` returns `dict(price, price_date, basis)`: basis `day`, `month` (price_date
  `YYYY-MM`) or `nearest` (the day the price is from). A date it cannot read (`""`, `None`, `"garbage"`) gives `None`, never the first row.
  A date after the newest price held, a future one too, gets the newest price with basis `nearest` and its real date.
  `get_fuel_price` is built on it and still returns the float or `None`.
- Current: the price's own day, the month's average for the month asked, or a day within `app.fuel.stale_days` (default 7, the same
  limit as `price_history_report.MAX_FILL_DAYS`, which now reads it) of min(date asked, today). A current price prints exactly as before.
- Out of date: the prices are refreshed first with `sync_fuel_history.py --sync-all` (what the app's fuel sync runs), in its own
  process, then looked up again. At most once a run, not again within `app.fuel.refetch_hours` (default 6; last attempt in
  `<data>/fuel/refresh_state.json`), only where the sync has set up the prices (a synced month), never for a future date, never from inside
  the sync itself. No network or a failed fetch: "Fuel price refresh failed." on stderr, then carry on.
- Still out of date: the price shows its date, `fmt_price_basis`: "(price of 2026-05-20)" or "(price of month 2026-05)". Shown in the
  drive fuel comparison (Ref Fuel Prices line, each car's row), `sync fuel-prices --query` and `--status`, the charging summary's ICE line
  and Projected Annual Savings, and `--compare-all` Avg Price; with several charges, the oldest out-of-date date used.

Checked again on made-up rows (2020-03-06 and 2026-05-20, month 2026-04, today 2026-11-04):

| date asked | price returned | shown |
|---|---|---|
| 2026-05-20 | that day | no note |
| 2026-04-02 | April's monthly average | no note |
| 2030-01-01 | 2026-05-20 | refresh not tried (future); "(price of 2026-05-20)" |
| 2019-01-01 | 2020-03-06 | refresh, then "(price of 2020-03-06)" |
| `""`, `None`, `"garbage"` | `None` | "no fuel price data" |

Tests: `tests/test_fuel_price_dates.py`.
