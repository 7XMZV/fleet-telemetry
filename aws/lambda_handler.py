#!/usr/bin/env python3
"""
Lambda entry point for the BlueICE fleet build.

Runs the same blueice_sync.py that runs on a laptop -- no second
implementation of the ingest, the decode or the reconciliation, because two
implementations drift and then the dashboard disagrees with itself.

What this file adds is only what Lambda needs, plus the guarantees asked for
in review:

  * /var/task is READ ONLY, so every writable path is pushed into /tmp before
    the sync tool is imported (it resolves those paths at import time).

  * State lives in the site bucket, and is written ONLY after a build has been
    published in full. A failed build or a failed upload leaves the previous
    state untouched, so the next run retries the same telemetry instead of
    marking it done. (review item 3)

  * Only one execution at a time. A lock object in the site bucket is taken
    with a conditional PUT, so two overlapping schedules cannot both rebuild
    and race each other's uploads. (review item 4)

  * The three dashboard files are staged under one build id and then copied
    into place, so a viewer cannot load a new index.html against a stale
    track.js. (review item 4)

  * Objects that disappear from the telemetry bucket are pruned from state,
    and every failure is raised rather than swallowed, so a broken run shows
    up as a failed invocation instead of a quiet success. (review item 5)

  * Synthetic demonstration data is OFF unless explicitly switched on.
    Production shows real telemetry only. (review item 6)

Environment:
    RAW_BUCKET        telemetry bucket
    RAW_PREFIX        default device-logs/
    SITE_BUCKET       where the dashboard is published
    BLUEICE_KEY_SECRET  Secrets Manager secret holding the decryption key
    FIXTURES_PREFIX   OPTIONAL. Empty/unset = real telemetry only.
    FORCE_REBUILD     optional, "1" to rebuild even when nothing changed
"""

import datetime as dt
import json
import os
import shutil
import sys
import time

TMP = "/tmp/blueice"
SITE = TMP + "/site"
INBOX = TMP + "/inbox"
FIXTURES = TMP + "/fixtures"

# MUST happen before blueice_sync is imported: it resolves these at import
# time. BLUEICE_DASHBOARD is deliberately NOT set -- it means "where the
# builder scripts live", not "where output goes", and pointing it at /tmp made
# the first run die looking for build_datasets.py in the output directory.
os.environ.setdefault("BLUEICE_OUT", SITE)
os.environ.setdefault("BLUEICE_STATE", TMP + "/state.json")
os.environ.setdefault("BLUEICE_CONFIG", TMP + "/config.json")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "sync"))
sys.path.insert(0, HERE)

# Lambda unpacks a layer to /opt/python and adds it to THIS process's sys.path,
# but it does not set PYTHONPATH -- so a child process started with
# sys.executable begins with a clean path. build_datasets.py runs the two
# builders as subprocesses, and without this they cannot import the decoder's
# pycryptodome dependency and die with "Missing dependency".
#
# Invisible locally, where pycryptodome is installed system-wide. It only
# appears on Lambda, which is exactly why it survived to the first real run.
_inherit = [p for p in ("/opt/python", HERE) if os.path.isdir(p)]
if os.environ.get("PYTHONPATH"):
    _inherit.append(os.environ["PYTHONPATH"])
os.environ["PYTHONPATH"] = os.pathsep.join(_inherit)

import boto3                                                    # noqa: E402
from botocore.exceptions import ClientError                     # noqa: E402
import blueice_sync as sync                                     # noqa: E402

RAW_BUCKET = os.environ["RAW_BUCKET"]
RAW_PREFIX = os.environ.get("RAW_PREFIX", "device-logs/")
SITE_BUCKET = os.environ["SITE_BUCKET"]

STATE_KEY = "_state/state.json"
LOCK_KEY = "_state/lock.json"
STAGE_PREFIX = "_staging/"

# How long a lock is honoured. Past this it is assumed the holder died --
# generous, because the function's own timeout is 600 s.
LOCK_TTL_S = 900

s3 = boto3.client("s3")

# name in /tmp -> published key, content type, cache policy.
# index.html is published LAST so it can never reference data it precedes.
PUBLISH = [
    ("data.js", "data.js", "application/javascript; charset=utf-8",
     "public, max-age=120"),
    ("track.js", "track.js", "application/javascript; charset=utf-8",
     "public, max-age=120"),
    ("vehicle.html", "index.html", "text/html; charset=utf-8",
     "public, max-age=60"),
]


def log(*a):
    print(*a, flush=True)


class DeployError(RuntimeError):
    """Raised so the invocation fails visibly instead of returning ok."""


# --------------------------------------------------------------------- lock


def acquire_lock(request_id):
    """One execution at a time (review item 4).

    Uses a conditional PUT: IfNoneMatch='*' succeeds only if the object does
    not already exist, which makes this a real mutual exclusion rather than a
    read-then-write race. If the running botocore is too old for conditional
    writes, fall back to a read-then-write, which is weaker but still catches
    the ordinary overlapping-schedule case.
    """
    body = json.dumps({
        "request_id": request_id,
        "taken": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }).encode()

    try:
        s3.put_object(Bucket=SITE_BUCKET, Key=LOCK_KEY, Body=body,
                      ContentType="application/json", IfNoneMatch="*")
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("PreconditionFailed", "ConditionalRequestConflict"):
            return _lock_is_stale_then_take(body)
        if code in ("NotImplemented", "InvalidRequest", "InvalidArgument"):
            log("  conditional PUT unsupported; using read-then-write lock")
            return _legacy_lock(body)
        raise
    except TypeError:                       # botocore without IfNoneMatch
        log("  botocore has no IfNoneMatch; using read-then-write lock")
        return _legacy_lock(body)


def _lock_age_seconds():
    try:
        head = s3.head_object(Bucket=SITE_BUCKET, Key=LOCK_KEY)
    except ClientError:
        return None
    return (dt.datetime.now(dt.timezone.utc) - head["LastModified"]).total_seconds()


def _lock_is_stale_then_take(body):
    age = _lock_age_seconds()
    if age is None:
        return False
    if age < LOCK_TTL_S:
        log("  another execution holds the lock (%.0fs old) -- exiting" % age)
        return False
    log("  stale lock (%.0fs old, TTL %ds) -- taking it over" % (age, LOCK_TTL_S))
    s3.put_object(Bucket=SITE_BUCKET, Key=LOCK_KEY, Body=body,
                  ContentType="application/json")
    return True


def _legacy_lock(body):
    age = _lock_age_seconds()
    if age is not None and age < LOCK_TTL_S:
        log("  another execution holds the lock (%.0fs old) -- exiting" % age)
        return False
    s3.put_object(Bucket=SITE_BUCKET, Key=LOCK_KEY, Body=body,
                  ContentType="application/json")
    return True


def release_lock():
    try:
        s3.delete_object(Bucket=SITE_BUCKET, Key=LOCK_KEY)
    except ClientError as exc:
        # Not fatal: the TTL will free it. But say so -- a lock that cannot be
        # released will stall every run until it expires.
        log("  ! could not release the lock: %s" % exc)


# -------------------------------------------------------------------- config


def write_config():
    cfg = {
        "source": {"type": "s3", "bucket": RAW_BUCKET, "prefix": RAW_PREFIX,
                   "region": os.environ.get("AWS_REGION", "eu-north-1")},
        "inbox": INBOX,
        "devices": [],
        "select": "latest-day",
        "gate_pct": float(os.environ.get("GATE_PCT", "99.0")),
        "rebuild_dashboard": True,
        "max_files_per_run": 500,
    }
    os.makedirs(TMP, exist_ok=True)
    with open(os.environ["BLUEICE_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    return cfg


# --------------------------------------------------------------------- state


def fetch_state():
    """Previous state, or None on the very first run.

    A missing object is a legitimate first run. Anything else -- denied,
    unreachable, corrupt -- is raised (review item 5): treating it as "first
    run" would silently re-ingest everything and republish over good output.
    """
    try:
        body = s3.get_object(Bucket=SITE_BUCKET, Key=STATE_KEY)["Body"].read()
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            log("no previous state -- first run")
            return None
        raise DeployError("could not read state: %s" % exc)

    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeployError("state object is corrupt: %s" % exc)

    with open(os.environ["BLUEICE_STATE"], "wb") as fh:
        fh.write(body)
    return parsed


def prune_deleted(st, live):
    """Drop state entries whose source object is gone (review item 5).

    Without this, a log deleted from the bucket stays in state for ever: the
    build keeps trying to read a local copy that a cold container does not
    have, and the ETag comparison keeps reporting a difference that can never
    be resolved.
    """
    gone = [k for k in list(st.get("objects", {}))
            if k.startswith(RAW_PREFIX) and k not in live]
    for k in gone:
        st["objects"].pop(k, None)
    if gone:
        log("  pruned %d deleted source log(s) from state" % len(gone))
        for k in gone[:5]:
            log("     - %s" % k)
    return len(gone)


def save_state():
    with open(os.environ["BLUEICE_STATE"], "rb") as fh:
        s3.put_object(Bucket=SITE_BUCKET, Key=STATE_KEY, Body=fh.read(),
                      ContentType="application/json")


# ------------------------------------------------------------------ telemetry


def bucket_etags():
    out, token = {}, None
    while True:
        kw = {"Bucket": RAW_BUCKET, "Prefix": RAW_PREFIX}
        if token:
            kw["ContinuationToken"] = token
        page = s3.list_objects_v2(**kw)
        for o in page.get("Contents", []):
            # The sync's own rule, not a copy of it: it excludes .bin so
            # firmware images are never ingested.
            if sync.looks_like_log(o["Key"]):
                out[o["Key"]] = o["ETag"].strip('"')
        if not page.get("IsTruncated"):
            return out
        token = page.get("NextContinuationToken")


def unchanged(state, live):
    """True when the bucket holds exactly what the last build consumed.

    Only entries under RAW_PREFIX count. State can also hold logs ingested
    from a local source, which never appear in a bucket listing; comparing
    against those made this return False on every single run.
    """
    if not state or not state.get("objects"):
        return False
    known = {k: v.get("etag") for k, v in state["objects"].items()
             if k.startswith(RAW_PREFIX)}
    return bool(known) and known == live


def stage_fixtures():
    """Synthetic demonstration machines. OFF unless asked for (review item 6).

    Production shows real telemetry only. Set FIXTURES_PREFIX to switch the
    generated examples on; they are badged as generated on every screen that
    shows them.

    Pulls the whole prefix, not just logs, because the fuel reconciliation
    needs <stem>.dispenses.json sitting beside its log (review item 7).
    """
    prefix = os.environ.get("FIXTURES_PREFIX", "").strip()
    if not prefix:
        log("fixtures off -- real telemetry only")
        return 0

    os.makedirs(FIXTURES, exist_ok=True)
    got, token = 0, None
    while True:
        kw = {"Bucket": SITE_BUCKET, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        page = s3.list_objects_v2(**kw)
        for o in page.get("Contents", []):
            name = os.path.basename(o["Key"])
            if not name:
                continue
            dest = os.path.join(FIXTURES, name)
            if os.path.isfile(dest) and os.path.getsize(dest) == o["Size"]:
                got += 1                       # warm container, already staged
                continue
            s3.download_file(SITE_BUCKET, o["Key"], dest)
            got += 1
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")

    if not got:
        raise DeployError(
            "FIXTURES_PREFIX=%r is set but nothing is stored there. Either "
            "upload the fixtures or unset it for real telemetry only." % prefix)

    dockets = len([f for f in os.listdir(FIXTURES) if f.endswith(".dispenses.json")])
    os.environ["BLUEICE_EXTRA"] = FIXTURES
    log("staged %d fixture file(s), including %d dispensing docket(s)" % (got, dockets))
    return got


# ------------------------------------------------------------------- publish


def publish(build_id):
    """Stage all three files, then copy them into place (review item 4).

    Uploading straight to the final keys leaves a window of several seconds --
    track.js alone is 8.6 MB -- in which a viewer can load a new index.html
    against a stale track.js. Staging first and then server-side copying
    shrinks that window to the time of three metadata operations.

    Every file must exist before anything is copied: a partial build must not
    half-replace a working dashboard.
    """
    staged = []
    for src_name, key, ctype, cache in PUBLISH:
        path = os.path.join(SITE, src_name)
        if not os.path.isfile(path):
            packaged = os.path.join(HERE, "dashboard", src_name)
            if os.path.isfile(packaged):
                path = packaged                  # vehicle.html ships in the zip
            else:
                raise DeployError(
                    "build produced no %s -- refusing to publish a partial "
                    "dashboard" % src_name)
        staged.append((path, key, ctype, cache))

    stage_keys = []
    for path, key, ctype, cache in staged:
        skey = "%s%s/%s" % (STAGE_PREFIX, build_id, key)
        with open(path, "rb") as fh:
            s3.put_object(Bucket=SITE_BUCKET, Key=skey, Body=fh.read(),
                          ContentType=ctype, CacheControl=cache)
        stage_keys.append((skey, key, ctype, cache))
        log("  staged    %-12s %8.2f MB" % (key, os.path.getsize(path) / 1048576))

    published = []
    for skey, key, ctype, cache in stage_keys:
        s3.copy_object(Bucket=SITE_BUCKET, Key=key,
                       CopySource={"Bucket": SITE_BUCKET, "Key": skey},
                       ContentType=ctype, CacheControl=cache,
                       MetadataDirective="REPLACE")
        published.append("/" + key)
    log("  published %d file(s) from build %s" % (len(published), build_id))

    for skey, _k, _c, _ca in stage_keys:        # tidy up; not fatal if it fails
        try:
            s3.delete_object(Bucket=SITE_BUCKET, Key=skey)
        except ClientError:
            pass
    return published


# -------------------------------------------------------------------- handler


def handler(event, context):
    t0 = time.time()
    request_id = getattr(context, "aws_request_id", "local-%d" % int(t0))
    force = os.environ.get("FORCE_REBUILD") == "1" or (
        isinstance(event, dict) and event.get("force") is True)

    for d in (TMP, SITE, INBOX):
        os.makedirs(d, exist_ok=True)

    if not acquire_lock(request_id):
        return {"status": "skipped-locked",
                "seconds": round(time.time() - t0, 1)}

    try:
        cfg = write_config()
        st = fetch_state()
        live = bucket_etags()
        log("bucket holds %d log object(s)" % len(live))

        if not force and unchanged(st, live):
            log("nothing changed (%.1fs)" % (time.time() - t0))
            return {"status": "unchanged", "objects": len(live),
                    "seconds": round(time.time() - t0, 1)}

        stage_fixtures()

        state = sync.load_state()
        pruned = prune_deleted(state, live)
        sync.pull(cfg, state)

        if not sync.build(cfg, state):
            raise DeployError(
                "the build produced nothing. State has NOT been saved, so the "
                "next run will retry this telemetry.")

        build_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        paths = publish(build_id)

        # ONLY NOW (review item 3). Everything below this line is safe to lose:
        # if saving state fails the next run simply rebuilds and republishes
        # the same thing, which is wasteful but correct. If it were saved
        # earlier, a failed publish would mark this telemetry as done and the
        # dashboard would silently stay stale for ever.
        sync.save_state(state)
        save_state()

        secs = round(time.time() - t0, 1)
        log("done in %ss" % secs)
        return {"status": "built", "objects": len(live), "pruned": pruned,
                "published": paths, "build": build_id, "seconds": secs}

    except Exception as exc:
        # Re-raise so the invocation is recorded as a failure and shows up in
        # metrics and alarms. Returning a success shape here is exactly how a
        # broken pipeline stays invisible (review item 5).
        log("FAILED after %.1fs: %s: %s"
            % (time.time() - t0, type(exc).__name__, exc))
        raise
    finally:
        release_lock()
