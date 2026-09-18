#!/usr/bin/env python3
"""
fuel_common.py - NSW FuelCheck Historical Data & Pricing Engine
================================================================
Harvests, ingests, indexes, and queries historical and live NSW fuel price data
from Data.NSW (CKAN open data package 'fuel-check') and the NSW FuelCheck API.

Provides:
1. CKAN discovery & download of monthly price history archives (Aug 2016 – present).
2. Pure stdlib high-performance streaming XLSX & CSV parser (zero 3rd party deps).
3. SQLite database storage for station registry, sync logs, and daily/monthly aggregates.
4. Fast O(1) historical price lookup APIs for vehicle fuel equivalence calculations.

Data is stored outside the repo working tree in `<tessie_data_home>/fuel/`.
"""

import csv
import datetime
import io
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, Generator, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.dirname(SCRIPT_DIR)
REPO_ROOT = os.path.dirname(TOOLS_DIR)

try:
    from .tessie_api_common import load_config, resolve_tessie_data_home
except ImportError:
    # Standalone execution fallback
    if SCRIPT_DIR not in sys.path:
        sys.path.insert(0, SCRIPT_DIR)
    from tessie_api_common import load_config, resolve_tessie_data_home

CKAN_PACKAGE_URL = "https://data.nsw.gov.au/data/api/3/action/package_show?id=fuel-check"
DEFAULT_USER_AGENT = "Tesla-Data-Toolkit/1.0 (macOS; FuelCheckSync)"

# Built-in Default Comparison Vehicles (Australian Green Vehicle Guide Top 20 Performers & Sellers)
DEFAULT_COMPARISON_VEHICLES = {
    "petrol_standard": {
        "name": "Medium Petrol SUV (e.g. Mazda CX-5 G25)",
        "category": "Medium SUV",
        "gvg_rank_top20": 7,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "U91",
        "co2_g_per_km": 191,
        "consumption": {
            "combined_l_per_100km": 8.2,
            "urban_l_per_100km": 9.8,
            "highway_l_per_100km": 7.1,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.045,
            "tyres_per_km": 0.025,
        }
    },
    "ford_ranger_diesel": {
        "name": "Ford Ranger 4x4 (2.0L Bi-Turbo / 3.0L V6 Diesel)",
        "category": "Light Commercial / Ute",
        "gvg_rank_top20": 1,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "Diesel",
        "co2_g_per_km": 201,
        "consumption": {
            "combined_l_per_100km": 7.6,
            "urban_l_per_100km": 9.1,
            "highway_l_per_100km": 6.8,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.060,
            "tyres_per_km": 0.035,
        }
    },
    "rav4_hybrid": {
        "name": "Toyota RAV4 Hybrid (2.5L eFour AWD)",
        "category": "Hybrid SUV",
        "gvg_rank_top20": 2,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "U91",
        "co2_g_per_km": 107,
        "consumption": {
            "combined_l_per_100km": 4.8,
            "urban_l_per_100km": 4.9,
            "highway_l_per_100km": 4.7,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.035,
            "tyres_per_km": 0.022,
        }
    },
    "hilux_diesel": {
        "name": "Toyota HiLux 4x4 (2.8L Turbo Diesel)",
        "category": "Light Commercial / Ute",
        "gvg_rank_top20": 3,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "Diesel",
        "co2_g_per_km": 207,
        "consumption": {
            "combined_l_per_100km": 7.9,
            "urban_l_per_100km": 9.5,
            "highway_l_per_100km": 7.0,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.062,
            "tyres_per_km": 0.035,
        }
    },
    "dmax_diesel": {
        "name": "Isuzu D-Max 4x4 (3.0L Turbo Diesel)",
        "category": "Light Commercial / Ute",
        "gvg_rank_top20": 4,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "Diesel",
        "co2_g_per_km": 210,
        "consumption": {
            "combined_l_per_100km": 8.0,
            "urban_l_per_100km": 9.6,
            "highway_l_per_100km": 7.1,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.058,
            "tyres_per_km": 0.034,
        }
    },
    "ford_everest_diesel": {
        "name": "Ford Everest 4WD (2.0L Bi-Turbo Diesel)",
        "category": "Large SUV",
        "gvg_rank_top20": 5,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "Diesel",
        "co2_g_per_km": 194,
        "consumption": {
            "combined_l_per_100km": 7.2,
            "urban_l_per_100km": 8.8,
            "highway_l_per_100km": 6.3,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.060,
            "tyres_per_km": 0.033,
        }
    },
    "prado_diesel": {
        "name": "Toyota LandCruiser Prado (2.8L Turbo Diesel 4WD)",
        "category": "Large 4WD / Diesel",
        "gvg_rank_top20": 6,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "Diesel",
        "co2_g_per_km": 208,
        "consumption": {
            "combined_l_per_100km": 7.9,
            "urban_l_per_100km": 9.2,
            "highway_l_per_100km": 7.0,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.065,
            "tyres_per_km": 0.035,
        }
    },
    "mazda_cx5_petrol": {
        "name": "Mazda CX-5 (2.5L G25 Petrol)",
        "category": "Medium SUV",
        "gvg_rank_top20": 7,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "U91",
        "co2_g_per_km": 191,
        "consumption": {
            "combined_l_per_100km": 8.2,
            "urban_l_per_100km": 9.8,
            "highway_l_per_100km": 7.1,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.045,
            "tyres_per_km": 0.025,
        }
    },
    "outlander_petrol": {
        "name": "Mitsubishi Outlander (2.5L Petrol AWD)",
        "category": "Medium SUV",
        "gvg_rank_top20": 8,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "U91",
        "co2_g_per_km": 185,
        "consumption": {
            "combined_l_per_100km": 8.1,
            "urban_l_per_100km": 9.7,
            "highway_l_per_100km": 7.1,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.046,
            "tyres_per_km": 0.026,
        }
    },
    "hyundai_tucson_petrol": {
        "name": "Hyundai Tucson (2.0L Petrol FWD)",
        "category": "Medium SUV",
        "gvg_rank_top20": 9,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "U91",
        "co2_g_per_km": 186,
        "consumption": {
            "combined_l_per_100km": 8.1,
            "urban_l_per_100km": 10.2,
            "highway_l_per_100km": 6.8,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.044,
            "tyres_per_km": 0.025,
        }
    },
    "mg_zs_petrol": {
        "name": "MG ZS / ZST (1.5L Petrol)",
        "category": "Small SUV",
        "gvg_rank_top20": 10,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "U91",
        "co2_g_per_km": 160,
        "consumption": {
            "combined_l_per_100km": 6.9,
            "urban_l_per_100km": 8.4,
            "highway_l_per_100km": 5.9,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.040,
            "tyres_per_km": 0.022,
        }
    },
    "corolla_hybrid": {
        "name": "Toyota Corolla Hybrid (1.8L Hybrid)",
        "category": "Small Car",
        "gvg_rank_top20": 11,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "U91",
        "co2_g_per_km": 91,
        "consumption": {
            "combined_l_per_100km": 4.0,
            "urban_l_per_100km": 3.9,
            "highway_l_per_100km": 4.1,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.028,
            "tyres_per_km": 0.018,
        }
    },
    "corolla_petrol": {
        "name": "Toyota Corolla (2.0L Petrol)",
        "category": "Small Car",
        "gvg_rank_top20": 12,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "U91",
        "co2_g_per_km": 139,
        "consumption": {
            "combined_l_per_100km": 6.0,
            "urban_l_per_100km": 7.5,
            "highway_l_per_100km": 5.1,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.030,
            "tyres_per_km": 0.020,
        }
    },
    "hyundai_i30_petrol": {
        "name": "Hyundai i30 (2.0L Petrol Hatch)",
        "category": "Small Car",
        "gvg_rank_top20": 13,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "U91",
        "co2_g_per_km": 168,
        "consumption": {
            "combined_l_per_100km": 7.4,
            "urban_l_per_100km": 9.9,
            "highway_l_per_100km": 5.9,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.038,
            "tyres_per_km": 0.021,
        }
    },
    "kia_sportage_diesel": {
        "name": "Kia Sportage (2.0L CRDi Diesel AWD)",
        "category": "Medium SUV",
        "gvg_rank_top20": 14,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "Diesel",
        "co2_g_per_km": 166,
        "consumption": {
            "combined_l_per_100km": 6.3,
            "urban_l_per_100km": 7.7,
            "highway_l_per_100km": 5.5,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.048,
            "tyres_per_km": 0.027,
        }
    },
    "subaru_forester_petrol": {
        "name": "Subaru Forester (2.5L Petrol AWD)",
        "category": "Medium SUV",
        "gvg_rank_top20": 15,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "U91",
        "co2_g_per_km": 168,
        "consumption": {
            "combined_l_per_100km": 7.4,
            "urban_l_per_100km": 9.3,
            "highway_l_per_100km": 6.3,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.049,
            "tyres_per_km": 0.026,
        }
    },
    "yaris_cross_hybrid": {
        "name": "Toyota Yaris Cross Hybrid (1.5L Hybrid)",
        "category": "Small SUV",
        "gvg_rank_top20": 16,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "U91",
        "co2_g_per_km": 86,
        "consumption": {
            "combined_l_per_100km": 3.8,
            "urban_l_per_100km": 3.6,
            "highway_l_per_100km": 3.9,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.026,
            "tyres_per_km": 0.017,
        }
    },
    "kia_carnival_diesel": {
        "name": "Kia Carnival (2.2L CRDi Diesel)",
        "category": "People Mover",
        "gvg_rank_top20": 17,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "Diesel",
        "co2_g_per_km": 170,
        "consumption": {
            "combined_l_per_100km": 6.5,
            "urban_l_per_100km": 7.8,
            "highway_l_per_100km": 5.7,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.052,
            "tyres_per_km": 0.030,
        }
    },
    "golf_gti_p98": {
        "name": "VW Golf GTI (2.0L Turbo Petrol P98)",
        "category": "Performance / Euro",
        "gvg_rank_top20": 18,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "P98",
        "co2_g_per_km": 159,
        "consumption": {
            "combined_l_per_100km": 7.0,
            "urban_l_per_100km": 8.9,
            "highway_l_per_100km": 5.9,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.055,
            "tyres_per_km": 0.030,
        }
    },
    "kluger_hybrid": {
        "name": "Toyota Kluger Hybrid (2.5L AWD Large SUV)",
        "category": "Large SUV",
        "gvg_rank_top20": 19,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "U91",
        "co2_g_per_km": 128,
        "consumption": {
            "combined_l_per_100km": 5.6,
            "urban_l_per_100km": 5.7,
            "highway_l_per_100km": 5.5,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.042,
            "tyres_per_km": 0.028,
        }
    },
    "camry_hybrid": {
        "name": "Toyota Camry Hybrid (2.5L Hybrid Sedan)",
        "category": "Medium Sedan",
        "gvg_rank_top20": 20,
        "gvg_url": "https://greenvehicleguide.gov.au/Vehicle/Search",
        "fuel_type": "U91",
        "co2_g_per_km": 96,
        "consumption": {
            "combined_l_per_100km": 4.2,
            "urban_l_per_100km": 4.0,
            "highway_l_per_100km": 4.4,
        },
        "running_costs": {
            "service_maintenance_per_km": 0.030,
            "tyres_per_km": 0.020,
        }
    },
}

COMPARISON_VEHICLES = DEFAULT_COMPARISON_VEHICLES


def get_gvg_top20_vehicles(tessie_dir: Optional[str] = None) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Returns list of (key, vehicle_dict) for all Top 20 Green Vehicle Guide benchmark vehicles,
    sorted by official GVG Top 20 sales rank.
    """
    reg = load_comparison_vehicles(tessie_dir)
    vehicles = reg.get("vehicles", {})
    if not vehicles:
        vehicles = DEFAULT_COMPARISON_VEHICLES

    top20 = []
    for k, v in vehicles.items():
        if isinstance(v, dict):
            rank = v.get("gvg_rank_top20")
            if rank is not None:
                try:
                    rank_int = int(rank)
                except (ValueError, TypeError):
                    rank_int = 999
                top20.append((rank_int, k, v))

    if not top20:
        # Fall back to built-in DEFAULT_COMPARISON_VEHICLES if custom config lacks GVG ranks
        for k, v in DEFAULT_COMPARISON_VEHICLES.items():
            if isinstance(v, dict):
                rank = v.get("gvg_rank_top20")
                if rank is not None:
                    try:
                        rank_int = int(rank)
                    except (ValueError, TypeError):
                        rank_int = 999
                    top20.append((rank_int, k, v))

    top20.sort(key=lambda x: x[0])
    return [(k, v) for _, k, v in top20]


def search_comparison_vehicles(query_str: str, tessie_dir: Optional[str] = None) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Fuzzy multi-field search across comparison vehicles by key, name, category, fuel type, or GVG rank.
    """
    reg = load_comparison_vehicles(tessie_dir)
    vehicles = dict(DEFAULT_COMPARISON_VEHICLES)
    if reg.get("vehicles") and isinstance(reg["vehicles"], dict):
        vehicles.update(reg["vehicles"])

    q = query_str.strip().lower()
    matches = []

    for k, v in vehicles.items():
        if not isinstance(v, dict):
            continue
        name = str(v.get("name", "")).lower()
        category = str(v.get("category", "")).lower()
        fuel = str(v.get("fuel_type", "")).lower()
        k_low = k.lower()
        rank = str(v.get("gvg_rank_top20", ""))

        score = 0
        if q == k_low or q == name:
            score += 100
        elif q in k_low:
            score += 50
        elif q in name:
            score += 40
        elif q in category:
            score += 30
        elif q in fuel:
            score += 20
        elif q == rank or f"#{q}" == f"#{rank}":
            score += 25

        if score > 0:
            matches.append((score, k, v))

    matches.sort(key=lambda x: x[0], reverse=True)
    return [(k, v) for _, k, v in matches]


def load_comparison_vehicles(tessie_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Loads comparison vehicles registry from JSON configuration.
    Checks:
    1. <tessie_data_home>/comparison_vehicles.json
    2. <repo_root>/Tessie/comparison_vehicles.json
    3. <repo_root>/Tessie/comparison_vehicles.example.json
    4. Built-in defaults
    """
    candidate_paths = []
    if tessie_dir:
        candidate_paths.append(os.path.join(tessie_dir, "comparison_vehicles.json"))

    try:
        data_home = resolve_tessie_data_home()
        if data_home:
            candidate_paths.append(os.path.join(data_home, "comparison_vehicles.json"))
    except Exception:
        pass

    candidate_paths.append(os.path.join(REPO_ROOT, "Tessie", "comparison_vehicles.json"))
    candidate_paths.append(os.path.join(REPO_ROOT, "Tessie", "comparison_vehicles.example.json"))

    merged_vehicles = dict(DEFAULT_COMPARISON_VEHICLES)
    default_key = "petrol_standard"

    for p in candidate_paths:
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "vehicles" in data and isinstance(data["vehicles"], dict):
                        merged_vehicles.update(data["vehicles"])
                        if "default_vehicle" in data:
                            default_key = data["default_vehicle"]
                        return {"default_vehicle": default_key, "vehicles": merged_vehicles}
                    elif isinstance(data, dict) and data:
                        # Direct dictionary of vehicles
                        merged_vehicles.update(data)
                        return {"default_vehicle": list(data.keys())[0], "vehicles": merged_vehicles}
            except Exception:
                continue

    return {"default_vehicle": default_key, "vehicles": merged_vehicles}



def get_comparison_vehicle(vehicle_query: Optional[str] = None, tessie_dir: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """
    Finds a comparison vehicle by key, name, or substring.
    Returns (vehicle_key, vehicle_spec).
    """
    registry = load_comparison_vehicles(tessie_dir)
    vehicles = registry.get("vehicles", {})
    default_key = registry.get("default_vehicle", "petrol_standard")

    if not vehicles:
        vehicles = DEFAULT_COMPARISON_VEHICLES

    if not vehicle_query:
        key = default_key if default_key in vehicles else list(vehicles.keys())[0]
        return key, vehicles[key]

    q_lower = vehicle_query.strip().lower()

    # 1. Exact key match
    if q_lower in vehicles:
        return q_lower, vehicles[q_lower]

    for k, v in vehicles.items():
        if k.lower() == q_lower:
            return k, v

    # 2. Exact or substring name match
    for k, v in vehicles.items():
        name = v.get("name", "")
        if q_lower in name.lower() or q_lower in k.lower():
            return k, v

    # 3. Category match
    for k, v in vehicles.items():
        category = v.get("category", "")
        if q_lower in category.lower():
            return k, v

    # Fallback to default
    key = default_key if default_key in vehicles else list(vehicles.keys())[0]
    return key, vehicles[key]

def is_sydney_metro(postcode_str: str) -> bool:
    try:
        pc = int(re.sub(r"\D", "", postcode_str or ""))
        if (1000 <= pc <= 2249) or (2740 <= pc <= 2786) or (2000 <= pc <= 2234):
            return True
        return False
    except Exception:
        return False


def resolve_fuel_dir(tessie_dir: Optional[str] = None) -> str:
    """Authoritative path to external fuel data directory."""
    base = tessie_dir or resolve_tessie_data_home()
    fuel_dir = os.path.join(base, "fuel")
    os.makedirs(fuel_dir, exist_ok=True)
    os.makedirs(os.path.join(fuel_dir, "raw"), exist_ok=True)
    return fuel_dir


def resolve_fuel_db_path(tessie_dir: Optional[str] = None) -> str:
    """Path to the SQLite fuel history database."""
    return os.path.join(resolve_fuel_dir(tessie_dir), "fuel_history.db")


def get_db_connection(tessie_dir: Optional[str] = None) -> sqlite3.Connection:
    """Initializes and returns an SQLite connection with standard pragmas and tables."""
    db_path = resolve_fuel_db_path(tessie_dir)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    _init_db_schema(conn)
    return conn


def _init_db_schema(conn: sqlite3.Connection):
    """Creates database schema if not already present."""
    with conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS fuel_stations (
            station_id TEXT PRIMARY KEY,
            name TEXT,
            brand TEXT,
            address TEXT,
            suburb TEXT,
            postcode TEXT,
            state TEXT DEFAULT 'NSW',
            is_metro INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS fuel_sync_log (
            resource_id TEXT PRIMARY KEY,
            title TEXT,
            month_year TEXT UNIQUE,
            format TEXT,
            url TEXT,
            file_size INTEGER DEFAULT 0,
            row_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            last_synced_at TEXT
        );

        CREATE TABLE IF NOT EXISTS fuel_daily_averages (
            date TEXT,
            fuel_type TEXT,
            region TEXT,
            avg_price_cents REAL,
            min_price_cents REAL,
            max_price_cents REAL,
            sample_count INTEGER,
            PRIMARY KEY (date, fuel_type, region)
        );

        CREATE INDEX IF NOT EXISTS idx_fuel_daily_date ON fuel_daily_averages(date);
        CREATE INDEX IF NOT EXISTS idx_fuel_daily_fuel ON fuel_daily_averages(fuel_type);

        CREATE TABLE IF NOT EXISTS fuel_monthly_averages (
            year_month TEXT,
            fuel_type TEXT,
            region TEXT,
            avg_price_cents REAL,
            min_price_cents REAL,
            max_price_cents REAL,
            sample_count INTEGER,
            PRIMARY KEY (year_month, fuel_type, region)
        );

        CREATE INDEX IF NOT EXISTS idx_fuel_monthly_ym ON fuel_monthly_averages(year_month);
        """)


def normalize_fuel_code(raw_code: str) -> str:
    """Standardizes fuel type codes."""
    if not raw_code:
        return "OTHER"
    code = str(raw_code).strip().upper()
    mapping = {
        "E10": "E10",
        "U91": "U91",
        "UNLEADED 91": "U91",
        "UNLEADED": "U91",
        "P95": "P95",
        "PREMIUM 95": "P95",
        "P98": "P98",
        "PREMIUM 98": "P98",
        "DL": "DL",
        "DIESEL": "DL",
        "DSL": "DL",
        "PDL": "PDL",
        "PREMIUM DIESEL": "PDL",
        "LPG": "LPG",
        "AUTOGAS": "LPG",
        "B20": "B20",
        "BIODIESEL": "B20",
        "CNG": "CNG",
        "EV": "EV",
    }
    return mapping.get(code, code)


def parse_date_val(val: Any) -> Optional[datetime.datetime]:
    """Parses date from Excel serial float or string formats."""
    if val is None or val == "":
        return None
    try:
        float_val = float(val)
        if 35000 < float_val < 65000:
            # Excel base: 1899-12-30
            base = datetime.datetime(1899, 12, 30)
            return base + datetime.timedelta(days=float_val)
    except (ValueError, TypeError):
        pass

    s = str(val).strip()
    date_formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%Y-%m-%d",
    ]
    for fmt in date_formats:
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def fetch_ckan_resources() -> List[Dict[str, Any]]:
    """
    Queries Data.NSW CKAN package metadata for all monthly fuel price resources.
    Returns sorted list of resource dicts from oldest to newest.
    """
    req = urllib.request.Request(CKAN_PACKAGE_URL, headers={"User-Agent": DEFAULT_USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    resources = data.get("result", {}).get("resources", [])
    month_map = {
        "jan": "01", "january": "01",
        "feb": "02", "february": "02",
        "mar": "03", "march": "03",
        "apr": "04", "april": "04",
        "may": "05",
        "jun": "06", "june": "06",
        "jul": "07", "july": "07",
        "aug": "08", "august": "08",
        "sep": "09", "sept": "09", "september": "09",
        "oct": "10", "october": "10",
        "nov": "11", "november": "11",
        "dec": "12", "december": "12",
    }

    valid_resources = []
    for r in resources:
        name = r.get("name", "")
        url = r.get("url", "")
        fmt = (r.get("format") or "").strip().upper()
        res_id = r.get("id")

        if not url or "download" not in url:
            continue

        combined_text = f"{name} {url}".lower()
        match = re.search(r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)[ _-]*(\d{4})", combined_text)
        if not match:
            match_alt = re.search(r"(\d{4})[-_](0[1-9]|1[0-2])", combined_text)
            if match_alt:
                year_month = f"{match_alt.group(1)}-{match_alt.group(2)}"
            else:
                continue
        else:
            m_str, y_str = match.group(1), match.group(2)
            m_num = month_map.get(m_str)
            if not m_num:
                continue
            year_month = f"{y_str}-{m_num}"

        valid_resources.append({
            "resource_id": res_id,
            "title": name,
            "month_year": year_month,
            "url": url,
            "format": "XLSX" if "XLS" in fmt or url.endswith(".xlsx") else "CSV",
            "size": r.get("size") or 0,
        })

    by_ym = {}
    for res in valid_resources:
        ym = res["month_year"]
        if ym not in by_ym:
            by_ym[ym] = res
        else:
            if res["format"] == "XLSX" and by_ym[ym]["format"] != "XLSX":
                by_ym[ym] = res

    return sorted(by_ym.values(), key=lambda x: x["month_year"])


def parse_xlsx_stream(file_bytes_or_path: Any) -> Generator[Dict[str, Any], None, None]:
    """
    Pure stdlib high-performance streaming parser for Excel (.xlsx) files.
    Yields normalized row dictionaries.
    """
    if isinstance(file_bytes_or_path, (bytes, bytearray)):
        zf = zipfile.ZipFile(io.BytesIO(file_bytes_or_path))
    else:
        zf = zipfile.ZipFile(file_bytes_or_path, "r")

    shared_strings = []
    if "xl/sharedStrings.xml" in zf.namelist():
        try:
            sst_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in sst_root:
                text_parts = [t.text or "" for t in si.iter() if t.tag.endswith("}t") or t.tag == "t"]
                shared_strings.append("".join(text_parts))
        except Exception:
            pass

    sheet_name = "xl/worksheets/sheet1.xml"
    if sheet_name not in zf.namelist():
        sheets = [n for n in zf.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
        sheet_name = sheets[0] if sheets else None

    if not sheet_name:
        return

    sheet_file = zf.open(sheet_name)
    header_map = None

    for event, elem in ET.iterparse(sheet_file, events=("end",)):
        if elem.tag.endswith("}row") or elem.tag == "row":
            row_vals = []
            for c in elem:
                if not c.tag.endswith("}c") and c.tag != "c":
                    continue
                t = c.get("t")
                val = ""
                for child in c:
                    if child.tag.endswith("}v") or child.tag == "v":
                        val = child.text or ""
                        break
                if t == "s" and val.isdigit():
                    idx = int(val)
                    val = shared_strings[idx] if idx < len(shared_strings) else val
                row_vals.append(val)

            if header_map is None:
                cleaned = [re.sub(r"[^a-zA-Z0-9]", "", str(c)).lower() for c in row_vals]
                if any(k in cleaned for k in ("fuelcode", "fueltype", "fuel", "price", "servicestationname", "stationname")):
                    header_map = {}
                    for idx, col in enumerate(row_vals):
                        clean_col = re.sub(r"[^a-zA-Z0-9]", "", str(col)).lower()
                        header_map[clean_col] = idx
            else:
                row_dict = _extract_row_data(row_vals, header_map)
                if row_dict:
                    yield row_dict

            elem.clear()


def parse_csv_stream(file_bytes_or_path: Any) -> Generator[Dict[str, Any], None, None]:
    """Parses a CSV file / stream and yields normalized row dicts."""
    if isinstance(file_bytes_or_path, (bytes, bytearray)):
        text_stream = io.StringIO(file_bytes_or_path.decode("utf-8", errors="replace"))
    else:
        text_stream = open(file_bytes_or_path, "r", encoding="utf-8", errors="replace")

    reader = csv.reader(text_stream)
    header_map = None
    for row_vals in reader:
        if header_map is None:
            cleaned = [re.sub(r"[^a-zA-Z0-9]", "", str(c)).lower() for c in row_vals]
            if any(k in cleaned for k in ("fuelcode", "fueltype", "fuel", "price", "servicestationname", "stationname")):
                header_map = {}
                for idx, col in enumerate(row_vals):
                    clean_col = re.sub(r"[^a-zA-Z0-9]", "", str(col)).lower()
                    header_map[clean_col] = idx
        else:
            row_dict = _extract_row_data(row_vals, header_map)
            if row_dict:
                yield row_dict

    if not isinstance(file_bytes_or_path, (bytes, bytearray)):
        text_stream.close()


def _extract_row_data(row_vals: List[str], header_map: Dict[str, int]) -> Optional[Dict[str, Any]]:
    """Maps header columns to a normalized row dictionary."""
    def get_val(col_names: List[str]) -> str:
        for name in col_names:
            clean = re.sub(r"[^a-zA-Z0-9]", "", name).lower()
            if clean in header_map:
                idx = header_map[clean]
                if idx < len(row_vals):
                    return row_vals[idx]
        return ""

    station_name = get_val(["ServiceStationName", "StationName", "TradingName", "Name"])
    address = get_val(["Address", "StationAddress"])
    suburb = get_val(["Suburb", "Town"])
    postcode = get_val(["Postcode", "PostalCode"])
    brand = get_val(["Brand", "BrandName"])
    fuel_code = get_val(["FuelCode", "FuelType", "Fuel", "Product"])
    date_val = get_val(["PriceUpdatedDate", "PriceUpdated", "Date", "Timestamp"])
    price_val = get_val(["Price", "FuelPrice", "PriceCents"])

    if not fuel_code or not price_val:
        return None

    try:
        price_float = float(price_val)
        if 0.4 < price_float < 4.0:
            price_float *= 100.0
    except (ValueError, TypeError):
        return None

    dt = parse_date_val(date_val)
    if not dt:
        return None

    return {
        "station_name": station_name.strip(),
        "address": address.strip(),
        "suburb": suburb.strip(),
        "postcode": postcode.strip(),
        "brand": brand.strip(),
        "fuel_code": normalize_fuel_code(fuel_code),
        "datetime": dt,
        "date_str": dt.strftime("%Y-%m-%d"),
        "price_cents": price_float,
        "is_metro": 1 if is_sydney_metro(postcode) else 0,
    }


def ingest_month_data(
    resource: Dict[str, Any],
    file_bytes_or_path: Any,
    conn: sqlite3.Connection,
    save_raw_path: Optional[str] = None
) -> int:
    """
    Ingests one monthly resource into SQLite:
    - Updates `fuel_stations`
    - Aggregates prices into `fuel_daily_averages` and `fuel_monthly_averages`
    - Records entry in `fuel_sync_log`
    """
    is_xlsx = resource.get("format") == "XLSX" or str(file_bytes_or_path).endswith(".xlsx")
    row_generator = parse_xlsx_stream(file_bytes_or_path) if is_xlsx else parse_csv_stream(file_bytes_or_path)

    stations: Dict[str, Tuple[str, str, str, str, str, int]] = {}
    daily_stats: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    total_rows = 0

    for row in row_generator:
        total_rows += 1
        s_key = f"{row['station_name']}_{row['address']}_{row['postcode']}"
        if s_key not in stations:
            stations[s_key] = (
                row["station_name"],
                row["brand"],
                row["address"],
                row["suburb"],
                row["postcode"],
                row["is_metro"],
            )

        date_str = row["date_str"]
        fuel = row["fuel_code"]
        price = row["price_cents"]
        is_metro = row["is_metro"]

        regions = ["all", "metro" if is_metro else "regional"]
        for r in regions:
            key = (date_str, fuel, r)
            if key not in daily_stats:
                daily_stats[key] = {
                    "sum": price,
                    "min": price,
                    "max": price,
                    "count": 1,
                }
            else:
                st = daily_stats[key]
                st["sum"] += price
                if price < st["min"]:
                    st["min"] = price
                if price > st["max"]:
                    st["max"] = price
                st["count"] += 1

    if total_rows == 0:
        return 0

    with conn:
        station_rows = [
            (k, v[0], v[1], v[2], v[3], v[4], 'NSW', v[5])
            for k, v in stations.items()
        ]
        conn.executemany("""
        INSERT INTO fuel_stations (station_id, name, brand, address, suburb, postcode, state, is_metro)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(station_id) DO UPDATE SET
            name = excluded.name,
            brand = excluded.brand,
            address = excluded.address,
            suburb = excluded.suburb,
            postcode = excluded.postcode,
            is_metro = excluded.is_metro
        """, station_rows)

        daily_rows = []
        for (d_str, f_code, r_code), st in daily_stats.items():
            avg_cents = st["sum"] / st["count"]
            daily_rows.append((
                d_str, f_code, r_code, avg_cents, st["min"], st["max"], st["count"]
            ))

        conn.executemany("""
        INSERT INTO fuel_daily_averages (date, fuel_type, region, avg_price_cents, min_price_cents, max_price_cents, sample_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date, fuel_type, region) DO UPDATE SET
            avg_price_cents = excluded.avg_price_cents,
            min_price_cents = excluded.min_price_cents,
            max_price_cents = excluded.max_price_cents,
            sample_count = excluded.sample_count
        """, daily_rows)

        month_ym = resource["month_year"]
        monthly_stats: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for (d_str, f_code, r_code), st in daily_stats.items():
            m_key = (f_code, r_code)
            if m_key not in monthly_stats:
                monthly_stats[m_key] = {
                    "sum": st["sum"],
                    "min": st["min"],
                    "max": st["max"],
                    "count": st["count"],
                }
            else:
                mst = monthly_stats[m_key]
                mst["sum"] += st["sum"]
                if st["min"] < mst["min"]:
                    mst["min"] = st["min"]
                if st["max"] > mst["max"]:
                    mst["max"] = st["max"]
                mst["count"] += st["count"]

        monthly_rows = []
        for (f_code, r_code), mst in monthly_stats.items():
            m_avg = mst["sum"] / mst["count"]
            monthly_rows.append((
                month_ym, f_code, r_code, m_avg, mst["min"], mst["max"], mst["count"]
            ))

        conn.executemany("""
        INSERT INTO fuel_monthly_averages (year_month, fuel_type, region, avg_price_cents, min_price_cents, max_price_cents, sample_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(year_month, fuel_type, region) DO UPDATE SET
            avg_price_cents = excluded.avg_price_cents,
            min_price_cents = excluded.min_price_cents,
            max_price_cents = excluded.max_price_cents,
            sample_count = excluded.sample_count
        """, monthly_rows)

        file_sz = len(file_bytes_or_path) if isinstance(file_bytes_or_path, (bytes, bytearray)) else os.path.getsize(file_bytes_or_path) if os.path.isfile(str(file_bytes_or_path)) else 0
        conn.execute("""
        INSERT INTO fuel_sync_log (resource_id, title, month_year, format, url, file_size, row_count, status, last_synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'synced', ?)
        ON CONFLICT(resource_id) DO UPDATE SET
            title = excluded.title,
            month_year = excluded.month_year,
            format = excluded.format,
            url = excluded.url,
            file_size = excluded.file_size,
            row_count = excluded.row_count,
            status = 'synced',
            last_synced_at = excluded.last_synced_at
        """, (
            resource["resource_id"],
            resource.get("title", ""),
            month_ym,
            resource.get("format", "XLSX"),
            resource.get("url", ""),
            file_sz,
            total_rows,
            datetime.datetime.now().isoformat()
        ))

    return total_rows


def get_fuel_price(
    date_val: Any,
    fuel_type: str = "U91",
    region: str = "all",
    tessie_dir: Optional[str] = None
) -> float:
    """
    Returns the average fuel price in AUD ($/L) for a given date.
    Falls back gracefully to the monthly average, nearest recorded date,
    or default ($1.95/L) if no data exists.
    """
    fuel_code = normalize_fuel_code(fuel_type)
    if isinstance(date_val, datetime.date) or isinstance(date_val, datetime.datetime):
        date_str = date_val.strftime("%Y-%m-%d")
        ym_str = date_val.strftime("%Y-%m")
    else:
        s = str(date_val).strip()[:10]
        date_str = s
        ym_str = s[:7]

    conn = get_db_connection(tessie_dir)
    cur = conn.cursor()

    cur.execute("""
    SELECT avg_price_cents FROM fuel_daily_averages
    WHERE date = ? AND fuel_type = ? AND region = ?
    """, (date_str, fuel_code, region))
    row = cur.fetchone()
    if row and row[0]:
        return row[0] / 100.0

    cur.execute("""
    SELECT avg_price_cents FROM fuel_monthly_averages
    WHERE year_month = ? AND fuel_type = ? AND region = ?
    """, (ym_str, fuel_code, region))
    row = cur.fetchone()
    if row and row[0]:
        return row[0] / 100.0

    cur.execute("""
    SELECT avg_price_cents FROM fuel_daily_averages
    WHERE fuel_type = ? AND region = ?
    ORDER BY ABS(julianday(date) - julianday(?)) ASC LIMIT 1
    """, (fuel_code, region, date_str))
    row = cur.fetchone()
    if row and row[0]:
        return row[0] / 100.0

    defaults = {"U91": 1.95, "P95": 2.10, "P98": 2.20, "DL": 2.05, "E10": 1.90, "LPG": 1.10}
    return defaults.get(fuel_code, 1.95)


def calculate_single_vehicle_running_cost(
    vehicle_spec: Dict[str, Any],
    distance_km: float,
    start_date: Any,
    tessie_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    Computes detailed running costs (fuel + optional maintenance/tyres) for a single comparison vehicle.
    """
    fuel_type = vehicle_spec.get("fuel_type", "U91")
    price_per_l = get_fuel_price(start_date, fuel_type=fuel_type, tessie_dir=tessie_dir)

    consumption = vehicle_spec.get("consumption", {})
    if isinstance(consumption, dict):
        l_per_100km = consumption.get("combined_l_per_100km") or consumption.get("l_per_100km") or 8.5
        urban_l_per_100km = consumption.get("urban_l_per_100km", l_per_100km)
        highway_l_per_100km = consumption.get("highway_l_per_100km", l_per_100km)
    else:
        l_per_100km = vehicle_spec.get("l_per_100km", 8.5)
        urban_l_per_100km = vehicle_spec.get("urban_l_per_100km", l_per_100km)
        highway_l_per_100km = vehicle_spec.get("highway_l_per_100km", l_per_100km)

    litres_used = (distance_km / 100.0) * l_per_100km
    fuel_cost = litres_used * price_per_l

    running_costs = vehicle_spec.get("running_costs", {})
    maint_per_km = running_costs.get("service_maintenance_per_km", 0.0) if isinstance(running_costs, dict) else 0.0
    tyres_per_km = running_costs.get("tyres_per_km", 0.0) if isinstance(running_costs, dict) else 0.0
    non_fuel_cost = distance_km * (maint_per_km + tyres_per_km)

    name = vehicle_spec.get("name") or vehicle_spec.get("label") or "Comparison Vehicle"
    category = vehicle_spec.get("category", "")

    return {
        "name": name,
        "label": name,
        "category": category,
        "fuel_type": fuel_type,
        "l_per_100km": l_per_100km,
        "urban_l_per_100km": urban_l_per_100km,
        "highway_l_per_100km": highway_l_per_100km,
        "fuel_price_per_l": price_per_l,
        "litres_used": litres_used,
        "fuel_cost": fuel_cost,
        "maintenance_cost": distance_km * maint_per_km,
        "tyre_cost": distance_km * tyres_per_km,
        "non_fuel_cost": non_fuel_cost,
        "total_running_cost": fuel_cost + non_fuel_cost,
        "estimated_cost": fuel_cost,  # standard alias for fuel cost
    }


def calculate_ice_comparison_costs(
    distance_km: float,
    start_date: Any,
    end_date: Optional[Any] = None,
    tessie_dir: Optional[str] = None,
    vehicle_key: Optional[str] = None
) -> Dict[str, Dict[str, Any]]:
    """
    Computes comparative ICE fuel & running costs for a given trip distance across comparison vehicles.
    """
    if not end_date:
        end_date = start_date

    registry = load_comparison_vehicles(tessie_dir)
    vehicles = registry.get("vehicles", {})
    if not vehicles:
        vehicles = DEFAULT_COMPARISON_VEHICLES

    if vehicle_key:
        matched_key, matched_spec = get_comparison_vehicle(vehicle_key, tessie_dir=tessie_dir)
        return {matched_key: calculate_single_vehicle_running_cost(matched_spec, distance_km, start_date, tessie_dir=tessie_dir)}

    results = {}
    for key, spec in vehicles.items():
        results[key] = calculate_single_vehicle_running_cost(spec, distance_km, start_date, tessie_dir=tessie_dir)
    return results


def get_sync_status_summary(tessie_dir: Optional[str] = None) -> Dict[str, Any]:
    """Returns a health & status summary of the local fuel history database."""
    conn = get_db_connection(tessie_dir)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM fuel_stations")
    station_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM fuel_daily_averages WHERE region = 'all' AND fuel_type = 'U91'")
    daily_count, min_date, max_date = cur.fetchone()

    cur.execute("SELECT COUNT(*), MIN(year_month), MAX(year_month) FROM fuel_monthly_averages WHERE region = 'all' AND fuel_type = 'U91'")
    monthly_count, min_ym, max_ym = cur.fetchone()

    cur.execute("SELECT COUNT(*) FROM fuel_sync_log WHERE status = 'synced'")
    synced_months = cur.fetchone()[0]

    db_path = resolve_fuel_db_path(tessie_dir)
    db_size = os.path.getsize(db_path) if os.path.isfile(db_path) else 0

    return {
        "db_path": db_path,
        "db_size_mb": round(db_size / (1024 * 1024), 2),
        "station_count": station_count,
        "synced_months_count": synced_months,
        "date_range": f"{min_date or 'N/A'} to {max_date or 'N/A'}",
        "monthly_range": f"{min_ym or 'N/A'} to {max_ym or 'N/A'}",
        "daily_records": daily_count,
    }
