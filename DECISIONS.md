# Design decisions

Every decision taken for this system, with the reasoning. Recorded so the next
person can tell a deliberate choice from an accident, and knows what to revisit
when the constraints change.

Status key: **Settled** — decided and implemented · **Deferred** — decided to
postpone, with a trigger · **Open** — genuinely unresolved.

---

## Scope and product

### D1 — What v1 delivers · Settled
**Live map + idle-time reporting. Fuel deferred to v2.**

Fuel-theft detection was the original business case, but the tank-level hardware
is not finished. Rather than wait, v1 ships the metric that needs *no new
sensor*: **idle time**, computed from RPM and ignition, both already working.
The reference shift shows **59.7% idle** — see [FINDINGS.md](FINDINGS.md) §4.

*Why it matters:* shipping a map with no insight looks like a toy. Shipping
"machine 340D-04 idled 8.6 hours yesterday" looks like money, and gives BlueICE a
reason to keep funding the programme while the fuel hardware is finished.

### D2 — Headline metric is engine hours and idle, not distance · Settled
Every maintenance schedule, resale valuation and rental contract in heavy
equipment is denominated in **engine hours**, not kilometres. 66 of the 75
machines are excavators, dozers, graders and cranes that barely move —
"distance covered" is close to meaningless for them.

Distance is still shown for road vehicles and where real gated measurements
exist; stationary plant with no measured travel renders `—`, never a
filtered-but-still-fictional number.

### D3 — Lifetime totals anchored to the cab hour meter · Settled
"Lifetime" means the machine's real hours, not "since we fitted the box". Read
each cab's hour meter at install, store it as an offset, report
`offset + hours_since_install`.

Totals are maintained as **incrementally updated counters**, never recomputed by
scanning history — otherwise the popup takes thirty seconds to open on day 400.

### D4 — One popup layout for all asset types · Settled
*(User overrode the two-layout proposal.)* One layout covering position, RPM,
torque, coolant, engine hours, idle %, distance and GPS status. Fields that do
not apply render `—` rather than `0`, so a parked excavator never reports a
confident `0 km` that someone mistakes for real.

Simpler to maintain than 26 per-model layouts, and more defensible now that fuel
is deferred and the field count is smaller.

---

## Data acquisition

### D5 — Transmit rate: 1 Hz target, 10 Hz local logging · Settled
The original requirement was **every 10 milliseconds**. That is not achievable
and not useful:

- The device's clock is quantised to **100 ms** — it cannot express 10 ms.
- 4G round-trip is 50–150 ms; a 10 ms freshness target over a 100 ms link is a
  contradiction.
- Browsers render at 60 Hz; 1,075 markers at 100 Hz collapses the render loop.
- 100 Hz is ~13 GB/month **per machine** — about 13 TB/month across the fleet.
- An excavator parked in a 193 m box does not benefit from 100 positions/second.

What was actually wanted — a map that *feels* live — is delivered by
transmitting at 1–10 s and **interpolating client-side at 60 fps**, plus pushing
**events** (ignition, geofence, fuel drop) immediately on occurrence.

### D6 — Upload is file-based over cellular, every 5 minutes · Settled
The device writes local log files and uploads them over its SIM. **Map freshness
therefore equals the upload cadence — 5 minutes.** For machines that sit in a
193 m box all day this is indistinguishable from live.

The local log is also the offline story: log always, transmit when there is
signal, backfill on reconnect. That matters because **the fuel thefts you most
want to catch happen at remote, no-signal sites.**

### D7 — Device identity comes from the filename · Settled, with a mitigation
*(User chose filename over adding a device-ID field.)*

The IMEI appears **nowhere inside the log** — not in the header, not in any
record. Identity exists only in
`862636058411560_2026_09_01_05_43_32_LOG003.TXT`.

**Mitigation, required:** the ingest pipeline must verify the filename's IMEI
against the credentials of the uploading device. This is server-side, costs
nothing, and catches the failure mode the filename cannot — one mis-copied file
permanently corrupting a machine's lifetime totals.

*Revisit if:* a firmware release happens anyway. Adding 4–8 bytes of device ID
per record is the robust fix and should ride along with the corruption fix.

### D8 — Fuel: retrofit capacitive probes · Deferred with the hardware
Tank **level** is the goal, not consumption rate — level is the only signal that
catches theft (you see 40 L vanish from a parked machine overnight).

OEM fuel level over J1939 is often coarse (5–10% steps), which on a 620 L tank
is a **30–60 L blind spot** — larger than a typical theft. So retrofit probes,
calibrated per machine, even where the CAN bus offers a level signal.

*Blocked on:* the meaning of the existing `fuel` field (see O2 below).

---

## Metrics and correctness

### D9 — Distance uses a movement gate plus a speed gate · Settled
Raw GPS integration invents about **100 km/day for a stationary machine**
(measured: 258 fixes in a 193.5 m box produced 0.52 km of apparent movement).

Implemented: discard steps below **12 m**, and require RPM to corroborate
motion. Map-match road vehicles later. A Kalman filter is the better long-term
answer but is hard to debug and there are more urgent problems.

### D10 — Engine hours derive from RPM only, never GPS · Settled
GPS is noisy and frequently absent; RPM and ignition are the reliable signals.
Thresholds: `rpm == 0` off, `0 < rpm < 900` idle, `rpm >= 900` working.

The **900 rpm** threshold is not arbitrary — RPM is cleanly bimodal with a
near-empty valley between 700 and 999 ([FINDINGS.md](FINDINGS.md) §4.1).

### D11 — Transport is excluded from operating hours · Settled
Segments faster than **6 km/h** (a tracked excavator's own top speed) over more
than 200 m are flagged *carried* — the machine was on a trailer. On the
reference shift that is 10 segments and 14.53 km, peaking at 200 km/h. A naive
system would bill this as machine activity.

### D12 — Observed vs inferred path, and a strict time window · Settled
A segment whose endpoints are less than 60 s apart is **observed** (solid). A
longer gap is **inferred** (dashed) — the machine went from A to B, but the
straight line is a guess.

The selected time window is **strict**: a segment is drawn *and* counted only if
it lies entirely inside it. Drawn and totalled are therefore always the same
thing. An earlier version drew overlapping segments while counting only
contained ones; that split was accurate but confusing, and the overlap rule on
its own had produced a fictional 17.46 km for a 40-minute window.

### D13 — Staleness: 15 min stale, 60 min offline, never hidden · Settled
Three missed 5-minute uploads marks an asset stale; an hour marks it offline.
Offline assets render **greyed, not removed** — because at a remote quarry
"no signal for six hours" and "stolen" produce the same dashboard until someone
drives out to check, and a machine that vanishes from the map looks like a
machine that does not exist.

---

## Platform

### D14 — AWS me-central-1 (UAE) · Settled
The fleet operates in northern Oman (23.4–24.5 N, 56.5–58.1 E). me-central-1 is
the closest region; me-south-1 (Bahrain) is the alternative. Keeps latency low
and data in-region.

### D15 — Ingest: S3 → Lambda → Postgres · Settled
*(User chose the file path over AWS IoT Core.)* Devices upload log files to S3;
a Lambda decodes, validates and deduplicates; Postgres with PostGIS and
TimescaleDB stores assets, telemetry and geometry in one place.

At ~1,075 assets and a 5-minute cadence the load is unremarkable. **Do not let
anyone sell you Kafka for this.**

### D16 — Ingest must be idempotent · Settled
Deduplicate on **file content hash** *and* on `(device_id, ts, ms)`. Uploads
retry and log rotations overlap. Because lifetime totals are cumulative and
never recomputed, **a single double-counted file is wrong forever.**

### D17 — Raw telemetry retained indefinitely · Settled
*(User chose "keep everything" over rollups.)* Compatible with D3: keep every
record *and* maintain running counters. Retention and query strategy are
separate concerns.

### D18 — MapLibre GL for rendering · Settled
Open source, no per-view billing, no vendor lock-in, and it renders the path
geometry as GPU line layers.

*Superseded in part:* the original driver was that 1,075 DOM markers make a
fleet-wide map unusable, so assets were drawn as a clustered GPU circle layer.
**The fleet map was later removed** (D26), leaving a single vehicle — which is
now a DOM marker, because one of them buys a CSS pulse for free. Restore the
circle-layer approach if a fleet view ever comes back; do not use DOM markers
at fleet scale.

### D19 — Everyone sees all assets, but scope the schema now · Settled
*(User chose unrestricted access.)* `site_id` is in the schema from day one even
though no rule uses it yet. Retrofitting row-level access across 1,075 assets
and multiple sites after launch is a rewrite of every query; adding it now is
one column and one filter.

---

## Front-end behaviour

### D20 — The dashboard is hidden until the vehicle is clicked · Settled
The vehicle page opens with the machine and nothing else. Clicking the marker
opens the panel; the X closes it.

### D21 — Trace history window is typed, not preset · Settled
*(User asked for range entry only; the preset buttons were removed.)*

The window is chosen solely by typing **From** and **To** in UTC. The available
data range is shown beneath the inputs so the user knows what is valid. Path,
dots, stops, RPM sparkline, playback range and every statistic recompute for the
typed window. A backwards range is rejected with a message; a window containing
no fixes says so rather than showing a misleading set of zeros.

### D22 — Per-fix dots and the per-sample dashboard · Reverted
*(Built, then removed at the user's request.)*

All 375 surviving fixes were drawn as clickable dots; clicking one opened that
sample's readings — timestamp, RPM, coolant, torque, raw fuel, flags, position,
gap since the previous fix. The "since previous" figure exposed the firmware
defect at the level of an individual sample.

**Removed** so trace history shows only the moving marker and the path lines.
The dots were the only click target, so the per-sample view went with them. The
underlying data is still in `track.js` and the decoded CSV, so this is
restorable if per-sample inspection is ever wanted again.

### D23 — Attach map layers on `style.load`, not `load` · Settled
MapLibre's `load` event waits for **raster tiles**. If the tile host is slow,
rate-limited or unreachable, `load` never fires and the entire page renders
blank — a dead demo on bad wifi. `style.load` only needs the style parsed, so
the data still draws over an empty basemap, with a visible
"Basemap unavailable" warning rather than silence.

### D24 — Simulated data is labelled everywhere it appears · Settled
Only one device is deployed, so the other 1,074 assets carry simulated positions
and telemetry to exercise the UI at full scale. Their make, model and tank
capacity are real. Every simulated asset shows a **Simulated** badge and cannot
be traced. **Do not present it as live data.**

### D25 — Data quality is shown, not hidden · Settled
The trace view distinguishes observed from inferred path, and the documentation
states the true **0.5% validity** everywhere a figure derives from it. A
prototype that hides a broken pipeline behind a polished map is worse than one
that names the problem.

*Note:* the data-quality banner and the patterns page carried much of this
messaging and were removed with the fleet map (D26). The burden now sits on
[FINDINGS.md](FINDINGS.md) and [HANDOVER.md](HANDOVER.md) — if the UI is ever
shown without those documents, put the 0.5% figure back on screen.

---

### D27 — Shift ribbon replaces the playback slider · Settled
The window is drawn as coloured blocks under the map — orange idling, blue
working — doubling as the scrubber (click, drag, arrow keys).

Crucially, **a block is painted only where consecutive fixes are under 60 s
apart**; data the firmware defect destroyed is left bare. A slider showing a
smooth continuous track would have implied continuous data that does not exist,
so the ribbon is both a visual and an honesty mechanism — and with the patterns
page gone (D26) it is the only place in the UI where the scale of the loss is
visible at all.

**Known limitation, do not quote the painted fraction.** Each block carries a
minimum rendered width so sub-second fragments remain visible. With 349
fragments that floor dominates the painted area, so the strip looks roughly
twelve times fuller than the data is. True continuous coverage of the reference
shift is **2.0%** (1,023 s of 14.2 h). The strip is trustworthy about *where*
data survives and about the clustering; it is not a measure of *how much*.

---

### D26 — Fleet map and patterns page removed · Settled
*(User's decision.)* `index.html` (the 1,075-asset fleet map) and
`patterns.html` (the behavioural and forensic charts) were deleted. The product
is now a single-vehicle view only.

What went with them: the fleet-wide overview, clustering, the asset search and
state filters, the data-quality banner, and every chart — the bimodal RPM
histogram, the corruption gap distribution, the dead-coolant comparison, and the
idle/working profile.

**The evidence those charts carried is preserved in
[FINDINGS.md](FINDINGS.md)**, and `build_patterns.py` still runs, so the charts
could be rebuilt. Worth knowing before a presentation: the patterns page was the
strongest visual argument for both the idle-time business case and the firmware
defect.

---

## Data sync

### D28 — Poll the bucket from a workstation until the Lambda exists · Settled, temporary
The agreed ingest design is S3 → Lambda on the object-created event → Postgres
(D-series, *Platform*). None of it is built, and the dashboard was being fed by
a log file copied into place by hand.

[`sync/`](sync/README.md) closes that gap the cheap way: a scheduled poll that
lists the bucket, downloads what is new, decodes it, and regenerates `data.js`
and `track.js`. Polling is the wrong long-term answer — it costs a `ListObjects`
per cycle whether or not anything arrived, and it cannot scale to 1,075 devices.
It is the right *interim* answer because it needs no AWS resources to exist
beyond a bucket and a read-only credential, and because it works today against a
local folder while there is no bucket at all.

**Revisit when:** the Lambda is written. Then delete `sync/` rather than keeping
two ingest paths that can disagree.

### D29 — Dedup records across files, never within one · Settled
[HANDOVER.md](HANDOVER.md) requires dedup on `(device_id, ts, ms)`. Applied
naively that is wrong: the reference shift contains **two pairs of valid records
sharing the same `(ts, ms)`**, which are two genuine writes into the same 100 ms
bucket, not a duplicate. Collapsing them moves the headline figures from
419 valid / 0.50% to 417 / 0.49% — a silent contradiction of
[FINDINGS.md](FINDINGS.md) introduced by a tool nobody would think to suspect.

So: a record key already seen in an **earlier file** is an overlap between
uploads and is dropped; repeats **inside one file** are kept. Invalid records
are never deduped at all — their timestamps are garbage and would collide by
chance, shrinking the denominator the corruption percentage is measured against.

The primary defence is upstream anyway: files are identified by SHA-256, so the
same bytes under a second key never reach the merge.

### D30 — The gate is reported, not enforced, on every sync · Settled
`--gate 99` is the firmware acceptance test. The sync reports the valid-record
percentage on every build and prints **GATE FAIL** at today's 0.50%, but exits 0.
Making it exit non-zero by default would mean every scheduled run failed until
the firmware was fixed, and an alert that is always red is an alert nobody reads.
`--fail-under-gate` opts into the strict behaviour for CI.

---

## Basemap

### D31 — Esri Dark Gray Canvas, not OpenStreetMap's own tiles · Settled, fragile
The dashboard originally pulled raster tiles straight from
`tile.openstreetmap.org` and desaturated them client-side to suit the dark
theme. OSM then began returning **403 Access blocked — "App is not following the
tile usage policy"**, which is correct: those are volunteer-funded servers and
this is not the kind of use they exist for.

Two replacements were tried and rejected:

| Provider | Why not |
|---|---|
| `basemaps.cartocdn.com` | Serves, but stamps **"API KEY REQUIRED"** across every tile. The anonymous tier is gone. |
| `tile.openstreetmap.org` | The original. Policy violation, and already blocked. |

**Esri's Dark Gray Canvas** is keyless, renders correctly, and is genuinely dark
— which removed the `raster-saturation: -0.72` hack the old setup needed. Note
its tile path is `{z}/{y}/{x}`; row before column, unlike almost everyone else.

**It stops at zoom 16.** Measured over Ibri: real tiles through z16, and z17+
return an identical 2,521-byte *"Map data not yet available"* placeholder. The
source is therefore declared `maxzoom: 16`, so MapLibre upscales the z16 tile
rather than requesting tiles that do not exist. Close in, the basemap is blurry
and — over open desert — largely featureless, which is honest: this is a
generalised canvas, not imagery. It matters here because the generated demo
route is under a kilometre across, so the map zooms well past 16.

**If sharp detail at high zoom is needed**, that is the reason to move to
OpenFreeMap: vector tiles scale to any zoom without placeholders.

**Revisit when:** this goes to a fleet, or the tiles start showing a watermark.
A courtesy tier can disappear without notice — CARTO's just did. The swap is one
constant, `BASEMAP` at the top of the map setup. The keyless alternative worth
knowing is **OpenFreeMap** (`tiles.openfreemap.org/styles/dark`), which is
vector rather than raster and so replaces the whole style object, not just the
URL. For anything serious, a keyed provider or self-hosted tiles.

### D32 — Count tiles that arrive, do not trust a "loaded" flag · Settled
The "Basemap unavailable" notice was driven by a source-level `isSourceLoaded`
event. On the Esri basemap that event was missed, and the page warned that the
basemap had failed **over a map that had drawn perfectly**. A warning that cries
wolf is worse than no warning, because the next real outage is ignored too.

It now counts tile-level `data` events (`e.tile` is only set on those) and warns
only when none arrived in nine seconds. Verified both ways: it stays silent on a
good load and appears on a genuinely blank one.

---

## Open questions

### O1 — Who owns the firmware, long term? · Open
Written by a colleague at BlueICE. There is no repository of record. **Get the
source into a BlueICE-owned Git repo.** See [HANDOVER.md](HANDOVER.md).

### O2 — What does the `fuel` field mean? · Open — blocks all fuel work
Median 3.15, max 387.87. Not consistent with litres in a 620 L tank; more likely
a consumption rate or an uncalibrated ADC value. **Ask the firmware author which
physical sensor feeds it and what the scaling is.** Theft detection needs
*level*.

### O3 — Tank capacities are unverified · Open
18 of 26 marked LOW confidence. Must be checked against operator manuals before
any fuel feature ships.

### O4 — Pilot size before full rollout · Open
Recommendation: **5–10 machines** across classes and sites, including one at a
no-signal location, run for a month. Exit criterion: `--gate 99` passes. Do not
install 1,075 devices that still lose 99.5% of their data.

### O5 — Resolved: preset windows removed
The "Moving only" preset was defined as "after the last stop", which was correct
for this one shift but arbitrary in general. All presets were removed in favour
of typed ranges (D21), so the question no longer arises.
