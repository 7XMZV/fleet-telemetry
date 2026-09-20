#!/usr/bin/env python3
"""
Build the two zips the deployment uploads.

    lambda-source/      the reviewed source tree  <-- the ONLY input
    dist/function.zip   that tree, zipped
    dist/layer.zip      pycryptodome for Lambda's Linux runtime

Run on any machine, Windows included: the layer is downloaded as a manylinux
wheel rather than compiled here.

    python package.py                # build from lambda-source/
    python package.py --sync-source  # refresh lambda-source/ from the project

REVIEW ITEM 1 -- builds directly from lambda-source/.

It used to assemble function.zip by reaching into the wider project through a
hardcoded file list. That meant the reviewed directory and the shipped artefact
were two different things: a file could be reviewed and not shipped, or shipped
and never reviewed, and nothing would report it.

Now lambda-source/ IS the build input. What is reviewed is what is packaged.
`--sync-source` is the only way to change it, and it prints every file it
copies so the change is visible in the diff.
"""

import argparse
import hashlib
import io
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
MINE = os.path.dirname(HERE)
SOURCE = os.path.join(HERE, "lambda-source")
DIST = os.path.join(HERE, "dist")

RUNTIME = "python3.13"
PY_VERSION = "3.13"
PLATFORM = "manylinux2014_x86_64"

# Pinned exactly. An unpinned dependency means the layer you reviewed and the
# layer that ships can be different builds, and nothing reports it.
PYCRYPTODOME = "pycryptodome==3.23.0"

# Zip entries normally carry the file's mtime, so two builds of identical
# content produce different bytes and a published SHA-256 proves nothing.
# Every entry is written with this fixed timestamp and fixed permissions, and
# entries are sorted, so the archive is byte-reproducible.
ZIP_EPOCH = (2026, 1, 1, 0, 0, 0)

# Where each file in lambda-source/ comes from, used only by --sync-source.
# The handler is authored here; everything else is shared with the project and
# must not be edited in two places.
PROVENANCE = {
    "lambda_handler.py":                 None,                   # authored here
    "decode_log.py":                     "decode_log.py",
    "fuel_reconcile.py":                 "fuel_reconcile.py",
    "sync/blueice_sync.py":              "sync/blueice_sync.py",
    "dashboard/build_datasets.py":       "dashboard/build_datasets.py",
    "dashboard/build_data.py":           "dashboard/build_data.py",
    "dashboard/build_track.py":          "dashboard/build_track.py",
    "dashboard/vehicle.html":            "dashboard/vehicle.html",
}

FORBIDDEN = [
    (b"BlueIceKey", "the decryption key"),
    (b"AKIA", "an AWS access key id"),
    (b"-----BEGIN", "a private key"),
]


def say(*a):
    print(*a, flush=True)


def add_deterministic(z, path, arcname):
    """Write one entry with a fixed timestamp and fixed mode.

    zipfile.write() stamps the file's mtime and permissions into the entry, so
    the same content built twice hashes differently. This makes the archive a
    pure function of its contents.
    """
    info = zipfile.ZipInfo(arcname.replace(os.sep, "/"), date_time=ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    with open(path, "rb") as fh:
        z.writestr(info, fh.read())


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sync_source():
    """Refresh lambda-source/ from the project, reporting every change."""
    say("refreshing lambda-source/ from the project")
    changed = 0
    for rel, origin in PROVENANCE.items():
        if origin is None:
            continue
        src = os.path.join(MINE, origin)
        dst = os.path.join(SOURCE, rel)
        if not os.path.isfile(src):
            raise SystemExit("missing project file: " + src)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.isfile(dst) and sha(src) == sha(dst):
            say("    unchanged  " + rel)
            continue
        shutil.copy2(src, dst)
        say("    UPDATED    %s   <- %s" % (rel, origin))
        changed += 1
    say("  %d file(s) changed" % changed)
    return changed


def check_source():
    """The reviewed tree must be complete and must carry no secrets."""
    missing = [r for r in PROVENANCE if not os.path.isfile(os.path.join(SOURCE, r))]
    if missing:
        raise SystemExit(
            "lambda-source/ is incomplete:\n  " + "\n  ".join(missing) +
            "\n\nRun:  python package.py --sync-source")

    drift = []
    for rel, origin in PROVENANCE.items():
        if origin is None:
            continue
        src = os.path.join(MINE, origin)
        if os.path.isfile(src) and sha(src) != sha(os.path.join(SOURCE, rel)):
            drift.append(rel)

    bad = []
    for root, _d, files in os.walk(SOURCE):
        for n in files:
            p = os.path.join(root, n)
            blob = open(p, "rb").read()
            for pat, what in FORBIDDEN:
                if pat in blob:
                    bad.append((os.path.relpath(p, SOURCE), what))
    if bad:
        for rel, what in bad:
            say("  [SECRET] %s contains %s" % (rel, what))
        raise SystemExit("refusing to package a secret")

    return drift


def build_function():
    out = os.path.join(DIST, "function.zip")
    entries = []
    for root, dirs, files in os.walk(SOURCE):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith(".pyc"):
                continue
            p = os.path.join(root, name)
            entries.append((os.path.relpath(p, SOURCE).replace(os.sep, "/"), p))
    entries.sort()                       # order must not depend on the filesystem
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, p in entries:
            add_deterministic(z, p, arc)
            n += 1
    say("  function.zip  %8.2f MB  (%d files, from lambda-source/)"
        % (os.path.getsize(out) / 1048576, n))
    return out


def build_layer():
    """pycryptodome for Lambda's Linux runtime.

    A prebuilt manylinux wheel, never compiled locally: a wheel built on
    Windows would be the wrong ABI and the layer would import-fail at runtime
    with a message pointing nowhere near the cause.
    """
    stage = os.path.join(DIST, "layer")
    shutil.rmtree(stage, ignore_errors=True)
    target = os.path.join(stage, "python")
    os.makedirs(target)

    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", PYCRYPTODOME,
         "--target", target, "--platform", PLATFORM,
         "--python-version", PY_VERSION, "--implementation", "cp",
         "--only-binary=:all:", "--quiet", "--no-compile"],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("pip failed building the layer:\n" + r.stdout + r.stderr)

    out = os.path.join(DIST, "layer.zip")
    entries = []
    for root, _d, files in os.walk(stage):
        for f in files:
            if f.endswith(".pyc"):
                continue
            p = os.path.join(root, f)
            entries.append((os.path.relpath(p, stage).replace(os.sep, "/"), p))
    entries.sort()
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, p in entries:
            add_deterministic(z, p, arc)
            n += 1
    say("  layer.zip     %8.2f MB  (%d files, %s, %s / %s)"
        % (os.path.getsize(out) / 1048576, n, PYCRYPTODOME, PLATFORM, RUNTIME))
    shutil.rmtree(stage, ignore_errors=True)
    return out


def verify(function_zip, layer_zip):
    ok = True
    with zipfile.ZipFile(function_zip) as z:
        names = set(z.namelist())
    for rel in PROVENANCE:
        if rel not in names:
            say("  ! function.zip is missing " + rel); ok = False
    with zipfile.ZipFile(layer_zip) as z:
        ln = z.namelist()
    if not any(n.startswith("python/Crypto/") for n in ln):
        say("  ! layer.zip has no python/Crypto/ -- Lambda will not import it")
        ok = False
    if not any(n.endswith(".so") for n in ln):
        say("  ! layer.zip has no compiled .so -- wrong wheel for Linux")
        ok = False
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sync-source", action="store_true",
                    help="refresh lambda-source/ from the project first")
    a = ap.parse_args()

    if a.sync_source:
        sync_source()
        say("")

    drift = check_source()
    if drift:
        say("  ! lambda-source/ differs from the project for:")
        for d in drift:
            say("      " + d)
        say("  ! packaging lambda-source/ AS REVIEWED. To adopt the project")
        say("    version instead, run: python package.py --sync-source")
        say("")

    for f in ("function.zip", "layer.zip"):
        p = os.path.join(DIST, f)
        if os.path.isfile(p):
            os.remove(p)
    os.makedirs(DIST, exist_ok=True)

    say("building from " + SOURCE)
    fz = build_function()
    lz = build_layer()
    if not verify(fz, lz):
        raise SystemExit("\npackaging FAILED -- see above")

    # The artefact hashes. Deterministic, so rebuilding this same source on any
    # machine reproduces them -- which is what makes publishing them useful.
    say("")
    for p in (fz, lz):
        say("  SHA-256  %s  %s" % (sha(p), os.path.basename(p)))
    say("\nok -- archives are byte-reproducible; a rebuild gives these hashes")


if __name__ == "__main__":
    main()
