# Reading the Vehicle Dashboard

Operating guide for `vehicle.html`: what every panel means, where each number
comes from, and the rules the interface follows so it never claims more than the
data supports.


For the whole project rather than just this screen, see [REPORT.md](../REPORT.md).

---

## What it is

One page showing **one machine**, in two modes. It opens with just the vehicle on
the map and no dashboard — **click the vehicle to open it**, close it with the ×
in the corner. A picker in the header switches machine.

| Mode | Answers |
|---|---|
| **Live now** | What is this machine doing, and what did the whole shift look like? |
| **Trace history** | Where did it go between two times I type, and what was it doing at each moment? |

### Opening it

Double-click `vehicle.html`. No server, no build step.

To let other machines on the network open it, run the server instead — it prints
the URL to share:

```bash
cd dashboard
python serve.py --sync-every 15
```

Or double-click `serve.cmd`. **There is no password on it**: anyone who can reach
the machine on that port can open the dashboard. See
[README.md](README.md) before sending that link round.

---

## The layout

Three zones, each holding exactly one kind of thing. The rule is **the number
rides with the thing it describes; controls live on the right; parameters stack
on the left.**

```
+----------------------------------------------------------------+
| BlueICE - FLEET TELEMETRY                    [ EX-340D-01  v ]  |
+----------------------------------------------------------------+
|                                                                |
| +-----------+                            +-------------------+ |
| |IDLE SHARE |                            | EX-340D-01        | |
| |   59.7    |                            | CAT 340 D         | |
| +-----------+       [#]  +------------+  | [Live now][Trace] | |
| |PATH OBSRVD|            |IDLING 15:41|  |                   | |
| |   3.19    |            |   640 rpm  |  | From __   To __   | |
| +-----------+      /     +------------+  | Data available    | |
| |PATH INFERD|     /                      | 05:43 - 21:16 UTC | |
| |  19.50    |    /   <-- solid: observed +-------------------+ |
| +-----------+   /    ... dashed: inferred                      |
| |TIME STOPPD|  /                                               |
| |   107     | /     (1) left rail        (3) right panel       |
| +-----------+/      (2) beside the vehicle                     |
|                     (4) shift ribbon                           |
|                                                                |
+----------------------------------------------------------------+
|  >  15:41  |I II  I  I II   I  I II  I I         IDLING (4)    |
+----------------------------------------------------------------+
```

| # | Zone | Holds |
|---|---|---|
| ① | **Left rail** | One card per measured parameter, each standing alone. A card that would hold two things is two cards. |
| ② | **Beside the vehicle** | State and engine speed, riding with the marker — the number and the thing it describes are the same object on screen. |
| ③ | **Right panel** | Identity, the mode switch, the time-range control, and in Live the shift totals. |
| ④ | **Shift ribbon** | The playback control, drawn as the shift itself rather than a plain slider. |

The map is full-bleed; everything else floats over it in a reserved zone.
**Solid track is path the device actually watched; dashed is inferred** — a
straight line between two fixes with no valid record in between.

---

## All cars

**"All cars"** in the header switches from one car to the whole fleet.

Every car appears on the map at once, coloured by engine state, with the
currently-selected one ringed. The panel opens with the **fleet fuel balance**,
then a count by state, then a list showing **fuel in tank** for each. Cars with
no level sensor read `—`.

### The fleet fuel balance

| Line | |
|---|---|
| Missing | Sum of the **positive** balances — fuel that left tanks unburned. Takes the cost colour. |
| Unrecorded surplus | Sum of the **negative** balances — fuel present that no docket explains. |
| Net across all cars | The two added together. |

> **Read the first two lines, not the net.** A loss on one car and a paperwork
> error on another cancel out: on the current fixtures +59.6 L and −90.7 L net
> to −31.1 L, which hides both. They are different problems for different
> people. Splitting by sign needs no threshold — the arithmetic separates them
> — and each line names the car that dominates it, because a count alone would
> make four cars in "surplus" sound fleet-wide when one of them is 90 L and the
> rest are sensor noise.

Click a marker **or** a row in the list to drop into that car's full
dashboard. The button toggles back.

The single-vehicle marker, the parameter rail and the playback controls are all
hidden in this view, deliberately: they describe one machine, and leaving them
on top of a fleet map would have them describing a machine the reader is not
looking at.

> **This uses plain DOM markers, which is fine for seven machines and wrong for
> a thousand.** If the simulated fleet is ever switched back on
> (`BLUEICE_SIMULATED=1`, ~1,075 assets) this must become a GPU circle layer —
> a thousand DOM nodes will not pan at 60 fps.

---

## Live now

The default mode: what the machine is doing now, and what the shift added up
to. The left rail carries the parameters; the right panel carries the totals.

### The three cards in Live

The left rail carries only what describes the machine **right now**. Anything
cumulative — fuel, distance, the reconciliation — is in the totals panel on the
right, so the rail is not a second copy of it.

| Card | Reads | How it is derived |
|---|---|---|
| Status | Working / Idling / Engine off, with rpm and how long ago | The latest record. Takes the cost colour while idling. |
| Engine hours | hours running this shift | Time with the engine running. Transport segments are excluded. |
| Coolant | °C | Shows an em-dash when the engine is off, and on this device the signal is dead — two values across a whole shift. Do not build overheating alerts on it. |

In **Trace** the rail changes to four different cards, all scoped to the typed
window — see below.

### Shift totals

The right-hand panel carries **totals, not charts**. Every figure is a sum over
the whole shift, so nothing has to be read off a plotted line.

| Row | |
|---|---|
| Working / Idling | Time with the engine under load, and time running while producing nothing. |
| Distance | After the 12 m movement gate. `—` when there was no measured travel. |
| **In the tank at start** | The level before the first refuel. |
| **Fuel delivered** | Litres pumped in by the bowser. Its note carries the *went in* subtotal. |
| **Fuel burned** | Litres the in-line meter measured. |
| **In the tank now** | The closing level. Its note carries the *accounted for* subtotal. |
| **Unaccounted for** | What went in, less what came out, **across the whole shift**. |

**The five fuel rows are an account, and the subtraction is on screen.**
`(325.3 + 330.0) − (559.8 + 35.9)` = `655.3 − 595.7` = **59.6 L missing**. The
two subtotals ride in the notes so the arithmetic can be checked without extra
rows. The opening level is shown for the same reason — without it the panel
appears to hold more fuel than was ever delivered.

> **Two "unaccounted" figures appear, over different periods, and both say so.**
> The totals row covers **the whole shift**. The `Fuel balance` card in the left
> rail covers the **latest dispense-to-dispense interval**, because a loss is far
> easier to localise between two known refuels than across a whole day. They will
> not be identical, and neither is wrong.

`Fuel burned` is the in-line meter integrated across the shift — all of it,
whether the machine was working or idling. `Fuel in tank now` is the level
sender's closing reading. The two are independent instruments, which is what
makes the reconciliation below mean anything.

> **Hours are only shown when the data supports them.** Where most records were
> destroyed by the firmware defect, the panel reports Working and Idling as a
> **share of the records that survived** and states the coverage, because
> spreading a sample proportion across the whole shift would turn 419 records
> into a confident "7 h 49 m idling" — an extrapolation dressed as a
> measurement. Above 95% valid it shows real hours.

---

## Fuel reconciliation

Only on machines that have a **tank level sensor** and a known tank geometry.
Everything else reads *no level sensor fitted* — the panel stays honest rather
than inventing a figure.

### The three signals

The comparison only means anything because the numbers come from three
different instruments:

| Signal | Instrument | Unit | Sees |
|---|---|---|---|
| `level` | tank sender on the machine | mm of depth | burn, refuels, **and theft** |
| `burned` | in-line meter in the fuel line | mm/h | the engine only |
| `dispensed` | flow meter on the bowser | litres | what was pumped in |

### The equation

```
unaccounted = ( level_start + dispensed ) − ( level_end + burned )
```

Everything that went into the tank, less everything that can be accounted for
coming out of it.

**Positive means litres are missing** — more fuel left the tank than the engine
burned. Negative means more fuel is present than anyone recorded delivering,
which usually means an unlogged dispense.

The period is **dispense-to-dispense**: from a few minutes after one refuel to
the start of the next. A theft is far easier to localise between two known
fills than inside an arbitrary day, and it keeps a single fill from being split
across two rows.

Note the trap this avoids: `burned` must **not** come from the tank. If it did,
the equation collapses to `−dispensed` and can never detect anything. The whole
design rests on comparing the tank against something that is not the tank.

### Why the endpoints are medians

Slosh on a moving machine is several millimetres, and a single reading would
put litres of noise straight into the balance. Each endpoint is the **median
over a window** either side of the boundary. On the demo data that holds the
noise floor to about ±0.4 L while a 60 L siphon reads −58.6 L.

### What it does not do

**No threshold, no alert.** It reports the comparison and stops. What size gap
means theft rather than sensor drift has to be measured from a real
installation — park an instrumented machine for a week with no dispensing and
whatever range the level wanders across is the floor any threshold must clear.
Picking a number before knowing that is how these systems end up ignored.

### Litres are an assumption

The tank speaks millimetres; the bowser speaks litres. Converting needs tank
*geometry*, not the capacity figures in the asset register — the same 38.78 mm
drop is 24 L or 40 L depending on how deep the tank is. The panel prints the
factor it used and marks it as an assumption. **A real refuel of a known volume
would measure it properly**, and that single event would also calibrate the
ECU fuel field's units, which have never been confirmed.

---

## Trace history

### Choosing the window

Type a **From** and **To** time in UTC. The available data range is printed
underneath. The window is strict: only fixes and segments *entirely inside it*
are drawn and counted. A backwards range is rejected; an empty window says so
rather than drawing nothing and leaving you guessing.

### What the map draws

- **Solid line** — path genuinely observed, meaning two consecutive fixes less
  than 60 s apart (`observedGapS`).
- **Dashed line** — inferred. The machine went from A to B, but the straight line
  between them is a guess.
- **The marker** — a top-down vehicle built from SVG primitives, tinted by engine
  state, that **rotates to face its direction of travel** as it steps through the
  window.

Engine state is carried by colour throughout — on the marker, in the readout, and
in every ribbon block:

| Colour | State |
|---|---|
| `#3987e5` blue | Working |
| `#d95926` orange | Idling |
| `#199e70` green | Transported |
| `#6b7785` grey | Engine off |
| `#484f58` dim | Offline |

### The four cards in Trace

The left rail changes in Trace: every figure is for the **window you typed**,
not the whole shift.

| Card | |
|---|---|
| Status | What the machine was doing **at the scrubbed moment** — it follows the ribbon and the playback, not the window. |
| Distance covered | Total path in the window, with the watched/inferred split beneath it. |
| Fuel used | Litres burned inside the window, from the in-line meter. `—` on machines with no fuel sensor. |
| Engine hours | Running time **actually measured** — only stretches between fixes less than 60 s apart are counted. |

> **Engine hours excludes the gaps on purpose.** Multiplying a sample
> proportion by the window length would report hours nobody recorded. On the
> corrupted August shift this card reads **17 min** across a 14-hour window,
> which is the honest answer: that is how much running time was genuinely
> observed. A figure near 14 h would be an extrapolation dressed as a
> measurement.

### The readout beside the marker

State, timestamp and engine speed **at the scrubbed position**. It rewrites on
every scrub and every playback tick, so you can read what the machine was doing
at any moment in the replay.

### The ribbon and playback

Beneath the map, the window is drawn as coloured blocks with a playhead. Click or
drag anywhere on it to jump. Play sweeps the whole window in about **30 seconds**
(500 steps at 60 ms), whatever the window's length.

| Key | Does |
|---|---|
| ← → | Step back or forward by 1% of the window |
| Home | Jump to the start of the window |
| End | Jump to the end |

> **The bare stretches are the point.** A block is painted **only where two
> consecutive fixes are less than 60 s apart**. Everything the firmware defect
> destroyed is left unpainted, so the gaps in the ribbon are the data loss, drawn.

> **Do not quote the ribbon as a coverage figure.** Each block is given a
> **minimum rendered width of 0.7 units in 1,000** so a sub-second fragment stays
> visible. With 349 fragments that floor accounts for most of the painted area,
> which makes the strip look far fuller than the data is. **True continuous
> coverage is 2.0%** — 1,023 seconds of a 14.2-hour shift. The strip is honest
> about *where* data survives and about the shape of the clustering; it
> overstates *how much*.

The path, ribbon, playback range and all four rail statistics recompute for
whatever window you type. In Trace the rail shows four cards — path observed,
path inferred, time stopped, idling stopped — not the six from Live.

Trace is available only for the real device (862636058411560); for simulated
assets the toggle is disabled and the panel explains why.

---

## Where the numbers come from

```
Not mine/*_LOG*.TXT          the vendor's encrypted log  (read-only, never modified)
        |
        |  decode_log.py     AES-128-ECB, 23-byte records, validation
        v
build_data.py  ->  data.js   asset list + this machine's live figures
build_track.py ->  track.js  movement history: points, segments, dwells
        |
        v
vehicle.html                 reads both files directly. No server needed.
```

Rebuild them by hand:

```bash
cd dashboard
python build_data.py
python build_track.py
```

Or let [`sync/`](../sync/README.md) do it on a schedule from the ingest bucket —
it runs the same two scripts, pointing them at whatever it downloaded via
`BLUEICE_LOGS`.

### What is real

Device **862636058411560 (EX-340D-01)** is real: every position, RPM, coolant,
torque, engine-hour, idle and distance figure is decoded from a production log.
Brands, models, quantities and tank capacities are real, from the BlueICE asset
register.

**The 1,074 simulated assets have been removed.** The picker holds the real
shifts plus **five generated fixtures**, `DEMO-FUEL-01` to `-05`, one for each
outcome the fuel reconciliation can produce:

| Fixture | Demonstrates | Balance reads |
|---|---|---|
| `DEMO-FUEL-01` | fuel missing | about **+60 L** |
| `DEMO-FUEL-02` | clean shift | about **0 L** |
| `DEMO-FUEL-03` | unlogged delivery | about **−90 L** |
| `DEMO-FUEL-04` | no refuel in the shift | nothing to compare |
| `DEMO-FUEL-05` | tank nearly empty (6% of capacity) | nothing to compare |

None of these machines exist and every balance is invented. Each is badged in
the picker with the case it demonstrates, again in its own panel, and here.
**Never quote a number from one.**

---

## The rules it follows

These are deliberate, and several cost the interface a more impressive-looking
number. If you change one, know what you are trading.

| Rule | Why |
|---|---|
| Unmeasured shows an em-dash, never `0` | A confident `0 km` that actually means "unmeasured" is worse than an honest blank. |
| Observed and inferred path drawn differently | With 99.5% of records lost, a confident smooth route would be a lie. |
| Distance is gated, not integrated | Raw GPS integration invents ~100 km/day for a machine that never moves. Gated 19.94 km against a naive 22.70 km. |
| Transport is excluded from operating hours | Segments above 6 km/h mean the machine was on a trailer. A naive system would bill that as machine activity. |
| The ECU fuel field is a "reading", never litres | It is a consumption *rate* (r = +0.997 against torque × rpm), but its **units** are unconfirmed, so it is never shown as litres. Litres appear only where a tank level and a known geometry exist. |
| Generated assets are badged wherever they appear | `DEMO-FUEL-01` is a fixture. A fuel balance is an accusation, so an invented one must never sit beside a real machine number or a real site. |
| Data quality is shown, not hidden | Every derived figure names the record count behind it. |

---

## When something fails

| Symptom | What it means |
|---|---|
| "Basemap unavailable" | Map tiles could not be fetched — no internet, or the tile host is blocked. **The data still draws over blank ground.** Degraded states are designed, not incidental. |
| Trace toggle is disabled | The selected asset is simulated. Only the real device has history. |
| Empty window message | No valid fixes fall entirely inside the times you typed. Widen the range. |
| Numbers look stale after a sync | A cached dataset. `serve.py` sends no-cache headers for exactly this reason; if you opened the file directly, hard-refresh. |
| Page loads but the map is blank | The map library comes from a public CDN. Without internet the library itself will not load. |

---

## Behaviour worth knowing

- **It collapses to a single column below 820px.** Rail, panel and map stack
  rather than overlapping. This was broken at one point and is now tested at
  375px, not inferred from a media query.
- **Motion is restrained and motivated.** Cards arrive with a short staggered
  entrance — transform and opacity only — and the whole thing goes static under
  `prefers-reduced-motion`.
- **Every control gives tactile feedback.** Buttons translate 1px down on press;
  the marker scales slightly. Without it a dark flat control gives no sign it
  received the press.
- **No web fonts, and no icon font.** Play, pause and the vehicle are SVG
  primitives. This runs on remote sites over poor connections, and a screen whose
  type or icons fail to load is worse than a plain one.
- **Every number is monospace**, so columns of figures align and a changing digit
  does not reflow the row.

The full design system — colour roles, type scale, component rules, motion spec
and an explicit banned list — is in [DESIGN.md](DESIGN.md). Feed it to a
generator when building a screen that has to match this one.
