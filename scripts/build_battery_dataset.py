"""Build data/battery_cycles_nasa.csv from the NASA Li-ion Battery Aging dataset.

Source: B. Saha and K. Goebel (2007), "Battery Data Set", NASA Prognostics Data Repository,
NASA Ames Research Center, Moffett Field, CA.
https://phm-datasets.s3.amazonaws.com/NASA/5.+Battery+Data+Set.zip

34 commercial 18650 Li-ion cells (rated 2.0 Ah) were repeatedly charged and discharged at
4, 24 and 43 °C until they wore out, with periodic impedance (EIS) measurements.

Usage (from the project root):
    python -m scripts.build_battery_dataset

One output row per discharge cycle:
    battery_id, cycle_count, ambient_temperature_c, discharge_current_a,
    avg_voltage_v, max_temperature_c, internal_resistance_mohm, capacity_ah, soh_pct
where internal_resistance_mohm = Re + Rct from the latest impedance test before the cycle, and
soh_pct = measured capacity / rated capacity (2.0 Ah) x 100.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from scipy.io import loadmat

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
ZIP_URL = "https://phm-datasets.s3.amazonaws.com/NASA/5.+Battery+Data+Set.zip"
ZIP_PATH = RAW_DIR / "nasa_battery_data_set.zip"
OUTPUT = ROOT / "data" / "battery_cycles_nasa.csv"
RATED_CAPACITY_AH = 2.0


def _download() -> None:
    if ZIP_PATH.exists():
        return
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {ZIP_URL} (210 MB) ...")
    with requests.get(ZIP_URL, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with ZIP_PATH.open("wb") as f:
            for chunk in resp.iter_content(1 << 20):
                f.write(chunk)


def _mat_files(zip_path: Path):
    """Yield (battery_id, loaded .mat dict); the archive nests zips inside a zip."""
    with zipfile.ZipFile(zip_path) as outer:
        for name in outer.namelist():
            if name.lower().endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(outer.read(name))) as inner:
                    for inner_name in inner.namelist():
                        if inner_name.lower().endswith(".mat"):
                            yield Path(inner_name).stem, loadmat(io.BytesIO(inner.read(inner_name)))
            elif name.lower().endswith(".mat"):
                yield Path(name).stem, loadmat(io.BytesIO(outer.read(name)))


def _scalar(value) -> float:
    arr = np.asarray(value).astype(complex).ravel()
    return float(arr[0].real) if arr.size else float("nan")


def _vector(data, field: str) -> np.ndarray:
    return np.asarray(data[field][0, 0], dtype=float).ravel()


def _cycles(battery_id: str, mat: dict) -> list[dict]:
    cycles = mat[battery_id][0, 0]["cycle"][0]
    rows, resistance, n_discharge = [], np.nan, 0
    for cycle in cycles:
        kind = str(cycle["type"][0])
        data = cycle["data"]
        if kind == "impedance":
            re_, rct = _scalar(data["Re"][0, 0]), _scalar(data["Rct"][0, 0])
            if np.isfinite(re_) and np.isfinite(rct) and 0 < re_ + rct < 1:  # ohms; skip glitches
                resistance = (re_ + rct) * 1000
        elif kind == "discharge":
            n_discharge += 1
            capacity = _scalar(data["Capacity"][0, 0])
            voltage = _vector(data, "Voltage_measured")
            current = _vector(data, "Current_measured")
            temperature = _vector(data, "Temperature_measured")
            loaded = current < -0.1  # samples where the cell was actually being discharged
            if not loaded.any():
                continue
            rows.append(
                {
                    "battery_id": battery_id,
                    "cycle_count": n_discharge,
                    "ambient_temperature_c": _scalar(cycle["ambient_temperature"]),
                    "discharge_current_a": round(float(-np.median(current[loaded])), 3),
                    "avg_voltage_v": round(float(voltage[loaded].mean()), 4),
                    "max_temperature_c": round(float(temperature.max()), 2),
                    "internal_resistance_mohm": round(resistance, 2),
                    "capacity_ah": round(capacity, 4),
                }
            )
    return rows


def main() -> None:
    _download()
    frames, seen = [], set()
    for battery_id, mat in _mat_files(ZIP_PATH):
        # B0025-B0028 are shipped in two of the archives; keep the first copy only.
        if battery_id in seen:
            print(f"{battery_id}: duplicate copy skipped")
            continue
        seen.add(battery_id)
        rows = _cycles(battery_id, mat)
        print(f"{battery_id}: {len(rows)} discharge cycles")
        frames.append(pd.DataFrame(rows))
    df = pd.concat(frames, ignore_index=True)
    report = {"raw_cycles": len(df)}

    # Cleaning:
    #  1. cycles without a prior impedance test (no resistance value) are dropped;
    #  2. capacities above 105% of rating or below 20% are measurement glitches;
    #  3. single-cycle spikes: points more than 10 SoH points from the battery's rolling median
    #     (window of 7 cycles) are dropped, a known artefact of this dataset;
    #  4. batteries left with fewer than 20 cycles can't show an ageing trend and are dropped.
    df = df.dropna(subset=["internal_resistance_mohm"])
    report["no_impedance_dropped"] = report["raw_cycles"] - len(df)
    df["soh_pct"] = (df.capacity_ah / RATED_CAPACITY_AH * 100).round(2)
    before = len(df)
    df = df[df.soh_pct.between(20, 105)]
    report["out_of_range_dropped"] = before - len(df)
    rolling = df.groupby("battery_id").soh_pct.transform(
        lambda s: s.rolling(7, center=True, min_periods=3).median()
    )
    before = len(df)
    df = df[(df.soh_pct - rolling).abs() <= 10]
    report["spikes_dropped"] = before - len(df)
    counts = df.battery_id.value_counts()
    df = df[df.battery_id.isin(counts[counts >= 20].index)]
    report.update(clean_cycles=len(df), batteries=df.battery_id.nunique())
    print(report)
    df.sort_values(["battery_id", "cycle_count"]).to_csv(OUTPUT, index=False)
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
