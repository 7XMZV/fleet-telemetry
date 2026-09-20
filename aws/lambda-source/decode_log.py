#!/usr/bin/env python3
"""
BlueICE telemetry log decoder.

Replaces decrypt_log.py. Three defects are fixed here:

  1. No byte-walking resynchronisation. The old decoder recovered from a bad
     length prefix with `pos += 1`, which walked past the end-of-records
     terminator into trailing data and fabricated ~36,700 records that do not
     exist. This decoder stops at the first frame that is not 32 bytes.

  2. Longitude is parsed signed. The old format string was '<IHHhHhiIB' --
     latitude signed, longitude UNSIGNED. Masked in Oman (positive longitude),
     silently corrupt anywhere west of Greenwich.

  3. Records are validated against physical ranges, and the validity rate is
     reported. The device currently emits ~0.5% valid records; without this
     check that corruption is invisible.

See FORMAT_SPEC.md for the full format description.

Usage:
    python decode_log.py LOGFILE.TXT                  # decode -> CSV + report
    python decode_log.py LOGFILE.TXT --stats-only     # report only, no CSV
    python decode_log.py LOGFILE.TXT --gate 99        # exit 1 if <99% valid
    python decode_log.py LOGFILE.TXT --keep-invalid   # write invalid rows too
"""

import argparse
import collections
import csv
import datetime as dt
import json
import os
import re
import struct
import sys

try:
    from Crypto.Cipher import AES
except ImportError:
    sys.exit("Missing dependency. Install with:  pip install pycryptodome")

# ---------------------------------------------------------------------------
# The decryption key
# ---------------------------------------------------------------------------
# NEVER hardcoded. It used to be a literal on this line, which put a
# fleet-wide key into every copy of this file -- including ones that travel.
#
# Resolution order, first hit wins:
#
#   1. BLUEICE_KEY_SECRET   name or ARN of an AWS Secrets Manager secret.
#                           Used in Lambda. Fetched once per container and
#                           held only in memory.
#   2. BLUEICE_LOG_KEY      the key itself, for local work. Accepts 16 raw
#                           characters, or "hex:" followed by 32 hex digits.
#
# Nothing falls back to a default. A missing key is an error with
# instructions, not a silent wrong answer.

_KEY_CACHE = None


class KeyUnavailable(RuntimeError):
    pass


def _key_from_secrets_manager(name):
    import boto3                                     # only needed on Lambda
    sm = boto3.client("secretsmanager")
    v = sm.get_secret_value(SecretId=name)
    raw = v.get("SecretString")
    if raw is None:                                  # binary secret
        import base64
        return base64.b64decode(v["SecretBinary"])
    raw = raw.strip()
    # A JSON secret is the usual shape: {"key": "..."} or {"BLUEICE_LOG_KEY": "..."}
    if raw.startswith("{"):
        import json
        d = json.loads(raw)
        for k in ("key", "BLUEICE_LOG_KEY", "log_key", "aes_key"):
            if k in d:
                raw = str(d[k]).strip()
                break
        else:
            raise KeyUnavailable(
                "secret %r is JSON but has none of key/BLUEICE_LOG_KEY/log_key/aes_key"
                % name)
    return raw


def _coerce(value):
    """16 raw bytes, from either spelling."""
    if isinstance(value, bytes):
        b = value
    elif value.lower().startswith("hex:"):
        b = bytes.fromhex(value[4:])
    else:
        b = value.encode("utf-8")
    if len(b) != 16:
        raise KeyUnavailable(
            "key must be 16 bytes for AES-128; got %d" % len(b))
    return b


def log_key():
    """The AES key, or a clear explanation of why there isn't one."""
    global _KEY_CACHE
    if _KEY_CACHE is not None:
        return _KEY_CACHE

    secret = os.environ.get("BLUEICE_KEY_SECRET", "").strip()
    if secret:
        _KEY_CACHE = _coerce(_key_from_secrets_manager(secret))
        return _KEY_CACHE

    env = os.environ.get("BLUEICE_LOG_KEY", "").strip()
    if env:
        _KEY_CACHE = _coerce(env)
        return _KEY_CACHE

    raise KeyUnavailable(
        "no decryption key available.\n"
        "  Set BLUEICE_KEY_SECRET to a Secrets Manager secret (Lambda), or\n"
        "  BLUEICE_LOG_KEY to the key itself for local work:\n"
        "      set BLUEICE_LOG_KEY=<16 characters>        (Windows)\n"
        "      export BLUEICE_LOG_KEY='<16 characters>'   (Linux/macOS)\n"
        "  The key is not stored in this repository.")


# ---------------------------------------------------------------------------
# Format constants -- see FORMAT_SPEC.md sections 1-3
# ---------------------------------------------------------------------------

MAGIC = b"AES2"
HEADER_PLEN = 48               # encrypted file header, contains no device ID
RECORD_PLEN = 32               # 23-byte record + 9 bytes of 0xCD fill
RECORD_FMT = "<IHHhHhiiB"      # both lat and lon signed -- fix (2)
RECORD_SIZE = struct.calcsize(RECORD_FMT)
assert RECORD_SIZE == 23, RECORD_SIZE

FLAG_GPS_FIX = 0x01
FLAG_IS_DEFAULT = 0x02
FLAG_RESERVED = 0xFC           # bits 2-7 must be clear

CSV_COLUMNS = [
    "device_id", "timestamp_utc", "ms", "rpm", "torque", "fuel",
    "coolant_c", "lat", "lon", "gps_fix", "is_default", "valid", "reject_reason",
]

Record = collections.namedtuple(
    "Record", "ts ms rpm torque fuel100 coolant lat6 lon6 flags"
)

# ---------------------------------------------------------------------------
# Validation -- FORMAT_SPEC.md section 7
# ---------------------------------------------------------------------------

TS_MIN = int(dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc).timestamp())
TS_MAX = int(dt.datetime(2035, 1, 1, tzinfo=dt.timezone.utc).timestamp())

RPM_MAX = 3000          # diesel construction plant
TORQUE_MIN, TORQUE_MAX = -2000, 5000
COOLANT_MIN, COOLANT_MAX = -40, 150


def reject_reason(r):
    """Return None if the record is physically plausible, else why it is not."""
    if not TS_MIN < r.ts < TS_MAX:
        return "timestamp"
    if r.ms >= 1000 or r.ms % 100 != 0:      # device has 100 ms resolution
        return "ms"
    if r.rpm > RPM_MAX:
        return "rpm"
    if not TORQUE_MIN <= r.torque <= TORQUE_MAX:
        return "torque"
    if not COOLANT_MIN <= r.coolant <= COOLANT_MAX:
        return "coolant"
    if not -90_000_000 < r.lat6 < 90_000_000:
        return "lat"
    if not -180_000_000 < r.lon6 < 180_000_000:
        return "lon"
    if r.flags & FLAG_RESERVED:
        return "flags"
    return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def device_id_from_filename(path):
    """Identity lives only in the filename -- FORMAT_SPEC.md section 5.

    The ingest pipeline MUST additionally verify this against the credentials
    of the uploading device; the file itself contains no device identifier.
    """
    m = re.match(r"(\d{15})_", os.path.basename(path))
    return m.group(1) if m else "UNKNOWN"


def parse(path):
    """Decode a log file, in either format the device has used.

    Firmware up to 2026-08 wrote AES-encrypted binary frames (FORMAT_SPEC.md
    1-4). Firmware from 2026-06 writes plaintext JSON lines (section 11). The
    two overlap in time, so sniff rather than assume: real buckets contain both.

    Returns (records, meta) in both cases, with the same Record tuple, so
    everything downstream is unaffected by which format arrived.
    """
    with open(path, "rb") as fh:
        head = fh.read(4)
    if head.lstrip()[:1] == b"{":
        return parse_jsonl(path)
    return parse_binary(path)


# Column order in the JSON format, established from the data rather than from
# the header. The header's own `fields` list is wrong -- see parse_jsonl.
JSON_COLS = ["timestamp", "rpm", "torque", "fuel", "coolant",
             "lat", "lon", "gps_fix", "reserved"]

# The device emits TWO files per shift, sharing a timestamp column:
#   9 columns -> engine telemetry (this is what Record models)
#   5 columns -> fuel telemetry: timestamp, flowrate (mm/h), level (mm), lat, lon
FUEL_COLS = ["timestamp", "flowrate", "level", "lat", "lon"]


def _loads_lenient(text):
    """json.loads, tolerating a trailing comma before a closing brace.

    The fuel log's header ends `"fields":[...],\\n}` -- invalid JSON that every
    strict parser rejects outright. Whether that is the firmware or the person
    who wrote the sample is unconfirmed, but a decoder that dies on the only
    file it is given is no use, so accept it and say so.
    """
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        fixed = re.sub(r",(\s*[}\]])", r"\1", text)
        try:
            return json.loads(fixed), "header had a trailing comma (invalid JSON)"
        except json.JSONDecodeError:
            return None, None


def _read_jsonl(path):
    """Split a JSON-lines log into (header, rows, meta). Shared by both shapes."""
    meta = {"size": os.path.getsize(path), "warnings": [],
            "stopped_at": 0, "trailing_bytes": 0, "format": "jsonl"}
    # Decode tolerantly. Three logs in the bucket start with a valid JSON
    # header and then contain raw binary partway through -- a strict utf-8
    # read raises and the WHOLE file is thrown away, including the tens of
    # thousands of good records before the corruption. Replacing the bad bytes
    # lets those lines parse; the damaged ones simply fail json.loads and are
    # counted, which is the honest outcome.
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        text = raw.decode("utf-8", errors="replace")
        meta["warnings"].append(
            "file contains non-UTF-8 bytes (first at %d); damaged lines are "
            "skipped, the rest are read" % exc.start)
    lines = text.splitlines()

    buf, header, start = "", None, 0
    for i, line in enumerate(lines[:50]):
        buf += line
        header, note = _loads_lenient(buf)
        if header is not None:
            start = i + 1
            if note:
                meta["warnings"].append(note)
            break
    if header is None:
        meta["warnings"].append("no JSON header found in the first 50 lines")
        header = {}
    meta["header"] = header

    rows, bad = [], 0
    for line in lines[start:]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(row, list):
            rows.append(row)
        else:
            bad += 1
    if bad:
        meta["warnings"].append(f"{bad:,} line(s) could not be read")
    return header, rows, meta


def log_kind(path):
    """Which of the three shapes this file is, without fully parsing it."""
    with open(path, "rb") as fh:
        head = fh.read(4)
    if head == MAGIC:
        return "binary"
    if head.lstrip()[:1] != b"{":
        return "unknown"
    _, rows, _ = _read_jsonl(path)
    if not rows:
        return "unknown"
    width = len(rows[0])
    if width >= 8:
        return "engine-json"
    if width == 5:
        return "fuel-json"
    return "unknown"


def parse_fuel(path):
    """Decode the 5-column fuel log.

    Returns (samples, meta) where each sample is (epoch_seconds, flowrate_mm_h,
    level_mm). This is deliberately NOT a Record stream: it carries no engine
    data, and forcing it into one would mean inventing an rpm and a coolant
    temperature that were never measured.

    `flowrate` is the in-line meter in the fuel line -- it sees only what the
    engine burned. `level` is the tank sender -- it sees burn, refuels and
    anyone siphoning. Their disagreement is the entire point.
    """
    header, rows, meta = _read_jsonl(path)
    samples, bad = [], 0
    for row in rows:
        if len(row) < 3:
            bad += 1
            continue
        try:
            stamp = dt.datetime.fromisoformat(row[0]).replace(
                tzinfo=dt.timezone.utc)
            samples.append((stamp.timestamp(), float(row[1]), float(row[2])))
        except (ValueError, TypeError):
            bad += 1
    if bad:
        meta["warnings"].append(f"{bad:,} fuel row(s) unreadable")

    # Litres per mm has to come from the tank, not from this file. When the
    # header carries the geometry (generated data does) pass it through;
    # otherwise the caller must supply it and label it as an assumption.
    lpm = header.get("litres_per_mm")
    if lpm is None and header.get("tank_litres") and header.get("tank_depth_mm"):
        lpm = header["tank_litres"] / header["tank_depth_mm"]
    meta["litres_per_mm"] = lpm
    meta["synthetic"] = bool(header.get("synthetic"))
    return samples, meta


def parse_jsonl(path):
    """Decode the 9-column engine log into Record tuples.

    **The header's `fields` list is not used, and must not be.** In the engine
    log it names five fields for nine columns and calls column 1 "flowrate"
    when that column plainly carries engine speed: it sits at 599-601 while the
    vehicle is stationary at one coordinate, its minimum matches the binary
    format's RPM minimum, and a fuel flow rate does not idle at 600. Zipping
    the declared names onto the columns would relabel RPM as flowrate and
    torque as fuel, and every derived figure after that would be wrong. Column
    order is taken from the data; the mismatch is reported as a warning.

    (Those five names do describe the OTHER file the device writes -- the
    5-column fuel log -- which is read by parse_fuel.)
    """
    header, rows, meta = _read_jsonl(path)
    declared = header.get("fields")
    interval_ms = header.get("log_interval_ms")

    if rows and len(rows[0]) == 5:
        raise ValueError(
            os.path.basename(path) + " is a 5-column FUEL log, not engine "
            "telemetry. Use parse_fuel(); forcing it into a Record would mean "
            "inventing rpm and coolant values that were never measured.")

    records, bad = [], 0
    for row in rows:
        if len(row) < 8:
            bad += 1
            continue
        try:
            # No timezone marker on these timestamps. Treated as UTC to match
            # the binary format's epoch seconds; unconfirmed.
            stamp = dt.datetime.fromisoformat(row[0]).replace(
                tzinfo=dt.timezone.utc)
            records.append(Record(
                ts=int(stamp.timestamp()),
                ms=stamp.microsecond // 1000,
                rpm=int(row[1]),
                torque=int(row[2]),
                fuel100=int(round(float(row[3]) * 100)),
                coolant=int(row[4]),
                lat6=int(round(float(row[5]) * 1e6)),
                lon6=int(round(float(row[6]) * 1e6)),
                flags=int(row[7]),
            ))
        except (ValueError, TypeError, IndexError):
            bad += 1

    if bad:
        meta["warnings"].append(f"{bad:,} row(s) could not be read as records")
    if declared is not None and records and len(declared) != len(JSON_COLS):
        meta["warnings"].append(
            f"header declares {len(declared)} field names for {len(JSON_COLS)} "
            f"columns ({', '.join(declared)}) -- ignored, column order taken "
            f"from the data")
    meta["synthetic"] = bool(header.get("synthetic"))

    # Coverage: how much of the shift the device actually wrote. Records here
    # are not corrupt -- they are present or absent -- so validity alone would
    # report ~100% while a quarter of the samples never existed.
    if interval_ms and len(records) > 1:
        span_s = (records[-1].ts + records[-1].ms / 1000.0) -                  (records[0].ts + records[0].ms / 1000.0)
        expected = span_s / (interval_ms / 1000.0) + 1
        if expected > 0:
            meta["expected_records"] = int(round(expected))
            meta["coverage_pct"] = round(100.0 * len(records) / expected, 2)

    return records, meta


def parse_binary(path):
    """Decode the AES-encrypted binary format.

    Stops cleanly at the end-of-records terminator -- no resynchronisation,
    ever.
    """
    with open(path, "rb") as fh:
        data = fh.read()

    meta = {"size": len(data), "warnings": []}

    if data[:4] != MAGIC:
        meta["warnings"].append(f"bad magic {data[:4]!r}, expected {MAGIC!r}")
        pos = 0
    else:
        pos = 4

    if pos + 2 <= len(data):
        plen = struct.unpack_from("<H", data, pos)[0]
        if plen == HEADER_PLEN:
            pos += 2 + HEADER_PLEN
        else:
            meta["warnings"].append(f"header length {plen}, expected {HEADER_PLEN}")

    cipher = AES.new(log_key(), AES.MODE_ECB)
    records = []

    while pos + 2 <= len(data):
        plen = struct.unpack_from("<H", data, pos)[0]
        if plen != RECORD_PLEN or pos + 2 + plen > len(data):
            break                                  # terminator or truncation
        plain = cipher.decrypt(data[pos + 2: pos + 2 + plen])
        pos += 2 + plen
        # First 23 bytes are the record; the remaining 9 are 0xCD fill, not
        # PKCS#7 padding -- do not strip by trailing-byte length.
        records.append(Record(*struct.unpack_from(RECORD_FMT, plain)))

    meta["stopped_at"] = pos
    meta["trailing_bytes"] = len(data) - pos
    return records, meta


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def report(records, meta, path):
    verdicts = [reject_reason(r) for r in records]
    valid = [r for r, v in zip(records, verdicts) if v is None]
    total = len(records)
    pct = 100.0 * len(valid) / total if total else 0.0

    print(f"File            : {os.path.basename(path)}")
    print(f"Device          : {device_id_from_filename(path)}")
    print(f"Size            : {meta['size']:,} bytes")
    for w in meta["warnings"]:
        print(f"  ! warning     : {w}")
    if meta["trailing_bytes"]:
        print(f"  ! trailing    : {meta['trailing_bytes']:,} bytes after "
              f"terminator at offset {meta['stopped_at']:,} (not parsed)")
    print()
    print(f"Framed records  : {total:,}")
    print(f"Valid records   : {len(valid):,}  ({pct:.2f}%)")
    print(f"Invalid records : {total - len(valid):,}  ({100 - pct:.2f}%)")

    # For the JSON format, validity is close to meaningless on its own: rows
    # are not corrupted, they are missing. Coverage is the real quality figure
    # and reporting only validity would show ~100% while a quarter of the
    # shift was never written.
    if meta.get("coverage_pct") is not None:
        print(f"Expected records: {meta['expected_records']:,}  "
              f"(at {meta['header'].get('log_interval_ms')} ms)")
        print(f"Coverage        : {meta['coverage_pct']:.2f}%  "
              f"({meta['expected_records'] - total:,} sample(s) never written)")

    bad = collections.Counter(v for v in verdicts if v)
    if bad:
        print("\nRejection reasons (first failing check per record):")
        for reason, n in bad.most_common():
            print(f"  {reason:<12} {n:>8,}")

    if not valid:
        print("\nNo valid records -- nothing further to report.")
        return pct

    valid.sort(key=lambda r: (r.ts, r.ms))
    t0, t1 = valid[0].ts, valid[-1].ts
    span = t1 - t0
    fmt = "%Y-%m-%d %H:%M:%S"
    print(f"\nValid span      : {dt.datetime.fromtimestamp(t0, dt.timezone.utc):{fmt}}"
          f" -> {dt.datetime.fromtimestamp(t1, dt.timezone.utc):{fmt}} UTC")
    if span:
        print(f"Duration        : {span:,}s ({span / 3600:.2f} h)")
        print(f"Slot rate       : {total / span:.2f} rec/s  "
              f"(all framed records over the valid span)")

    # Engine state -- FORMAT_SPEC.md section 9
    off = sum(1 for r in valid if r.rpm == 0)
    idle = sum(1 for r in valid if 0 < r.rpm < 900)
    work = sum(1 for r in valid if r.rpm >= 900)
    running = idle + work
    print(f"\nEngine state    : off={off:,}  idle={idle:,}  working={work:,}")
    if running:
        print(f"Idle share      : {100.0 * idle / running:.1f}% of running time")

    fixes = sum(1 for r in valid if r.flags & FLAG_GPS_FIX)
    print(f"GPS fix         : {fixes:,} of {len(valid):,} "
          f"({100.0 * fixes / len(valid):.1f}%)")

    for name, vals, scale, unit in [
        ("rpm", [r.rpm for r in valid], 1, ""),
        ("torque", [r.torque for r in valid], 1, ""),
        ("fuel", [r.fuel100 for r in valid], 100.0, " (units unconfirmed)"),
        ("coolant", [r.coolant for r in valid], 1, " C"),
    ]:
        s = sorted(v / scale for v in vals)
        print(f"  {name:<8} min={s[0]:>9.2f}  median={s[len(s) // 2]:>9.2f}  "
              f"max={s[-1]:>9.2f}{unit}")

    return pct


def write_csv(records, path, out_path, keep_invalid):
    device = device_id_from_filename(path)
    written = 0
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_COLUMNS)
        for r in records:
            reason = reject_reason(r)
            if reason and not keep_invalid:
                continue
            try:
                stamp = dt.datetime.fromtimestamp(
                    r.ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            except (OverflowError, OSError, ValueError):
                stamp = f"INVALID({r.ts})"
            w.writerow([
                device, stamp, r.ms, r.rpm, r.torque, f"{r.fuel100 / 100:.2f}",
                r.coolant, f"{r.lat6 / 1e6:.6f}", f"{r.lon6 / 1e6:.6f}",
                r.flags & FLAG_GPS_FIX and 1 or 0,
                (r.flags & FLAG_IS_DEFAULT) and 1 or 0,
                0 if reason else 1, reason or "",
            ])
            written += 1
    return written, out_path


def main():
    ap = argparse.ArgumentParser(description="Decode a BlueICE telemetry log.")
    ap.add_argument("logfile")
    ap.add_argument("-o", "--output", help="CSV path (default: <logfile>_decoded.csv)")
    ap.add_argument("--stats-only", action="store_true", help="report only, write no CSV")
    ap.add_argument("--keep-invalid", action="store_true",
                    help="include invalid records in the CSV, flagged")
    ap.add_argument("--gate", type=float, metavar="PCT",
                    help="exit non-zero if validity is below PCT (release gate; use 99)")
    args = ap.parse_args()

    if not os.path.isfile(args.logfile):
        sys.exit(f"No such file: {args.logfile}")

    records, meta = parse(args.logfile)
    pct = report(records, meta, args.logfile)

    if not args.stats_only:
        out = args.output or re.sub(r"\.TXT$", "", args.logfile,
                                    flags=re.I) + "_decoded.csv"
        n, out = write_csv(records, args.logfile, out, args.keep_invalid)
        print(f"\nWrote {n:,} rows -> {out}")

    if args.gate is not None:
        # The gate asks one question: is the device delivering the data it
        # should? For the binary format that is validity. For the JSON format
        # rows are never corrupt, so validity alone would read ~100% while a
        # quarter of the samples were never written -- exactly the false green
        # light this gate exists to prevent. Take the worse of the two.
        coverage = meta.get("coverage_pct")
        if coverage is None:
            score, basis = pct, "valid"
        else:
            score = min(pct, coverage)
            basis = "valid" if pct <= coverage else "coverage"
            print(f"\nGate inputs     : {pct:.2f}% valid, "
                  f"{coverage:.2f}% coverage -- gating on the worse")
        print(f"\nRelease gate    : {score:.2f}% {basis} vs "
              f"{args.gate:.2f}% required")
        if score < args.gate:
            print("RESULT: FAIL -- firmware does not meet the acceptance gate.")
            return 1
        print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
