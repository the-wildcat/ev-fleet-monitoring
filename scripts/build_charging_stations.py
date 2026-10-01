"""Build data/charging_stations_india.csv from India's official public charging-station list.

Source: Bureau of Energy Efficiency (BEE), Ministry of Power, Government of India:
"EV Public Charging Stations Data till 26th October 2025"
https://www.beeindia.gov.in/WriteReadData/RTF1984/EV_PCS_Data_29277.pdf

Usage (from the project root):
    python -m scripts.build_charging_stations

Steps:
  1. Download the PDF into data/raw/ (skipped if already there).
  2. Extract every table row (1,159 pages; takes ~10 minutes, cached in data/raw/).
  3. Clean: parse numbers, repair swapped coordinates, drop coordinates outside India,
     normalise connector types, drop exact duplicate rows.
  4. Group chargers at the same location into one station: max power, total connectors,
     connector types, and whether a car can charge there (LEV points are for 2/3-wheelers).
"""

from __future__ import annotations

import csv
import hashlib
import re
import sys
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PDF_URL = "https://www.beeindia.gov.in/WriteReadData/RTF1984/EV_PCS_Data_29277.pdf"
PDF_PATH = RAW_DIR / "bee_ev_pcs_2025-10-26.pdf"
ROWS_CACHE = RAW_DIR / "bee_rows.csv"
OUTPUT = ROOT / "data" / "charging_stations_india.csv"

COLUMNS = [
    "operator",
    "ownership",
    "state",
    "district",
    "city",
    "address",
    "lat",
    "lon",
    "connector_raw",
    "charger_kw",
    "connector_kw",
    "connectors",
]
INDIA_LAT, INDIA_LON = (6.0, 37.5), (68.0, 98.0)

# Connector names in the source vary ("CCS", "CCS-2", "CCS2 (IS-17017-2-3)", ...).
# Order matters: the first matching pattern wins.
CONNECTOR_RULES: list[tuple[str, str, bool]] = [
    # (regex, normalised type, usable by electric cars)
    (r"\bLEV\b|IS-17017-2-7|LECCS", "LEV (2/3-wheeler)", False),
    (r"CHADEMO", "CHAdeMO", True),
    (r"CCS|COMBO", "CCS2", True),
    (r"GB/?T", "GB/T", True),
    (r"BHARAT\s*DC|DC\s*-?\s*001", "Bharat DC-001", True),
    (r"BHARAT\s*AC|AC\s*-?\s*001", "Bharat AC-001", True),
    (r"TYPE\s*-?\s*(II|2)|IEC\s*62196", "Type 2 AC", True),
    (r"15\s*A|3\s*PIN|INDUSTRIAL|SOCKET", "15A socket", False),
]


def _download() -> None:
    if PDF_PATH.exists():
        return
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {PDF_URL} ...")
    resp = requests.get(PDF_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=180)
    resp.raise_for_status()
    PDF_PATH.write_bytes(resp.content)


def _extract_rows() -> pd.DataFrame:
    if not ROWS_CACHE.exists():
        import pdfplumber  # dev dependency, only needed to rebuild the dataset

        print("Extracting tables from the PDF (10-20 minutes) ...")
        partial = ROWS_CACHE.with_suffix(".partial")
        with pdfplumber.open(PDF_PATH) as pdf, partial.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(COLUMNS)
            total = len(pdf.pages)
            for i, page in enumerate(pdf.pages, 1):
                for table in page.extract_tables():
                    writer.writerows(
                        r for r in table if r and not (r[0] or "").startswith("CPO Name")
                    )
                # pdfplumber caches every parsed page; without this, memory grows to many GB.
                page.close()
                if i % 100 == 0 or i == total:
                    print(f"  page {i}/{total}", flush=True)
        partial.replace(ROWS_CACHE)  # only a complete extraction becomes the cache
    df = pd.read_csv(ROWS_CACHE, dtype=str, keep_default_na=False)
    df.columns = COLUMNS[: len(df.columns)]
    return df


def normalise_connector(raw: str) -> tuple[str, bool]:
    text = re.sub(r"\s+", " ", raw or "").upper()
    for pattern, name, car_ok in CONNECTOR_RULES:
        if re.search(pattern, text):
            return name, car_ok
    return "Other", False


def _number(series: pd.Series) -> pd.Series:
    cleaned = series.str.replace(",", "", regex=False).str.extract(r"(-?\d+(?:\.\d+)?)")[0]
    return pd.to_numeric(cleaned, errors="coerce")


def _clean(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    report = {"raw_rows": len(df)}
    for col in ("operator", "ownership", "state", "district", "city", "address"):
        df[col] = df[col].str.replace(r"\s+", " ", regex=True).str.strip()
    df["state"] = df["state"].str.title()
    df["city"] = df["city"].str.title()
    for col in ("lat", "lon", "charger_kw", "connector_kw", "connectors"):
        df[col] = _number(df[col])

    in_india = df.lat.between(*INDIA_LAT) & df.lon.between(*INDIA_LON)
    swapped = ~in_india & df.lon.between(*INDIA_LAT) & df.lat.between(*INDIA_LON)
    df.loc[swapped, ["lat", "lon"]] = df.loc[swapped, ["lon", "lat"]].to_numpy()
    report["swapped_fixed"] = int(swapped.sum())
    valid = df.lat.between(*INDIA_LAT) & df.lon.between(*INDIA_LON)
    report["bad_coordinates_dropped"] = int((~valid).sum())
    df = df[valid].copy()

    before = len(df)
    df = df.drop_duplicates()
    report["duplicate_rows_dropped"] = before - len(df)

    norm = df["connector_raw"].map(normalise_connector)
    df["connector"] = norm.str[0]
    df["car_compatible"] = norm.str[1]
    # Use the charger rating, falling back to the connector rating; implausible values dropped.
    df["power_kw"] = df.charger_kw.where(df.charger_kw.between(1, 400), df.connector_kw)
    df.loc[~df.power_kw.between(1, 400), "power_kw"] = pd.NA
    df["connectors"] = df.connectors.fillna(1).clip(lower=1, upper=50)
    report["clean_charger_rows"] = len(df)
    report["connector_types"] = df.connector.value_counts().to_dict()
    return df, report


def _group_stations(df: pd.DataFrame) -> pd.DataFrame:
    """One row per site: chargers sharing operator and (rounded) location."""
    df = df.assign(_lat=df.lat.round(5), _lon=df.lon.round(5))
    car = df[df.car_compatible]
    grouped = df.groupby(["operator", "_lat", "_lon"], sort=False)
    stations = grouped.agg(
        state=("state", "first"),
        district=("district", "first"),
        city=("city", "first"),
        address=("address", "first"),
        ownership=("ownership", "first"),
        lat=("lat", "first"),
        lon=("lon", "first"),
        total_connectors=("connectors", "sum"),
        connector_types=("connector", lambda s: "; ".join(sorted(set(s)))),
    )
    car_power = car.groupby(["operator", "_lat", "_lon"]).power_kw.max().rename("max_car_kw")
    car_dc = (
        car[car.connector.isin(["CCS2", "CHAdeMO", "GB/T", "Bharat DC-001"])]
        .groupby(["operator", "_lat", "_lon"])
        .size()
        .rename("_dc")
    )
    stations = stations.join(car_power).join(car_dc)
    stations["car_compatible"] = stations.index.isin(
        car.set_index(["operator", "_lat", "_lon"]).index
    )
    stations["has_dc_fast"] = stations["_dc"].fillna(0) > 0
    stations = stations.drop(columns="_dc").reset_index().drop(columns=["_lat", "_lon"])
    stations["name"] = stations.operator + " · " + stations.city
    cols = [
        "name",
        "operator",
        "ownership",
        "state",
        "district",
        "city",
        "address",
        "lat",
        "lon",
        "car_compatible",
        "has_dc_fast",
        "max_car_kw",
        "total_connectors",
        "connector_types",
    ]
    return stations[cols].sort_values(["state", "city", "operator"]).reset_index(drop=True)


def main() -> None:
    _download()
    print(f"PDF sha256: {hashlib.sha256(PDF_PATH.read_bytes()).hexdigest()}")
    chargers, report = _clean(_extract_rows())
    stations = _group_stations(chargers)
    report["stations"] = len(stations)
    report["car_compatible_stations"] = int(stations.car_compatible.sum())
    report["dc_fast_stations"] = int(stations.has_dc_fast.sum())
    for key, value in report.items():
        print(f"{key}: {value}")
    stations.to_csv(OUTPUT, index=False, float_format="%.6f")
    print(f"Wrote {OUTPUT.relative_to(ROOT)} ({len(stations)} stations)")


if __name__ == "__main__":
    sys.exit(main())
