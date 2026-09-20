#!/usr/bin/env python3
"""
Generate the fuel-reconciliation fixtures: DEMO-FUEL-01 through 05.

    python make_fuel_demo.py                 -> Mine/demo/, all five cases
    python make_fuel_demo.py --case clean    -> just one

There is no real fuel data yet. This produces complete, self-consistent
scenarios in the exact shape the device will send, so the reconciliation panel
can be built and -- more importantly -- tested against every outcome it can
produce, not just the interesting one.

EVERYTHING THIS WRITES IS GENERATED. Every file carries `"synthetic": true` in
its header and a device id in the 9999999999999xx range, which is not a real
IMEI. A fuel balance is an accusation; a fabricated one must never be
mistakable for a measurement.

The cases, and what each one is for:

    siphon     DEMO-FUEL-01   60 L taken between two refuels   -> about +60 L
    clean      DEMO-FUEL-02   nothing wrong                    -> about   0 L
    unlogged   DEMO-FUEL-03   90 L put in with no docket       -> about -90 L
    norefuel   DEMO-FUEL-04   no refuel in the shift           -> nothing to compare
    lowtank    DEMO-FUEL-05   tank down to single-figure %     -> nothing to compare

`clean` matters as much as `siphon`: a reconciliation that cannot reliably read
ZERO on a good shift is useless, because every shift would look like a theft.
`unlogged` is the case nobody thinks to build -- fuel APPEARING is a
bookkeeping failure, not a loss, and it must not wear the cost colour.

Three signals are emitted, and they are independent by construction, because
that is the only reason the arithmetic works:

  * level      mm of depth, from the tank sender. Sees everything -- burn,
               refuels, theft -- plus measurement noise.
  * flowrate   mm/h, from the in-line meter in the fuel line. Sees ONLY what
               the engine actually burned. Blind to refuels and to theft.
  * dispensed  litres, from the bowser's own flow meter, written to a separate
               per-machine docket file.

A siphon shows up as level falling while the in-line meter reads nothing. An
unlogged delivery shows up as level rising with no docket to explain it.
"""

import argparse
import datetime as dt
import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "demo")

DEVICE = "999999999999999"          # deliberately not a real IMEI
VEHICLE_ID = "DEMO-FUEL-01"

# Every outcome the reconciliation panel can produce, one machine each. A
# single case only ever exercises one branch, and the branches that matter most
# are the ones nobody thinks to look at: a clean shift that must read zero, and
# fuel APPEARING, which is a bookkeeping failure rather than a loss.
#
# kind:  dispense  tank rises AND a bowser docket is written
#        unlogged  tank rises with NO docket -- the negative-balance case
#        siphon    tank falls with no burn to explain it
CASES = {
    "siphon": dict(
        device="999999999999999", vehicle="DEMO-FUEL-01", hours=3.0, open_mm=420.0,
        blurb="60 L siphoned between two refuels", base=(23.4288, 58.0991),
        events=[(45, "dispense", 180.0, 3.0),
                (110, "siphon", 60.0, 4.0),
                (160, "dispense", 150.0, 3.0)]),
    "clean": dict(
        device="999999999999998", vehicle="DEMO-FUEL-02", hours=2.0, open_mm=300.0,
        blurb="nothing wrong -- the balance must read about zero",
        base=(23.2250, 56.5150),        # Ibri
        events=[(40, "dispense", 150.0, 3.0),
                (100, "dispense", 120.0, 3.0)]),
    "unlogged": dict(
        device="999999999999997", vehicle="DEMO-FUEL-03", hours=2.0, open_mm=300.0,
        blurb="90 L put in with no docket -- balance goes NEGATIVE",
        base=(24.3400, 56.7100),        # Sohar
        events=[(40, "dispense", 120.0, 3.0),
                (75, "unlogged", 90.0, 3.0),
                (110, "dispense", 100.0, 3.0)]),
    "norefuel": dict(
        device="999999999999996", vehicle="DEMO-FUEL-04", hours=2.0, open_mm=600.0,
        blurb="no refuel at all -- nothing to reconcile between",
        base=(22.9330, 57.5330),        # Nizwa
        events=[]),
    "lowtank": dict(
        device="999999999999995", vehicle="DEMO-FUEL-05", hours=2.0, open_mm=80.0,
        blurb="tank running down to single-figure percent",
        base=(23.6100, 57.9200),        # Rustaq road
        events=[]),
}

# Tank geometry. An assumption until a real refuel measures it: the panel
# prints it on screen rather than presenting the litres as fact.
TANK_L = 620.0
TANK_MM = 800.0
L_PER_MM = TANK_L / TANK_MM         # 0.775

# Burn rates as depth, so they are in the same unit the meter reports.
# ~20 L/h working and ~8 L/h idling for plant this size.
WORK_MM_H = 20.0 / L_PER_MM
IDLE_MM_H = 8.0 / L_PER_MM

# Measurement noise on the level sender. Slosh while the machine is moving is
# what makes a small theft hard to see; a generator without it would let any
# threshold look perfect.
NOISE_MOVING_MM = 3.0
NOISE_STILL_MM = 0.4

BASE_LAT, BASE_LON = 23.4288, 58.0991

EVENTS = [
    # (minutes from start, kind, litres, minutes it takes)
    (45, "dispense", 180.0, 3.0),
    (110, "siphon", 60.0, 4.0),
    (160, "dispense", 150.0, 3.0),
]


def build(hours, interval_ms, seed, start, events=None, open_mm=420.0,
          device=DEVICE, vehicle=VEHICLE_ID, base=None):
    rng = random.Random(seed)
    step_s = interval_ms / 1000.0
    step_h = step_s / 3600.0
    n = int(round(hours * 3600 / step_s))

    # Expand the events into per-sample rates.
    ev = []
    for at_min, kind, litres, dur_min in (events if events is not None else EVENTS):
        i0 = int(at_min * 60 / step_s)
        i1 = i0 + max(1, int(dur_min * 60 / step_s))
        ev.append({"i0": i0, "i1": i1, "kind": kind, "litres": litres,
                   "mm_per_sample": (litres / L_PER_MM) / (i1 - i0),
                   "at": start + dt.timedelta(minutes=at_min)})

    # Starting depth is chosen so neither refuel hits the top of the tank.
    # At 520 mm the second fill overflowed and ~21 L disappeared into the
    # clamp, which would have shown up as a phantom loss in any interval
    # spanning it.
    level = open_mm                    # mm of depth at the start of the shift
    engine, hold = "working", 0
    heading = rng.uniform(0, 2 * math.pi)
    lat, lon = base if base else (BASE_LAT, BASE_LON)
    coolant = 30.0

    nine, five = [], []
    truth = {"burned_mm": 0.0, "stolen_l": 0.0, "dispensed_l": 0.0,
             "unlogged_l": 0.0}

    for i in range(n):
        t = start + dt.timedelta(seconds=i * step_s)
        active = [e for e in ev if e["i0"] <= i < e["i1"]]
        refuelling = any(e["kind"] in ("dispense", "unlogged") for e in active)
        siphoning = any(e["kind"] == "siphon" for e in active)

        # --- engine state -------------------------------------------------
        # Nobody refuels or siphons a running machine.
        if refuelling or siphoning:
            engine, hold = "off", 0
        else:
            if hold <= 0:
                engine = "idle" if rng.random() < 0.55 else "working"
                hold = int(rng.uniform(40, 300) / step_s)
            hold -= 1

        if engine == "off":
            rpm, torque, burn_mm_h = 0, 0, 0.0
        elif engine == "idle":
            rpm = int(620 + rng.gauss(0, 15))
            torque = max(0, int(round(7 + rng.gauss(0, 3))))
            burn_mm_h = IDLE_MM_H * (1 + rng.gauss(0, 0.05))
        else:
            rpm = int(1180 + rng.gauss(0, 90))
            torque = max(0, int(round(21 + rng.gauss(0, 6))))
            burn_mm_h = WORK_MM_H * (1 + rng.gauss(0, 0.07))

        # --- the tank -----------------------------------------------------
        burned_mm = burn_mm_h * step_h
        level -= burned_mm
        truth["burned_mm"] += burned_mm
        for e in active:
            if e["kind"] in ("dispense", "unlogged"):
                level += e["mm_per_sample"]
                key = "dispensed_l" if e["kind"] == "dispense" else "unlogged_l"
                truth[key] += e["mm_per_sample"] * L_PER_MM
            else:
                level -= e["mm_per_sample"]
                truth["stolen_l"] += e["mm_per_sample"] * L_PER_MM
        level = max(0.0, min(TANK_MM, level))

        # --- position -----------------------------------------------------
        moving = engine == "working" and rng.random() < 0.5
        if moving:
            heading += rng.gauss(0, 0.05)
            speed = abs(rng.gauss(0.4, 0.25))         # m/s, tracked plant
            lat += speed * step_s * math.cos(heading) / 111_320.0
            lon += speed * step_s * math.sin(heading) / (
                111_320.0 * math.cos(math.radians(lat)))

        # --- what the sensors report --------------------------------------
        noise = NOISE_MOVING_MM if moving else NOISE_STILL_MM
        level_read = round(level + rng.gauss(0, noise / 3.0), 2)
        # The in-line meter is in the fuel line: it sees the engine only.
        flow_read = round(burn_mm_h * (1 + rng.gauss(0, 0.02)), 2)

        target = 86.0 if engine == "working" else (78.0 if engine == "idle" else 30.0)
        coolant += (target - coolant) * 0.0008
        stamp = t.strftime("%Y-%m-%dT%H:%M:%S.") + "%03d" % (t.microsecond // 1000)

        nine.append([stamp, rpm, torque,
                     round(burn_mm_h * L_PER_MM, 2),      # ECU estimate, L/h
                     int(round(coolant)), round(lat, 6), round(lon, 6), 1, 0])
        five.append([stamp, flow_read, level_read, round(lat, 6), round(lon, 6)])

    dispenses = [{
        "machine_id": vehicle,
        "device_id": device,
        "timestamp_utc": e["at"].isoformat(timespec="seconds") + "Z",
        "litres": e["litres"],
        "bowser_id": "BWS-02",
        "operator": "generated",
    } for e in ev if e["kind"] == "dispense"]

    return nine, five, dispenses, truth


def write_log(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("{\n")
        items = list(header.items())
        for i, (k, v) in enumerate(items):
            fh.write('"%s":%s%s\n' % (k, json.dumps(v),
                                      "," if i < len(items) - 1 else ""))
        fh.write("}\n")
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")


def emit(case, name, out_dir, interval_ms, seed, start):
    """Write one case: two logs plus its own docket file."""
    device, vehicle = case["device"], case["vehicle"]
    nine, five, dispenses, truth = build(
        case["hours"], interval_ms, seed, start,
        events=case["events"], open_mm=case["open_mm"],
        device=device, vehicle=vehicle, base=case.get("base"))

    stem = "%s_%s_LOG000" % (device, start.strftime("%Y_%m_%d_%H_%M_%S"))
    common = {
        "vehicle_id": vehicle,
        "imei": device,
        "log_interval_ms": interval_ms,
        "synthetic": True,
        "generated_by": "make_fuel_demo.py",
        "case": name,
        "note": "GENERATED DATA - not measured. Never cite in a report.",
        "tank_litres": TANK_L,
        "tank_depth_mm": TANK_MM,
        "litres_per_mm": round(L_PER_MM, 6),
    }
    engine_path = os.path.join(out_dir, stem + ".txt")
    fuel_path = os.path.join(out_dir, stem + ".1.txt")
    # Per-machine, not a single shared dispenses.json -- several demo machines
    # now live in this folder and one shared docket file would credit every
    # refuel to all of them.
    disp_path = os.path.join(out_dir, stem + ".dispenses.json")

    write_log(engine_path, dict(common, fields=[
        "timestamp", "rpm", "torque", "fuel", "coolant",
        "latitude", "longitude", "gps_fix", "reserved"]), nine)
    write_log(fuel_path, dict(common, fields=[
        "timestamp", "flowrate", "level", "latitude", "longitude"]), five)
    with open(disp_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"synthetic": True, "bowser_flow_meter": True,
                   "machine_id": vehicle, "dispenses": dispenses}, fh, indent=2)

    burned_l = truth["burned_mm"] * L_PER_MM
    expected = truth["dispensed_l"] - (truth["dispensed_l"] + truth["unlogged_l"]
                                       - truth["stolen_l"] - truth["stolen_l"] * 0)
    # (what went in, recorded) - (what came out) -> what the panel should say.
    # Unlogged fuel went in without a docket, so it reads as a NEGATIVE balance.
    expect = truth["stolen_l"] - truth["unlogged_l"]
    print("  %-9s %-14s %5.1f h  burned %6.1f L  docketed %6.1f L  "
          "unlogged %5.1f L  siphoned %5.1f L  -> expect %+6.1f L"
          % (name, vehicle, case["hours"], burned_l, truth["dispensed_l"],
             truth["unlogged_l"], truth["stolen_l"], expect))
    return expect


def main():
    ap = argparse.ArgumentParser(
        description="Generate the DEMO-FUEL-* fuel-reconciliation fixtures.")
    ap.add_argument("--case", default="all",
                    help="one of: " + ", ".join(CASES) + ", or 'all'")
    ap.add_argument("--interval-ms", type=int, default=100)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--start", default="2026-09-09T06:00:00")
    ap.add_argument("-o", "--out", default=OUT_DIR)
    args = ap.parse_args()

    names = list(CASES) if args.case == "all" else [args.case]
    for n in names:
        if n not in CASES:
            raise SystemExit("unknown case %r. Known: %s" % (n, ", ".join(CASES)))

    os.makedirs(args.out, exist_ok=True)
    start = dt.datetime.fromisoformat(args.start)
    print("writing %d case(s) to %s" % (len(names), args.out))
    print()
    for i, n in enumerate(names):
        # Each case gets its own day so the picker labels them apart.
        emit(CASES[n], n, args.out, args.interval_ms, args.seed + i,
             start + dt.timedelta(days=i))
    print()
    print("Every file is marked synthetic=true and carries a non-IMEI device id.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
