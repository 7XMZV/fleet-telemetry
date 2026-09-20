# Review item 8 — the 0.5% valid-record rate

**Short answer:** the 0.5% is real, it is one point on a curve that runs from
100% to 0.11%, and the decoder is *probably* correct — but a claim I made
earlier, and which appears in several documents, does not survive checking. It
is corrected below.

---

## 1. What the corpus actually shows

27 logs from device `862636058411560`, 2026-01-30 to 2026-08-31, 2,061,767
records. Decoded with the same code and the same key.

| Format | n | Dates | Validity range |
|---|---:|---|---|
| JSON-lines (plaintext) | 16 | 2026-06-14 → 06-27 | 59.82% – **100.00%** |
| Binary (AES2) | 13 | 2026-06-29 → 08-31 | **0.00%** – 59.89% |

The 0.50% figure in the original bug report is the 2026-09-01 binary log. It is
not representative: the same device produced 100.00% files in June.

---

## 2. A correction to my earlier claim

I have been stating, in `BUG_REPORT.md`, `FINDINGS.md`, the README and a
presentation, that:

> "The same firmware produces entire flawless logs. That single fact rules out
> the AES key, the record layout, the framing and the decoder as causes."

**That reasoning is wrong, and I should have checked it before publishing it.**

Every 100% log is **JSON-lines**. JSON logs are plaintext and self-describing:
decoding them uses no AES key, no 23-byte struct, and no 34-byte framing. They
exercise a completely different code path. They therefore say nothing about
whether the binary decoder is correct.

Worse, the two formats **do not overlap in time**:

```
last JSON log    2026-06-27
first binary log 2026-06-29
```

So "binary format" and "after 28 June" are perfectly confounded. On this corpus
alone it is impossible to separate:

- **H1** the device began writing corrupt binary frames, or
- **H2** the binary decoder has a systematic defect

No binary log has ever decoded above 59.89%. That is exactly what H2 would look
like, and I cannot exclude it with the evidence I was citing.

---

## 3. What can still be shown

Three independent checks, none of which the validator itself performs, so they
cannot be circular.

### 3.1 The framing cannot silently drift

Each frame is a 2-byte little-endian length followed by that many bytes:

```
000036  20 00  07 0C A5 A5 3B 33     0x0020 = 32
000058  20 00  D9 B9 AA 10 8C BD     34 bytes later
00007A  20 00  6B 0E D0 96 62 84     and again
```

The decoder checks the length on **every** frame and stops at the first one
that is not 32. It does not byte-walk to resynchronise — that was a defect in
the original vendor script and is explicitly fixed here.

This matters for the diagnosis: a decoder that loses alignment produces a run
of garbage *after* the point of loss. It cannot produce the observed pattern of
good and bad records interleaved throughout a file while remaining aligned to
the end. Corruption that is per-frame implies the plaintext written into those
frames was already wrong.

### 3.2 The surviving records reproduce the device's declared behaviour

The JSON header states `"log_interval_ms": 100`. Inter-record spacing is not
part of the validity test, so it is an independent measurement:

| | records | monotonic | median Δt | median GPS jump | p95 jump |
|---|---:|---:|---:|---:|---:|
| JSON, 100% (ground truth) | 61,080 | 100.0% | **0.10 s** | 0.0 m | 0 m |
| Binary, 59.89% | 73,885 | 100.0% | **0.10 s** | 0.0 m | 0 m |
| Binary, 0.50% | 419 | 100.0% | 1.00 s | 0.0 m | 7 m |

The binary decoder reproduces the 10 Hz cadence the device documents, to the
same precision as the plaintext format, and the positions are spatially
continuous. A misread record layout would not produce timestamps that are
monotonic, correctly spaced *and* paired with coherent positions.

(The 0.50% file shows a 1.00 s median gap because only 419 of 84,328 records
survive — the spacing between survivors, not the logging rate.)

### 3.3 Field ranges agree with the plaintext format

| field | JSON (truth) | binary 0.50% | binary 59.89% |
|---|---|---|---|
| rpm | 548 – 1817 | 544 – 1300 | 1 – 3000 |
| torque | 0 – 79 | 0 – 73 | 0 – 100 |
| coolant | 39 – 39 | 53 – 88 | −40 – 120 |

Comparable and physically sensible. Note the 59.89% file reaches the validator
bounds exactly (rpm 3000, coolant −40/120), which is what you would expect if
some records that *pass* the range test are nonetheless corrupt — the filter is
a plausibility test, not a proof.

---

## 4. What I am claiming, and what I am not

**Claimed:** the binary decoder reads correctly-formed frames correctly. The
framing is verified per frame, the surviving records reproduce the documented
10 Hz cadence, and their fields agree with the plaintext format.

**Not claimed:** that every rejected record is genuinely corrupt. The evidence
is consistent with that, but it does not exclude a systematic decoder defect
affecting a subset of records, because there is no binary log from the period
when the device was known to be healthy.

---

## 5. What would settle it

Any one of these, in order of how easy it should be:

1. **A simultaneous capture.** One shift written in both formats. Decode both,
   compare record for record. This is definitive and is the one I would ask for.
2. **A known-pattern log.** Firmware writes a counter or a fixed sequence into
   a binary log. Any layout error becomes obvious immediately.
3. **A binary log from before 2026-06-28**, if one exists anywhere. That breaks
   the confound on its own.

Until one of those exists, the correct statement in every document is that the
binary decoder is *consistent with* the device's documented behaviour, not that
it is *proven* correct.

---

## 6. Documents corrected

The unsupported claim has been removed from:

- `BUG_REPORT.md`
- `FINDINGS.md`

It also appears in the public README and in a presentation. Those are outside
this deployment change; they need the same correction and I have flagged it.
