# Data sync

Pulls telemetry logs out of the ingest bucket and rebuilds the dashboard
datasets, on a schedule, without anyone copying a file by hand.

```bash
# Windows
powershell -ExecutionPolicy Bypass -File install.ps1 -Bucket <bucket-name>
powershell -ExecutionPolicy Bypass -File schedule.ps1 -Minutes 15

# Linux / macOS / Git Bash
./install.sh --bucket <bucket-name>
# then: crontab -e  ->  */15 * * * * cd <this dir> && python blueice_sync.py update >> sync.log 2>&1
```

That is the whole thing. Everything below is what it does and why.

---

## Status of what has actually been tested

Be clear about this before trusting it in front of anyone.

| | |
|---|---|
| The full pipeline — list, download, hash, decode, quarantine, select, merge, rebuild | **Tested.** `python selftest.py` drives the S3 code path with a stub client over a fake bucket built from the real reference log. 20 assertions, all passing. |
| Local-directory mode, end to end, against the real log | **Tested.** Reproduces `data.js` and `track.js` byte for byte. |
| boto3 wiring — session, region, `head_bucket` | **Reaches AWS and fails at authentication**, which is as far as it can go here. |
| A real bucket, real credentials, real `ListObjectsV2` pagination | **Not tested.** There is no bucket and no credential on the machine this was written on. `blueice_sync.py check` is the thing that tells you whether it works, and it is the first thing `install.ps1` runs. |

## It works today without AWS

The bucket does not exist yet ([HANDOVER.md](../HANDOVER.md) step 4). Until it
does, point the sync at a directory:

```bash
cp config.local.example.json config.json   # or: install.ps1 -Local
python blueice_sync.py update
```

That is the configuration shipped in `config.json`, so the tool runs out of the
box. The local source is **read-only** — it copies out and never writes back —
which is what makes it safe to aim at `Not mine/`.

---

## Commands

```bash
python blueice_sync.py check     # deps, config, and can I actually reach the bucket
python blueice_sync.py pull      # download anything new into inbox/   (--dry-run)
python blueice_sync.py build     # rebuild data.js / track.js from what is local
python blueice_sync.py update    # pull + build. This is what the scheduler runs.
python blueice_sync.py status    # what is ingested, and how healthy it is
```

For a laptop being shown to people rather than left running, the dashboard's own
server can drive the sync instead of a scheduled task:

```bash
cd ../dashboard
python serve.py --sync-every 15
```

Do not run both against the same folder — one scheduler is enough.

`build` and `update` take `--fail-under-gate`, which exits 1 when the
valid-record percentage is below `gate_pct`. Use it in CI; **do not** use it in
the scheduled task, or every run will look like a failure until the firmware is
fixed.

## Configuration

`config.json`, from `config.example.json`. **No credentials go in it.** boto3
reads them from the standard chain: an IAM role, `aws configure`, or
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`. Nothing here will ever ask you to
type a key into a prompt or a file.

| Key | |
|---|---|
| `source.type` | `s3`, or `local` for a directory standing in for the bucket |
| `source.bucket` / `prefix` / `region` / `profile` | where to look |
| `source.endpoint_url` | for MinIO or an S3-compatible store; leave null for AWS |
| `devices` | empty = every device found; list IMEIs to restrict |
| `select` | `latest-day` (default), `latest-file`, or `all` — see below |
| `gate_pct` | the firmware acceptance threshold, reported on every run |

### `select`

The dashboard shows one device over one span of time, so something has to choose
which logs feed it.

- **`latest-day`** — every file whose data falls on the most recent UTC day for
  that device, merged. The default: the device uploads in chunks, and a day is
  the unit a site supervisor thinks in.
- **`latest-file`** — the single newest file. Right when one file is one shift,
  which is what the reference log happens to be.
- **`all`** — everything. Honest, but unbounded; the dashboard was not built for
  months of history and will get slow.

---

## The four things it is careful about

**1. `Not mine/` is never written to.** Downloads land in `inbox/<device>/`.
The local source only ever reads.

**2. Ingest is idempotent.** Every object is keyed by ETag and verified by
SHA-256 after download. The same file re-uploaded under a different key is
recognised by hash, recorded, and kept *out* of the build. This matters more
than it looks: lifetime totals are cumulative and never recomputed, so a
double-counted file is wrong forever ([HANDOVER.md](../HANDOVER.md) step 4).

**3. Records are deduped across files, never within one.** Two records sharing
`(ts, ms)` inside a single log are two real writes into the same 100 ms bucket —
the reference shift contains exactly two such pairs. Deduping them would move
the headline figures from 419 valid / 0.50% to 417 / 0.49% and quietly
contradict [FINDINGS.md](../FINDINGS.md). A key already seen in an *earlier*
file is a genuine overlap between uploads, and that one is dropped. Invalid
records are never deduped at all — their timestamps are garbage, so they would
collide with each other by chance and shrink the denominator the corruption
percentage is measured against.

**4. Undecodable files are quarantined, not built from.** They go to
`inbox/_rejected/` with the reason recorded in `state.json`, and `status` lists
them. A file that will not parse is evidence, not garbage — keep it.

## The gate

Every build reports valid records as a percentage and compares it to
`gate_pct` (99%). Today it prints:

```
  ** GATE FAIL: 0.5% valid, need 99.0%.
```

That is correct and expected — it is the firmware defect in
[BUG_REPORT.md](../BUG_REPORT.md), and the sync is designed to keep working
while it is unfixed. **The day this line changes to GATE PASS is the day the
firmware fix is real**, and it is worth watching for.

---

## How the dashboard gets the data

`build` sets `BLUEICE_LOGS` (an `os.pathsep`-separated list) and runs
`build_data.py` and `build_track.py`. Both builders fall back to their original
behaviour when that variable is unset, so a manual `python build_data.py` still
works exactly as it did — verified byte for byte against the previous output.

## Files

| | |
|---|---|
| `blueice_sync.py` | the tool |
| `selftest.py` | drives the S3 path with a stub client; no AWS needed |
| `install.ps1` / `install.sh` | dependencies, config, then `check` |
| `schedule.ps1` | registers/removes the Windows scheduled task |
| `run_update.cmd` | one cycle, appending to `sync.log`. What the task runs. |
| `config.example.json` | S3 template |
| `config.local.example.json` | no-AWS template |
| `state.json` | what has been ingested. Generated; safe to delete, everything re-downloads. |
| `inbox/` | downloaded logs. Generated. |

---

## When the bucket gets built

This tool is a workstation stopgap, and it should be deleted when the real
ingest path exists. The design agreed in [DECISIONS.md](../DECISIONS.md) is
S3 → Lambda on the object-created event → Postgres/PostGIS/Timescale in
`me-central-1`; polling a bucket from a laptop is the thing you do until that
Lambda is written.

Two pieces of it are worth carrying over rather than rewriting: the
**idempotency rules** above, and the **quarantine-don't-discard** behaviour.

The sync only needs read access. A least-privilege policy for it:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::BUCKET",
      "Condition": { "StringLike": { "s3:prefix": ["logs/*"] } } },
    { "Effect": "Allow", "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::BUCKET/logs/*" }
  ]
}
```

Replace `BUCKET`. Note there is no `PutObject` and no `DeleteObject` — this tool
never writes to the bucket, and the credential it runs under should not be able
to.
