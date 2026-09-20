#!/usr/bin/env python3
"""
Build `track.js` — the movement pattern for track.html.

All real data from device 862636058411560. The point of this file is to separate
what was *observed* from what is *inferred*:

  - A path segment between two fixes less than OBSERVED_GAP_S apart is drawn
    solid: we genuinely watched the machine cover that ground.
  - A longer gap is drawn dashed: the machine went from A to B, but the straight
    line between them is a guess. With 99.5% of records corrupted these gaps are
    large and frequent, and pretending otherwise would invent a route.

  - A DWELL is a run of fixes staying inside DWELL_RADIUS_M. Dwells carry the
    idle/working split, which is where this becomes useful: it says not just
    "the machine was here" but "the machine was here wasting fuel".

Usage:
    python build_track.py
"""

import collections
import datetime as dt
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MINE = os.path.dirname(HERE)
PROJECT = os.path.dirname(MINE)
sys.path.insert(0, MINE)

from decode_log import parse, reject_reason, FLAG_GPS_FIX  # noqa: E402,F401
from build_data import _find, LOGS, LOG, parse_logs  # noqa: E402,F401
from decode_log import device_id_from_filename  # noqa: E402

OUTDIR = os.environ.get("BLUEICE_OUT") or HERE
OUT = os.path.join(OUTDIR, "track.js")

DWELL_RADIUS_M = 150     # fixes inside this radius are the same stop
DWELL_MIN_S = 60         # shorter than this is a passing point, not a stop
OBSERVED_GAP_S = 60      # beyond this the connecting line is inferred

# Beyond this, the connection is not an inference at all -- it is a break in
# the record. A car's history now spans weeks, and a straight line between two
# fixes days apart is fiction: on the first multi-log build it added 6,747 km
# of "inferred" travel to an excavator. Such segments are kept (so the gap is
# visible) but are NOT drawn as a path and NOT counted as distance.
INFER_MAX_GAP_S = 3600
IDLE_MAX = 900
MACHINE_TOP_KMH = 6      # a tracked excavator's own top speed; above this it was carried

# The shortest interval a speed estimate is trusted over. The JSON-format logs
# sample at 10 Hz, and dividing GPS jitter by 0.1 s produces speeds in the
# thousands of km/h -- one June log reported a peak of 28,575 km/h. Speed is
# therefore measured across at least this many seconds. Where fixes are already
# further apart than this -- which is most of the binary-format data -- nothing
# changes, because the next fix is used exactly as before.
SPEED_BASELINE_S = 5

# Points written to track.js for drawing. Statistics are always computed from
# every fix; only the rendered geometry is thinned. Undecimated, a 10 Hz shift
# produced a 19 MB track.js and 61,079 SVG rectangles in the shift ribbon,
# which no browser will draw.
RENDER_MAX_PTS = 3000


def hav(a_lat, a_lon, b_lat, b_lon):
    R = 6_371_000
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    x = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b_lon - a_lon) / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(x))


def main():
    records, _ = parse_logs(LOGS)
    V = sorted((r for r in records if reject_reason(r) is None), key=lambda r: (r[0], r[1]))
    F = [r for r in V if r[8] & FLAG_GPS_FIX]
    if len(F) < 2:
        raise SystemExit("not enough GPS fixes")

    # Every field of the record travels with the point, so each dot on the map
    # can open its own dashboard showing exactly what the device reported at
    # that instant.
    pts = [{
        "i": i,
        "t": r[0] + r[1] / 1000,
        "lat": round(r[6] / 1e6, 6),
        "lon": round(r[7] / 1e6, 6),
        "rpm": r[2],
        "torque": r[3],
        "fuel": round(r[4] / 100, 2),
        "coolant": r[5],
        "fix": 1 if r[8] & FLAG_GPS_FIX else 0,
        "flags": r[8],
        "state": "off" if r[2] == 0 else ("idle" if r[2] < IDLE_MAX else "working"),
    } for i, r in enumerate(F)]

    # ---- speed, measured over a usable baseline ------------------------
    # For each fix, look ahead to the first fix at least SPEED_BASELINE_S away
    # and use that displacement. Two-pointer, so still linear.
    speed_kmh = [0.0] * len(pts)
    j = 0
    for i in range(len(pts)):
        if j < i + 1:
            j = i + 1
        while j < len(pts) - 1 and pts[j]["t"] - pts[i]["t"] < SPEED_BASELINE_S:
            j += 1
        if j < len(pts):
            dt_s = pts[j]["t"] - pts[i]["t"]
            if dt_s > 0:
                speed_kmh[i] = hav(pts[i]["lat"], pts[i]["lon"],
                                   pts[j]["lat"], pts[j]["lon"]) / dt_s * 3.6

    # ---- segments: observed vs inferred --------------------------------
    segments = []
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        gap = b["t"] - a["t"]
        d = hav(a["lat"], a["lon"], b["lat"], b["lon"])
        kmh = speed_kmh[i]
        segments.append({
            "from": [a["lon"], a["lat"]], "to": [b["lon"], b["lat"]],
            "t0": a["t"], "t1": b["t"], "gap": round(gap, 1),
            "m": round(d, 1), "kmh": round(kmh, 1),
            "observed": gap <= OBSERVED_GAP_S,
            # A gap too long to guess across. Neither observed nor inferred.
            "broken": gap > INFER_MAX_GAP_S,
            # An excavator cannot exceed ~6 km/h under its own power.
            "carried": kmh > MACHINE_TOP_KMH and d > 200,
            "state": b["state"],
        })

    # ---- dwells --------------------------------------------------------
    raw, cur = [], None
    for p in pts:
        if cur and hav(cur["lat"], cur["lon"], p["lat"], p["lon"]) <= DWELL_RADIUS_M:
            cur["end"] = p["t"]; cur["pts"].append(p)
        else:
            if cur:
                raw.append(cur)
            cur = {"lat": p["lat"], "lon": p["lon"], "start": p["t"], "end": p["t"], "pts": [p]}
    if cur:
        raw.append(cur)

    dwells = []
    for i, c in enumerate(raw):
        dur = c["end"] - c["start"]
        if dur < DWELL_MIN_S:
            continue
        idle = sum(1 for p in c["pts"] if p["state"] == "idle")
        work = sum(1 for p in c["pts"] if p["state"] == "working")
        n = idle + work
        lat = sum(p["lat"] for p in c["pts"]) / len(c["pts"])
        lon = sum(p["lon"] for p in c["pts"]) / len(c["pts"])
        dwells.append({
            "seq": len(dwells) + 1,
            "lat": round(lat, 6), "lon": round(lon, 6),
            "start": c["start"], "end": c["end"], "minutes": round(dur / 60, 1),
            "n": len(c["pts"]), "idle": idle, "working": work,
            "idlePct": round(100.0 * idle / n, 0) if n else 0,
        })

    observed_m = sum(s["m"] for s in segments if s["observed"])
    inferred_m = sum(s["m"] for s in segments
                     if not s["observed"] and not s["broken"])
    broken_n = sum(1 for s in segments if s["broken"])
    carried = [s for s in segments if s["carried"]]

    # ---- thin the geometry for drawing ---------------------------------
    # Every statistic above was computed from every fix. Only the drawn
    # geometry is reduced, and a merged segment carries the summed distance of
    # the raw segments it replaces, so a distance measured over a window in the
    # UI still adds up to the same thing.
    step = max(1, math.ceil(len(pts) / RENDER_MAX_PTS))
    if step > 1:
        keep = list(range(0, len(pts), step))
        if keep[-1] != len(pts) - 1:
            keep.append(len(pts) - 1)
        r_pts, r_segments = [], []
        for k, idx in enumerate(keep):
            p = dict(pts[idx])
            nxt = keep[k + 1] if k + 1 < len(keep) else None
            # The ribbon paints a block only across contiguous data. After
            # thinning, two kept points can be minutes apart inside a perfectly
            # continuous stretch, so state the answer here rather than letting
            # the UI infer it from the timestamps and paint the whole shift as
            # a gap.
            p["contig"] = 1 if (nxt is not None and
                                all(segments[m]["observed"]
                                    for m in range(idx, nxt))) else 0
            r_pts.append(p)
        for a_i, b_i in zip(keep, keep[1:]):
            grp = segments[a_i:b_i]
            if not grp:
                continue
            r_segments.append({
                "from": grp[0]["from"], "to": grp[-1]["to"],
                "t0": grp[0]["t0"], "t1": grp[-1]["t1"],
                "gap": round(grp[-1]["t1"] - grp[0]["t0"], 1),
                "m": round(sum(g["m"] for g in grp), 1),
                "kmh": round(max(g["kmh"] for g in grp), 1),
                "observed": all(g["observed"] for g in grp),
                "broken": any(g["broken"] for g in grp),
                "carried": sum(1 for g in grp if g["carried"]) * 2 > len(grp),
                "state": grp[-1]["state"],
            })
    else:
        r_pts = [dict(p, contig=1 if (i < len(segments)
                                      and segments[i]["observed"]) else 0)
                 for i, p in enumerate(pts)]
        for x in segments:
            x.setdefault("broken", False)
        r_segments = segments

    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "device": device_id_from_filename(LOG),
        "asset": "EX-340D-01 · CAT 340 D · Excavator",
        "fixes": len(pts),
        "validRecords": len(V),
        "framed": len(records),
        "validPct": round(100.0 * len(V) / len(records), 2),
        "t0": pts[0]["t"], "t1": pts[-1]["t"],
        "points": r_pts,
        "segments": r_segments,
        # Drawn geometry only. "fixes" above is the true count every statistic
        # was computed from; renderStep 1 means nothing was thinned.
        "renderStep": step,
        "renderedPoints": len(r_pts),
        "dwells": dwells,
        "observedKm": round(observed_m / 1000, 2),
        "inferredKm": round(inferred_m / 1000, 2),
        "brokenSegments": broken_n,
        "inferMaxGapS": INFER_MAX_GAP_S,
        "carriedSegments": len(carried),
        "carriedKm": round(sum(s["m"] for s in carried) / 1000, 2),
        "maxKmh": round(max(s["kmh"] for s in segments), 1),
        "machineTopKmh": MACHINE_TOP_KMH,
        "dwellRadiusM": DWELL_RADIUS_M,
        "observedGapS": OBSERVED_GAP_S,
    }

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("// Generated by build_track.py — real data, do not edit.\n")
        fh.write("window.TRACK = ")
        json.dump(payload, fh, separators=(",", ":"))
        fh.write(";\n")

    print(f"fixes         : {len(pts)}")
    print(f"segments      : {len(segments)}  observed={sum(1 for s in segments if s['observed'])}")
    print(f"path          : {payload['observedKm']} km observed / "
          f"{payload['inferredKm']} km inferred")
    print(f"carried       : {len(carried)} segments, {payload['carriedKm']} km "
          f"(> {MACHINE_TOP_KMH} km/h, max {payload['maxKmh']} km/h)")
    print(f"dwells (>{DWELL_MIN_S}s): {len(dwells)}")
    for d in dwells:
        print(f"   #{d['seq']} {dt.datetime.fromtimestamp(d['start'], dt.timezone.utc):%H:%M}"
              f"  {d['minutes']:>6.1f} min  idle {d['idlePct']:>3.0f}%  n={d['n']}")
    print(f"wrote         : {OUT} ({os.path.getsize(OUT):,} bytes)")


if __name__ == "__main__":
    main()
