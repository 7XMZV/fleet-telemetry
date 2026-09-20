#!/usr/bin/env python3
"""
Tests for the review items, run against the packaged function.

    set BLUEICE_LOG_KEY=<key>
    python test_handler.py

Unpacks dist/function.zip into a /var/task stand-in, so it exercises the
ARTEFACT rather than the working tree. Reads against the real telemetry bucket
are real; the site bucket is simulated in memory, because it does not have a
test copy and these tests must not write to production.

Covers:
    3  state is saved only after a successful publish
    4  overlapping executions are refused; publish is staged then copied
    5  deleted source logs are pruned; failures raise
    6  fixtures are off unless asked for
    7  dispensing dockets travel with the demo logs
"""

import datetime as dt
import io
import json
import os
import shutil
import sys
import tempfile
import traceback
import zipfile

AWS = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(tempfile.gettempdir(), "blueice_task_test")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -- " + detail) if detail and not cond else ""))


def fresh_task():
    shutil.rmtree(TASK, ignore_errors=True)
    os.makedirs(TASK)
    with zipfile.ZipFile(os.path.join(AWS, "dist", "function.zip")) as z:
        z.extractall(TASK)


class FakeS3:
    """The site bucket in memory; the telemetry bucket for real."""

    def __init__(self, real, site, fail_put_on=None):
        self._real = real
        self.site = site
        self.store = {}
        self.fail_put_on = fail_put_on
        self.puts = []
        self.copies = []
        self.exceptions = real.exceptions

    def __getattr__(self, n):
        return getattr(self._real, n)

    # ---- site bucket simulated -------------------------------------------
    def _site(self, kw):
        return kw.get("Bucket") == self.site

    def get_object(self, **kw):
        if self._site(kw):
            k = kw["Key"]
            if k not in self.store:
                raise self._real.exceptions.NoSuchKey(
                    {"Error": {"Code": "NoSuchKey"}}, "GetObject")
            return {"Body": io.BytesIO(self.store[k])}
        return self._real.get_object(**kw)

    def put_object(self, **kw):
        if self._site(kw):
            k = kw["Key"]
            if self.fail_put_on and self.fail_put_on in k:
                raise RuntimeError("simulated upload failure on " + k)
            if kw.get("IfNoneMatch") == "*" and k in self.store:
                from botocore.exceptions import ClientError
                raise ClientError({"Error": {"Code": "PreconditionFailed"}},
                                  "PutObject")
            body = kw["Body"]
            self.store[k] = body if isinstance(body, bytes) else body.read()
            self.puts.append(k)
            return {}
        raise AssertionError("unexpected write to " + str(kw.get("Bucket")))

    def copy_object(self, **kw):
        if self._site(kw):
            src = kw["CopySource"]["Key"]
            self.store[kw["Key"]] = self.store[src]
            self.copies.append((src, kw["Key"]))
            return {}
        raise AssertionError("unexpected copy")

    def delete_object(self, **kw):
        if self._site(kw):
            self.store.pop(kw["Key"], None)
            return {}
        raise AssertionError("unexpected delete")

    def head_object(self, **kw):
        if self._site(kw):
            if kw["Key"] not in self.store:
                from botocore.exceptions import ClientError
                raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
            return {"LastModified": dt.datetime.now(dt.timezone.utc)}
        return self._real.head_object(**kw)

    def list_objects_v2(self, **kw):
        if self._site(kw):
            pre = kw.get("Prefix", "")
            items = [{"Key": k, "Size": len(v)}
                     for k, v in self.store.items() if k.startswith(pre)]
            return {"Contents": items, "IsTruncated": False}
        return self._real.list_objects_v2(**kw)

    def download_file(self, bucket, key, dest):
        if bucket == self.site:
            with open(dest, "wb") as fh:
                fh.write(self.store[key])
            return
        return self._real.download_file(bucket, key, dest)


def load_handler(env):
    for m in list(sys.modules):
        if m in ("lambda_handler", "blueice_sync", "decode_log"):
            del sys.modules[m]
    os.environ.update(env)
    for v in ("BLUEICE_OUT", "BLUEICE_STATE", "BLUEICE_CONFIG", "BLUEICE_EXTRA"):
        os.environ.pop(v, None)
    sys.path.insert(0, os.path.join(TASK, "sync"))
    sys.path.insert(0, TASK)
    import lambda_handler as H
    return H


BASE_ENV = {
    "RAW_BUCKET": "your-telemetry-bucket",
    "RAW_PREFIX": "device-logs/",
    "SITE_BUCKET": "blueice-fleet-site-TEST",
    "AWS_REGION": "eu-north-1",
}


class Ctx:
    aws_request_id = "test-request-1"


def main():
    if not os.environ.get("BLUEICE_LOG_KEY"):
        sys.exit("set BLUEICE_LOG_KEY first -- the key is no longer in the code")

    fresh_task()
    import boto3
    real = boto3.client("s3")

    print("\n--- 6: fixtures OFF by default ---")
    env = dict(BASE_ENV)
    env.pop("FIXTURES_PREFIX", None)
    os.environ.pop("FIXTURES_PREFIX", None)
    H = load_handler(env)
    H.s3 = FakeS3(real, env["SITE_BUCKET"])
    n = H.stage_fixtures()
    check("stage_fixtures() is a no-op when FIXTURES_PREFIX is unset", n == 0)
    check("BLUEICE_EXTRA not set, so only real telemetry is built",
          "BLUEICE_EXTRA" not in os.environ)

    print("\n--- 7: dispensing dockets are uploaded with the demo logs ---")
    src = io.open(os.path.join(AWS, "deploy.py"), encoding="utf-8").read()
    check("deploy.py globs *.dispenses.json", "*.dispenses.json" in src)
    # The dockets themselves are demonstration data and are deliberately not in
    # the review package, so this check is skipped in a clean-room run rather
    # than failed. The assertion that matters -- that deploy.py uploads them --
    # is above and always runs.
    demo = os.path.join(os.path.dirname(AWS), "demo")
    if os.path.isdir(demo):
        have = len([f for f in os.listdir(demo) if f.endswith(".dispenses.json")])
        check("demo dockets present to upload (%d)" % have, have > 0)
    else:
        print("  [SKIP] demo dockets not in this tree (expected in a clean "
              "extraction -- demo data is not part of the review package)")

    print("\n--- 4: only one execution at a time ---")
    H = load_handler(dict(BASE_ENV))
    fake = FakeS3(real, BASE_ENV["SITE_BUCKET"])
    H.s3 = fake
    first = H.acquire_lock("req-A")
    second = H.acquire_lock("req-B")
    check("first caller takes the lock", first is True)
    check("second caller is refused while it is held", second is False)
    H.release_lock()
    third = H.acquire_lock("req-C")
    check("lock is reusable once released", third is True)
    H.release_lock()

    print("\n--- 4: publish stages, then copies into place ---")
    H = load_handler(dict(BASE_ENV))
    fake = FakeS3(real, BASE_ENV["SITE_BUCKET"])
    H.s3 = fake
    os.makedirs(H.SITE, exist_ok=True)
    for n_, body in (("data.js", b"D"), ("track.js", b"T"), ("vehicle.html", b"H")):
        io.open(os.path.join(H.SITE, n_), "wb").write(body)
    published = H.publish("BUILDID")
    staged = [k for k in fake.puts if k.startswith("_staging/")]
    check("all three files staged first", len(staged) == 3, str(staged))
    check("then copied to their final keys", len(fake.copies) == 3)
    check("index.html is published last",
          fake.copies[-1][1] == "index.html", str(fake.copies))
    check("staging is cleaned up",
          not [k for k in fake.store if k.startswith("_staging/")])
    check("published the three expected paths",
          sorted(published) == ["/data.js", "/index.html", "/track.js"])

    print("\n--- 4/3: a partial build refuses to publish ---")
    H = load_handler(dict(BASE_ENV))
    fake = FakeS3(real, BASE_ENV["SITE_BUCKET"])
    H.s3 = fake
    shutil.rmtree(H.SITE, ignore_errors=True)
    os.makedirs(H.SITE, exist_ok=True)
    io.open(os.path.join(H.SITE, "data.js"), "wb").write(b"D")   # track.js missing
    try:
        H.publish("BUILDID2")
        check("partial build is refused", False, "it published anyway")
    except H.DeployError as e:
        check("partial build is refused", "track.js" in str(e))
    check("nothing was written during the refused publish",
          not [k for k in fake.store if k in ("data.js", "track.js", "index.html")])

    print("\n--- 3: state is written only after a successful publish ---")
    H = load_handler(dict(BASE_ENV))
    fake = FakeS3(real, BASE_ENV["SITE_BUCKET"], fail_put_on="_staging/")
    H.s3 = fake
    shutil.rmtree(H.SITE, ignore_errors=True)
    os.makedirs(H.SITE, exist_ok=True)
    for n_, body in (("data.js", b"D"), ("track.js", b"T"), ("vehicle.html", b"H")):
        io.open(os.path.join(H.SITE, n_), "wb").write(body)
    io.open(os.environ["BLUEICE_STATE"], "w").write('{"objects":{}}')
    try:
        H.publish("BUILDID3")
    except Exception:
        pass
    check("state object absent after a failed upload",
          H.STATE_KEY not in fake.store,
          "state was saved despite the upload failing")

    print("\n--- 5: deleted source logs are pruned from state ---")
    H = load_handler(dict(BASE_ENV))
    st = {"objects": {
        "device-logs/VEH_001/a_LOG000.TXT": {"etag": "x"},
        "device-logs/VEH_001/gone_LOG001.TXT": {"etag": "y"},
        "local-reference_LOG000.txt": {"etag": "z"},     # not from the bucket
    }}
    pruned = H.prune_deleted(st, {"device-logs/VEH_001/a_LOG000.TXT": "x"})
    check("the vanished object is pruned", pruned == 1)
    check("the surviving object is kept",
          "device-logs/VEH_001/a_LOG000.TXT" in st["objects"])
    check("non-bucket entries are left alone",
          "local-reference_LOG000.txt" in st["objects"])

    print("\n--- 5: failures raise instead of returning success ---")
    H = load_handler(dict(BASE_ENV))

    class Denied(FakeS3):
        def get_object(self, **kw):
            if self._site(kw):
                from botocore.exceptions import ClientError
                raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")
            return self._real.get_object(**kw)

    H.s3 = Denied(real, BASE_ENV["SITE_BUCKET"])
    try:
        H.fetch_state()
        check("a denied state read raises", False, "it returned instead")
    except H.DeployError as e:
        check("a denied state read raises", "could not read state" in str(e))

    H = load_handler(dict(BASE_ENV, FIXTURES_PREFIX="_fixtures/"))
    H.s3 = FakeS3(real, BASE_ENV["SITE_BUCKET"])       # empty prefix
    try:
        H.stage_fixtures()
        check("fixtures switched on but absent raises", False, "it continued")
    except H.DeployError as e:
        check("fixtures switched on but absent raises", "nothing is stored" in str(e))

    print("\n--- 9: the packaged artefact carries no key ---")
    with zipfile.ZipFile(os.path.join(AWS, "dist", "function.zip")) as z:
        blob = b"".join(z.read(n) for n in z.namelist())
    check("no decryption key in function.zip", b"BlueIceKey" not in blob)
    check("decode_log reads the key from the environment",
          b"BLUEICE_KEY_SECRET" in blob)

    print("\n" + "=" * 58)
    print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("    FAILED: " + f)
    print("=" * 58)
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
