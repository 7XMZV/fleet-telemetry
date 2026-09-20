# Findings — what the data actually says

Every figure here was derived from one production log,
`862636058411560_2026_09_01_05_43_32_LOG003.TXT` (4,195,960 bytes), decoded with
`decode_log.py`. Nothing is estimated or simulated. Each finding names the
command that reproduces it.

Reproduce the headline numbers with:

```bash
python decode_log.py "../Not mine/862636058411560_2026_09_01_05_43_32_LOG003.TXT" --gate 99
```

---

## 1. The blocker: the logger intermittently discards nearly everything

| | |
|---|---|
| Records framed | **84,328** |
| Records physically valid | **419** |
| **Validity rate** | **0.50%** |

Framing is provably intact — every record carries a correct 32-byte length
prefix and the arithmetic closes exactly: `4 + (2+48) + 84328 × 34 = 2,867,206`.
The remaining **1,328,754 bytes** sit after an end-of-records terminator and are
not records at all.

So this is **not** a decoder problem. The key and the 23-byte layout are both
correct: when a record survives it decodes perfectly.

> **Corrected after syncing the whole bucket.** The 0.50% above is one shift.
> Across 27 logs from the same device the validity rate ranges from **100.00%**
> (2026-06-16 and 2026-06-24, 61,080 of 61,080 records) down to **0.11%**
> (2026-07-16), with a step change around 2026-07-09 after which it never
> recovers above 1%.
>
> **Correction.** An earlier version of this note said the flawless logs ruled
> out the key, the record layout, the framing and the decoder. They do not:
> every 100% log is JSON-lines, which is decoded without any of those. The
> formats do not overlap in time either (last JSON 2026-06-27, first binary
> 2026-06-29), so format and date are confounded and a systematic binary
> decoder defect is not excluded. See `aws/DECODER-VALIDATION.md`. Full table and consequences in
> [BUG_REPORT.md](BUG_REPORT.md#what-27-logs-show-that-one-log-could-not).
>
> Every figure in the rest of this section is still measured from the single
> 2026-09-01 shift and is labelled as such — they were never claims about the
> fleet, and they remain correct for that shift.

Rejection reasons (first failing check per record):

| Check | Records rejected |
|---|---|
| timestamp | 70,268 |
| ms | 8,723 |
| lat | 4,792 |
| lon | 62 |
| coolant | 35 |
| flags | 15 |
| rpm | 14 |

**Impact.** The device intends ~1.65 records/second. It delivers roughly **one
usable sample every two minutes**. Live position is stale, engine-hour totals are
computed from 0.5% of the evidence, and any future fuel-theft detection would
miss ~199 of every 200 readings.

### 1.1 The corruption is irregular, not periodic

This refines the original "uninitialised buffer" diagnosis and is the most useful
detail for whoever fixes the firmware.

| | |
|---|---|
| Median gap between surviving records | **10 slots** |
| Mean gap | **177.6 slots** |
| Largest gap | **15,622 slots** |
| Runs that are a single isolated record | **359 of 386** |

Gaps are **not** a multiple of any common buffer size (16/32/64/100/128/200/256
all tested — 0% divisibility except trivially at 16). A fixed-size buffer
flushing once per cycle would produce evenly spaced survivors. This heavy-tailed,
irregular pattern looks more like **a race between the sampling task and the
write task** than a clean buffer-flush bug.

### 1.2 The ciphertext confirms structured plaintext

| | |
|---|---|
| Total 16-byte cipher blocks | 168,656 |
| Unique cipher blocks | 84,338 |
| **Duplicate ratio** | **49.99%** |

Block 1 is essentially unique per record; **block 2 is byte-identical across
thousands of consecutive records** (only four distinct values across the first
20,000). Block 2 holds the high bytes of latitude, the longitude and the flags.
Those staying frozen while block 1 is random is consistent with a record buffer
being encrypted and written while most of its slots were never populated.

The 9 fill bytes after each 23-byte record are `0xCD`, a common
uninitialised-memory marker, and the file header ends in `0xCD` too.

---

## 2. The hardware is 10 Hz, not 10 ms

The `ms` field takes **exactly ten distinct values: 0, 100, 200 … 900**. Every
timestamp lands on a 100 ms boundary, so the device *cannot express* an interval
finer than 100 ms.

| Claim | Reality |
|---|---|
| "sends every 10 ms" | timestamps quantised to **100 ms** |
| implied 100 Hz | measured **1.65 records/second** |
| a 10 ms log of this 14.2 h shift | would be **174 MB** — the file is **4.2 MB** |

The 10 ms figure is wrong by 10× against the clock and ~60× against the observed
write rate. Anything in the vendor datasheet that repeats it should be treated as
unverified.

---

## 3. Coolant is a dead signal

Across the 419 valid records `coolant` takes **two values**: `53` (417×) and
`88` (2×). A real coolant temperature moves continuously through warm-up and
load.

| Field | Distinct values in the same 419 records |
|---|---|
| rpm | **191** |
| fuel | **156** |
| torque | **53** |
| **coolant** | **2** |

The other sensors are alive; this one is not. Likely a stuck read, an
unconnected sender, or a hardcoded default. **Do not build overheating alerts on
this field until it is confirmed working.**

> Correction to an earlier draft: this field was initially described as
> "53–88 °C tracking a plausible warm-up curve". That was wrong — it is two
> values, not a curve.

---

## 3.1 The `fuel` field is a consumption rate

> **This section previously concluded the opposite**, on the strength of
> `r = 0.34` against RPM. That correlation was computed over the **419 records
> that survived the August corruption** — 0.50% of the shift, scattered. Clean
> data from June overturns it. The original reasoning is kept below, because
> the failure mode is worth remembering: a statistic drawn from 0.5% of a
> signal told us the opposite of the truth, confidently.

Measured on the June log — **61,080 records, zero invalid**:

| | |
|---|---|
| Correlation with RPM | **r = +0.592** |
| Correlation with torque × rpm (mechanical power) | **r = +0.997** |
| Median across the shift | 7.75 |
| Full range | 0.00 to 67.25 |

Fuel flow is very nearly proportional to power output, and r = 0.997 against
torque × rpm is that relationship, not a coincidence. The field is the **ECU's
own consumption estimate**.

Two supporting observations:

1. **It does not drain.** Across the June shift it runs 2.8 → 18.9 → 9.8 →
   21.4 → 16.9 → 11.3 → 9.7 → 9.1 → 2.1 → 2.0 by decile, rising and falling
   with load. A tank level cannot do that without refuelling four times.
2. **The 9-column log's header mislabels it.** The declared `fields` list names
   five columns for nine and calls column 1 "flowrate" when that column carries
   engine speed. Those five names in fact describe the *other* file the device
   writes — the 5-column fuel log. Do not zip the header onto the engine log.

**Units remain unconfirmed** (O2 stands). If the field is L/h, the June shift
burned 31 L over 2.34 h. Nothing in the data fixes the scale; only a
measurement against a known volume will.

### 3.2 The fuel log is a separate file

The device writes **two files per shift**, sharing a timestamp column:

| Columns | Carries |
|---|---|
| 9 | timestamp, rpm, torque, ECU fuel estimate, coolant, lat, lon, 2 flags |
| 5 | timestamp, flowrate (mm/h), **level (mm)**, lat, lon |

The 5-column file is the fuel telemetry: an in-line flow meter reading burn,
and a tank sender reading depth. **Both are in millimetres, not litres** —
converting needs tank *geometry*, not the capacity figures in the register, and
the same 38.78 mm drop is 24 L or 40 L depending on how deep the tank is.

Its header is **invalid JSON** — a trailing comma before the closing brace.
`decode_log.py` tolerates it and warns; whether the firmware or the sample
author produced it is unconfirmed.

---

## 4. The business case: ~60% idle

Of the valid records with the engine running:

| | |
|---|---|
| Idling (0 < rpm < 900) | **250** |
| Working (rpm ≥ 900) | **169** |
| **Idle share** | **59.7%** |

Torque corroborates it: median **7** while idling versus **19** while working.

At a typical excavator idle burn of ~10 L/h, ~60% of a 14-hour shift is on the
order of **85 wasted litres per machine per day**. Across 45 excavators that is
the largest number in this programme — and it needs **only RPM and ignition**,
both of which already work. No fuel sensor required.

### 4.1 RPM is cleanly bimodal — which is why idle detection is reliable

| RPM bin | Samples |
|---|---|
| 500–599 | 99 |
| 600–699 | **129** |
| 700–799 | 15 |
| 800–899 | 7 |
| 900–999 | 8 |
| 1000–1099 | 65 |
| 1100–1199 | **82** |
| 1200–1299 | 13 |
| 1300–1399 | 1 |

Two clear peaks with a near-empty valley between 700 and 999. **The 900 rpm idle
threshold sits in that valley — it is measured, not assumed.**

### 4.2 Sampling bias — read shapes, not totals

Valid records cluster between **15:16 and 18:15**, with single stragglers at
07:16 and 21:16. That reflects *where data survived the corruption*, not what the
machine did. Behavioural **shapes** (the bimodal split, the idle/work contrast)
are robust to this. **Absolute counts and any time-of-day profile are not.**

---

## 5. Movement

From the 375 fixes carrying a GPS lock (89.5% of valid records).

| | |
|---|---|
| Path actually observed (fixes < 60 s apart) | **3.19 km** |
| Path inferred (straight lines across data gaps) | **19.50 km** |
| Segments total / observed | 374 / 349 |

Only 3.19 km was genuinely watched. The rest is a straight line between two fixes
with nothing valid in between — a consequence of the corruption, and drawn dashed
in the UI so it is never mistaken for a real route.

### 5.1 Two stops, and they are the story

| Stop | Window | Duration | Idling | Samples |
|---|---|---|---|---|
| 1 | 15:24–15:40 | 15.9 min | **100%** | 32 |
| 2 | 15:43–17:14 | **91.1 min** | **72%** | 223 |

**81 of 107 stopped minutes had the engine running and the machine going
nowhere** — roughly 13 litres, one machine, one afternoon.

### 5.2 The machine was transported, not driven

**10 segments totalling 14.53 km exceed 6 km/h, peaking at 200.1 km/h.** A
tracked excavator cannot move that fast under its own power, so it was on a
low-bed trailer. These must **never** count toward operating hours — a naive
system would bill transport as machine activity.

### 5.3 Raw GPS invents ~100 km/day

Measured: **258 fixes inside a 193.5 m bounding box produced 0.52 km of apparent
movement** — about **2 m of phantom distance per sample**. At a 1 Hz transmit
rate over a 14-hour shift that is roughly **100 km/day invented for a machine
that never moved.**

Mitigation implemented: a **12 m movement gate plus RPM corroboration**. On the
reference shift this yields 19.94 km against 22.70 km from naive integration.

---

## 6. Security and integrity defects

Recorded for completeness; each is separate from the corruption fix.

1. **Single hardcoded key across the fleet** — `<redacted>` in plaintext in
   `decrypt_log.py` and presumably in every device. Compromise one, compromise
   all 1,075.
2. **AES-128-ECB** — identical plaintext blocks yield identical ciphertext. The
   49.99% block reuse in §1.2 *is* that leak.
3. **No message authentication.** Records can be forged or altered undetectably —
   material for a system whose purpose is detecting fuel theft.
4. **Unsigned OTA.** `latest.json` declares `"size": 128000`; `FIRMWARE.bin` is
   **131072 bytes**. There is no hash and no signature, so anyone able to serve
   that JSON can push arbitrary firmware to the entire fleet. The size mismatch
   alone may brick or reject updates.
5. **No device identifier anywhere in the log** — not in the header, not in any
   record. Identity depends entirely on the filename.

---

## 7. Defects in the supplied decoder (`decrypt_log.py`)

1. **Fabricates ~36,700 records.** Its `pos += 1` resynchronisation walks past the
   terminator into the 1.33 MB trailing region and "decodes" noise. It reports
   121,069 records where only 84,328 are framed and 419 are real.
2. **Longitude parsed unsigned** (`'<IHHhHhiIB'`). Masked in Oman; silently
   corrupt anywhere west of Greenwich.
3. **No validation**, so the 99.5% corruption is invisible in its output.

All three are fixed in `decode_log.py`.

---

## 8. Fleet composition

From `blueice_fuel_tanks_unverified.csv` (the BlueICE asset register).

| Class | Units | Total tank capacity |
|---|---|---|
| Excavators | 45 | 27,755 L |
| Bulldozers | 13 | 9,610 L |
| Dump Trucks | 9 | 4,880 L |
| Motorgraders | 5 | 2,004 L |
| Mobile Cranes | 3 | 1,000 L |
| **Total** | **75** | **45,249 L** |

26 equipment types; brands CAT (11), SANY (7), Komatsu (3), Volvo (2),
Hyundai (2), Zoomlion (1). Plus ~1,000 road vehicles → **~1,075 assets**.

**Caveat:** the file is titled *unverified* and **18 of 26 capacities are marked
LOW confidence**, several with notes such as "submitted value is 46 L higher than
the checked OEM value". Fuel-theft alerting cannot be built on guessed
capacities — a 46 L error is a stolen jerrycan you would never see. These must be
verified against each machine's operator manual before any fuel feature ships.

---

## 9. Where the fleet operates

Valid fixes span **23.428–24.451 N, 56.552–58.099 E** — northern Oman, around
Ibri in Ad Dhahirah. This is why the target AWS region is **me-central-1 (UAE)**,
not a European one.
