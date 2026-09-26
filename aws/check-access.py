#!/usr/bin/env python3
"""
Can these credentials actually deploy the dashboard?

    python check-access.py --profile blueice-deploy

Run this the moment a new key arrives, BEFORE deploy.py. A half-finished
deployment that stopped because of one missing permission is much harder to
reason about than a list of what is missing up front.

Every check here is READ ONLY. Nothing is created, modified or deleted, so it
is safe to run against a production account as often as you like.
"""

import argparse
import json
import subprocess
import sys

REGION = "eu-north-1"
RAW_BUCKET = "your-telemetry-bucket"

# (label, argv, why it matters)  -- all reads; a denial here means the matching
# write in deploy.py would also be denied.
CHECKS = [
    ("identity",
     ["sts", "get-caller-identity"],
     "confirms the key works at all"),
    ("read telemetry",
     ["s3api", "list-objects-v2", "--bucket", RAW_BUCKET,
      "--prefix", "device-logs/", "--max-items", "1"],
     "the function reads the device logs from here"),
    ("Lambda",
     ["lambda", "list-functions", "--max-items", "1"],
     "creates blueice-fleet-build"),
    ("IAM",
     ["iam", "list-roles", "--max-items", "1"],
     "creates the function's execution role"),
    ("EventBridge",
     ["events", "list-rules", "--limit", "1"],
     "creates the every-5-minutes schedule"),
    ("CloudWatch Logs",
     ["logs", "describe-log-groups", "--limit", "1"],
     "so failures are visible instead of silent"),
    ("CloudFront",
     ["cloudfront", "list-distributions", "--max-items", "1"],
     "serves the page over HTTPS with the password gate"),
    ("S3 buckets",
     ["s3api", "list-buckets"],
     "creates the your-site-bucket bucket"),
]

DENIED = ("AccessDenied", "not authorized", "AccessDeniedException",
          "UnauthorizedOperation", "explicit deny")


def run(profile, argv):
    cmd = ["aws", "--region", REGION]
    if profile:
        cmd += ["--profile", profile]
    cmd += argv
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", help="named AWS CLI profile to test")
    a = ap.parse_args()

    print("checking deploy access in %s%s\n"
          % (REGION, "  (profile: %s)" % a.profile if a.profile else ""))

    missing = []
    for label, argv, why in CHECKS:
        ok, out = run(a.profile, argv)
        if ok:
            extra = ""
            if label == "identity":
                try:
                    extra = "  " + json.loads(out)["Arn"]
                except Exception:
                    pass
            print("  [ OK ]     %-18s%s" % (label, extra))
        elif any(d in out for d in DENIED):
            print("  [DENIED]   %-18s  needed to: %s" % (label, why))
            missing.append((label, why))
        else:
            first = out.strip().splitlines()[0] if out.strip() else "unknown error"
            print("  [ ERR ]    %-18s  %s" % (label, first[:90]))
            missing.append((label, why))

    print()
    if not missing:
        print("READY -- run:  python deploy.py --profile %s --password \"...\""
              % (a.profile or "<profile>"))
        return 0

    print("NOT READY -- %d permission(s) missing:\n" % len(missing))
    for label, why in missing:
        print("   %-18s %s" % (label, why))
    print("\nSend aws/deploy-policy.json to whoever administers the account and")
    print("ask for an IAM user with it attached -- see aws/ACCESS-REQUEST.md.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
