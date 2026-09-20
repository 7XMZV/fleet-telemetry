# BlueICE Fleet Telemetry — Full Project Report

**Author:** Hamza · Internship, BlueICE Construction, Oman
**Date:** 8 September 2026
**Reference data:** one production log, `862636058411560_2026_09_01_05_43_32_LOG003.TXT`,
4,195,960 bytes, covering a single 14.2-hour shift on 31 August 2026.


This document explains the whole project in one place: what was asked for, what
was found, what was built, what is real, what was tested, and what the next
person has to do. Every figure in it is measured from that log and reproducible
with the commands given. Nothing is estimated unless it says so.

The supporting documents ([FINDINGS.md](FINDINGS.md), [DECISIONS.md](DECISIONS.md),
[FORMAT_SPEC.md](FORMAT_SPEC.md), [BUG_REPORT.md](BUG_REPORT.md),
[HANDOVER.md](HANDOVER.md)) go deeper on each area. This report is the one to
read first, and the only one you need to read in full.

---

## 1. Executive summary

Three sentences, if that is all you have time for:

1. **BlueICE's machines idle about 60% of a shift.** Measured, not modelled — and
   detectable with the sensors that already work, needing no new hardware. This
   is the number that justifies the programme.
2. **The telemetry device throws away 99.5% of the data it records.** It is a
   firmware defect, it is diagnosed, and it blocks fleet rollout. No amount of
   cloud engineering can recover data that was never written correctly.
3. **Everything else works.** The undocumented log format is fully reverse
   engineered and specified, a correct decoder replaces the vendor's broken one,
   a working dashboard runs on the real data, and the data pipeline is
   automated from the ingest bucket to the screen.

### The numbers that matter

| | | |
|---|---|---|
| Idle share of a working shift | **59.7%** | 250 idling samples vs 169 working |
| Wasted fuel, one excavator, one day | **~85 L** | at ~10 L/h idle burn |
| Excavators in the fleet | **45** | of 75 heavy machines |
| Records surviving the firmware | **0.50%** | 419 of 84,328 |
| Assets in scope | **~1,075** | 75 heavy plant + ~1,000 road vehicles |

---

## 2. The brief, and how it changed

The starting request was a live map of the company's vehicles where clicking a
car opens a dashboard showing distance covered, fuel consumed, and current RPM.

Three things reshaped that during the work, and all three are worth understanding
because they explain why the delivered product does not look like the original
sketch:

**The fleet is mostly heavy plant, not cars.** 45 excavators, 13 bulldozers,
9 dump trucks, 5 motorgraders, 3 mobile cranes. Excavators and dozers do not
travel; distance is close to meaningless for them. **Engine hours and idle time
are the metrics that count**, which is why the product leads with idle share
rather than kilometres.

**"Fuel consumed" cannot be delivered yet.** The `fuel` field exists but nobody
knows what it measures — level, rate, or a raw ADC value (open question O2). It
is shown as a *reading*, explicitly not as litres, and every fuel feature is
deferred to v2. Charting an unknown unit as litres would have been the single
most damaging thing this project could have shipped.

**Only one device is deployed.** A live map of a thousand cars is not currently
possible. The prototype is a **single-vehicle view** built on real data, with the
other 1,074 assets simulated and labelled as such so the interface can be
exercised at fleet scale.

---

## 3. What was delivered

| | |
|---|---|
| **[FORMAT_SPEC.md](FORMAT_SPEC.md)** | The binary log format, reverse engineered and specified. Authoritative — it did not exist before. |
| **`decode_log.py`** | A correct decoder with validation and a firmware release gate. Replaces the vendor's `decrypt_log.py`, which is broken in three ways. |
| **[FINDINGS.md](FINDINGS.md)** | Every result, each with the command that reproduces it. |
| **[BUG_REPORT.md](BUG_REPORT.md)** | The blocking firmware defect, written for the firmware owner: evidence, diagnosis, four things to check, and a measurable acceptance test. |
| **`dashboard/`** | Working prototype — live status and history replay for one vehicle, on real decoded data. |
| **`dashboard/serve.py`** | Serves it on the local network so other machines can open it. |
| **`sync/`** | Pulls logs from the ingest bucket on a schedule and rebuilds the dashboard data. Automated install, update and scheduling. |
| **`presentation/`** | A 15-slide deck, generated from a design canvas. |
| **[DECISIONS.md](DECISIONS.md)** | 30 design decisions with reasoning, plus 5 open questions. |
| **[HANDOVER.md](HANDOVER.md)** | What the next person does, in order. |

The vendor's own files live in `../Not mine/` — the raw log, `FIRMWARE.bin`,
`latest.json` and the original `decrypt_log.py`. **They are read-only inputs.
Nothing in this project modifies them**, and that was verified after every stage
of the work.

---

## 4. The device and its data

### 4.1 What the hardware does

A custom device on each machine reads engine data over CAN and position over
GPS, writes AES-encrypted binary log files to local storage, and uploads them
over a cellular SIM roughly every five minutes. The format was undocumented.

**The format, reverse engineered** (full detail in [FORMAT_SPEC.md](FORMAT_SPEC.md)):

- File: 4-byte magic `AES2`, a 48-byte encrypted header, then a stream of
  record frames, then a `0x0000` terminator.
- Frame: a 2-byte length prefix (`32`) followed by a 32-byte AES-128-ECB
  payload.
- Payload: a **23-byte packed record**, then 9 bytes of `0xCD` fill —
  *not* PKCS#7 padding, which matters because stripping it by trailing-byte
  length corrupts the record.
- Record: `ts` (uint32), `ms` (uint16), `rpm` (uint16), `torque` (int16),
  `fuel100` (uint16), `coolant` (int16), `lat` (int32), `lon` (int32),
  `flags` (uint8) — little-endian, struct format `<IHHhHhiiB`.
- Key: `<redacted>`, hardcoded and shared across the whole fleet.

**The device identifier appears nowhere in the file** — not in the header, not
in any record. Identity depends entirely on the filename. Any ingest pipeline
must therefore check the filename's IMEI against the uploading device's
credentials, or identity is trivially forgeable.

### 4.2 The decoder

The vendor supplied `decrypt_log.py`. It runs, it produces output, and the
output is wrong. Three defects, all fixed in `decode_log.py`:

1. **It fabricates about 36,700 records that never existed.** Its `pos += 1`
   resynchronisation walks past the end-of-records terminator into 1.33 MB of
   trailing data and decodes noise as telemetry. It reports 121,069 records where
   only 84,328 are framed and 419 are real.
2. **Longitude is parsed as unsigned** (`'<IHHhHhiIB'` — capital `I`). Masked in
   Oman, where longitude is positive; silently corrupt anywhere west of
   Greenwich.
3. **No validation at all**, so the 99.5% corruption is invisible in its output.

This is the most dangerous class of bug in the project: a tool that looks like
it works. Anyone who trusted its output would have built a system on fabricated
data.

`decode_log.py` stops cleanly at the terminator, never resynchronises, validates
every record against physical limits, and carries a release gate:

```bash
python decode_log.py <log> --gate 99      # exit 0 = pass, 1 = fail
```

---

## 5. Findings

### 5.1 The business case: machines idle ~60% of a shift

Of the valid records with the engine running:

| | |
|---|---|
| Idling (0 < rpm < 900) | **250** |
| Working (rpm ≥ 900) | **169** |
| **Idle share** | **59.7%** |

Torque corroborates it independently: median **7** while idling against **19**
while working.

**The 900 rpm threshold is measured, not assumed.** The RPM distribution is
cleanly bimodal — a peak at 600–699 (129 samples), a near-empty valley from 700
to 999 (30 samples across three bins), and a second peak at 1100–1199 (82
samples). The threshold sits in the valley, which is why idle detection is
reliable rather than a judgement call.

The two recorded stops tell the same story: **81 of 107 stopped minutes had the
engine running and the machine going nowhere** — roughly 13 litres, one machine,
one afternoon.

At a typical excavator idle burn of ~10 L/h, ~60% of a 14-hour shift is on the
order of **85 wasted litres per machine per day**. Across 45 excavators that is
the largest number in this programme, and **it needs only RPM and ignition —
both of which already work.** No fuel sensor required, which means the business
case does not depend on the unfinished fuel hardware.

> **One caveat, and it is important.** Valid records cluster between 15:16 and
> 18:15 with single stragglers at 07:16 and 21:16 — that reflects *where data
> survived the corruption*, not what the machine did. Behavioural **shapes** (the
> bimodal split, the idle/work contrast) are robust to this. **Absolute counts
> and any time-of-day profile are not.** Quote the shape, not the totals.

### 5.2 The blocker: 99.50% of records are corrupt

| | |
|---|---|
| Records framed | **84,328** |
| Records physically valid | **419** |
| **Validity rate** | **0.50%** |

**This is a firmware defect, not a decoding problem**, and the arithmetic proves
it. Every record carries a correct 32-byte length prefix and the framing closes
exactly: `4 + (2 + 48) + 84,328 × 34 = 2,867,206`. The remaining 1,328,754 bytes
sit after the terminator and are not records at all. When a record survives, it
decodes perfectly.

Records fail on physically impossible values — 70,268 on timestamp, 8,723 on
`ms`, 4,792 on latitude, the rest on longitude, coolant, flags and RPM.

**Diagnosis.** Each record encrypts to two AES-ECB blocks. Across the file,
49.99% of cipher blocks are duplicates: block 1 is essentially unique per record,
while **block 2 is byte-identical across thousands of consecutive records** —
only four distinct values across the first 20,000. Block 2 holds the high bytes
of latitude, the longitude and the flags. Position fields frozen while timestamp
and RPM are random is consistent with **a record buffer being encrypted and
written while most of its slots were never populated**. The 9 fill bytes are
`0xCD`, a common uninitialised-memory marker, and the file header ends in `0xCD`
too.

**Refined further by the gap analysis.** Median gap between survivors is 10
slots, mean 177.6, largest 15,622, and 359 of 386 runs are a single isolated
record. No gap is a multiple of any common buffer size (16/32/64/100/128/200/256
all tested). A fixed-size buffer flushing once per cycle would produce evenly
spaced survivors. This heavy-tailed, irregular pattern points to **a race between
the sampling task and the write task** rather than a clean buffer-flush bug —
which is why "check the concurrency first" is the top recommendation in the bug
report.

**Impact.** The device intends ~1.65 records/second and delivers roughly **one
usable sample every two minutes**. Live position is up to two minutes stale on a
good link, engine-hour totals rest on 0.5% of the evidence, and fuel-theft
detection would miss ~199 of every 200 readings. Installing this firmware on
1,075 assets means paying for 1,075 installations twice.

**Acceptance test.** `python decode_log.py <new_log> --gate 99`. Do not accept
"it's fixed" without a fresh full-shift log passing that gate.

### 5.3 Three things that were believed and are not true

**The hardware is 10 Hz, not 10 ms.** The `ms` field takes exactly ten values —
0, 100, 200 … 900. The device *cannot express* an interval finer than 100 ms.
The measured write rate is 1.65 records/second. A true 10 ms log of this shift
would be 174 MB; the file is 4.2 MB. Anything in the datasheet repeating the
10 ms figure is unverified.

**Coolant is a dead signal.** Across 419 valid records it takes **two values**:
`53` (417 times) and `88` (twice). For comparison, in the same records RPM takes
191 distinct values, fuel 156, torque 53. The other sensors are alive; this one
is not — a stuck read, an unconnected sender, or a hardcoded default. **Do not
build overheating alerts on it.**

> This report corrects an earlier draft of my own, which described coolant as
> "53–88 °C tracking a plausible warm-up curve". That was wrong. It is two
> values, not a curve.

**The `fuel` field is the ECU's consumption estimate.** It tracks mechanical
power almost exactly — **r = +0.997 against torque × rpm**, and +0.592 against
RPM, measured over 61,080 clean records. It rises and falls with load rather
than draining, so it is a rate, not a tank level.

This corrects an earlier conclusion in this report. The same field was read as
*not* a consumption rate on the strength of `r = 0.34` — a figure computed from
the **419 records that survived the August corruption**, 0.50% of the shift.
A statistic drawn from half a percent of a signal said the opposite of the
truth, confidently. **The units are still unconfirmed**, so the dashboard still
labels it "reading" and never litres.

### 5.4 Movement, and why raw GPS lies

From the 375 fixes carrying a GPS lock (89.5% of valid records):

| | |
|---|---|
| Path genuinely observed (fixes < 60 s apart) | **3.19 km** |
| Path inferred (straight lines across gaps) | **19.50 km** |
| Gated distance (12 m movement gate + RPM) | **19.94 km** |
| Naive GPS integration over the same fixes | **22.70 km** |

**Raw GPS invents about 100 km/day.** Measured directly: 258 fixes inside a
193.5 m bounding box produced 0.52 km of apparent movement — roughly 2 m of
phantom distance per sample. At a 1 Hz rate over a 14-hour shift that is ~100 km
a day invented for a machine that never moved. The dashboard therefore applies a
**12 m movement gate plus RPM corroboration**, and stationary plant with no
measured travel shows an em-dash, never a confident zero.

**Only 3.19 km was actually watched.** The rest is a straight line between two
fixes with nothing valid in between — a direct consequence of the corruption.
The map draws observed path solid and inferred path dashed, so a viewer can
never mistake a guess for a route.

**The machine was transported, not driven.** 10 segments totalling 14.53 km
exceed 6 km/h, peaking at 200.1 km/h. A tracked excavator cannot move that fast
under its own power — it was on a low-bed trailer. These are **excluded from
operating hours by design**. A naive system would bill transport as machine
activity.

### 5.5 Security and integrity defects

Recorded for completeness; each is separate from the corruption fix and should be
scheduled on its own.

1. **One hardcoded key for the entire fleet** — `<redacted>`, in plaintext
   in the vendor's decoder and presumably in every device. Compromise one,
   compromise all 1,075.
2. **AES-128-ECB.** Identical plaintext blocks produce identical ciphertext. The
   49.99% block reuse above *is* that leak, in production data.
3. **No message authentication.** Records can be forged or altered undetectably —
   material for a system whose stated purpose is detecting fuel theft.
4. **Unsigned OTA updates.** `latest.json` declares `"size": 128000`;
   `FIRMWARE.bin` is **131,072 bytes**. No hash, no signature. Anyone able to
   serve that JSON can push arbitrary firmware to the whole fleet, and the size
   mismatch alone may brick or reject updates.
5. **No device identifier in the log**, as noted in §4.1.

Recommended: per-device keys and AES-GCM, and a signed manifest.

### 5.6 The fleet

| Class | Units | Total tank capacity |
|---|---|---|
| Excavators | 45 | 27,755 L |
| Bulldozers | 13 | 9,610 L |
| Dump Trucks | 9 | 4,880 L |
| Motorgraders | 5 | 2,004 L |
| Mobile Cranes | 3 | 1,000 L |
| **Total** | **75** | **45,249 L** |

26 equipment types; CAT (11), SANY (7), Komatsu (3), Volvo (2), Hyundai (2),
Zoomlion (1). Plus ~1,000 road vehicles, giving ~1,075 assets. Valid fixes span
23.428–24.451 N, 56.552–58.099 E — northern Oman, around Ibri in Ad Dhahirah,
which is why the target AWS region is **me-central-1 (UAE)**.

**The capacity file is titled *unverified*, and 18 of 26 capacities are marked
LOW confidence** — several with notes such as "submitted value is 46 L higher
than the checked OEM value". Fuel-theft alerting cannot be built on guessed
capacities: a 46 L error is a stolen jerrycan you would never see. These must be
checked against operator manuals before any fuel feature ships.

---

## 6. The prototype

`dashboard/vehicle.html` — one page, no build step. It opens with the vehicle on
the map and no dashboard; clicking the marker opens it.

**Layout: three zones, each holding one kind of thing.**

| Where | What |
|---|---|
| Beside the vehicle | Engine state and RPM, riding with the marker, so the number and the thing it describes are the same object on screen |
| Right | Identity, the Live/Trace switch, the typed time-range control, and the shift totals |
| Left rail | One card per measured parameter, each standing alone |

**Live** shows the current state beside the machine, a rail of measured
parameters — fuel in tank, engine hours, distance, coolant — and a panel of
**shift totals**: working and idling time, distance, litres burned, litres
burned *while idling*, litres delivered, and the unaccounted balance. Totals
rather than charts, so every figure is a number that can be quoted rather than
one to be estimated off a line.

**Trace** replays a typed UTC time window. The map draws the path — solid where
observed, dashed where inferred — and a marker that steps through it and rotates
to face its direction of travel. An "At this moment" panel reports state,
timestamp and engine speed at the scrubbed position. Beneath the map, a **shift
ribbon** replaces the plain slider: the window drawn as coloured blocks, orange
idling, blue working, with a white playhead you can click, drag or arrow-key.

**The bare stretches of that ribbon are the point.** A block is painted only
where two consecutive fixes are less than 60 s apart, so everything the firmware
destroyed is left unpainted.

> **A correction worth repeating, because it is easy to get wrong in a
> presentation.** I originally reported the ribbon as ~24.5% covered. That
> measured *painted pixels*: each block has a minimum rendered width so a
> sub-second fragment stays visible, and with 349 fragments that floor accounts
> for most of the painted area. **True continuous coverage is 2.0%** — 1,023
> seconds of 14.2 hours. The strip is honest about *where* data survives and
> about the shape of the clustering; it overstates *how much*. **Quote the 2.0%,
> never the painted fraction.**

The fuel chart uses robust y-scaling (2nd to 98th percentile) and states how many
samples sit off-scale, because the two stragglers near 280 and 388 against a
median of 3.8 would otherwise flatten the entire real signal onto the baseline.

**Design system.** [`dashboard/DESIGN.md`](dashboard/DESIGN.md) captures the
tokens, component rules, motion spec and an explicit banned list, extracted from
the working prototype rather than written aspirationally. Its colour set was
validated with a contrast/CVD checker: the Working/Idling/Transported series has
a worst-pair CVD ΔE of 9.4 and normal-vision ΔE of 20.9, all above 3:1 contrast
on the panel colour. Feed it to a generator when adding screens.

---

## 7. Architecture and the data pipeline

### 7.1 The target

```
Device (CAN + GPS, 10 Hz local log, AES-128 encrypted files)
   │  cellular, file upload every 5 min
   ▼
S3  ──►  Lambda (decode · validate · dedup)  ──►  Postgres / PostGIS / Timescale
                                                        │
                                                        ▼
                                               MapLibre GL dashboard
```

Region **me-central-1 (UAE)**, closest to the Oman fleet. The Lambda should reuse
`decode_log.py` rather than reimplement the format.

**Make ingest idempotent from day one** — dedup on file hash *and*
`(device_id, ts, ms)`. Lifetime totals are cumulative and never recomputed, so a
double-counted file is wrong forever.

### 7.2 The sync, which exists because the Lambda does not

`sync/` closes the gap the cheap way: a scheduled poll that lists the bucket,
downloads what is new, decodes it, and regenerates the dashboard datasets.

```bash
powershell -ExecutionPolicy Bypass -File install.ps1 -Bucket <bucket-name>
powershell -ExecutionPolicy Bypass -File schedule.ps1 -Minutes 15
```

`install.sh` and a cron line cover Linux. **It never asks for an access key** —
boto3 reads credentials from the standard chain (IAM role, `aws configure`, or
environment variables), and the least-privilege policy it needs is
`ListBucket` + `GetObject` only, with no write permissions at all.

Four things it is careful about:

1. **`Not mine/` is never written to.** Downloads land in `inbox/<device>/`.
2. **Ingest is idempotent.** Objects are keyed by ETag and verified by SHA-256
   after download; the same file re-uploaded under a different key is caught by
   hash and kept out of the build.
3. **Records are deduped across files, never within one.** This one is subtle and
   it matters: the reference shift contains **two pairs of valid records sharing
   the same `(ts, ms)`** — two genuine writes into the same 100 ms bucket.
   Collapsing them moves the headline figures from 419/0.50% to 417/0.49% and
   quietly contradicts this report, via a tool nobody would think to suspect.
   Invalid records are never deduped at all, since their timestamps are garbage
   and would collide by chance, shrinking the denominator the corruption
   percentage is measured against.
4. **Undecodable files are quarantined, not discarded.** A file that will not
   parse is evidence.

Every build reports the valid-record percentage against the 99% gate. Today it
prints **GATE FAIL at 0.50%** and exits 0 — deliberately, because an alert that
is always red is an alert nobody reads. `--fail-under-gate` opts into strictness
for CI. **The day that line reads GATE PASS is the day the firmware fix is
real.**

**This is a workstation stopgap and should be deleted when the Lambda exists.**
Polling costs a `ListObjects` per cycle whether or not anything arrived and will
not scale to 1,075 devices. Two of its rules are worth carrying into the Lambda
rather than rewriting: the idempotency rules, and quarantine-don't-discard.

### 7.3 Serving it to other people

```bash
cd dashboard
python serve.py --sync-every 15
```

Binds all interfaces, prints the URLs to share (marking virtual adapters that
nobody else can reach), serves the dashboard at `/`, and optionally runs the sync
in the background so viewers see current data.

Three things to know before sending that link round: **there is no
authentication** — anyone who can reach the machine on that port can open it, and
there is no login to switch on later; **Windows Firewall** is the usual reason a
colleague cannot connect, and the server prints the `netsh` command to open the
port; and **viewers need internet**, because the map library and tiles come from
public CDNs, though the page still draws the data over blank ground and says so
when they are unreachable.

The server sends no-cache headers deliberately. A browser holding a cached
`data.js` shows yesterday's shift under today's layout, and numbers that are
merely *old* are the hardest kind of wrong to notice.

---

## 8. What is real and what is not

This section exists because the honest answer is more useful than an impressive
one, and because a demo that blurs the line will eventually be caught blurring
it.

| | Source |
|---|---|
| Device `862636058411560` (**EX-340D-01**) | **Real.** Position, RPM, coolant, torque, engine hours, idle share, distance and all history are decoded from the production log of 31 August 2026. |
| Brands, models, quantities, tank capacities | **Real**, from the BlueICE asset register — but see the LOW-confidence caveat in §5.6. |
| All other 1,074 assets in the picker | **Simulated.** Only one device is deployed. Every simulated asset says so in its own panel and cannot be traced. |
| The S3 ingest bucket | **Does not exist yet.** The sync runs against a local folder until it does. |

The simulated fleet exists so the picker and layout can be exercised at fleet
scale. **Never present it as live data.**

---

## 9. What was tested, and what was not

| | |
|---|---|
| Decoder against the reference log | **Tested.** 419/84,328 reproducible; framing arithmetic closes exactly. |
| The 99.5% figure | **Measured**, not inferred, and cross-checked three ways (framing arithmetic, rejection reasons, ciphertext block analysis). |
| Dashboard on real data | **Working**, including at 375px where an earlier version was badly broken. |
| Sync pipeline — list, download, hash, decode, quarantine, select, merge, rebuild | **Tested.** `sync/selftest.py` drives the S3 code path with a stub client over a fake bucket built by slicing the real log into two overlapping uploads. 20 assertions, all passing. |
| Sync against a local folder, end to end | **Tested.** Reproduces `data.js` and `track.js` byte for byte. |
| boto3 wiring | **Reaches AWS and fails at authentication** — as far as it can go with no credentials on the machine. |
| A real bucket, real credentials, real pagination | **Not tested.** There is no bucket. `blueice_sync.py check` is what will tell you. |
| Local network serving | **Tested** on this machine's LAN address. Not tested from a genuinely separate computer. |
| Firmware fix | **Not tested — the fix does not exist.** |

---

## 10. Open questions

| # | Question | Blocks |
|---|---|---|
| O1 | Who owns the firmware long term, and where is the source? There is **no repository of record** and one person holds the only copy. | Continuity |
| O2 | What does the `fuel` field actually measure — level, rate, or raw ADC? | All fuel work |
| O3 | Are the tank capacities correct? 18 of 26 are unverified. | Theft alerting |
| O4 | Pilot size and timing. | Rollout |

---

## 11. What to do next, in order

**1. Get the firmware fixed. It blocks everything.** Hand
[BUG_REPORT.md](BUG_REPORT.md) to the colleague who wrote the firmware — it
contains evidence, a diagnosis, four specific things to check, and the acceptance
test. Do not accept "it's fixed" without a fresh full-shift log passing
`--gate 99`. Today it reports 0.50%.

**2. Get everything into a BlueICE-owned Git repository.** Firmware source, the
decoder, the dashboard, these documents. An outside dependency on one engineer's
laptop is an operational risk to a system 1,075 assets would depend on. *This is
not hypothetical: during this project four of these documents were deleted from
disk and had to be restored from an open session.*

**3. Answer O2 and O3** — the `fuel` field's meaning, and the tank capacities.
Both block the original business case.

**4. Build the ingest pipeline** — S3 → Lambda → Postgres in `me-central-1`,
reusing `decode_log.py`, idempotent from day one. Then delete `sync/`.

**5. Pilot before rollout.** 5–10 machines across classes and sites, including
one at a no-signal location, for a month. Exit criterion: `--gate 99` passes.

---

## 12. If you present this

**Lead with the number, not the map.** That machine idled ~60% of a 14-hour
shift — real data, from BlueICE's own equipment, needing no new hardware. Then
state the blocker honestly: 99.5% data loss, diagnosed, with a defined fix and a
measurable acceptance test.

A demo that says *"here is the value, and here is the one thing standing in the
way"* is stronger than a polished map hiding a broken pipeline — and it is the
version that leaves the firmware owner with support rather than blame.

---

## 13. File map

```
Mine/
├── REPORT.md            ← this document
├── HANDOVER.md            what to do first, in order
├── FINDINGS.md            every result, with the command that reproduces it
├── DECISIONS.md           30 decisions and 5 open questions, with reasoning
├── FORMAT_SPEC.md         the binary log format — authoritative
├── BUG_REPORT.md          the blocking firmware defect, for the firmware owner
├── decode_log.py          the decoder. Replaces the vendor's decrypt_log.py
├── *_decoded.csv          the reference shift, decoded
├── dashboard/             the prototype
│   ├── vehicle.html         one vehicle: live status + history replay
│   ├── GUIDE.md             how to read it — every panel, rule and failure mode
│   ├── serve.py             serve it on the local network
│   ├── build_data.py        → data.js   (asset list)
│   ├── build_track.py       → track.js  (movement history)
│   └── DESIGN.md            the design system
├── sync/                  bucket → dashboard, on a schedule
│   ├── blueice_sync.py       check | pull | build | update | status
│   ├── selftest.py          drives the S3 path with no AWS needed
│   ├── install.ps1 / .sh    dependencies, config, then check
│   └── schedule.ps1         the Windows scheduled task
└── presentation/          the 15-slide deck, generated from a design canvas

Not mine/                READ ONLY — vendor files, never modified
├── 862636058411560_..._LOG003.TXT
├── FIRMWARE.bin
├── latest.json
└── decrypt_log.py
```

### Reproducing the headline figures

```bash
pip install pycryptodome
python decode_log.py "../Not mine/862636058411560_2026_09_01_05_43_32_LOG003.TXT" --gate 99
```

---

*This report was written from measured data. Where I got something wrong earlier
in the project — the coolant curve, the ribbon coverage figure — the correction
is recorded above rather than quietly removed, because the next person needs to
know which numbers were once stated wrongly and might still be circulating.*
