#!/usr/bin/env bash
#
# One-time setup for the telemetry sync (Linux / macOS / Git Bash).
#
#   ./install.sh
#   ./install.sh --bucket blueice-telemetry-raw --prefix logs/ --region me-central-1
#   ./install.sh --local          # no AWS: read a folder instead
#
# Installs the two Python dependencies, writes config.json if missing, and runs
# `blueice_sync.py check` so you find out straight away whether the bucket is
# reachable.
#
# This script never asks for an access key and never stores one. boto3 reads
# credentials from the standard chain -- an IAM role, `aws configure`, or
# AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY. Set those up yourself.
set -euo pipefail

cd "$(dirname "$0")"

BUCKET=""; PREFIX="logs/"; REGION="me-central-1"; PROFILE=""; LOCAL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --bucket)  BUCKET="$2"; shift 2 ;;
    --prefix)  PREFIX="$2"; shift 2 ;;
    --region)  REGION="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --local)   LOCAL=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done

step() { printf '\n== %s\n' "$1"; }

step "Python"
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then echo "  Python 3.8+ not found on PATH."; exit 1; fi
echo "  $PY  ($("$PY" -c 'import sys;print(".".join(map(str,sys.version_info[:3])))'))"

step "Dependencies"
"$PY" -m pip install --quiet --disable-pip-version-check pycryptodome boto3
"$PY" -c "import Crypto, boto3; print('  pycryptodome', Crypto.__version__, '| boto3', boto3.__version__)"

step "Configuration"
if [ -f config.json ]; then
  echo "  config.json already exists -- left alone."
  echo "  Delete it and re-run if you want it rewritten."
elif [ "$LOCAL" = "1" ]; then
  cp config.local.example.json config.json
  echo "  Wrote config.json in local mode (reads ../../Not mine, no AWS)."
else
  BUCKET="$BUCKET" PREFIX="$PREFIX" REGION="$REGION" PROFILE="$PROFILE" "$PY" - <<'PY'
import json, os
cfg = json.load(open("config.example.json", encoding="utf-8"))
cfg.pop("_readme", None)
cfg["source"].pop("_type", None)
cfg["source"]["bucket"] = os.environ["BUCKET"]
cfg["source"]["prefix"] = os.environ["PREFIX"]
cfg["source"]["region"] = os.environ["REGION"]
if os.environ.get("PROFILE"):
    cfg["source"]["profile"] = os.environ["PROFILE"]
json.dump(cfg, open("config.json", "w", encoding="utf-8"), indent=2)
PY
  if [ -z "$BUCKET" ]; then
    echo "  Wrote config.json with an EMPTY bucket."
    echo "  Edit source.bucket, or re-run with --bucket <name>."
  else
    echo "  Wrote config.json for s3://$BUCKET/$PREFIX in $REGION"
  fi
fi

step "Credentials"
if [ -n "${AWS_ACCESS_KEY_ID:-}" ]; then
  echo "  AWS_ACCESS_KEY_ID is set in the environment."
elif [ -f "$HOME/.aws/credentials" ]; then
  echo "  Found $HOME/.aws/credentials"
else
  echo "  No AWS credentials found."
  echo "  On EC2 an attached IAM role is the right answer and needs nothing here."
  echo "  Otherwise run 'aws configure', or export AWS_ACCESS_KEY_ID and"
  echo "  AWS_SECRET_ACCESS_KEY. Do not put a key in config.json."
fi

step "Check"
set +e
"$PY" blueice_sync.py check
code=$?
set -e

echo
if [ $code -eq 0 ]; then
  echo "Ready. Next:"
  echo "  python blueice_sync.py update      pull new logs and rebuild the dashboard"
  echo "  crontab -e  ->  */15 * * * * cd $(pwd) && $PY blueice_sync.py update >> sync.log 2>&1"
else
  echo "Not ready -- see the FAIL lines above."
fi
exit $code
