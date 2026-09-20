# Vehicle Dashboard — prototype

A single-vehicle telemetry view: what a machine is doing right now, and a
replay of a chosen slice of its history. Vertical slice of the agreed
architecture, running against **real decoded telemetry** from the one deployed
device.

## How to read it

[`GUIDE.md`](GUIDE.md) is the operating guide: every panel, where each
number comes from, the keyboard controls, the rules the interface follows, and
what to do when something looks wrong. Hand that to anyone who has to *use* the
dashboard; this file is for whoever has to *run* it.

## Adding new screens

[`DESIGN.md`](DESIGN.md) is the design system: colour roles with hex values,
type scale, component behaviour, motion spec, and an explicit banned list. It is
extracted from this prototype rather than written aspirationally, and its colour
set was validated with a contrast/CVD checker. Feed it to Stitch or any other
generator so a new alerts screen or fleet list matches what already exists.

## Run it

Open `vehicle.html` in a browser. No server, no build step.

## Open it from other computers

```bash
python serve.py
```

Or double-click `serve.cmd`. It prints the URL to hand round — something like
`http://10.200.52.191:8765/` — and serves the dashboard at `/`. Leave the window
open; closing it stops the server.

Add `--sync-every 15` to pull new logs from the bucket every 15 minutes while it
runs, so the people looking at it see current data
(see [sync/README.md](../sync/README.md)). `--local-only` binds to this machine
only, `--port` moves it.

Three things to know before you send the link to anyone:

- **There is no password.** Anyone who can reach this machine on that port can
  open the dashboard. Fine for a site office; not fine on an open network. There
  is no login to switch on later — if the audience matters, put it behind
  something that authenticates.
- **Windows Firewall is the usual reason a colleague cannot connect.** Windows
  prompts the first time Python binds the port; if that prompt was missed, open
  it from an **administrator** prompt:
  ```
  netsh advfirewall firewall add rule name="BlueICE dashboard 8765" dir=in action=allow protocol=TCP localport=8765
  ```
- **Viewers need internet.** The map library and the map tiles come from public
  CDNs, not from this server. Without it the page still draws the data over
  blank ground and says so in a caution note.
- **The basemap is a courtesy tier and can vanish.** Tiles come from Esri's
  keyless Dark Gray Canvas. OpenStreetMap's own servers blocked this app for
  breaching their tile policy, and CARTO's free tier now watermarks every tile.
  Swap the `BASEMAP` constant in `vehicle.html` before rolling this out to a
  fleet — see [DECISIONS.md](../DECISIONS.md) D31.

The server sends no-cache headers deliberately. A browser holding a cached
`data.js` shows yesterday's shift with today's layout, and numbers that are
merely *old* are the hardest kind of wrong to spot.

## Regenerate the data

```bash
python build_datasets.py   # -> data.js + track.js, every log as its own picker entry

# or one shift at a time:
python build_data.py       # -> data.js
python build_track.py      # -> track.js
```

Both read the vendor log from `../../Not mine/` (read-only — nothing there is
ever modified); `build_data.py` also reads the asset register from OneDrive.

To build from logs somewhere else — which is how [`sync/`](../sync/README.md)
feeds these scripts from the ingest bucket — set `BLUEICE_LOGS` to an
`os.pathsep`-separated list of files. Unset, both scripts behave exactly as they
always did. Multiple logs are merged with records deduped **across** files but
never within one.

```bash
BLUEICE_LOGS="/path/a.TXT:/path/b.TXT" python build_data.py     # ';' on Windows
```

> `build_patterns.py` and `patterns.js` are **retained but unused** — the
> patterns page was removed. The script is kept because it is the analysis that
> produced several numbers in [FINDINGS.md](../FINDINGS.md) (the RPM histogram,
> the corruption gap distribution, the dead-coolant comparison) and it still
> runs. Delete both if you do not want them.

## What is real and what is not

| | Source |
|---|---|
| Device `862636058411560` (**EX-340D-01**) | **Real.** Position, RPM, coolant, torque, engine hours, idle %, distance and all history are decoded from the production log of 2026-08-31. |
| Brands, models, quantities, tank capacities | **Real.** From the BlueICE asset register. |
| `DEMO-FUEL-01` … `-05` | **Generated.** Five fixtures for the fuel-reconciliation panel, one per outcome: fuel missing, clean shift, unlogged delivery, no refuel, tank nearly empty. No such machines exist and every balance is invented. Each is badged in the picker with the case it demonstrates, and again in its own panel. |

**The 1,074 simulated assets have been removed.** They existed only to prove
the picker worked at fleet scale, and 1,074 invented machines standing beside
the real ones was the standing risk in every demo. `BLUEICE_SIMULATED=1` brings
them back if that is ever wanted. The fleet is still ~1,075 machines; the
dashboard no longer pretends to show them all.

## How the page works

The map opens with **just the vehicle and no dashboard**. Click the marker to
open it, close it with the X. A picker in the header switches vehicle, and
**"All cars"** shows the whole fleet at once — every car on the map coloured by
state, a **fleet fuel balance** (missing, unrecorded surplus, net) and a list of
fuel levels; click any marker or row to drop into that car.

The marker is a top-down vehicle built from SVG primitives, tinted by engine
state, that **rotates to face its direction of travel** during replay. It is
drawn inline rather than pulled from an icon font on purpose: it is the one
element that must never fail to render, and a remote font is exactly what dies
on conference wifi.

The layout splits across three places, each holding one kind of thing:

| Where | What |
|---|---|
| **Beside the car** | Engine state and RPM, riding with the marker, so the number and the thing it describes are the same object on screen. In Trace it follows the scrubber. |
| **Right** | Vehicle identity, the Live / Trace switch, the time-range control, and in Live the three shift charts. |
| **Left rail** | One card per parameter, each standing alone. |

Cards arrive with a short staggered entrance, transform and opacity only, and
the whole thing collapses to static under `prefers-reduced-motion`.

**Live mode carries shift totals**, not charts. Working and idling time,
distance, fuel burned, fuel burned *while idling*, litres delivered, and the
unaccounted balance — each one a sum over the whole shift, so nothing has to be
read off a plotted line.

The charts they replaced were removed for cause: the RPM sparkline crushed
108,000 samples into 330 px (about 330 samples per pixel, unreadable), and the
distance chart drew a flat line at 0.00 km on machines that never moved, which
implied a measurement nobody made.

**Hours are shown only when the data supports them.** Where the firmware defect
destroyed most records, working and idling are reported as a **share of what
survived**, with the coverage stated. Spreading a sample proportion across the
whole span would turn 419 records into a confident "7 h 49 m idling" — an
extrapolation dressed as a measurement.

**Trace history** — replays a **typed time window**: From/To inputs in UTC, with
the available data range shown beneath them. The map draws the **path** (solid
where observed, dashed where inferred) and the **moving marker** stepping through
it under the playback control.

An **"At this moment"** panel sits at the top of the trace view showing the
vehicle's state, the timestamp and the engine speed **at the scrubbed position** —
it rewrites on every scrub and on every playback tick, so you can read what the
machine was doing at any point in the replay.

Beneath the map, a **shift ribbon** replaces the plain playback slider: the
window drawn as coloured blocks — orange where idling, blue where working — with
a white playhead. Click or drag anywhere on it to jump to that moment; arrow
keys, Home and End work too. Play sweeps the whole window in about 30 seconds.

**The bare stretches of the ribbon are the point.** A block is only painted
where two consecutive fixes are less than 60 s apart; everything the firmware
defect destroyed is left unpainted.

**Only 2.0% of the shift is covered by continuous data** (1,023 seconds of
14.2 hours). Note that the strip *looks* fuller than that: each block is given a
minimum rendered width so a sub-second fragment is still visible, and with 349
fragments that floor accounts for most of the painted area. The strip is honest
about **where** data survives and about the shape of the clustering; it
overstates **how much**. Quote the 2.0%, never the painted fraction.

The path, ribbon, playback range and all four statistics recompute for the
window. The window is strict — only fixes and
segments entirely inside it are shown and counted. A backwards range is
rejected; an empty window says so.

Available only for the real device (862636058411560); for simulated assets the
Trace toggle is disabled and the panel explains why.

## What the prototype demonstrates

- **Idle share as the headline metric.** The real device idled **59.7% of a
  14.2 h shift**. Needs only RPM and ignition — no fuel sensor. This is the
  number that justifies the programme.
- **Distance gating.** The panel shows **19.94 km** after a 12 m movement gate
  plus RPM corroboration. Raw GPS integration over the same fixes gives
  **22.70 km**; the difference is receiver jitter. Stationary plant with no
  measured travel shows `—`, never a filtered-but-still-fictional number.
- **Observed vs inferred path.** Only **3.19 km** of the shift was genuinely
  watched (fixes < 60 s apart, solid line); **19.50 km** is dashed because no
  valid record exists in between. With 99.5% of records corrupted that is
  unavoidable, and drawing a confident smooth route would be a lie.
- **Transport is not work.** Segments above 6 km/h mean the machine was on a
  trailer — 10 segments and 14.53 km on the reference shift, peaking at
  200 km/h. Excluded from operating hours by design.
- **Honest data quality.** Every derived figure is computed from the 419
  surviving records of 84,328, and the docs say so.
- **Fuel is present but empty**, marked *sensor pending (v2)* — the deferred
  scope is visible rather than silently absent.
- **The data loss is visible, not buried.** The shift ribbon paints only the
  intervals actually captured, so ~75% of it is bare. You can see the defect
  rather than read about it.
- **Degrades visibly.** Map layers attach on `style.load`, not `load`, so a slow
  or unreachable tile host still renders the data over a blank basemap, with a
  "Basemap unavailable" notice instead of a silent empty rectangle.

## Known limits

- **Static snapshot.** There is no ingest yet — `data.js` and `track.js` are
  generated offline. The real path is S3 → Lambda → Postgres, then this UI reads
  an API.
- **One vehicle at a time.** The fleet-wide map was removed; the picker is the
  only way to change asset.
- **History exists for one device only**, because only one is deployed.
- **Per-sample inspection was removed** along with the fix dots. Torque, GPS-fix
  flag, raw fuel and inter-fix gaps are still in `track.js` and the decoded CSV,
  but no longer surfaced in the UI.

## Next step

Replace `data.js` and `track.js` with API calls once ingest exists. Nothing else
in `vehicle.html` needs to change — the rendering path already assumes an asset
list and a list of fixes with the same shape.
