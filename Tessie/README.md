# 🗺️ Tessie Drive Log Analyzer & Known Places Matcher

Analyze exported [Tessie](https://tessie.com/) drive telemetry, auto-import from iCloud, resolve GPS coordinates to friendly place nicknames, and link passenger entry/exit timestamps directly to TeslaCam dashcam footage.

---

## 🌟 Key Features

1. **Automatic iCloud Import (`--import-icloud`):**
   - Discovers and categorizes files directly from `~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie/`.
   - Strips vehicle VIN prefixes and standardizes filenames with accurate date ranges.
2. **Format Recognition:**
   - **Format A (Trip Summary):** Parses departure/arrival times, distances, energy used, and odometer readings.
   - **Format B (Telemetry Trace):** High-frequency point-by-point GPS, power (kW), and speed traces.
   - **Specialized Logs:** Charges history, parking idle durations, battery health, tire pressure, and firmware alerts.
3. **Spatial Geofencing & Location Nicknames:**
   - Resolves street address variations (e.g. front driveway vs side street) and GPS coordinates into user-defined nicknames (e.g. *Home*, *School*, *Swimming*, *Supermarket*).
   - Discover frequent clusters with `./Tools/tessie_analyzer.py --cluster`.
4. **TeslaCam Video Linking:**
   - Automatically cross-references trip departure and arrival times against connected `TeslaCam` drives.
   - Identifies passenger **entry** (getting in) and **exit** (arriving & unloading) windows, highlighting `left_repeater`, `right_repeater`, and `back` camera MP4 files.

---

## 📍 Configuring Known Places (`places.json`)

Place configurations are kept **strictly local** (excluded from Git for privacy). A generic template is provided in `places.example.json`:

```json
{
  "Home": {
    "keywords": ["123 Example Street", "Example Suburb"],
    "lat": -33.80000,
    "lon": 151.00000,
    "radius_m": 150
  },
  "School": {
    "keywords": ["School Road", "Education Lane"],
    "lat": -33.80500,
    "lon": 151.05000,
    "radius_m": 250
  },
  "Work": {
    "keywords": ["Business Park", "Corporate Blvd"],
    "lat": -33.81000,
    "lon": 151.10000,
    "radius_m": 250
  }
}
```

---

## 🚀 Usage

All tools can be run from the repository root or the `Tools/` directory:

```bash
# 1. Import and standardize latest files from iCloud
./Tools/tessie_analyzer.py --import-icloud

# 2. Discover frequent location clusters from drive history
./Tools/tessie_analyzer.py --cluster

# 3. Analyze all trips since a specific date
./Tools/tessie_analyzer.py --since 2026-09-01

# 4. Search trips for a specific place nickname
./Tools/tessie_analyzer.py --place "School"

# 5. Filter trips from the last N days
./Tools/tessie_analyzer.py --days 3
```
