#!/usr/bin/env python3
"""
Pull telemetry logs from the ingest bucket and rebuild the dashboard datasets.

This is the "get fresh data without touching anything by hand" tool. It is
deliberately small and deliberately paranoid, because the thing it feeds -- the
0.50% valid-record figure and everything derived from it -- is the project's
central claim. A sync tool that silently changed those numbers would be worse
than no sync tool at all.

    python blueice_sync.py check      # can I reach the bucket? are deps installed?
    python blueice_sync.py pull       # download anything new into inbox/
    python blueice_sync.py build      # rebuild data.js / track.js from what is local
    python blueice_sync.py update     # pull + build  <- this is what the scheduler runs
    python blueice_sync.py status     # what is ingested, and how healthy is it

Guarantees this tool makes:

  1. `../../Not mine/` is never written to. Downloads land in `inbox/`.
  2. Ingest is idempotent. A file already ingested is identified by SHA-256 and
     skipped, so running `update` in a loop cannot double-count anything.
  3. Records are deduped ACROSS files, never WITHIN a file. Two records sharing
     (ts, ms) inside one log are two real writes into the same 100 ms bucket --
     the reference shift has two such pairs. Dropping them would change 419 to
     417 and 0.50% to 0.49%, contradicting FINDINGS.md. Overlapping uploads are
     a different problem and are handled.
  4. A file that will not decode is quarantined, not built from.
  5. The valid-record gate is reported on every run. It is a warning, not a
     failure, because today it legitimately fails at 0.50% and the sync must
     still work while the firmware is broken.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MINE = os.path.dirname(HERE)
PROJECT = os.path.dirname(MINE)
sys.path.insert(0, MINE)

# Everything this tool WRITES can be redirected. On a laptop the defaults are
# right and nobody sets these. On Lambda the code ships read-only under
# /var/task, so the state file, the inbox and the built dashboard all have to
# land in /tmp instead -- see aws/lambda_handler.py.
DASHBOARD = os.environ.get("BLUEICE_DASHBOARD") or os.path.join(MINE, "dashboard")

CONFIG_PATH = os.environ.get("BLUEICE_CONFIG") or os.path.join(HERE, "config.json")
EXAMPLE_PATH = os.path.join(HERE, "config.example.json")
STATE_PATH = os.environ.get("BLUEICE_STATE") or os.path.join(HERE, "state.json")

# The decoder needs pycryptodome. `check` has to be able to run and *report*
# that it is missing, so the import must not be fatal at module level.
try:
    from decode_log import parse, reject_reason, device_id_from_filename
    DECODER_ERR = None
except Exception as exc:                                   # pragma: no cover
    parse = reject_reason = device_id_from_filename = None
    DECODER_ERR = exc


def say(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------------------
# Config and state
# ---------------------------------------------------------------------------

DEFAULTS = {
    "source": {"type": "s3", "bucket": "", "prefix": "", "region": "me-central-1",
               "profile": None, "endpoint_url": None},
    "devices": [],
    "inbox": "inbox",
    "select": "latest-day",
    "gate_pct": 99.0,
    "rebuild_dashboard": True,
    "max_files_per_run": 500,
}


def load_config(path=CONFIG_PATH):
    if not os.path.isfile(path):
        raise SystemExit(
            "No config at " + path + "\n"
            "Copy config.example.json to config.json and fill in the bucket,\n"
            "or run install.ps1 / install.sh which does it for you.")
    # utf-8-sig, not utf-8: Notepad and PowerShell's Out-File both write a BOM,
    # and this file is meant to be hand-edited on a Windows workstation.
    cfg = json.load(open(path, encoding="utf-8-sig"))
    merged = dict(DEFAULTS)
    merged.update(cfg)
    merged["source"] = dict(DEFAULTS["source"], **cfg.get("source", {}))
    return merged


def load_state():
    if os.path.isfile(STATE_PATH):
        try:
            return json.load(open(STATE_PATH, encoding="utf-8-sig"))
        except json.JSONDecodeError:
            # A half-written state file must not wedge the scheduler forever.
            shutil.copy(STATE_PATH, STATE_PATH + ".corrupt")
            say("! state.json was unreadable; moved aside to state.json.corrupt")
    return {"objects": {}, "runs": []}


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    os.replace(tmp, STATE_PATH)          # atomic: a killed run cannot truncate it


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

class Obj:
    __slots__ = ("key", "size", "etag", "modified")

    def __init__(self, key, size, etag, modified):
        self.key, self.size, self.etag, self.modified = key, size, etag, modified


class S3Source:
    """The real thing. Needs boto3 and credentials from the usual chain."""

    kind = "s3"

    def __init__(self, cfg, client=None):
        self.bucket = cfg["bucket"]
        self.prefix = cfg.get("prefix") or ""
        if not self.bucket:
            raise SystemExit("config.json: source.bucket is empty.")
        if client is not None:
            self.client = client                # selftest injects a stub here
            return
        try:
            import boto3
        except ImportError:
            raise SystemExit(
                "boto3 is not installed. Run install.ps1 / install.sh, or:\n"
                "    python -m pip install boto3")
        session = boto3.Session(profile_name=cfg.get("profile") or None,
                                region_name=cfg.get("region") or None)
        self.client = session.client("s3", endpoint_url=cfg.get("endpoint_url") or None)

    def describe(self):
        return "s3://" + self.bucket + "/" + self.prefix

    def list(self):
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": self.prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = self.client.list_objects_v2(**kw)
            for it in resp.get("Contents", []):
                if it["Key"].endswith("/"):
                    continue
                yield Obj(it["Key"], it["Size"],
                          str(it.get("ETag", "")).strip('"'),
                          str(it.get("LastModified", "")))
            if not resp.get("IsTruncated"):
                return
            token = resp.get("NextContinuationToken")

    def fetch(self, key, dest):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        self.client.download_file(self.bucket, key, dest)


class LocalSource:
    """A directory standing in for the bucket.

    Two uses: exercising the whole pipeline before AWS exists (which is where
    this project is today), and pointing at a mounted share if the fleet ever
    drops files somewhere other than S3. Read-only -- it copies out and never
    writes back, which is what lets it be aimed at `Not mine/` safely.
    """

    kind = "local"

    def __init__(self, cfg):
        self.root = os.path.abspath(os.path.join(HERE, cfg.get("path") or "."))
        if not os.path.isdir(self.root):
            raise SystemExit("source.path does not exist: " + self.root)

    def describe(self):
        return self.root

    def list(self):
        for base, _dirs, files in os.walk(self.root):
            for f in sorted(files):
                p = os.path.join(base, f)
                st = os.stat(p)
                key = os.path.relpath(p, self.root).replace("\\", "/")
                yield Obj(key, st.st_size, str(st.st_size) + "-" + str(st.st_mtime_ns),
                          dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc).isoformat())

    def fetch(self, key, dest):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(os.path.join(self.root, key.replace("/", os.sep)), dest)


def make_source(cfg, client=None):
    s = cfg["source"]
    if s["type"] == "local":
        return LocalSource(s)
    if s["type"] == "s3":
        return S3Source(s, client=client)
    raise SystemExit("unknown source.type " + repr(s["type"]) + " (expected 's3' or 'local')")


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

# The device writes .TXT. `.bin` is deliberately NOT here: `Not mine/` contains
# FIRMWARE.bin, and a sync that hoovered up firmware images and quarantined them
# would be noise at best and confusing at worst.
LOG_SUFFIXES = (".txt", ".log")


def looks_like_log(key):
    return key.lower().endswith(LOG_SUFFIXES)


def inbox_dir(cfg):
    return os.path.abspath(os.path.join(HERE, cfg["inbox"]))


def inspect(path):
    """Decode a downloaded file far enough to know whether it is usable.

    Returns a dict of facts, or {'ok': False, 'why': ...}. Nothing here is
    inferred: framed/valid are counted, the time range comes from valid
    records only.
    """
    if parse is None:
        return {"ok": False, "why": "decoder unavailable: " + str(DECODER_ERR)}

    # A `.1.txt` is the FUEL companion to an engine log, not a broken engine
    # log. Quarantining it threw away the only real fuel data in the bucket,
    # and the reconciliation silently had nothing to work with.
    try:
        from decode_log import log_kind
        if log_kind(path) == "fuel-json":
            from decode_log import parse_fuel
            samples, fmeta = parse_fuel(path)
            return {"ok": True, "companion": "fuel", "device": device_id_from_filename(path),
                    "framed": len(samples), "valid": len(samples),
                    "pct": 100.0 if samples else 0.0,
                    "first_ts": int(samples[0][0]) if samples else None,
                    "last_ts": int(samples[-1][0]) if samples else None,
                    "trailing_bytes": 0, "warnings": fmeta.get("warnings", [])}
    except Exception:
        pass                      # fall through to the engine path

    try:
        records, meta = parse(path)
    except Exception as exc:
        return {"ok": False, "why": "parse failed: " + str(exc)}
    if not records:
        return {"ok": False, "why": "no framed records (bad magic, or not a log)"}
    valid = [r for r in records if reject_reason(r) is None]
    ts = sorted(r.ts for r in valid)
    return {
        "ok": True,
        "device": device_id_from_filename(path),
        "framed": len(records),
        "valid": len(valid),
        "pct": round(100.0 * len(valid) / len(records), 2),
        "first_ts": ts[0] if ts else None,
        "last_ts": ts[-1] if ts else None,
        "trailing_bytes": meta.get("trailing_bytes", 0),
        "warnings": meta.get("warnings", []),
    }


def utc_day(ts):
    if not ts:
        return None
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")


def pull(cfg, state, dry_run=False, client=None):
    src = make_source(cfg, client=client)
    inbox = inbox_dir(cfg)
    quarantine = os.path.join(inbox, "_rejected")
    devices = set(cfg.get("devices") or [])
    seen_hashes = {}
    for k, v in state["objects"].items():
        if v.get("sha256"):
            seen_hashes.setdefault(v["sha256"], k)

    say("source   " + src.describe())
    listed = added = skipped = rejected = 0

    for obj in src.list():
        if not looks_like_log(obj.key):
            continue
        listed += 1
        prior = state["objects"].get(obj.key)
        if prior and prior.get("etag") == obj.etag and prior.get("size") == obj.size:
            if prior.get("duplicate_of") or os.path.isfile(prior.get("local") or ""):
                skipped += 1
                continue
        if added >= cfg["max_files_per_run"]:
            say("! stopping at max_files_per_run=" + str(cfg["max_files_per_run"])
                + "; run again for the rest")
            break
        if dry_run:
            say("  would fetch " + obj.key + "  (" + format(obj.size, ",") + " bytes)")
            added += 1
            continue

        base = os.path.basename(obj.key)
        staged = os.path.join(inbox, "_staging", base)
        src.fetch(obj.key, staged)

        digest = sha256(staged)
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        if digest in seen_hashes and seen_hashes[digest] != obj.key:
            # Same bytes under a second key. Register it so we never fetch it
            # again, but do not let it into the build -- that is exactly the
            # double-count HANDOVER warns about.
            state["objects"][obj.key] = {
                "etag": obj.etag, "size": obj.size, "sha256": digest,
                "duplicate_of": seen_hashes[digest], "local": None,
                "usable": False, "why": "identical bytes to " + seen_hashes[digest],
                "ingested": now,
            }
            os.remove(staged)
            skipped += 1
            continue

        facts = inspect(staged)
        if not facts["ok"]:
            os.makedirs(quarantine, exist_ok=True)
            dest = os.path.join(quarantine, base)
            if os.path.exists(dest):
                os.remove(dest)
            shutil.move(staged, dest)
            state["objects"][obj.key] = {
                "etag": obj.etag, "size": obj.size, "sha256": digest,
                "local": dest, "usable": False, "why": facts["why"], "ingested": now,
            }
            say("  QUARANTINE " + base + ": " + facts["why"])
            rejected += 1
            continue

        device = facts["device"]
        if devices and device not in devices:
            os.remove(staged)
            skipped += 1
            continue
        dest = os.path.join(inbox, device, base)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest):
            os.remove(dest)
        shutil.move(staged, dest)
        rec = {"etag": obj.etag, "size": obj.size, "sha256": digest, "local": dest,
               "usable": True, "modified": obj.modified,
               "day": utc_day(facts["first_ts"]), "ingested": now}
        for k in ("device", "framed", "valid", "pct", "first_ts", "last_ts"):
            rec[k] = facts[k]
        state["objects"][obj.key] = rec
        seen_hashes[digest] = obj.key
        say("  + " + base + "  device " + device + "  "
            + format(facts["valid"], ",") + "/" + format(facts["framed"], ",")
            + " valid (" + str(facts["pct"]) + "%)")
        added += 1

    staging = os.path.join(inbox, "_staging")
    if os.path.isdir(staging) and not os.listdir(staging):
        os.rmdir(staging)
    say("listed " + str(listed) + " | new " + str(added) + " | already had "
        + str(skipped) + " | quarantined " + str(rejected))
    return added


# ---------------------------------------------------------------------------
# Selection and build
# ---------------------------------------------------------------------------

def usable(state):
    """Engine logs that can be built from.

    Fuel companions are usable and kept, but they are NOT build inputs -- they
    are picked up automatically beside their engine log. Including them here
    would have the builder try to render a shift out of a file with no rpm.
    """
    out = []
    for v in state["objects"].values():
        if v.get("companion"):
            continue
        if v.get("usable") and v.get("local") and os.path.isfile(v["local"]):
            out.append(v)
    return out


def companions(state):
    return [v for v in state["objects"].values()
            if v.get("companion") and v.get("local")
            and os.path.isfile(v["local"])]


def select_logs(cfg, state):
    """Which logs the dashboard should be built from.

    latest-day  every file whose data falls on the most recent UTC day present
                for the chosen device. The default, because the device uploads
                in chunks and one day is the unit a supervisor thinks in.
    latest-file the single newest file. Cheapest; right when one file is one shift.
    all         everything for the device. Honest but unbounded -- the dashboard
                was not built for months of history.
    """
    files = usable(state)
    if not files:
        return [], None
    # EVERY device, not just the first one found. The dashboard shows a fleet;
    # picking devices[0] silently downloaded three machines and then left them
    # out of the picker, which looks exactly like the sync not working.
    devices = cfg.get("devices") or sorted({f["device"] for f in files})
    mode = cfg["select"]
    chosen = []
    for device in devices:
        mine = [f for f in files if f["device"] == device]
        if not mine:
            continue
        if mode == "all":
            pick = mine
        elif mode == "latest-file":
            pick = [max(mine, key=lambda f: (f.get("last_ts") or 0, f["ingested"]))]
        else:
            if mode != "latest-day":
                say("! unknown select " + repr(mode) + "; using latest-day")
            days = [f["day"] for f in mine if f.get("day")]
            if not days:
                continue
            newest = max(days)
            pick = [f for f in mine if f.get("day") == newest]
        chosen += pick
    chosen.sort(key=lambda f: (f["device"], f.get("first_ts") or 0, f["local"]))
    return chosen, (devices[0] if len(devices) == 1 else
                    "%d devices" % len(devices))


def extra_logs():
    """Logs that must stay in the picker even though the sync did not fetch them.

    The reference shifts in `Not mine/` are what every documented figure was
    measured from, and the demo fixtures are the only thing exercising the fuel
    reconciliation. Losing either to a routine sync would be a silent
    regression. Companion fuel files (`*.1.txt`) are excluded: they are picked
    up automatically beside their engine log.
    """
    import glob
    # BLUEICE_EXTRA replaces the default locations with an os.pathsep-separated
    # list of directories. Lambda has neither `Not mine/` nor `demo/` in its
    # package -- 49 MB of fixtures would not fit -- so it stages them out of S3
    # into /tmp and points this at that directory instead.
    env = os.environ.get("BLUEICE_EXTRA", "").strip()
    if env:
        dirs = [d for d in env.split(os.pathsep) if d.strip()]
        pats = [os.path.join(d, g) for d in dirs
                for g in ("*_LOG*.TXT", "*_LOG*.txt")]
    else:
        pats = [os.path.join(PROJECT, "Not mine", "*_LOG*.TXT"),
                os.path.join(PROJECT, "Not mine", "*_LOG*.txt"),
                os.path.join(MINE, "demo", "*_LOG*.txt")]
    out = []
    for pat in pats:
        out += glob.glob(pat)
    seen, keep = set(), []
    for p in sorted(out):
        if p.lower().endswith(".1.txt"):
            continue
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            keep.append(p)
    return keep


def build(cfg, state):
    # Hand over EVERY ingested log, not a selected shift. build_datasets.py
    # now groups by car and decides for itself which shift is Live and which
    # are history; passing only the latest day gave each car a Live reading
    # and almost no past to trace through.
    chosen = usable(state)
    if not chosen:
        say("nothing usable to build from -- run `pull` first.")
        return False
    paths = [f["local"] for f in chosen]
    cars = sorted({f["device"] for f in chosen})
    say("building " + str(len(cars)) + " car(s) from "
        + str(len(paths)) + " ingested log(s)")

    # Build through build_datasets.py, not build_data/build_track directly.
    # Those two render exactly ONE shift, so a scheduled sync would quietly
    # replace the whole picker with whatever it had just downloaded -- the
    # reference shifts and every fuel fixture would vanish on the first
    # successful run, and nobody would connect the loss to the sync.
    keep = [] if not cfg.get("keep_existing_logs", True) else extra_logs()
    have = {os.path.basename(p).lower() for p in paths}
    keep = [p for p in keep if os.path.basename(p).lower() not in have]
    if keep:
        say("  keeping " + str(len(keep)) + " existing log(s) in the picker")

    r = subprocess.run([sys.executable, "build_datasets.py"] + paths + keep,
                       cwd=DASHBOARD, capture_output=True, text=True)
    if r.returncode != 0:
        say("! build_datasets.py failed:\n" + r.stdout + "\n" + r.stderr)
        return False
    for line in [l for l in r.stdout.strip().splitlines() if "wrote" in l]:
        say("  " + line.strip())

    framed = sum(f["framed"] for f in chosen)
    valid = sum(f["valid"] for f in chosen)
    pct = round(100.0 * valid / framed, 2) if framed else 0.0
    say("  " + format(valid, ",") + " valid of " + format(framed, ",")
        + " framed = " + str(pct) + "%")
    if pct < cfg["gate_pct"]:
        say("  ** GATE FAIL: " + str(pct) + "% valid, need " + str(cfg["gate_pct"]) + "%.")
        say("     The firmware defect (BUG_REPORT.md) is still present. The dashboard is")
        say("     built and correct, but it draws " + str(pct) + "% of what the machine recorded.")
    else:
        say("  GATE PASS at " + str(cfg["gate_pct"]) + "% -- tell whoever fixed the firmware.")
    state.setdefault("last_build", {}).update(
        {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
         "device": (cars[0] if len(cars) == 1 else "%d cars" % len(cars)),
         "files": len(paths), "framed": framed,
         "valid": valid, "pct": pct, "gate_pass": pct >= cfg["gate_pct"]})
    return True


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_check(cfg, state, args):
    ok = [True]

    def line(good, label, detail=""):
        ok[0] = ok[0] and good
        say("  [" + ("PASS" if good else "FAIL") + "] " + label
            + ("  " + detail if detail else ""))

    say("environment")
    line(sys.version_info >= (3, 8), "python >= 3.8", sys.version.split()[0])
    line(DECODER_ERR is None, "decoder importable (pycryptodome)",
         "" if DECODER_ERR is None else str(DECODER_ERR))
    line(os.path.isdir(DASHBOARD), "dashboard/ present", DASHBOARD)

    say("config")
    src = cfg["source"]
    line(src["type"] in ("s3", "local"), "source.type", src["type"])
    if src["type"] == "s3":
        line(bool(src["bucket"]), "source.bucket set", src["bucket"] or "<empty>")
        try:
            import boto3
            line(True, "boto3 installed", boto3.__version__)
        except ImportError:
            line(False, "boto3 installed", "run install.ps1 / install.sh")
            say("\nresult: NOT READY")
            return 1
        try:
            s = make_source(cfg)
            s.client.head_bucket(Bucket=src["bucket"])
            line(True, "bucket reachable", "s3://" + src["bucket"])
            resp = s.client.list_objects_v2(Bucket=src["bucket"],
                                            Prefix=src.get("prefix") or "", MaxKeys=1)
            line(True, "ListObjects permitted",
                 str(resp.get("KeyCount", 0)) + " object(s) at that prefix")
        except Exception as exc:
            line(False, "bucket reachable", type(exc).__name__ + ": " + str(exc))
            say("\n  Credentials come from the standard chain -- an IAM role, "
                "`aws configure`,\n  or AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in the "
                "environment.\n  This tool never asks you to type a key into it.")
    else:
        try:
            s = make_source(cfg)
            n = sum(1 for o in s.list() if looks_like_log(o.key))
            line(True, "source directory readable",
                 s.describe() + " (" + str(n) + " log file(s))")
        except SystemExit as exc:
            line(False, "source directory readable", str(exc))

    say("\nresult: " + ("READY" if ok[0] else "NOT READY"))
    return 0 if ok[0] else 1


def _record_run(state, n):
    state.setdefault("runs", []).append(
        {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "new": n})
    state["runs"] = state["runs"][-50:]


def cmd_pull(cfg, state, args):
    n = pull(cfg, state, dry_run=args.dry_run)
    if not args.dry_run:
        _record_run(state, n)
        save_state(state)
    return 0


def cmd_build(cfg, state, args):
    okay = build(cfg, state)
    save_state(state)
    if args.fail_under_gate and not state.get("last_build", {}).get("gate_pass"):
        return 1
    return 0 if okay else 1


def cmd_update(cfg, state, args):
    n = pull(cfg, state)
    _record_run(state, n)
    if n == 0 and state.get("last_build") and not args.force:
        say("no new files; dashboard already current (use --force to rebuild anyway)")
        save_state(state)
        return 0
    okay = build(cfg, state)
    save_state(state)
    if args.fail_under_gate and not state.get("last_build", {}).get("gate_pass"):
        return 1
    return 0 if okay else 1


def cmd_status(cfg, state, args):
    files = usable(state)
    bad = [v for v in state["objects"].values() if v.get("usable") is False
           and not v.get("duplicate_of")]
    dupes = [v for v in state["objects"].values() if v.get("duplicate_of")]
    say("source        " + cfg["source"]["type"] + ": "
        + str(cfg["source"].get("bucket") or cfg["source"].get("path")))
    say("ingested      " + str(len(files)) + " usable | " + str(len(bad))
        + " quarantined | " + str(len(dupes)) + " duplicate")
    if files:
        by_dev = {}
        for f in files:
            by_dev.setdefault(f["device"], []).append(f)
        for dev, fs in sorted(by_dev.items()):
            framed = sum(f["framed"] for f in fs)
            valid = sum(f["valid"] for f in fs)
            pct = round(100.0 * valid / framed, 2) if framed else 0
            days = sorted({f["day"] for f in fs if f.get("day")})
            say("  " + dev + "  " + str(len(fs)) + " file(s)  "
                + format(valid, ",") + "/" + format(framed, ",") + " valid ("
                + str(pct) + "%)  "
                + (days[0] if days else "?") + " .. " + (days[-1] if days else "?"))
    lb = state.get("last_build")
    if lb:
        say("last build    " + lb["at"] + "  device " + str(lb["device"]) + "  "
            + str(lb["pct"]) + "% valid  gate "
            + ("PASS" if lb["gate_pass"] else "FAIL"))
    else:
        say("last build    never")
    for v in bad:
        say("  quarantined: " + os.path.basename(v.get("local") or "?")
            + " -- " + str(v.get("why")))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Sync BlueICE telemetry logs from the ingest bucket "
                    "and rebuild the dashboard.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="verify deps, config and bucket access")
    p = sub.add_parser("pull", help="download new logs into inbox/")
    p.add_argument("--dry-run", action="store_true", help="list what would be fetched")
    p = sub.add_parser("build", help="rebuild data.js / track.js from local logs")
    p.add_argument("--fail-under-gate", action="store_true",
                   help="exit 1 if valid%% is below gate_pct (for CI)")
    p = sub.add_parser("update", help="pull then build -- what the scheduler runs")
    p.add_argument("--force", action="store_true",
                   help="rebuild even if nothing new arrived")
    p.add_argument("--fail-under-gate", action="store_true")
    sub.add_parser("status", help="what is ingested and how healthy it is")

    args = ap.parse_args(argv)
    cfg = load_config()
    state = load_state()
    fn = {"check": cmd_check, "pull": cmd_pull, "build": cmd_build,
          "update": cmd_update, "status": cmd_status}[args.cmd]
    try:
        return fn(cfg, state, args)
    except Exception as exc:                      # noqa: BLE001
        # This runs unattended every 30 minutes. A missing credential or an
        # expired token is an operational condition, not a crash, and a
        # scheduled task's log has to stay readable -- a Python traceback in
        # sync.log tells whoever finds it nothing they can act on.
        msg = aws_hint(exc)
        if msg is None:
            raise
        say("")
        say("FAILED: " + msg)
        return 1


# Boto raises a lot of distinct types; these are the ones an operator can
# actually do something about. Anything else falls through to a real traceback,
# because an unrecognised failure should be loud.
def aws_hint(exc):
    name = type(exc).__name__

    if name == "NoCredentialsError":
        return ("No AWS credentials found.\n"
                "  Run `aws configure`, or set AWS_ACCESS_KEY_ID and "
                "AWS_SECRET_ACCESS_KEY.\n"
                "  A scheduled task runs as you, so configure them for this "
                "Windows account.")
    if name in ("EndpointConnectionError", "ConnectTimeoutError",
                "ReadTimeoutError", "ConnectionClosedError"):
        return ("Could not reach AWS -- network down, or source.region is "
                "wrong.\n  " + str(exc).split("\n")[0])
    if name == "ProfileNotFound":
        return "source.profile names a profile that does not exist: " + str(exc)

    code = None
    if hasattr(exc, "response") and isinstance(getattr(exc, "response"), dict):
        code = exc.response.get("Error", {}).get("Code")
    if code in ("InvalidAccessKeyId", "SignatureDoesNotMatch"):
        return "The access key is not valid. Re-run `aws configure`."
    if code in ("ExpiredToken", "ExpiredTokenException", "RequestExpired"):
        return "The credentials have expired. Refresh them and run again."
    if code in ("AccessDenied", "AccessDeniedException", "403"):
        return ("Access denied. The key needs s3:ListBucket on the bucket and "
                "s3:GetObject\n  on the prefix -- see the policy in README.md.")
    if code in ("NoSuchBucket", "404"):
        return "No such bucket: " + str(getattr(exc, "response", {})
                                        .get("Error", {}).get("BucketName", "?"))
    if code == "PermanentRedirect" or code == "AuthorizationHeaderMalformed":
        return ("The bucket is in a different region than source.region says.\n"
                "  " + str(exc).split("\n")[0])
    if code:
        return "AWS refused the request (" + str(code) + "): " + str(exc).split("\n")[0]
    return None


if __name__ == "__main__":
    sys.exit(main())
