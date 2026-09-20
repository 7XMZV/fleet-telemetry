#!/usr/bin/env python3
"""
Deploy the BlueICE fleet dashboard.

    python deploy.py --dry-run --profile blueice-deploy     # print, change nothing
    python deploy.py --profile blueice-deploy               # deploy
    python deploy.py --profile blueice-deploy --code-only   # push new code only

REVISED FOR REVIEW -- this version performs NO administrator operations.

Removed at the administrator's request, because these are admin-controlled:

  * creating or editing IAM roles, and attaching IAM policies
  * creating the site bucket
  * changing the site bucket's policy or public-access settings
  * creating a CloudFront Origin Access Control

Also removed because the permission was not granted:

  * creating or publishing a CloudFront Function (the shared-password gate)

The script now REUSES these pre-created resources and fails loudly if any of
them is missing, rather than trying to create them:

    site bucket      blueice-fleet-site
    execution role   arn:aws:iam::123456789012:role/blueice-fleet-build-role
    OAC              E25UBTALZV3Q69

What it still does, all of it scoped to the named resources above:

    1. package    build function.zip and the pycryptodome layer locally
    2. fixtures   upload the synthetic demo logs into the existing site bucket
    3. layer      publish a new version of blueice-pycryptodome
    4. function   create or update blueice-fleet-build
    5. schedule   create blueice-fleet-build-5min and point it at the function

It does NOT create a CloudFront distribution. The administrator creates the
authenticated one -- see ADMIN-STEPS.md.

Production is real telemetry only. --with-fixtures adds the synthetic
demonstration machines, for a demo deployment.
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MINE = os.path.dirname(HERE)
PROJECT = os.path.dirname(MINE)

REGION = "eu-north-1"
ACCOUNT = "123456789012"

# ---- pre-created by the administrator. Reused, never created. --------------
SITE_BUCKET = "blueice-fleet-site"
EXEC_ROLE_ARN = "arn:aws:iam::%s:role/blueice-fleet-build-role" % ACCOUNT
OAC_ID = "E25UBTALZV3Q69"

# ---- names fixed by the administrator --------------------------------------
RAW_BUCKET = "your-telemetry-bucket"
RAW_PREFIX = "device-logs/"
FN_NAME = "blueice-fleet-build"
LAYER_NAME = "blueice-pycryptodome"
RULE_NAME = "blueice-fleet-build-5min"
# The decryption key is never in code or in an environment variable in the
# clear. The function is given the NAME of a Secrets Manager secret and fetches
# it at cold start. Override with BLUEICE_KEY_SECRET when deploying.
KEY_SECRET = os.environ.get("BLUEICE_KEY_SECRET", "blueice/fleet/log-key")

RUNTIME = "python3.13"
# Cold start measured locally at 118 s against the real telemetry bucket:
# 98 MB of logs plus 56 MB of fixtures downloaded, 2.06 M records decoded, an
# 8.6 MB track.js written. The timeout is several times that so a slow S3 day
# cannot leave a half-published dashboard.
TIMEOUT_S = 600
MEMORY_MB = 3008          # also buys CPU; the build is CPU-bound
EPHEMERAL_MB = 2048       # /tmp: ~160 MB of inputs and outputs, plus room


class Runner:
    """Every AWS call goes through here, so --dry-run is honest."""

    def __init__(self, dry, profile):
        self.dry = dry
        self.profile = profile
        self.step = 0

    def aws(self, *args, allow_fail=False, quiet=False):
        cmd = ["aws", "--region", REGION]
        if self.profile:
            cmd += ["--profile", self.profile]
        cmd += list(args)
        shown = " ".join(a if " " not in a else '"%s"' % a for a in cmd)
        if self.dry:
            print("    would run: " + shown[:200])
            return None
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            if allow_fail:
                return None
            raise SystemExit("\nFAILED: %s\n%s%s" % (shown, r.stdout, r.stderr))
        if r.stdout.strip() and not quiet:
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                return r.stdout.strip()
        return {}

    def read(self, *args):
        """A read that may legitimately find nothing."""
        if self.dry:
            return None
        return self.aws(*args, allow_fail=True)

    def say(self, msg):
        self.step += 1
        print("\n[%d] %s" % (self.step, msg))


# ------------------------------------------------------------------ preflight


def preflight(r):
    """Confirm every pre-created resource is there before changing anything.

    Without this the script fails halfway with an AWS error that does not say
    which side is at fault -- the deployment or the setup.
    """
    r.say("preflight: the resources the administrator pre-created")
    if r.dry:
        print("    would check: site bucket, execution role, OAC")
        return

    ok = True
    if r.read("s3api", "head-bucket", "--bucket", SITE_BUCKET) is None:
        print("    [MISSING] s3://%s" % SITE_BUCKET); ok = False
    else:
        print("    [ok] site bucket   s3://%s" % SITE_BUCKET)

    role = r.read("iam", "get-role", "--role-name", EXEC_ROLE_ARN.split("/")[-1])
    if not role:
        print("    [MISSING] %s" % EXEC_ROLE_ARN); ok = False
    else:
        print("    [ok] exec role     %s" % EXEC_ROLE_ARN)

    oac = r.read("cloudfront", "get-origin-access-control", "--id", OAC_ID)
    if not oac:
        print("    [MISSING] OAC %s" % OAC_ID); ok = False
    else:
        print("    [ok] OAC           %s" % OAC_ID)

    if not ok:
        raise SystemExit(
            "\nOne or more pre-created resources is missing or not visible to\n"
            "this role. Nothing has been changed. Ask the administrator to\n"
            "confirm the resource exists and that the deploy role can read it.")


# ---------------------------------------------------------------------- steps


def step_package(r):
    r.say("package function.zip and layer.zip")
    if r.dry:
        print("    would run: python package.py")
        return
    out = subprocess.run([sys.executable, os.path.join(HERE, "package.py")],
                         capture_output=True, text=True)
    print(out.stdout.rstrip())
    if out.returncode != 0:
        raise SystemExit(out.stderr)


def step_fixtures(r):
    """The demo machines and the two reference shifts.

    49 MB and 4 MB -- too big for a function zip, so they live in the site
    bucket and the function stages them into /tmp. Without them the dashboard
    is four real machines and no fuel data at all, because no real machine has
    a fuel sensor fitted yet.

    This writes objects into an EXISTING bucket. It does not create the bucket
    and does not touch its policy or access settings.
    """
    # *.dispenses.json too (review item 7). The fuel reconciliation resolves
    # <stem>.dispenses.json beside its log; without the dockets every demo
    # machine reconciles against zero deliveries and reports fuel missing that
    # was in fact pumped in -- a plausible-looking wrong answer.
    found = (glob.glob(os.path.join(MINE, "demo", "*.txt"))
             + glob.glob(os.path.join(MINE, "demo", "*.dispenses.json"))
             + glob.glob(os.path.join(PROJECT, "Not mine", "*_LOG*.TXT"))
             + glob.glob(os.path.join(PROJECT, "Not mine", "*_LOG*.txt")))
    seen, files = set(), []
    for f in sorted(found):                    # Windows matches .TXT and .txt
        k = os.path.normcase(os.path.abspath(f))
        if k not in seen:
            seen.add(k)
            files.append(f)
    mb = sum(os.path.getsize(f) for f in files) / 1048576
    r.say("fixtures -> s3://%s/_fixtures/  (%d files, %.0f MB)"
          % (SITE_BUCKET, len(files), mb))
    for f in files:
        r.aws("s3", "cp", f,
              "s3://%s/_fixtures/%s" % (SITE_BUCKET, os.path.basename(f)),
              "--only-show-errors", quiet=True)
    if not r.dry:
        dockets = len([f for f in files if f.endswith(".dispenses.json")])
        print("    uploaded %d file(s), including %d dispensing docket(s)"
              % (len(files), dockets))


def step_layer(r):
    r.say("publish layer %s" % LAYER_NAME)
    out = r.aws("lambda", "publish-layer-version",
                "--layer-name", LAYER_NAME,
                "--description", "pycryptodome for the AES-128-ECB log decoder",
                "--compatible-runtimes", RUNTIME,
                "--zip-file", "fileb://" + os.path.join(HERE, "dist", "layer.zip"))
    arn = (out or {}).get("LayerVersionArn", "<dry-run>")
    print("    " + str(arn))
    return arn


def step_function(r, layer_arn, code_only, with_fixtures):
    r.say("function %s" % FN_NAME)
    zip_arg = "fileb://" + os.path.join(HERE, "dist", "function.zip")
    # FIXTURES_PREFIX is deliberately absent: production is real telemetry
    # only (review item 6). --with-fixtures adds it for a demonstration
    # deployment, and says so loudly.
    pairs = ["RAW_BUCKET=%s" % RAW_BUCKET, "RAW_PREFIX=%s" % RAW_PREFIX,
             "SITE_BUCKET=%s" % SITE_BUCKET]
    if KEY_SECRET:
        pairs.append("BLUEICE_KEY_SECRET=%s" % KEY_SECRET)
    if with_fixtures:
        pairs.append("FIXTURES_PREFIX=_fixtures/")
    env = "Variables={%s}" % ",".join(pairs)

    exists = r.read("lambda", "get-function", "--function-name", FN_NAME)
    if exists is None and not r.dry:
        # --role points at the PRE-CREATED execution role. This script never
        # creates or edits a role; it only references one by ARN.
        r.aws("lambda", "create-function", "--function-name", FN_NAME,
              "--runtime", RUNTIME, "--role", EXEC_ROLE_ARN,
              "--handler", "lambda_handler.handler",
              "--timeout", str(TIMEOUT_S), "--memory-size", str(MEMORY_MB),
              "--ephemeral-storage", "Size=%d" % EPHEMERAL_MB,
              "--layers", layer_arn, "--environment", env,
              "--zip-file", zip_arg)
        print("    created, using pre-created role %s" % EXEC_ROLE_ARN)
        return

    r.aws("lambda", "update-function-code", "--function-name", FN_NAME,
          "--zip-file", zip_arg)
    print("    code updated")
    if code_only:
        return
    if not r.dry:
        r.aws("lambda", "wait", "function-updated", "--function-name", FN_NAME)
    r.aws("lambda", "update-function-configuration", "--function-name", FN_NAME,
          "--timeout", str(TIMEOUT_S), "--memory-size", str(MEMORY_MB),
          "--ephemeral-storage", "Size=%d" % EPHEMERAL_MB,
          "--layers", layer_arn, "--environment", env)
    print("    configuration updated")


def step_schedule(r):
    r.say("schedule %s (every 5 minutes)" % RULE_NAME)
    fn_arn = "arn:aws:lambda:%s:%s:function:%s" % (REGION, ACCOUNT, FN_NAME)
    r.aws("events", "put-rule", "--name", RULE_NAME,
          "--schedule-expression", "rate(5 minutes)",
          "--description", "Rebuild the BlueICE fleet dashboard when new logs land")
    r.aws("lambda", "add-permission", "--function-name", FN_NAME,
          "--statement-id", "events-invoke", "--action", "lambda:InvokeFunction",
          "--principal", "events.amazonaws.com",
          "--source-arn", "arn:aws:events:%s:%s:rule/%s" % (REGION, ACCOUNT, RULE_NAME),
          allow_fail=True)              # already present on a re-run
    r.aws("events", "put-targets", "--rule", RULE_NAME,
          "--targets", json.dumps([{"Id": "1", "Arn": fn_arn}]))
    print("    armed -- most runs exit in about a second with nothing to do")


# ----------------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print every call without making any")
    ap.add_argument("--profile", default=os.environ.get("AWS_PROFILE"),
                    help="named AWS CLI profile (blueice-deploy)")
    ap.add_argument("--code-only", action="store_true",
                    help="push new function code and nothing else")
    ap.add_argument("--with-fixtures", action="store_true",
                    help="also publish the synthetic demonstration machines. "
                         "OFF by default -- production is real telemetry only.")
    a = ap.parse_args()

    r = Runner(a.dry_run, a.profile)
    print("BlueICE fleet dashboard -> AWS %s" % REGION)
    print("profile: %s%s" % (a.profile or "(default)",
                             "   [DRY RUN]" if a.dry_run else ""))
    print("no IAM, no bucket creation, no bucket policy, no OAC, no CF function")
    print("key: Secrets Manager secret %r (never in code or plain env)" % KEY_SECRET)

    step_package(r)
    if a.code_only:
        step_function(r, None, code_only=True, with_fixtures=False)
        print("\ncode pushed.")
        return

    preflight(r)
    if a.with_fixtures:
        step_fixtures(r)
    else:
        print("\n[i] Synthetic fixtures NOT uploaded (--with-fixtures to include).")
        print("    Production shows real telemetry only. No real machine has a")
        print("    fuel sensor yet, so the fuel panel will read 'no fuel sensor'.")
    layer_arn = step_layer(r)
    step_function(r, layer_arn, code_only=False,
                  with_fixtures=a.with_fixtures)
    step_schedule(r)

    print("\n[i] No CloudFront distribution is created by this script.")
    print("    The administrator creates the authenticated distribution --")
    print("    see ADMIN-STEPS.md. Until then the dashboard is built into the")
    print("    private bucket every 5 minutes and is not publicly reachable.")

    print("\nto build now rather than waiting for the schedule:")
    print("  aws lambda invoke --function-name %s \\" % FN_NAME)
    print("    --payload '{\"force\":true}' --profile %s out.json"
          % (a.profile or "<profile>"))


if __name__ == "__main__":
    main()
