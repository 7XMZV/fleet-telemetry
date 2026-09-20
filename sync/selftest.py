#!/usr/bin/env python3
"""
Exercise the sync end to end without touching AWS.

There is no bucket, no boto3 and no credentials on the machine this was written
on, so the S3 code path would otherwise ship completely unexercised. This drives
it with a stub client that implements the three S3 calls the tool actually uses,
over a fake bucket built from the real reference log. Everything below the
client -- listing, pagination, idempotency, hash dedup, quarantine, selection,
merge -- is the real code.

What this does NOT prove: that boto3 authenticates, that the bucket policy is
right, or that a real ListObjectsV2 paginates the way the stub does. Run
`blueice_sync.py check` against the real bucket for that.

    python selftest.py
"""

import io
import json
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MINE = os.path.dirname(HERE)
PROJECT = os.path.dirname(MINE)
sys.path.insert(0, HERE)
sys.path.insert(0, MINE)

import blueice_sync as S                                        # noqa: E402
from decode_log import parse, reject_reason                    # noqa: E402

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(("  [PASS] " if ok else "  [FAIL] ") + label
          + ("" if ok else "   got " + repr(got) + ", want " + repr(want)))
    if not ok:
        FAILURES.append(label)


def note(label, value):
    print("  [ -- ] " + label + "   " + str(value))


# ---------------------------------------------------------------------------
# A fake bucket, built from the real log
# ---------------------------------------------------------------------------

HEADER_BYTES = 4 + 2 + 48        # magic + length prefix + header payload
FRAME_BYTES = 2 + 32             # length prefix + record payload


def slice_log(raw, first_frame, last_frame):
    """Cut a valid log file out of a bigger one, on frame boundaries.

    The format is magic + header + N record frames + a 0x0000 terminator
    (FORMAT_SPEC.md). A slice on frame boundaries is therefore itself a valid
    log, which is what lets this build overlapping uploads to test dedup with.
    """
    a = HEADER_BYTES + first_frame * FRAME_BYTES
    b = HEADER_BYTES + last_frame * FRAME_BYTES
    return raw[:HEADER_BYTES] + raw[a:b] + b"\x00\x00"


def build_fake_bucket(root, reference):
    raw = open(reference, "rb").read()
    os.makedirs(root, exist_ok=True)
    keys = {}

    # Two overlapping uploads from the same device: frames 0-20000 and
    # 10000-30000. The 10000-frame overlap is what cross-file dedup must catch.
    a = slice_log(raw, 0, 20000)
    b = slice_log(raw, 10000, 30000)
    keys["logs/862636058411560/862636058411560_2026_09_01_05_43_32_PART001.TXT"] = a
    keys["logs/862636058411560/862636058411560_2026_09_01_06_43_32_PART002.TXT"] = b

    # The same bytes as PART001 under a second key -- a re-upload. Must be
    # recognised by hash and kept out of the build.
    keys["logs/reupload/862636058411560_2026_09_01_05_43_32_PART001.TXT"] = a

    # Something that is not a log at all.
    keys["logs/862636058411560/862636058411560_garbage.TXT"] = b"not a log" * 200

    # Something that is not a log file by extension -- must be ignored entirely.
    keys["logs/manifest.json"] = b'{"note":"ignored"}'

    for k, v in keys.items():
        p = os.path.join(root, k.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(v)
    return keys


class StubS3:
    """The three calls blueice_sync makes on an S3 client, and nothing else.

    Paginates at 2 keys per page on purpose, so the continuation-token loop is
    actually executed rather than assumed.
    """

    PAGE = 2

    def __init__(self, root):
        self.root = root
        self.calls = {"list": 0, "download": 0, "head": 0}

    def _all(self):
        out = []
        for base, _d, files in os.walk(self.root):
            for f in sorted(files):
                p = os.path.join(base, f)
                out.append((os.path.relpath(p, self.root).replace("\\", "/"), p))
        return sorted(out)

    def head_bucket(self, Bucket):
        self.calls["head"] += 1
        return {}

    def list_objects_v2(self, Bucket, Prefix="", ContinuationToken=None, MaxKeys=None):
        self.calls["list"] += 1
        items = [(k, p) for k, p in self._all() if k.startswith(Prefix)]
        start = int(ContinuationToken) if ContinuationToken else 0
        page = items[start:start + self.PAGE]
        contents = [{"Key": k, "Size": os.path.getsize(p),
                     "ETag": '"' + str(os.path.getsize(p)) + '"',
                     "LastModified": "2026-09-01T05:43:32+00:00"} for k, p in page]
        more = start + self.PAGE < len(items)
        return {"Contents": contents, "KeyCount": len(contents),
                "IsTruncated": more,
                "NextContinuationToken": str(start + self.PAGE) if more else None}

    def download_file(self, Bucket, Key, dest):
        self.calls["download"] += 1
        shutil.copy2(os.path.join(self.root, Key.replace("/", os.sep)), dest)


# ---------------------------------------------------------------------------

def main():
    reference = None
    for d in (os.path.join(PROJECT, "Not mine"), PROJECT, MINE):
        import glob
        hits = sorted(glob.glob(os.path.join(d, "*_LOG*.TXT")))
        if hits:
            reference = hits[0]
            break
    if not reference:
        raise SystemExit("no reference log found to build the fake bucket from")

    tmp = tempfile.mkdtemp(prefix="blueice-selftest-")
    bucket_root = os.path.join(tmp, "bucket")
    inbox = os.path.join(tmp, "inbox")
    build_fake_bucket(bucket_root, reference)
    stub = StubS3(bucket_root)

    cfg = dict(S.DEFAULTS)
    cfg["source"] = dict(S.DEFAULTS["source"], type="s3", bucket="fake-bucket",
                         prefix="logs/")
    cfg["inbox"] = inbox
    state = {"objects": {}, "runs": []}

    print("\n1. vendor files are untouched")
    before = {p: os.stat(os.path.join(PROJECT, "Not mine", p)).st_mtime_ns
              for p in os.listdir(os.path.join(PROJECT, "Not mine"))}

    print("\n2. first pull from the (stubbed) bucket")
    added = S.pull(cfg, state, client=stub)
    check("two usable logs ingested", added, 2)
    check("pagination ran (5 keys at 2/page)", stub.calls["list"], 3)
    usable = S.usable(state)
    check("usable files in state", len(usable), 2)
    quarantined = [v for v in state["objects"].values()
                   if v.get("usable") is False and not v.get("duplicate_of")]
    check("garbage quarantined", len(quarantined), 1)
    dupes = [v for v in state["objects"].values() if v.get("duplicate_of")]
    check("re-upload caught by hash", len(dupes), 1)
    check("manifest.json ignored",
          any("manifest" in k for k in state["objects"]), False)
    check("nothing landed outside inbox/",
          all(os.path.abspath(v["local"]).startswith(os.path.abspath(inbox))
              for v in state["objects"].values() if v.get("local")), True)

    print("\n3. second pull is a no-op (idempotency)")
    downloads_before = stub.calls["download"]
    added2 = S.pull(cfg, state, client=stub)
    check("nothing new", added2, 0)
    check("nothing re-downloaded", stub.calls["download"], downloads_before)

    print("\n4. state survives a save/load round trip")
    S.STATE_PATH = os.path.join(tmp, "state.json")
    S.save_state(state)
    reloaded = S.load_state()
    check("objects preserved", len(reloaded["objects"]), len(state["objects"]))

    print("\n5. selection")
    chosen, device = S.select_logs(cfg, state)
    check("device identified", device, "862636058411560")
    check("latest-day selects both parts", len(chosen), 2)
    cfg2 = dict(cfg, select="latest-file")
    check("latest-file selects one", len(S.select_logs(cfg2, state)[0]), 1)

    print("\n6. merge arithmetic -- the invariant that matters")
    sys.path.insert(0, os.path.join(MINE, "dashboard"))
    os.environ["BLUEICE_LOGS"] = reference
    import build_data                                            # noqa: E402
    one_rec, _ = parse(reference)
    one_valid = [r for r in one_rec if reject_reason(r) is None]
    m_rec, m_meta = build_data.parse_logs([reference])
    m_valid = [r for r in m_rec if reject_reason(r) is None]
    check("single log: framed unchanged", len(m_rec), len(one_rec))
    check("single log: valid unchanged", len(m_valid), len(one_valid))
    check("single log: no dedup applied", m_meta["duplicates"], 0)
    note("(the reference log holds 2 same-(ts,ms) valid pairs; they survive)",
         len(one_valid))

    paths = [f["local"] for f in chosen]
    two_rec, two_meta = build_data.parse_logs(paths)
    two_valid = [r for r in two_rec if reject_reason(r) is None]
    a_rec, _ = parse(paths[0])
    b_rec, _ = parse(paths[1])
    a_valid = [r for r in a_rec if reject_reason(r) is None]
    b_valid = [r for r in b_rec if reject_reason(r) is None]
    note("part A valid / part B valid", str(len(a_valid)) + " / " + str(len(b_valid)))
    note("overlap dropped", two_meta["duplicates"])
    check("merged valid = A + B - overlap",
          len(two_valid), len(a_valid) + len(b_valid) - two_meta["duplicates"])
    check("merged framed = A + B (invalid records never deduped)",
          len(two_rec), len(a_rec) + len(b_rec) - two_meta["duplicates"])
    check("overlap actually occurred", two_meta["duplicates"] > 0, True)

    print("\n7. local source is read-only")
    after = {p: os.stat(os.path.join(PROJECT, "Not mine", p)).st_mtime_ns
             for p in os.listdir(os.path.join(PROJECT, "Not mine"))}
    check("'Not mine/' unchanged", after, before)

    shutil.rmtree(tmp, ignore_errors=True)
    os.environ.pop("BLUEICE_LOGS", None)

    print("\n" + ("ALL PASSED" if not FAILURES
                  else str(len(FAILURES)) + " FAILED: " + ", ".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
