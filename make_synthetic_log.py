#!/usr/bin/env python3
"""
Generate a synthetic telemetry log in the JSON-lines format.

    python make_synthetic_log.py -o out.txt
    python make_synthetic_log.py -o out.txt --hours 2.34 --seed 7

For demos, load tests and exercising the dashboard when no real log of the
right shape exists. It is NOT a substitute for measured data, and nothing
generated here should reach a report.

Two safeguards, both deliberate:

  * The header carries `"synthetic": true` and a `"generated_by"` string. Any
    reader can tell this apart from a device log, which matters because the
    file is otherwise byte-compatible with one.
  * The header's `fields` list is the CORRECT nine names. Real device logs
    declare five names for nine columns and label engine speed "flowrate";
    reproducing that bug in generated data would spread it.

Behaviour is modelled on what the real logs actually show (FINDINGS.md):
bimodal RPM with the idle peak near 600 and the working peak near 1150, torque
tracking the split, and roughly 60% of running time idling -- so a dashboard
built from this looks like the real thing rather than like noise.
"""

import argparse
import datetime as dt
import json
import math
import os
import random
import sys

# Ibri, Ad Dhahirah -- inside the coordinate box the real fixes occupy.
BASE_LAT, BASE_LON = 23.428844, 58.099136

IDLE_RPM, WORK_RPM = 600, 1150
IDLE_TORQUE, WORK_TORQUE = 7, 19


def build(hours, interval_ms, seed, vehicle_id, imei, start):
    rng = random.Random(seed)
    step = interval_ms / 1000.0
    n = int(round(hours * 3600 / step))

    rows = []
    lat, lon = BASE_LAT, BASE_LON
    coolant = 25.0                      # cold start, warms to operating temp
    state = "idle"
    hold = 0                            # samples remaining in the current state
    heading = rng.uniform(0, 2 * math.pi)

    for i in range(n):
        t = start + dt.timedelta(seconds=i * step)

        # --- engine state: long-ish runs of idling and working ----------
        if hold <= 0:
            # ~60% of running time idling, in runs of 20 s to 4 min.
            state = "idle" if rng.random() < 0.60 else "working"
            hold = int(rng.uniform(20, 240) / step)
        hold -= 1

        if state == "idle":
            rpm = IDLE_RPM + rng.gauss(0, 14)
            torque = max(0, IDLE_TORQUE + rng.gauss(0, 3))
            fuel = max(0.0, 3.8 + rng.gauss(0, 0.9))
        else:
            rpm = WORK_RPM + rng.gauss(0, 85)
            torque = max(0, WORK_TORQUE + rng.gauss(0, 6))
            fuel = max(0.0, 9.5 + rng.gauss(0, 2.4))

        # --- coolant: a real warm-up curve, unlike the dead signal the
        #     hardware currently reports. This is generated data; it should
        #     look like a working sensor.
        target = 88.0 if state == "working" else 82.0
        coolant += (target - coolant) * 0.0006 + rng.gauss(0, 0.02)
        coolant = min(95.0, max(20.0, coolant))

        # --- position: an excavator repositions slowly and rarely --------
        if state == "working" and rng.random() < 0.002:
            heading += rng.gauss(0, 0.8)
        if state == "working":
            speed_ms = abs(rng.gauss(0.35, 0.25))       # < 6 km/h, tracked plant
        else:
            speed_ms = abs(rng.gauss(0.02, 0.02))       # essentially parked
        dlat = speed_ms * step * math.cos(heading) / 111_320.0
        dlon = speed_ms * step * math.sin(heading) / (
            111_320.0 * math.cos(math.radians(lat)))
        lat += dlat
        lon += dlon

        rows.append([
            t.strftime("%Y-%m-%dT%H:%M:%S.") + "%03d" % (t.microsecond // 1000),
            int(round(rpm)),
            int(round(torque)),
            round(fuel, 2),
            int(round(coolant)),
            round(lat, 6),
            round(lon, 6),
            1,      # gps_fix
            0,      # reserved
        ])

    header = {
        "vehicle_id": vehicle_id,
        "imei": imei,
        "log_interval_ms": interval_ms,
        "fields": ["timestamp", "rpm", "torque", "fuel", "coolant",
                   "latitude", "longitude", "gps_fix", "reserved"],
        "synthetic": True,
        "generated_by": "make_synthetic_log.py",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(
            timespec="seconds"),
        "seed": seed,
        "note": "GENERATED DATA - not measured. Do not cite in any report.",
    }
    return header, rows


def main():
    ap = argparse.ArgumentParser(
        description="Generate a synthetic telemetry log (JSON-lines format).")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--hours", type=float, default=2.34)
    ap.add_argument("--interval-ms", type=int, default=100)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--vehicle-id", default="VEH_001")
    ap.add_argument("--imei", default="862636058411560")
    ap.add_argument("--start", default="2026-06-24T13:00:25",
                    help="ISO start timestamp, UTC")
    args = ap.parse_args()

    start = dt.datetime.fromisoformat(args.start)
    header, rows = build(args.hours, args.interval_ms, args.seed,
                         args.vehicle_id, args.imei, start)

    with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("{\n")
        items = list(header.items())
        for i, (k, v) in enumerate(items):
            fh.write('"%s":%s%s\n' % (k, json.dumps(v),
                                      "," if i < len(items) - 1 else ""))
        fh.write("}\n")
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")

    idle = sum(1 for r in rows if r[1] < 900)
    size = os.path.getsize(args.output)
    print("wrote %s" % args.output)
    print("  rows        : %s" % format(len(rows), ","))
    print("  span        : %.2f h at %d ms" % (args.hours, args.interval_ms))
    print("  size        : %s bytes" % format(size, ","))
    print("  idle share  : %.1f%% of samples" % (100.0 * idle / len(rows)))
    print("  coverage    : 100%% -- every expected sample is present")
    print("  header      : synthetic=true, seed=%d" % args.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
