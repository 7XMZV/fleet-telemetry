# Design System: BlueICE Fleet Telemetry

Source of truth for generating new screens that sit alongside `vehicle.html`.
Feed this to Stitch when producing an alerts screen, a fleet list, a maintenance
view, a site report, or any other surface in this product.

**This system was not invented for this document.** It is extracted from the
working prototype, and its colour set was validated with a contrast/CVD checker
rather than chosen by eye. Where a generic design rule conflicts with something
this project measured, the measurement wins and §8 says why.

---

## 1. Visual Theme & Atmosphere

A dark, instrument-panel interface for heavy construction equipment. The mood is
**control room, not consumer app**: quiet dark ground, numbers that read at a
glance from a metre away, and colour used only to mean something. Think the
gauge cluster of a machine rather than a marketing page.

The product is read by site supervisors and fleet managers, often on a laptop in
poor light, sometimes projected. Legibility beats elegance every time.

- **Density: 6 of 10 — daily-app balanced, leaning dense.** Numbers are the
  content. Padding is tight enough to fit a working set on one screen, loose
  enough that nothing collides.
- **Variance: 3 of 10 — predictable and placed.** Deliberately low. Operators
  return to the same screen daily and must find the same figure in the same
  place. Asymmetry for its own sake is a defect here, not a style.
- **Motion: 5 of 10 — fluid, motivated, restrained.** Motion carries meaning
  (something arrived, something is live, time is moving) and is otherwise absent.

**Spatial grammar:** three zones, each holding exactly one kind of thing.

| Zone | Holds |
|---|---|
| Attached to the object on the map | The live reading for that object |
| Left rail | One card per measured parameter |
| Right column | Identity, mode switches, controls, charts |

New screens should keep this separation: **the number rides with the thing it
describes; controls live to the right; parameters stack on the left.**

---

## 2. Colour Palette & Roles

One palette for the whole product. Dark theme is the only theme.

### Surfaces and ink

- **Ground** (`#0d0d0d`) — page background. Deliberately off-black, never `#000`.
- **Panel** (`#161b22`) — every card, popover and floating surface.
- **Recessed** (`#1c232c`) — inputs, inner wells, the track behind a control.
- **Hairline** (`#2c2c2a`) — 1px borders and dividers. Structural, never decorative.
- **Primary Ink** (`#e6edf3`) — headline figures, values, active labels.
- **Secondary Ink** (`#c3c2b7`) — supporting copy, legends, inactive controls.
- **Muted Ink** (`#898781`) — units, axis ticks, captions. **Not field labels**:
  at 11px uppercase this is too faint once projected, so labels take Secondary
  Ink instead.

### State colours — semantic, never decorative

These four carry meaning. **Do not reuse them as series colours or accents.**

- **Working** (`#3987e5`) — engine under load. Also the single interactive accent:
  focus rings, the active tab, the playhead.
- **Idling** (`#d95926`) — running but producing nothing. This is the *cost*
  colour: wherever it appears it means fuel is burning for no output.
- **Transported** (`#199e70`) — the machine is being carried, not working.
- **Off** (`#6b7785`) — ignition off.
- **Offline** (`#484f58`) — no upload in the last hour. Rendered greyed, never hidden.
- **Caution** (`#fab219`) — data-quality warnings and invalid input only. Never a series.

**Validated, not eyeballed.** Working / Idling / Transported were checked as a
categorical set against the `#161b22` panel: worst all-pairs CVD ΔE **9.4**,
normal-vision ΔE **20.9**, all three ≥ 3:1 contrast. If you add a fourth series
colour, re-run that check; do not pick one that "looks different enough".

**Banned:** purple or violet accents, neon or outer glows, gradient fills on
text, any second accent colour, pure `#000000`, pure `#ffffff`.

---

## 3. Typography Rules

- **Interface:** `system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`.
  Base 14px / 1.55.
- **All numbers:** `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`.
  Non-negotiable. Every measured value, timestamp, coordinate, duration and
  identifier is monospace so columns of figures align and a digit change does not
  reflow the row.
- **Hierarchy comes from weight, size and colour** — 21px semibold mono for a
  card's headline figure, 9.5px uppercase muted for its label. Nothing screams.

**Scale in use:** label **11px** uppercase (tracking .6px) · body 11.5–13px ·
card figure **27px** mono with its unit inline at 14px · hero figure 42px mono.

> **The card scale was raised from 9.5px/21px after testing on the real screen.**
> This product is read on a laptop in poor light and is regularly projected in a
> site office, and the original scale did not survive either — the labels in
> particular vanished at a metre. Field labels also moved from Muted Ink to
> **Secondary Ink** for the same reason. If you are adding a card, use 11px and
> 27px; do not copy the older figures out of a screenshot.
>
> **The unit belongs inside the figure**, not on the note line below it:
> `-58.6 L` is one value, while a number on one line and its unit on the next is
> two things the reader has to reassemble. This cost real legibility before it
> was fixed.

**Serif is banned outright.** This is a dashboard.

> **Deviation from the generic rule, deliberate:** the usual advice is to replace
> system fonts with Geist or Satoshi for character. This product loads **no web
> fonts on purpose**. It runs on remote sites over poor connections, and the same
> reasoning that keeps the map marker as inline SVG rather than an icon font
> applies here: a screen whose type fails to load is worse than a screen with
> ordinary type. If the deployment gains reliable bandwidth, `Geist` + `Geist
> Mono` is the intended upgrade and nothing else needs to change.

---

## 4. Component Stylings

**Cards.** Panel fill, 1px hairline border, radius **10–11px**, shadow
`0 12px 34px rgba(0,0,0,.5)` (untinted black is correct against this ground).
One card holds one idea. A card that would hold two things is two cards.

**Parameter card** — the atom of this product. Muted uppercase label, mono figure,
muted unit line. When the parameter represents waste or risk, it takes the Idling
border and figure colour so cost reads before you have parsed the number.

**Empty values.** A parameter with no data shows an em-dash glyph, never `0`.
A confident `0 km` that is actually "unmeasured" is worse than an honest blank.

**Buttons.** Flat, recessed fill, hairline border, radius 8px. Active state
translates 1px down. The selected segment of a toggle takes a translucent
Working wash with a Working border. **No glows, no gradients, no custom cursors.**

**Inputs.** Label above in muted uppercase, input on Recessed fill with hairline
border, message below. Focus ring in Working. Never a placeholder as a label.

**Controls that carry data.** Where a control can also show information, it
should. The playback scrubber is a colour-coded ribbon of the shift rather than a
plain track, so the same pixels give you both "where am I" and "what happened".

**Loading.** Skeletal blocks matching final dimensions. No spinners.

**Tactile feedback.** Every button translates 1px down on `:active`; the vehicle
marker scales to .94. Without it a dark flat control gives no sign it received
the press.

**Icons are SVG primitives, never glyphs.** Play, pause and the vehicle are
composed from `polygon`/`rect`. Unicode symbols such as `▶` render as colour
emoji on some platforms, which looks broken beside mono numerals, and an icon
font is a network dependency this product cannot rely on.

**Degraded states are designed, not incidental.** If a dependency fails, say so
in place and keep rendering everything that still works. The map does this: tiles
unreachable produces a caution note and the data still draws over blank ground.

---

## 5. Layout Principles

- **Grid over flex math.** Never `calc()` percentage hacks.
- **Nothing overlaps.** Floating panels sit in reserved zones with real gaps.
  Map controls and panels are placed so they never collide.
- **Cards stack in a column with 8–10px gaps.** Same rhythm in both rails.
- **Full-height uses `100dvh`, never `100vh`.** `vh` shifts when the iOS Safari
  address bar hides and the whole layout jumps mid-scroll.
- **Below 820px nothing floats.** The map takes a fixed 44dvh block and the rail,
  panel and player become static in one scrolling column, ordered
  map → identity → controls → parameters. Absolutely positioned rails overlap
  each other and bury the map on a phone; this must be tested at 375px, not
  assumed from the presence of a media query.
- **Touch targets ≥ 44px** on anything an operator taps in the field.

**Shape lock:** radii live in a 5–11px band (5 ribbon, 6–8 controls, 9–10 inner
panels, 10–11 cards). One 20px pill exists for the map hint. Do not introduce
2xl/pill radii — they read as consumer-app and break the instrument feel.

---

## 6. Motion & Interaction

- **Entrance:** panels arrive staggered in reading order, 70ms apart,
  `.42s cubic-bezier(.16,1,.3,1)`, `translateY(10px) scale(.985)` → rest.
  Sequence matches priority: identity first, numbers last.
- **Transform and opacity only.** Never animate width, height, top or left.
- **Every animation must answer "what does this communicate?"** Valid answers:
  something arrived, something is live, a value changed, time is moving.
  "It looked good" is not one.
- **Exactly one perpetual loop exists** — the pulse on the live vehicle marker,
  because it means *this is live right now*. Nothing else loops.

> **Deviation, deliberate:** the generic guidance says every active component
> should carry an infinite micro-loop. On an instrument panel that is actively
> harmful — ambient motion competes with the one indicator that is genuinely
> live, and operators stop trusting movement as a signal. Restraint here is the
> feature.

- **`prefers-reduced-motion` collapses everything to static**, including the
  marker pulse and the heading rotation. Mandatory, not optional.

---

## 7. Anti-Patterns (Banned)

**Typographic**
- No em-dash (`—`) or en-dash (`–`) in prose, labels, ranges or captions. Ranges
  read "07:16 to 21:30". *One* exception: the standalone em-dash as an
  empty-value glyph in a data cell.
- No middle-dot chains. Maximum one `·` per metadata line.
- No emojis. No serif. No Inter.

**Visual**
- No purple, neon, glows, gradient text, custom cursors, pure black or white.
- No decorative status dots. A coloured dot must encode real state.
- No decorative hairlines or crosshair grids used to "look designed".
- No card containers where a hairline or whitespace would group just as well.
- No three equal cards in a row.

**Content**
- No invented precision. Every number traces to a measurement or is labelled mock.
- No generic placeholder identities. Assets carry real makes and models.
- No marketing verbs: elevate, seamless, unleash, next-gen.
- No scroll cues, version stamps, locale/weather strips, or section-number eyebrows.

**Integrity — specific to this product, and the ones that matter most**
- **Never present simulated data as live.** Only one device is deployed; every
  other asset is labelled simulated wherever it appears.
- **Never draw inferred data as observed.** Path measured from consecutive fixes
  is solid; a straight line across a data gap is dashed. They are visually
  distinct at a glance.
- **Never hide a data-quality problem behind a polished surface.** This fleet's
  firmware currently discards 99.5% of records. The interface shows that.
- **Never chart a field whose units are unconfirmed under a confident label.**
  The `fuel` field is drawn as "Fuel reading", not litres, until its scaling is
  confirmed.
- **Never let outliers set a chart's scale.** Use robust bounds and say how many
  samples sit off-scale.

---

## 8. When This Conflicts With Generic Advice

Three rules here contradict standard premium-UI guidance. Each is deliberate:

| Generic advice | This system | Why |
|---|---|---|
| Replace system fonts with Geist/Satoshi | System stack only | Zero web-font dependency on remote sites with poor connectivity |
| Perpetual micro-loops on active components | Exactly one loop, on the live marker | Ambient motion destroys the one signal that means "live" |
| High variance, asymmetric layouts | Variance 3, fixed zones | Operators must find the same figure in the same place daily |

If a new screen needs to break one of these, it needs a reason of the same kind:
a measurement, a failure mode, or a user in a specific situation.
