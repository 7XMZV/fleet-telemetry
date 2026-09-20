#!/usr/bin/env python3
"""
Build the dashboard from SEVERAL logs at once, each selectable in the picker.

    python build_datasets.py                       # every log found
    python build_datasets.py a.TXT b.txt           # just these, in this order

`build_data.py` and `build_track.py` each render exactly one shift. This runs
them once per log and merges the results, so the page carries every shift at
the same time and switching between them is instant -- no rebuild, no editing
a config, no losing the other one.

Each log becomes its own entry in the vehicle picker, with its own id
(`<imei>@<date>`), its own live figures and its own history. The simulated
fleet is shared, because it is identical in every build.

Output is the usual pair, `data.js` and `track.js`, so nothing else changes.
`track.js` gains `window.TRACKS`, a map of asset id to history;
`window.TRACK` still points at the first shift for anything reading the old
shape.
"""

import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MINE = os.path.dirname(HERE)
PROJECT = os.path.dirname(MINE)
sys.path.insert(0, MINE)

from decode_log import parse_fuel                       # noqa: E402
from fuel_reconcile import reconcile                    # noqa: E402


# What each generated fixture is there to demonstrate. Shown in the picker,
# because five machines all labelled "generated" tell you nothing about which
# branch of the panel each one exercises.
# IMEIs the BlueICE asset register actually maps to a machine. Anything else is
# named by whatever the device calls itself in its header -- honest, and it
# stops four devices sharing one name.
REGISTERED = {
    "862636058411560": "EX-340D-01",
}

CASE_LABELS = {
    "siphon":   "fuel missing",
    "clean":    "clean shift",
    "unlogged": "unlogged delivery",
    "norefuel": "no refuel",
    "lowtank":  "tank nearly empty",
}


def fuel_vehicle_id(log):
    """The name and demo case the device calls itself, from its own header."""
    from decode_log import _read_jsonl
    try:
        header, _, _ = _read_jsonl(log)
        return (header.get("vehicle_id") or "GENERATED",
                CASE_LABELS.get(header.get("case"), ""))
    except Exception:
        return ("GENERATED", "")


def fuel_block(log):
    """Attach fuel reconciliation, if this log has a fuel companion.

    The device writes two files per shift: `<stem>.txt` for engine telemetry
    and `<stem>.1.txt` for fuel. The bowser's dispense records arrive
    separately as dispenses.json. All three are needed -- the tank alone
    cannot tell burning from siphoning.
    """
    stem, _ = os.path.splitext(log)
    companion = stem + ".1.txt"
    if not os.path.isfile(companion):
        return None

    samples, meta = parse_fuel(companion)
    if len(samples) < 2:
        return None

    lpm = meta.get("litres_per_mm")
    if lpm is None:
        # Without tank geometry the mm cannot become litres. Say so rather
        # than picking a number that would look like a measurement.
        return {"hasLevelSensor": True, "unknownGeometry": True,
                "synthetic": meta.get("synthetic", False)}

    # Per-machine dockets first. A single shared dispenses.json would credit
    # every refuel to every machine in the folder, which with several demo
    # cases side by side is a silent, plausible-looking wrong answer.
    disp_path = stem + ".dispenses.json"
    if not os.path.isfile(disp_path):
        disp_path = os.path.join(os.path.dirname(log), "dispenses.json")
    dispenses = []
    if os.path.isfile(disp_path):
        for d in json.load(open(disp_path, encoding="utf-8"))["dispenses"]:
            t = dt.datetime.fromisoformat(
                d["timestamp_utc"].replace("Z", "+00:00")).timestamp()
            dispenses.append({"t": t, "litres": d["litres"],
                              "bowser": d.get("bowser_id", "")})

    periods = reconcile(samples, dispenses, lpm)

    # Level over time for the chart, thinned. The chart is what makes a theft
    # legible -- a step down with no dispense against it.
    step = max(1, len(samples) // 600)
    series = [[round(s[0], 1), round(s[2] * lpm, 1)] for s in samples[::step]]

    # Cumulative litres burned, so fuel used over ANY window is just the
    # difference between its two ends. The level series cannot answer that on
    # its own -- a refuel inside the window would hide the burn.
    cum, acc, burn = [], 0.0, []
    for i, s in enumerate(samples):
        if i:
            acc += samples[i-1][1] * (s[0] - samples[i-1][0]) / 3600.0
        cum.append(acc)
    for i in range(0, len(samples), step):
        burn.append([round(samples[i][0], 1), round(cum[i] * lpm, 2)])
    if burn and burn[-1][0] != round(samples[-1][0], 1):
        burn.append([round(samples[-1][0], 1), round(cum[-1] * lpm, 2)])

    burned_mm = sum(
        samples[i][1] * (samples[i + 1][0] - samples[i][0]) / 3600.0
        for i in range(len(samples) - 1))
    hours = (samples[-1][0] - samples[0][0]) / 3600.0

    # What is in the tank now. Median of the closing samples rather than the
    # last one: the sender wanders several mm while the machine moves, and a
    # single reading would put litres of slosh into the headline figure.
    tail = sorted(s[2] for s in samples[-600:])
    current_mm = tail[len(tail) // 2]
    head = sorted(s[2] for s in samples[:600])
    opening_mm = head[len(head) // 2]
    depth = meta["header"].get("tank_depth_mm")

    return {
        "hasLevelSensor": True,
        "synthetic": meta.get("synthetic", False),
        "litresPerMm": round(lpm, 4),
        "tankLitres": meta["header"].get("tank_litres"),
        "tankDepthMm": meta["header"].get("tank_depth_mm"),
        "assumedGeometry": True,     # until a real refuel measures it
        "burnedL": round(burned_mm * lpm, 1),
        "currentLevelL": round(current_mm * lpm, 1),
        "currentLevelPct": round(100.0 * current_mm / depth) if depth else None,
        "openingLevelL": round(opening_mm * lpm, 1),
        "dispensedTotalL": round(sum(d["litres"] for d in dispenses), 1),
        # The same reconciliation as `periods`, but across the whole shift, so
        # the totals column adds up on its own. Without it a reader sees more
        # fuel in the tank than was ever delivered and has no way to check the
        # difference -- the opening level is what closes it.
        "wentInL": round(opening_mm * lpm
                         + sum(d["litres"] for d in dispenses), 1),
        "cameOutL": round((current_mm + burned_mm) * lpm, 1),
        "shiftBalanceL": round(
            (opening_mm * lpm + sum(d["litres"] for d in dispenses))
            - (current_mm + burned_mm) * lpm, 1),
        "litresPerHour": round(burned_mm * lpm / hours, 1) if hours else None,
        "dispenses": [{"t": d["t"], "litres": d["litres"], "bowser": d["bowser"]}
                      for d in dispenses],
        "periods": periods,
        "latest": periods[-1] if periods else None,
        "levelSeries": series,
        "burnSeries": burn,
    }

OUTDIR = os.environ.get("BLUEICE_OUT") or HERE
DATA_JS = os.path.join(OUTDIR, "data.js")
TRACK_JS = os.path.join(OUTDIR, "track.js")


def payload(path):
    """Pull the JSON object out of a `window.X = {...};` file."""
    src = open(path, encoding="utf-8").read()
    m = re.search(r"=\s*(\{.*\})\s*;?\s*$", src, re.S)
    if not m:
        raise SystemExit("could not read a payload out of " + path)
    return json.loads(m.group(1))


def find_logs():
    hits = []
    for d in (os.path.join(PROJECT, "Not mine"), PROJECT, MINE):
        for pattern in ("*_LOG*.TXT", "*_LOG*.txt"):
            hits += glob.glob(os.path.join(d, pattern))
        if hits:
            break
    # Case-insensitive filesystems return the same file twice.
    seen, out = set(), []
    for h in sorted(hits):
        k = os.path.normcase(os.path.abspath(h))
        if k not in seen:
            seen.add(k)
            out.append(h)
    return out


def build_one(live_logs, history_logs):
    """Live figures from the newest shift; history from everything.

    The two builders are driven from different log sets on purpose. "What is
    this car doing" is the latest shift; "where has it been" is its whole
    record. Feeding both the same files would either turn Live into a summary
    of months, or throw the history away.
    """
    def run(script, logs):
        env = dict(os.environ, BLUEICE_LOGS=os.pathsep.join(logs))
        r = subprocess.run([sys.executable, script], cwd=HERE, env=env,
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("%s failed on %s:\n%s\n%s"
                             % (script, os.path.basename(logs[0]),
                                r.stdout, r.stderr))
    run("build_data.py", live_logs)
    run("build_track.py", history_logs)
    return payload(DATA_JS), payload(TRACK_JS)


# How many logs of history each car carries. Uncapped, a car with months in
# the bucket would parse millions of records on every 5-minute sync and the
# schedule would never finish. Raise with BLUEICE_HISTORY_LOGS.
HISTORY_LOGS = int(os.environ.get("BLUEICE_HISTORY_LOGS", "8"))


def device_of(log):
    """The car this log belongs to. Identity lives in the filename."""
    m = re.match(r"(\d{15})_", os.path.basename(log))
    return m.group(1) if m else os.path.basename(log)


def main():
    logs = sys.argv[1:] or find_logs()
    if not logs:
        raise SystemExit("no logs found")
    missing = [p for p in logs if not os.path.isfile(p)]
    if missing:
        raise SystemExit("no such file:\n  " + "\n  ".join(missing))

    # Resolve against the CALLER's directory before handing the list on. The
    # builders are launched with cwd=HERE, so a relative path like
    # `demo/x.txt` -- which is exactly what `build_datasets.py demo/*.txt`
    # produces, and what the README tells people to type -- stopped resolving
    # the moment it crossed into the subprocess.
    logs = [os.path.abspath(p) for p in logs]

    # Drop fuel companions. A `*.1.txt` is the 5-column fuel file that sits
    # beside an engine log and is loaded automatically with it -- passing one
    # in explicitly, which any `demo/*.txt` glob does, made the decoder raise
    # rather than ignore it.
    companions = [p for p in logs if p.lower().endswith(".1.txt")]
    if companions:
        logs = [p for p in logs if not p.lower().endswith(".1.txt")]
        print("  ignoring %d fuel companion file(s) -- loaded with their engine log"
              % len(companions))

    # ONE ENTRY PER CAR, not per log file. A car is a thing in the yard with a
    # history; a log file is an upload. Keying the picker on files meant one
    # machine appeared three times with no way to tell the entries apart, and
    # an update added a row instead of refreshing the car.
    #
    # Filenames start with the IMEI and an ISO-ish timestamp, so sorting them
    # is chronological without opening anything.
    by_car = {}
    for log in sorted(logs, key=lambda p: os.path.basename(p).lower()):
        by_car.setdefault(device_of(log), []).append(log)

    print("building %d car(s) from %d log(s)" % (len(by_car), len(logs)))
    fleet = None
    real_assets, tracks = [], {}

    for imei, car_logs in sorted(by_car.items()):
        live_logs = [car_logs[-1]]                  # newest shift = Live
        history_logs = car_logs[-HISTORY_LOGS:]     # everything = Trace
        log = live_logs[0]
        name = os.path.basename(log)
        data, track = build_one(live_logs, history_logs)

        if fleet is None:                       # simulated fleet is shared
            fleet = data

        real = next(a for a in data["assets"] if a["id"] == data["realAssetId"])
        day = dt.datetime.fromtimestamp(
            track["t1"], dt.timezone.utc).strftime("%Y-%m-%d")
        label = dt.datetime.fromtimestamp(
            track["t1"], dt.timezone.utc).strftime("%d %b")

        # The car's id IS the car. Stable across every update, so a new upload
        # replaces that car's Live figures and lengthens its history instead
        # of creating a second entry for the same machine.
        asset_id = imei

        fuel = fuel_block(log)
        generated = bool(fuel and fuel.get("synthetic"))
        # A generated asset must never wear a real machine's name. The fuel
        # demo carries an invented balance, and a balance is an accusation.
        # Identity, in order of authority:
        #   1. the asset register, for devices BlueICE has actually mapped
        #   2. the name the device calls itself in its own header
        #   3. the IMEI
        # build_data.py hardcodes EX-340D-01 because it was written when one
        # device existed. With four in the bucket that made every real car
        # share a name -- two of them identical down to the date label.
        hdr_name, demo_case = fuel_vehicle_id(log)
        if generated:
            display = hdr_name
        else:
            known = REGISTERED.get(real["id"])
            base = known or (hdr_name if hdr_name != "GENERATED" else real["id"])
            display = "%s (%s)" % (base, label)
            demo_case = ""

        real = dict(real, id=asset_id, name=display,
                    shiftDate=day, sourceLog=name,
                    generated=generated, simulated=False, demoCase=demo_case)
        if generated:
            # It inherits the real machine's identity from build_data's
            # template. A fixture must not claim a real model at a real
            # BlueICE site -- that is how a screenshot of an invented theft
            # ends up pointing at somewhere that exists.
            real.update(brand="—", model="fuel reconciliation fixture",
                        assetClass="Demo", site=None)
        if fuel:
            real["fuel"] = fuel
        track = dict(track, assetId=asset_id, sourceLog=name, shiftDate=day)

        real["historyLogs"] = len(history_logs)
        real["latestShift"] = day
        real_assets.append(real)
        tracks[asset_id] = track
        print("  %-22s live %s from %-44s  history %d log(s), %s fixes"
              % (display, day, name[:44], len(history_logs),
                 format(track["fixes"], ",")))

    simulated = [a for a in fleet["assets"] if a.get("simulated")]
    fleet["assets"] = real_assets + simulated
    fleet["realAssetId"] = real_assets[0]["id"]
    fleet["datasets"] = [
        {"id": a["id"], "name": a["name"], "shiftDate": a["shiftDate"],
         "sourceLog": a["sourceLog"]} for a in real_assets]

    with open(DATA_JS, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("window.FLEET = " + json.dumps(fleet, separators=(",", ":")) + ";\n")
    with open(TRACK_JS, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("window.TRACKS = " + json.dumps(tracks, separators=(",", ":")) + ";\n")
        # Anything written against the single-track shape still works.
        fh.write("window.TRACK = window.TRACKS[%s];\n"
                 % json.dumps(real_assets[0]["id"]))

    print("\nwrote data.js  (%s bytes, %d real + %d simulated assets)"
          % (format(os.path.getsize(DATA_JS), ","),
             len(real_assets), len(simulated)))
    print("wrote track.js (%s bytes, %d dataset(s))"
          % (format(os.path.getsize(TRACK_JS), ","), len(tracks)))
    print("\nswitch between them in the vehicle picker.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
