"""Generate realistic, deterministic example data (offline, stdlib only).

Run::

    python examples/generate_examples.py

It writes ``examples/samples.csv`` and ``examples/samples.json`` containing
several dated site cores (with known start years, occasional missing rings),
one deliberately undated core (``UNKNOWN_01``), and a couple of rows that
demonstrate sparse-year listings.
"""

from __future__ import annotations

import csv
import json
import math
import os
import random

YEARS = list(range(1900, 2021))          # 122 yr climate signal
KNOWN_NARROW = [1914, 1947, 1976, 2002]  # pointer years


def climate_signal() -> dict[int, float]:
    rnd = random.Random(20240517)
    sig = {}
    prev = 1.0
    for y in YEARS:
        # AR(1)-like common signal
        shock = rnd.gauss(0, 0.12)
        if y in KNOWN_NARROW:
            shock -= 0.55
        v = max(0.25, 1.0 + 0.55 * (prev - 1.0) + shock)
        sig[y] = v
        prev = v
    return sig


def make_core(sig, start, length, seed, *, miss_prob=0.06):
    rnd = random.Random(seed)
    base = rnd.uniform(0.8, 2.4)        # tree-specific mean width (mm)
    trend = rnd.uniform(-0.004, 0.0)    # mild age trend
    rows = []
    for i in range(length):
        y = start + i
        w = base * sig[y] * (1 + trend * i) * rnd.lognormvariate(0, 0.05)
        missing = (y in KNOWN_NARROW
                   and rnd.random() < miss_prob)
        if missing:
            w = 0.0
        rows.append({"year": y, "width": 0.0 if missing else round(w, 3),
                     "missing": missing})
    return rows


def write_csv(path, samples):
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["sample_id", "unit", "start_year", "year",
                     "width", "missing"])
        for s in samples:
            for i, r in enumerate(s["rings"]):
                if s.get("sparse") and r["missing"]:
                    continue  # demonstrate implicit gap filling
                wr.writerow([
                    s["sample_id"] if i == 0 else "",
                    s["unit"] if i == 0 else "",
                    s["start_year"] if i == 0 else "",
                    r["year"] if s["dated"] and s.get("explicit_years")
                    else "",
                    r["width"],
                    1 if r["missing"] else 0])


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    sig = climate_signal()

    dated_specs = [
        ("SITE_A01", 1901, 118, 11),
        ("SITE_A02", 1905, 110, 22),
        ("SITE_B01", 1920, 95, 33),
        ("SITE_B02", 1931, 85, 44),
        ("SITE_C01", 1940, 72, 55),
    ]
    samples = []
    for sid, start, length, seed in dated_specs:
        rows = make_core(sig, start, length, seed)
        samples.append({"sample_id": sid, "unit": "mm",
                        "start_year": start, "dated": True,
                        "rings": rows})
    # one sample listed with explicit years and a gap (sparse format)
    rows = make_core(sig, 1950, 60, 66)
    samples.append({"sample_id": "SITE_C02", "unit": "mm",
                    "start_year": "", "dated": True,
                    "explicit_years": True, "sparse": True, "rings": rows})

    # undated core: 60 rings really starting 1948, answer hidden from user
    true_start = 1948
    unknown = make_core(sig, true_start, 60, 77)
    samples.append({"sample_id": "UNKNOWN_01", "unit": "mm",
                    "start_year": "", "dated": False, "rings": unknown})

    write_csv(os.path.join(here, "samples.csv"), samples)

    payload = {"samples": []}
    for s in samples:
        if s["sample_id"] == "UNKNOWN_01":
            payload["samples"].append({
                "sample_id": s["sample_id"], "unit": "mm",
                "rings": [{"width": r["width"],
                           "missing": r["missing"]} for r in s["rings"]]})
        else:
            payload["samples"].append({
                "sample_id": s["sample_id"], "unit": "mm",
                "start_year": int(s["start_year"]) if s["start_year"] else None,
                "rings": [{"year": r["year"] if s.get("explicit_years")
                           else None,
                           "width": r["width"],
                           "missing": r["missing"]} for r in s["rings"]]})
    with open(os.path.join(here, "samples.json"), "w",
              encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("wrote samples.csv / samples.json with", len(samples),
          "samples; UNKNOWN_01 true start (for the instructor) =",
          true_start)


if __name__ == "__main__":
    main()
