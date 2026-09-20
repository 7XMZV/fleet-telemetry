# BlueICE Telemetry Log — Binary Format Specification

**Status:** Reverse-engineered and verified against production sample
`862636058411560_2026_09_01_05_43_32_LOG003.TXT` (4,195,960 bytes, device IMEI
862636058411560, shift of 2026-08-31).

**Why this document exists:** as of this writing, the only description of this
format was an inline comment in `decrypt_log.py`, and that comment contains an
error (see §6.2). This file is the authoritative reference. Keep it in the
repository next to the decoder.

---

## 1. File layout

A log file is a flat sequence of AES-encrypted, length-prefixed records.

```
offset  size  content
------  ----  -----------------------------------------------
0       4     magic, ASCII "AES2"
4       2     uint16 LE  header payload length  (observed: 48)
6       48    encrypted file header  (see §5)
54      ...   data records, repeated (see §2)
...     2     uint16 LE  0x0000  = end-of-records terminator
...     ...   trailing bytes after terminator — NOT records (see §6.1)
```

Total file size therefore satisfies:

```
size_of_valid_region = 4 + (2 + 48) + N * (2 + 32) + 2
```

In the reference sample: `4 + 50 + 84328 * 34 = 2,867,206`, followed by
**1,328,754 bytes of trailing data** that must not be parsed.

---

## 2. Data record framing

Each data record is:

```
2 bytes   uint16 LE   payload length  (always 32 in all observed data)
32 bytes              AES-128-ECB ciphertext
```

The 32-byte plaintext is a **23-byte record** followed by **9 bytes of fill**.

> **The fill is not PKCS#7.** The trailing byte is `0xCD`, not `0x09`. Do not
> attempt to strip padding by reading the last byte as a length — see §6.3.

**Parser rule:** read `plen`; if `plen != 32`, stop. Do not attempt resynchronisation
by advancing one byte at a time (§6.1).

---

## 3. Record layout (23 bytes, little-endian, packed)

| Offset | Type   | Field      | Units / scaling        | Observed valid range |
|-------:|--------|------------|------------------------|----------------------|
| 0      | uint32 | `ts`       | Unix epoch seconds, UTC | 2026-08-31 shift     |
| 4      | uint16 | `ms`       | milliseconds           | **{0,100,…,900} only** |
| 6      | uint16 | `rpm`      | rev/min                | 544 – 1300           |
| 8      | int16  | `torque`   | % or Nm — *unconfirmed* | 0 – 73              |
| 10     | uint16 | `fuel100`  | value / 100 — **semantics unknown** | 0 – 387.87 |
| 12     | int16  | `coolant`  | °C — **see §3.2**      | only `53` and `88`   |
| 14     | int32  | `lat`      | degrees × 1e6          | 23.428 – 24.451 N    |
| 18     | int32  | `lon`      | degrees × 1e6          | 56.552 – 58.099 E    |
| 22     | uint8  | `flags`    | bitfield, see §4       | 0 or 1               |

**Python struct format:** `'<IHHhHhiiB'` (23 bytes)

### 3.1 Time resolution

`ms` takes **exactly ten distinct values** — 0, 100, 200 … 900. Every timestamp
lands on a 100 ms boundary. **The device has 10 Hz resolution and cannot express
an interval finer than 100 ms.** Any specification claiming 10 ms sampling is
incorrect for this hardware.

Observed average write rate in the reference sample: **1.65 records/second**
over a 51,238 s (14.2 h) shift.

### 3.2 Unconfirmed fields

- **`fuel100`** — median 3.15, max 387.87. A 620 L tank sitting at 3.15 is not a
  level reading. This is most likely an instantaneous **consumption rate (L/h)**
  or an **uncalibrated ADC value**. *This must be confirmed with the firmware
  author before any fuel feature is built.* Theft detection requires tank **level**.
- **`torque`** — plausible magnitudes but units unverified.
- **`coolant`** — **probably a dead signal.** Across 419 valid records it takes
  only **two values: `53` (417×) and `88` (2×)**. Real coolant temperature varies
  continuously with warm-up and load. In the same records RPM takes 191 distinct
  values, fuel 156, torque 53 — the other sensors are live, this one is not.
  Likely a stuck read, an unconnected sender, or a hardcoded default. Do not
  build overheating alerts on this field until it is confirmed working.

---

## 4. Flags bitfield

| Bit | Mask   | Name         | Meaning                                    |
|----:|--------|--------------|--------------------------------------------|
| 0   | `0x01` | `gps_fix`    | 1 = position fields valid                  |
| 1   | `0x02` | `is_default` | 1 = record contains firmware default values |
| 2–7 | —      | reserved     | **must be 0** — non-zero indicates corruption |

In the reference sample, valid records show only `flags ∈ {0, 1}`. Corrupt
records show 98+ distinct flag bytes, which makes this field an effective
corruption detector.

---

## 5. File header

48 encrypted bytes at offset 6. Decrypts to three unstructured 16-byte blocks
with no printable content.

**The IMEI is not present** — not as ASCII, not as a packed integer, nowhere in
the file. Verified by exhaustive search of the whole 4 MB sample.

**Consequence: device identity exists only in the filename.**

```
862636058411560_2026_09_01_05_43_32_LOG003.TXT
<--- IMEI ---> <---- upload timestamp ----><-seq->
```

Because a renamed or mis-copied file silently attributes telemetry to the wrong
machine — permanently corrupting that asset's lifetime totals — **the ingest
pipeline must verify the filename IMEI against the credentials of the uploading
device.** This is a server-side control and requires no firmware change.

---

## 6. Known defects

### 6.1 `decrypt_log.py` fabricates records (decoder bug)

The shipped decoder recovers from an invalid length by advancing one byte:

```python
if plen == 0 or plen % 16 != 0 or plen > 48 or pos + 2 + plen > len(data):
    pos += 1          # <-- walks past the terminator into trailing data
    skipped += 1
    continue
```

At the `0x0000` terminator it steps into the 1.33 MB trailing region and
"decodes" noise. It reports **121,069 records** where only **84,328** are framed
and **419** are physically valid. **~36,700 reported records do not exist.**

**Fix:** stop at the first non-32 length. Never resynchronise.

### 6.2 Longitude parsed as unsigned (decoder bug)

The format string `'<IHHhHhiIB'` declares latitude signed (`i`) and **longitude
unsigned (`I`)**. Oman is at positive longitude so this is currently masked, but
any longitude west of Greenwich decodes as ≈ +4295° instead of a negative value.

**Fix:** `'<IHHhHhiiB'` — both signed.

### 6.3 Fill bytes are not PKCS#7

```python
pad = plain[-1]
if 1 <= pad <= 16:
    plain = plain[:-pad]
```

The actual trailing byte is `0xCD` (205), so the condition is false and the
branch never fires. It is harmless today, but it is not a padding scheme and
should not be relied on. Read the first 23 bytes and ignore the remaining 9.

### 6.4 OTA manifest size mismatch

`latest.json` declares `"size": 128000`; `FIRMWARE.bin` is **131072 bytes**.
The manifest also carries **no hash and no signature**, so anyone able to serve
that JSON can push arbitrary firmware to every device in the fleet.

### 6.5 Cryptography

- **AES-128-ECB** — identical plaintext blocks produce identical ciphertext.
  Measured **49.99% block reuse** in the reference sample; the ciphertext
  visibly leaks structure.
- **Single hardcoded key** `<redacted>` shared across the entire fleet.
  Compromising one device compromises all 1,075.
- **No message authentication.** Records can be forged or altered undetectably —
  a material weakness for a system intended to detect fuel theft.

**Recommended:** per-device keys, AES-GCM (or CBC with a random IV), and a
signature check on OTA images.

### 6.6 Record corruption — **the blocking defect**

**99.50% of records in the reference sample are invalid.** See `BUG_REPORT.md`.

---

## 7. Validation criteria

A record is considered valid when **all** of the following hold. These are the
checks implemented by `decode_log.py --validate`.

| Field | Constraint |
|-------|------------|
| `ts` | within the expected operating window (not 1970, not 2106) |
| `ms` | `< 1000` and a multiple of 100 |
| `rpm` | `0 … 3000` (diesel plant) |
| `torque` | `-2000 … 5000` |
| `coolant` | `-40 … 150` °C |
| `lat` | `-90 … 90` |
| `lon` | `-180 … 180` |
| `flags` | `< 4` (bits 2–7 clear) |

**Acceptance gate for firmware release: ≥ 99% of framed records must pass.**

---

## 8. Worked example

Record 6 of the reference sample — a known-good record.

```
ciphertext (32 B) -> plaintext block 1: 2999956a f401 5802 0700 d100 3500 1ef3
```

| Bytes      | Field     | Raw        | Decoded                    |
|------------|-----------|------------|----------------------------|
| `2999956a` | `ts`      | 1788188969 | 2026-08-31 15:09:29 UTC    |
| `f401`     | `ms`      | 500        | .500 s                     |
| `5802`     | `rpm`     | 600        | 600 rpm — diesel idle      |
| `0700`     | `torque`  | 7          | 7                          |
| `d100`     | `fuel100` | 209        | 2.09                       |
| `3500`     | `coolant` | 53         | 53 °C                      |

---

## 9. Derived metrics — implementation notes

**Engine hours / idle time** — derive from `rpm` and ignition **only, never from
GPS**. This is the reliable signal in this system.

- engine off: `rpm == 0`
- idle: `0 < rpm < 900`
- working: `rpm >= 900`

In the reference sample's valid records: **250 idle vs 169 working — ~60% idle
across a 14 h shift.** At roughly 10 L/h for an excavator that is on the order of
85 wasted litres per machine per day.

**Distance — do not integrate raw GPS.** Measured in the reference sample: 258
fixes inside a **193.5 m** bounding box produced **0.52 km** of apparent
movement — about **2 m of phantom distance per sample**. At 1 Hz over a 14 h
shift that is roughly **100 km/day invented for a stationary machine.**

Apply both a **movement gate** (discard steps below ~10–15 m; require several
consecutive consistent fixes) and a **speed gate** (only accumulate distance when
RPM corroborates motion). Map-match road vehicles where possible. For stationary
plant, prefer showing no distance at all over a filtered-but-still-fictional number.

**Lifetime totals** — maintain as incrementally-updated counters anchored to the
cab hour-meter reading taken at install. Never compute a lifetime total by
scanning raw history.

---

## 10. Ingest requirements

- **Idempotency is mandatory.** Deduplicate on file content hash *and* on
  `(device_id, ts, ms)`. Uploads retry, and log rotations overlap. Because
  lifetime totals are cumulative and never recomputed, a single double-counted
  file is wrong forever.
- **Upload cadence:** every 5 minutes over cellular. Map freshness equals this
  cadence — mark an asset stale after 3 missed uploads (15 min) and offline after
  60 min. Render offline assets greyed, never hidden.
