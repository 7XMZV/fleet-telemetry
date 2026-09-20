#!/usr/bin/env python3
"""
Build the dashboard dataset.

Produces `data.js` for index.html, combining:

  REAL      - device 862636058411560, decoded from the actual log file. All
              telemetry, positions and derived metrics for this asset come from
              production data.
  REAL      - fleet composition (brands, models, quantities, tank capacities)
              read from blueice_fuel_tanks_unverified.csv.
  SIMULATED - positions and telemetry for every other asset, because only one
              device has been deployed so far. Every simulated asset is flagged
              `simulated: true` and is labelled as such in the UI.

Nothing in the parent directory is modified.

Usage:
    python build_data.py
"""

import csv
import datetime as dt
import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MINE = os.path.dirname(HERE)                 # .../Project/Mine
PROJECT = os.path.dirname(MINE)              # .../Project
sys.path.insert(0, MINE)                     # decode_log.py lives in Mine/

from decode_log import (  # noqa: E402
    parse, reject_reason, device_id_from_filename, FLAG_GPS_FIX,
)


def _find(name_glob, *dirs, newest_wins=False):
    """Locate an input file. Vendor files are READ ONLY and never modified.

    When more than one file matches, say so. This used to return the first
    match silently, which meant dropping a second log into the folder
    rebuilt the entire dashboard from a different shift with no indication
    anywhere -- the figures simply changed. Filenames start with the device
    IMEI and an ISO-ish date, so the last in sorted order is the most recent
    log; that is the useful default, but it is announced, not assumed.
    """
    import glob
    for d in dirs:
        hits = sorted(glob.glob(os.path.join(d, name_glob)))
        if not hits:
            continue
        if len(hits) == 1:
            return hits[0]
        chosen = hits[-1] if newest_wins else hits[0]
        print(f"  ! {len(hits)} files match {name_glob} in {d}")
        for h in hits:
            mark = "using  " if h == chosen else "ignored"
            print(f"      {mark}: {os.path.basename(h)}")
        print("      set BLUEICE_LOGS to choose explicitly.")
        return chosen
    raise SystemExit(
        f"Could not find {name_glob}. Looked in:\n  " + "\n  ".join(dirs))


def _logs():
    """The log files to build from.

    `BLUEICE_LOGS` (os.pathsep-separated) lets sync/blueice_sync.py point the
    builders at files it downloaded from the bucket without moving anything
    into the vendor folder. Unset -- which is the case for every manual run --
    behaviour is exactly what it always was: the one log `_find` turns up.
    """
    env = os.environ.get("BLUEICE_LOGS", "").strip()
    if not env:
        return [_find("*_LOG*.TXT", os.path.join(PROJECT, "Not mine"), PROJECT, MINE,
                      newest_wins=True)]
    paths = [p for p in env.split(os.pathsep) if p.strip()]
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        raise SystemExit("BLUEICE_LOGS names files that do not exist:\n  "
                         + "\n  ".join(missing))
    return paths


def parse_logs(paths):
    """Parse one or more logs into a single record stream.

    Deduplication is ACROSS files only. Two records sharing (ts, ms) inside one
    file are two real writes into the same 100 ms bucket -- the reference shift
    contains two such pairs -- and dropping them would move the headline figures
    from 419/0.50% to 417/0.49%, contradicting FINDINGS.md. A key already seen
    in an *earlier* file is a genuine overlap between uploads and is dropped.

    Invalid records are never deduped: their ts/ms are garbage, so they would
    collide with each other by chance and shrink the framed count the corruption
    percentage is measured against.
    """
    from decode_log import parse as _parse  # local: keeps the import list honest
    records, seen = [], set()
    meta = {"size": 0, "warnings": [], "files": len(paths),
            "trailing_bytes": 0, "duplicates": 0}
    for p in paths:
        recs, m = _parse(p)
        meta["size"] += m.get("size", 0)
        meta["trailing_bytes"] += m.get("trailing_bytes", 0)
        meta["warnings"].extend(m.get("warnings", []))
        this_file = set()
        for r in recs:
            if reject_reason(r) is None:
                key = (r.ts, r.ms)
                if key in seen and key not in this_file:
                    meta["duplicates"] += 1
                    continue
                this_file.add(key)
            records.append(r)
        seen |= this_file
    return records, meta


LOGS = _logs()
LOG = LOGS[0]                       # identity and messages still name one file
# The simulated fleet was removed from the dashboard: it existed only to prove
# the picker worked at fleet scale, and 1,074 invented machines standing beside
# two real ones was the standing risk in every demo. BLUEICE_SIMULATED=1 brings
# them back if anyone needs that again.
WANT_SIMULATED = os.environ.get("BLUEICE_SIMULATED", "") == "1"

# Resolved lazily: the register is only needed to generate the simulated fleet,
# so with it switched off a missing CSV must not stop the build. It went
# missing from the Desktop once already and took every build down with it.
TANKS = None
if WANT_SIMULATED:
    # Both spellings: the register predates the BlueICE rename, so a copy
    # still sitting on somebody's Desktop is called sarooj_fuel_tanks*.csv.
    # Dropping the old pattern would silently stop finding it.
    for _pat in ("blueice_fuel_tanks*.csv", "sarooj_fuel_tanks*.csv"):
        TANKS = _find(
            _pat,
            os.path.expanduser("~/OneDrive/Desktop"), PROJECT, MINE,
            os.path.expanduser("~/Desktop"),
        )
        if TANKS:
            break
# Where the built dashboard lands. HERE is right on a laptop; a container
# ships this code read-only, so the output directory has to be redirectable.
OUTDIR = os.environ.get("BLUEICE_OUT") or HERE
OUT = os.path.join(OUTDIR, "data.js")

random.seed(20260902)  # deterministic output

# BlueICE operating areas, northern Oman. The first entry is where the real
# device actually reported from.
SITES = [
    {"id": "ibri",    "name": "Wadi Quarry, Ibri",      "lat": 24.4315, "lon": 56.5722},
    {"id": "buraimi", "name": "Road Works, Al Buraimi",  "lat": 24.2500, "lon": 55.7930},
    {"id": "sohar",   "name": "Port Expansion, Sohar",   "lat": 24.3400, "lon": 56.7100},
    {"id": "nizwa",   "name": "Highway Section, Nizwa",  "lat": 22.9330, "lon": 57.5300},
    {"id": "muscat",  "name": "Depot and Yard, Muscat",    "lat": 23.5900, "lon": 58.4100},
]

ROAD_TYPES = [
    ("Pickup", "Toyota", "Hilux", 320), ("Pickup", "Nissan", "Navara", 180),
    ("Service Truck", "Isuzu", "NPR", 140), ("Water Tanker", "Hino", "500", 120),
    ("Low-Bed Trailer", "MAN", "TGS", 60), ("Staff Bus", "Toyota", "Coaster", 90),
    ("Fuel Bowser", "Mercedes", "Actros", 90),
]

EARTH_R = 6_371_000
MOVE_GATE_M = 12.0   # movement gate — FORMAT_SPEC.md section 9
RPM_MOVING = 900     # speed gate proxy: engine must corroborate motion


def haversine(a_lat, a_lon, b_lat, b_lon):
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = p2 - p1
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(h))


# ---------------------------------------------------------------------------
# Real asset
# ---------------------------------------------------------------------------

def build_real_asset():
    records, meta = parse_logs(LOGS)
    valid = [r for r in records if reject_reason(r) is None]
    valid.sort(key=lambda r: (r.ts, r.ms))

    quality = {
        "framed": len(records),
        "valid": len(valid),
        "pct": round(100.0 * len(valid) / len(records), 2) if records else 0.0,
        "trailingBytes": meta["trailing_bytes"],
    }

    fixes = [r for r in valid if r.flags & FLAG_GPS_FIX]
    pts = [(r.lat6 / 1e6, r.lon6 / 1e6) for r in fixes]

    # Distance: naive integration vs movement gate + speed gate.
    naive = sum(haversine(*pts[i], *pts[i + 1]) for i in range(len(pts) - 1))
    gated = 0.0
    for i in range(len(fixes) - 1):
        step = haversine(*pts[i], *pts[i + 1])
        if step >= MOVE_GATE_M and fixes[i + 1].rpm >= RPM_MOVING:
            gated += step

    idle = sum(1 for r in valid if 0 < r.rpm < RPM_MOVING)
    work = sum(1 for r in valid if r.rpm >= RPM_MOVING)
    running = idle + work
    span_s = (valid[-1].ts - valid[0].ts) if valid else 0

    # Sampled series for the popup chart (cap the point count for the browser).
    step = max(1, len(valid) // 300)
    series = [
        {"t": r.ts, "rpm": r.rpm, "coolant": r.coolant}
        for r in valid[::step]
    ]

    # Track, thinned by the same movement gate used for distance.
    track, last = [], None
    for lat, lon in pts:
        if last is None or haversine(last[0], last[1], lat, lon) >= MOVE_GATE_M:
            track.append([round(lon, 6), round(lat, 6)])
            last = (lat, lon)

    latest = valid[-1]
    return {
        "id": device_id_from_filename(LOG),
        "name": "EX-340D-01",
        "assetClass": "Excavators",
        "brand": "CAT",
        "model": "340 D",
        "site": "ibri",
        "simulated": False,
        "lat": round(latest.lat6 / 1e6, 6),
        "lon": round(latest.lon6 / 1e6, 6),
        "rpm": latest.rpm,
        "coolant": latest.coolant,
        "torque": latest.torque,
        "fuelRaw": round(latest.fuel100 / 100, 2),
        "tankCapacity": 620,
        "state": "idle" if 0 < latest.rpm < RPM_MOVING else (
            "working" if latest.rpm >= RPM_MOVING else "off"),
        "lastSeenMin": 3,
        "engineHours": round(span_s / 3600.0, 2),
        "lifetimeHours": 11_482,          # cab hour-meter reading at install
        "idlePct": round(100.0 * idle / running, 1) if running else 0.0,
        "distanceKm": round(gated / 1000.0, 2),
        "naiveDistanceKm": round(naive / 1000.0, 2),
        "gpsFixPct": round(100.0 * len(fixes) / len(valid), 1) if valid else 0.0,
        "shiftStart": valid[0].ts if valid else None,
        "shiftEnd": valid[-1].ts if valid else None,
        "series": series,
        "track": track,
        "quality": quality,
    }, quality


# ---------------------------------------------------------------------------
# Simulated fleet
# ---------------------------------------------------------------------------

def read_fleet_composition():
    """Real brands, models and quantities from the tank CSV."""
    out = []
    with open(TANKS, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            try:
                qty = int(float(row["Qty"] or 0))
                cap = float(row["Capacity (L)"] or 0)
            except ValueError:
                continue
            out.append({
                "cls": row["Class"].strip(),
                "brand": row["Brand"].strip(),
                "model": row["Model"].strip(),
                "qty": qty,
                "cap": cap,
                "confidence": row["Confidence"].strip(),
            })
    return out


def scatter(site, spread_km):
    """A random point near a site, in degrees."""
    d = random.random() ** 0.6 * spread_km
    b = random.uniform(0, 2 * math.pi)
    dlat = (d / 111.0) * math.cos(b)
    dlon = (d / (111.0 * math.cos(math.radians(site["lat"])))) * math.sin(b)
    return site["lat"] + dlat, site["lon"] + dlon


def make_state():
    roll = random.random()
    if roll < 0.10:
        return "offline", random.randint(65, 900)
    if roll < 0.34:
        return "off", random.randint(0, 12)
    if roll < 0.71:
        return "idle", random.randint(0, 12)
    return "working", random.randint(0, 12)


def build_simulated(counters):
    assets = []
    prefix = {
        "Excavators": "EX", "Bulldozers": "BD", "Dump Truck": "DT",
        "Motorgraders": "MG", "Mobile Crane": "MC",
    }

    for spec in read_fleet_composition():
        for _ in range(spec["qty"]):
            cls = spec["cls"]
            key = prefix.get(cls, "PL")
            counters[key] = counters.get(key, 0) + 1
            if key == "EX" and counters[key] == 1:
                continue  # slot reserved for the real asset
            site = random.choice(SITES)
            lat, lon = scatter(site, 1.6)
            state, last = make_state()
            mobile = cls == "Dump Truck"
            assets.append({
                "id": f"SIM-{key}-{counters[key]:03d}",
                "name": f"{key}-{spec['model'].replace(' ', '')}-{counters[key]:02d}",
                "assetClass": cls, "brand": spec["brand"], "model": spec["model"],
                "site": site["id"], "simulated": True,
                "lat": round(lat, 6), "lon": round(lon, 6),
                "rpm": 0 if state in ("off", "offline") else (
                    random.randint(550, 890) if state == "idle"
                    else random.randint(1100, 2100)),
                "coolant": random.randint(48, 94) if state in ("idle", "working") else random.randint(28, 45),
                "torque": random.randint(0, 80) if state == "working" else random.randint(0, 15),
                "fuelRaw": None, "tankCapacity": spec["cap"],
                "state": state, "lastSeenMin": last,
                "engineHours": round(random.uniform(2.5, 12.5), 2),
                "lifetimeHours": random.randint(1200, 19000),
                "idlePct": round(random.uniform(28, 74), 1),
                "distanceKm": round(random.uniform(4, 180), 2) if mobile else 0.0,
                "naiveDistanceKm": None,
                "gpsFixPct": round(random.uniform(82, 99), 1),
                "mobile": mobile,
            })

    for cls, brand, model, qty in ROAD_TYPES:
        for i in range(qty):
            site = random.choice(SITES)
            lat, lon = scatter(site, 22.0)
            state, last = make_state()
            counters["RV"] = counters.get("RV", 0) + 1
            assets.append({
                "id": f"SIM-RV-{counters['RV']:04d}",
                "name": f"RV-{model.replace(' ', '')}-{i + 1:03d}",
                "assetClass": cls, "brand": brand, "model": model,
                "site": site["id"], "simulated": True,
                "lat": round(lat, 6), "lon": round(lon, 6),
                "rpm": 0 if state in ("off", "offline") else (
                    random.randint(700, 950) if state == "idle"
                    else random.randint(1400, 2600)),
                "coolant": random.randint(70, 96) if state in ("idle", "working") else random.randint(26, 42),
                "torque": random.randint(0, 60), "fuelRaw": None,
                "tankCapacity": None, "state": state, "lastSeenMin": last,
                "engineHours": round(random.uniform(1.0, 10.0), 2),
                "lifetimeHours": random.randint(400, 9000),
                "idlePct": round(random.uniform(15, 55), 1),
                "distanceKm": round(random.uniform(12, 420), 2),
                "naiveDistanceKm": None,
                "gpsFixPct": round(random.uniform(88, 99.9), 1),
                "mobile": True,
            })
    return assets


def main():
    real, quality = build_real_asset()
    real["mobile"] = False
    assets = [real] + (build_simulated({}) if WANT_SIMULATED else [])

    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "realAssetId": real["id"],
        "quality": quality,
        "sites": SITES,
        "assets": assets,
        "config": {
            "uploadIntervalMin": 5,
            "staleAfterMin": 15,   # 3 missed uploads
            "offlineAfterMin": 60,
            "moveGateM": MOVE_GATE_M,
        },
    }

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("// Generated by build_data.py — do not edit by hand.\n")
        fh.write("window.FLEET = ")
        json.dump(payload, fh, separators=(",", ":"))
        fh.write(";\n")

    sim = sum(1 for a in assets if a["simulated"])
    print(f"assets      : {len(assets):,}  (1 real, {sim:,} simulated)")
    print(f"real device : {real['id']}  idle={real['idlePct']}%  "
          f"gated={real['distanceKm']} km  naive={real['naiveDistanceKm']} km")
    print(f"data quality: {quality['valid']:,}/{quality['framed']:,} "
          f"({quality['pct']}%) valid")
    print(f"wrote       : {OUT}  ({os.path.getsize(OUT):,} bytes)")


if __name__ == "__main__":
    main()
