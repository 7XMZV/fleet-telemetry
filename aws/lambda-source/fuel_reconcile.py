#!/usr/bin/env python3
"""
Fuel reconciliation: did more fuel leave the tank than the engine burned?

    from fuel_reconcile import reconcile
    result = reconcile(samples, dispenses, litres_per_mm)

Three independent signals have to agree, and the whole point is that they come
from three different instruments:

    level       mm of depth, tank sender on the machine. Sees everything --
                burn, refuels, and anyone helping themselves.
    flowrate    mm/h, in-line meter in the fuel line. Sees ONLY what the
                engine burned. Blind to refuels and to theft.
    dispensed   litres, the bowser's own flow meter, arriving separately.

    unaccounted = ( level_start + dispensed ) - ( level_end + burned )

Read as: everything that went into the tank, less everything that can be
accounted for coming out of it. **Positive means litres are missing** -- more
fuel left the tank than the engine burned. Negative means more fuel is present
than anyone recorded delivering, which usually means an unlogged dispense.

Two things make this work in the presence of a real sensor:

  * Endpoints are a MEDIAN over a window, never a single reading. Slosh on a
    moving machine is several mm, and one sample would put litres of noise
    straight into the balance.
  * The period runs dispense-to-dispense, between fills rather than across
    them. A fill in the middle of a window has to be trusted exactly; a fill
    at the boundary does not.

This module computes the comparison and nothing else. It sets no threshold and
raises no alert -- what counts as theft rather than sensor drift has to be
measured from a real installation first, and inventing a number here would
turn a measurement into an accusation.
"""

import datetime as dt
import statistics as st

# Half-width of the median window at each endpoint, in samples either side.
# 600 samples is a minute at 10 Hz -- long enough to average out slosh, short
# enough that real drain over the window is negligible.
ENDPOINT_WINDOW = 600


def _median_level(levels, i, half=ENDPOINT_WINDOW):
    lo, hi = max(0, i - half), min(len(levels), i + half)
    return st.median(levels[lo:hi]) if hi > lo else levels[i]


def _nearest(times, t):
    return min(range(len(times)), key=lambda i: abs(times[i] - t))


def intervals_from_dispenses(times, dispenses, settle_s=240):
    """Dispense-to-dispense periods, excluding the fills themselves.

    A fill takes a few minutes and the level is meaningless while fuel is
    pouring in, so each period starts `settle_s` after a dispense timestamp
    and ends at the next one.
    """
    marks = sorted(d["t"] for d in dispenses)
    out = []
    for a, b in zip(marks, marks[1:]):
        t0 = a + settle_s
        if t0 < b:
            out.append((t0, b))
    return out


def reconcile(samples, dispenses, litres_per_mm, periods=None):
    """Compute the balance for each period.

    samples    list of (epoch_seconds, flowrate_mm_h, level_mm), time-ordered
    dispenses  list of {"t": epoch_seconds, "litres": float, ...}
    """
    if len(samples) < 2:
        return []
    times = [s[0] for s in samples]
    flow = [s[1] for s in samples]
    level = [s[2] for s in samples]

    if periods is None:
        periods = intervals_from_dispenses(times, dispenses)
    out = []

    for t0, t1 in periods:
        i0, i1 = _nearest(times, t0), _nearest(times, t1)
        if i1 <= i0:
            continue

        l0 = _median_level(level, i0)
        l1 = _median_level(level, i1)

        # Integrate the in-line meter over the actual sample spacing rather
        # than assuming a fixed rate: a dropped sample must not be counted as
        # fuel that was never burned.
        burned_mm = 0.0
        for i in range(i0, i1):
            dt_h = (times[i + 1] - times[i]) / 3600.0
            burned_mm += flow[i] * dt_h

        inside = [d for d in dispenses if t0 < d["t"] < t1]
        dispensed_l = sum(d["litres"] for d in inside)

        # (what went in) - (what came out). POSITIVE means litres are
        # missing: more left the tank than the engine burned.
        went_in = l0 * litres_per_mm + dispensed_l
        came_out = l1 * litres_per_mm + burned_mm * litres_per_mm
        balance_l = went_in - came_out
        out.append({
            "start": t0, "end": t1,
            "hours": round((t1 - t0) / 3600.0, 2),
            "levelStartMm": round(l0, 2),
            "levelEndMm": round(l1, 2),
            "levelStartL": round(l0 * litres_per_mm, 1),
            "levelEndL": round(l1 * litres_per_mm, 1),
            "burnedL": round(burned_mm * litres_per_mm, 1),
            "dispensedL": round(dispensed_l, 1),
            "balanceL": round(balance_l, 1),
            "samples": i1 - i0,
        })
    return out


def latest_complete(results):
    """The most recent finished period, which is what the card shows."""
    return results[-1] if results else None


def iso(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
