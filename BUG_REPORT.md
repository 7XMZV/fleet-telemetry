# Bug Report — Telemetry logger intermittently discards up to 99.9% of records

**Severity:** Blocking. This prevents fleet rollout.
**Component:** Device firmware, log-write path.
**Affected:** `FIRMWARE.bin` v1.4 (per `latest.json`), device 862636058411560.
**Sample:** `862636058411560_2026_09_01_05_43_32_LOG003.TXT` — one full 14.2 h shift.
**Corpus:** 27 logs, 2026-01-30 to 2026-08-31, 2.06 M records (see section
"What 27 logs show" — the single sample is one point on a curve, not the norm).

---

## Summary

The device writes ~1.65 records per second as designed, and the sensor data
itself is good. But **only 419 of 84,328 records in a full shift decode to
physically valid values — 0.50%.** The remaining 99.50% contain random bytes.

This is not a decoder problem. The framing is provably intact: every record in
the file carries a correct 32-byte length prefix, and the file size matches the
record count exactly (`4 + 50 + 84328 × 34 = 2,867,206`). The AES key and the
23-byte record layout are both correct — when a record survives, it decodes
perfectly.

---

## What 27 logs show that one log could not

This report was written from a single shift. Syncing the whole bucket since
then produced 27 logs from the same device, and the picture is materially
different from "the logger discards 99.5% of records".

| Day | Framed | Valid | Valid % |
|---|---:|---:|---:|
| 2026-01-30 | 121,473 | 48,880 | 40.24% |
| 2026-06-01 | 32,318 | 32,245 | **99.77%** |
| 2026-06-14 | 29,791 | 17,918 | 60.15% |
| 2026-06-16 | 32,832 | 32,832 | **100.00%** |
| 2026-06-21 | 41,726 | 25,551 | 61.24% |
| 2026-06-24 | 61,080 | 61,080 | **100.00%** |
| 2026-06-29 | 45,410 | 1 | 0.00% |
| 2026-07-01 | 123,382 | 56,099 | 45.47% |
| 2026-07-07 | 123,369 | 73,885 | 59.89% |
| 2026-07-09 | 123,444 | 16,375 | 13.27% |
| 2026-07-13 | 123,456 | 1,088 | 0.88% |
| 2026-07-15 | 123,435 | 252 | 0.20% |
| 2026-07-16 | 123,435 | 130 | 0.11% |
| 2026-08-31 | 84,328 | 419 | 0.50% |

(Abridged — 27 logs in total, worst per day shown where a day has several.)

**Three things follow, and each one changes what to look for.**

### 1. The device has written perfect logs -- but only in the other format

`2026-06-16` and `2026-06-24` are **100.00% valid**. Every record decodes to
physically plausible values.

**An earlier draft of this report used that to rule out the AES key, the record
layout, the framing and the decoder. That reasoning does not hold, and is
withdrawn.** Every 100% log is JSON-lines -- plaintext, self-describing, decoded
without the key, the struct or the framing. It exercises a different code path
and says nothing about the binary decoder.

The two formats also do not overlap in time: the last JSON log is 2026-06-27,
the first binary log 2026-06-29. "Binary" and "after 28 June" are therefore
perfectly confounded, and no binary log has ever decoded above 59.89%.

What the corpus does support is in `aws/DECODER-VALIDATION.md`: the framing is
checked every frame and cannot silently drift, and the surviving binary records
reproduce the device's documented 10 Hz cadence with spatially continuous
positions. That is consistent with correct decoding of correctly-formed frames.
It is not proof that the rejected records were corrupt when written.

**Settling it needs one shift captured in both formats simultaneously**, or a
binary log from before 2026-06-28. Either breaks the confound.

### 2. There is a step change around 2026-07-09, and it never recovers

Before that date validity oscillates between 40% and 100% — bad, but variable.
From 2026-07-09 it falls to 13.27%, then 0.88%, 0.87%, 0.20%, 0.11%, and never
again exceeds 1%. The two August logs are 0.50% and 0.37%.

Something changed on or around 2026-07-09 and stuck. A firmware update, a
configuration change, a hardware fault, or a counter that finally wrapped — the
device's own change log for that week is the first place to look.

### 3. Log length does not explain it

The obvious theory — longer logs overrun a buffer — does not survive the data:

| Framed | Valid % | Day |
|---:|---:|---|
| 123,369 | 59.89% | 2026-07-07 |
| 123,435 | 0.20% | 2026-07-15 |

Two logs of effectively identical length, 300× apart in validity. Whatever the
mechanism is, it is not a function of how many records the file holds.

### What this means for the fix

The original diagnosis below — a buffer flushed with uninitialised slots —
still fits the *shape* of the corruption within a bad file, and should still be
read. But it was inferred from one log and framed as a constant defect. It is
not constant. Any fix should be validated against a spread of logs from across
this date range, not against one shift, or a change that merely moves the
device back into its "60%" state will look like a success.

---

## Impact

At the current rate the device delivers roughly **one usable sample every two
minutes** instead of the intended ~1.65 per second.

- Live map position is up to 2 minutes stale even with a good cellular link
- Engine-hour and idle-time totals are computed from 0.5% of the data
- Any future fuel-theft detection would miss ~199 of every 200 readings
- Installing this firmware on 1,075 assets means paying for 1,075 installations
  twice

---

## Evidence

Reproduce with the decoder in this repository:

```bash
python decode_log.py 862636058411560_2026_09_01_05_43_32_LOG003.TXT --gate 99
```

Output:

```
Framed records  : 84,328
Valid records   : 419  (0.50%)
Invalid records : 83,909  (99.50%)

Rejection reasons (first failing check per record):
  timestamp      70,268
  ms              8,723
  lat             4,792
  lon                62
  coolant            35
  flags              15
  rpm                14
```

A record is rejected only on physically impossible values — timestamps outside
2020–2035, `ms` not a multiple of 100, RPM above 3000, coolant outside −40…150 °C,
latitude outside ±90. Full criteria in `FORMAT_SPEC.md` §7.

### What a good record looks like

```
ts=1788188969 (2026-08-31 15:09:29 UTC)  ms=500  rpm=600
torque=7  fuel=2.09  coolant=53 C
```

Realistic diesel idle. The 419 valid records form a completely coherent shift:
RPM 544–1300 with a clean bimodal idle/working split, GPS clustered
in a 193 m box in northern Oman, flags only ever 0 or 1. **The hardware and the
sensors are working correctly.**

---

## Diagnosis — likely a buffer flushed with uninitialised slots

The decisive clue is in the ciphertext structure. Each record encrypts to two
AES-ECB blocks. Across the file:

| | |
|---|---|
| Total 16-byte cipher blocks | 168,656 |
| Unique cipher blocks | 84,338 |
| **Duplicate ratio** | **49.99%** |

Block 1 is essentially unique per record. **Block 2 is constant across thousands
of consecutive records** — only four distinct values across the first 20,000
records (occurring 6,797 / 4,935 / 3,713 / 349 times).

Block 2 holds the high bytes of latitude, the longitude, and the flags. Those
staying byte-identical for thousands of records while block 1 — timestamp, RPM,
torque, fuel, coolant — is random is not consistent with corrupted sensor reads.
It is consistent with **a record buffer being encrypted and flushed to storage
while most of its slots were never populated**: the position fields retain a
stale value written once, and the rest of each slot is whatever was in RAM.

Supporting detail: the 9 fill bytes after each 23-byte record are `0xCD`, and
the encrypted file header also ends in `0xCD` and contains no readable content.
A repeated `0xCD` fill is a common uninitialised-memory marker.

### The corruption is irregular, not periodic

This narrows it further. Measuring the gaps between surviving records:

| | |
|---|---|
| Median gap | **10 slots** |
| Mean gap | **177.6 slots** |
| Largest gap | **15,622 slots** |
| Runs that are a single isolated record | **359 of 386** |

No gap is a multiple of any common buffer size (16 / 32 / 64 / 100 / 128 / 200 /
256 all tested). **A fixed-size buffer flushing once per cycle would produce
evenly spaced survivors — these are heavy-tailed and irregular**, which points
more towards a **race between the sampling task and the write task** than a
straightforward buffer-flush bug. Short clusters of survivors separated by long
droughts is what you would expect if records occasionally win that race.

**Suggested areas to check:**

1. **Any concurrency between the sensor-sampling task and the flush task** —
   the irregular gap pattern above makes this the most likely candidate. Is the
   record being written while it is still being populated?
2. The record-buffer flush path — is the write triggered on a timer regardless
   of how many slots were filled?
3. Buffer initialisation — is the buffer zeroed or fully populated before encryption?
4. The index/count tracking valid entries — is a partially-filled buffer written
   in full?

---

## Acceptance criteria

**≥ 99% of framed records must pass validation across a full working shift.**

```bash
python decode_log.py <new_log_file> --gate 99
```

Exit code 0 = pass, 1 = fail.

**One passing log is not evidence of a fix.** The device already produces
100.00% logs intermittently — `2026-06-16` and `2026-06-24` both did, with no
fix applied — so a single clean capture may only mean the device happened to be
in its good state that day.

Please capture **at least five full shifts across several days**, including one
of 120,000+ records, and gate every one of them. The defect's own history is
the acceptance bar: it has managed 100%, 60%, 45%, 13% and 0.11% on the same
firmware, so a fix has to hold across that spread, not land on one point of it.

---

## Appendix — separate issues, NOT part of this request

Recorded so they are not lost. These are deliberately **not** bundled into this
fix; each should be scheduled on its own.

1. **OTA manifest mismatch.** `latest.json` declares `"size": 128000`;
   `FIRMWARE.bin` is 131072 bytes. The manifest carries no hash and no
   signature, so anyone able to serve it can push arbitrary firmware to the fleet.
2. **No device identifier in the log.** The IMEI appears nowhere in the file —
   not in the header, not in any record. Identity depends entirely on the
   filename. Mitigated server-side for now by checking the filename IMEI against
   the uploading device's credentials.
3. **Cryptography.** AES-128-**ECB** with a single fleet-wide hardcoded key, and
   no message authentication. ECB leaks structure (the 49.99% block reuse above
   is that leak). Recommend per-device keys and AES-GCM.
4. **`fuel100` semantics unconfirmed.** Median 3.15, max 387.87 — not consistent
   with litres in a 620 L tank. Please confirm which physical sensor feeds this
   field and its scaling. Theft detection requires tank **level**.
5. **`coolant` appears to be a dead signal.** Across the 419 valid records it
   takes **two distinct values: `53` (417 times) and `88` (twice)**. A real
   coolant temperature varies continuously through warm-up and load. For
   comparison, in the same records RPM takes 191 distinct values, fuel 156, and
   torque 53 — so the other sensors are live and this one is not. Likely a stuck
   read, an unconnected sender, or a hardcoded default. Worth checking while the
   log-write path is open, but **separate from the corruption fix**.

---

*Analysis and decoder: see `FORMAT_SPEC.md` and `decode_log.py` in this repository.*
