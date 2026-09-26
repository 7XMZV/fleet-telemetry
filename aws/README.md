# Putting the fleet dashboard on AWS

The dashboard currently runs off a laptop: a scheduled task syncs the bucket
and `serve.py` serves the page. This directory moves both halves into AWS so it
keeps working with the laptop shut.

```
device  ──►  S3 your-telemetry-bucket/device-logs/     (already exists)
                    │
                    │  EventBridge, every 5 minutes
                    ▼
             Lambda blueice-fleet-build
                    │  · ~1 s when the bucket is unchanged (most runs)
                    │  · ~2 min when there is new data
                    ▼
             S3 your-site-bucket  (private)
                    │
                    ▼
             CloudFront  ──►  https://d….cloudfront.net
             · HTTPS  · gzip  · shared-password gate
```

It runs **the same `blueice_sync.py` the laptop runs.** There is no second
implementation of the ingest, the decode or the fuel reconciliation, because
two implementations drift and then the dashboard disagrees with itself.

---

## 1. Before you start

**You need credentials that can deploy.** The key currently configured on the
laptop cannot — it is scoped to reading the telemetry bucket and is denied
Lambda, IAM, CloudFront, EventBridge and CloudWatch Logs.

[`ACCESS-REQUEST.md`](ACCESS-REQUEST.md) is a message you can send as-is to
whoever administers the account. Attach [`deploy-policy.json`](deploy-policy.json)
to it — that policy grants exactly what the deployment needs and nothing else.

Then, on the machine doing the deploy:

```bash
aws configure --profile blueice-deploy
```

Never put the keys on a command line or paste them into chat — they end up in
shell history and transcripts.

Check the new key before using it:

```bash
python check-access.py --profile blueice-deploy
```

It only reads, so it is safe to run any time. It prints `READY`, or names
exactly which permission is missing and what it was needed for.

**Also rotate `AKIAEXAMPLEEXAMPLE`.** It was pasted into a chat transcript,
and it has `s3:PutObject` on the raw telemetry bucket — anyone holding it can
write into the source of truth.

---

## 2. Check it without touching anything

```bash
python deploy.py --dry-run
```

Prints every AWS call it would make and changes nothing. Worth reading once.

---

## 3. Deploy

```bash
python deploy.py --profile blueice-deploy --password "choose-a-real-one"
```

That is the whole deployment — 13 steps, ending with the URL printed.

Idempotent — every step checks for what it is about to create and reuses or
updates it, so re-running after a failure resumes rather than duplicating.

Omit `--password` and it stops after the build half: the dashboard is produced
into a private bucket and is not reachable from the internet.

Afterwards, to push code changes only:

```bash
python deploy.py --profile blueice-deploy --code-only
```

---

## 4. What gets created

| Resource | Name | Notes |
|---|---|---|
| S3 bucket | `your-site-bucket` | Private. Public access fully blocked. |
| Lambda layer | `blueice-pycryptodome` | The decoder's one binary dependency. |
| IAM role | `blueice-fleet-build-role` | Read raw, write site, write logs. |
| Lambda | `blueice-fleet-build` | 3008 MB, 600 s timeout, 2048 MB `/tmp`. |
| EventBridge rule | `blueice-fleet-build-5min` | `rate(5 minutes)`. |
| CloudFront | distribution + `blueice-fleet-auth` | HTTPS, gzip, password gate. |

Sizing comes from a measured local run of the packaged function, not a guess:

```
cold, nothing cached   118 s   98 MB bucket logs + 56 MB fixtures, 2.06 M records
bucket unchanged       0.9 s   ETag comparison only, no download, no rebuild
published              index.html 0.07 MB · data.js 0.26 MB · track.js 8.61 MB
```

`track.js` gzips to **0.78 MB**, which CloudFront does automatically — that is
the difference between a usable page on a site connection and an unusable one.

**Watch the memory metric on the first few real runs.** 3008 MB is chosen for
CPU (the build is CPU-bound), and the actual high-water mark has not been
measured on Lambda. If `Max Memory Used` sits far below it, lower it and save
money; if it approaches it, raise it.

---

## 5. What the public side is made of

`deploy.py` builds these when you pass `--password`; this is what to look at if
something is wrong.

| Piece | Why |
|---|---|
| Origin access control | Lets CloudFront read the bucket while it stays private to everyone else. |
| `blueice-fleet-auth` | The edge function holding the password. Created, then **published** — an unpublished function is not attached to live traffic. |
| Distribution | `Compress: true` (8.61 MB → 0.78 MB) and `PriceClass_200`, which includes the Middle East. `PriceClass_100` would serve Oman from Europe. |
| Bucket policy | Applied **last**, naming the distribution. Written before the distribution exists, it would lock the bucket to nobody. |

To change the password later, re-run `deploy.py` with the new `--password`. It
updates and republishes the function; nothing else changes.

## 6. First build

The schedule fires within five minutes, but to see it now:

```bash
aws lambda invoke --function-name blueice-fleet-build --payload '{"force":true}' out.json
```

```bash
aws logs tail /aws/lambda/blueice-fleet-build --follow
```

---

## 7. What the running function may do

Much less than the deployer. `deploy.py` attaches this inline:

- read `your-telemetry-bucket`
- read and write `your-site-bucket`
- write its own CloudWatch logs
- create CloudFront invalidations

It cannot delete raw telemetry, touch IAM, or reach any other bucket.

---

## 8. Costs

Roughly **$1–3/month** at this size. The shape of the bill:

- Lambda — 8,640 invocations/month, nearly all ~1 s no-ops. Pennies.
- S3 — ~160 MB stored, a few thousand requests.
- CloudFront — 0.78 MB gzipped per full page load. The first 1 TB/month is
  free tier; after that it is the largest line, so if the page is opened
  constantly all day, splitting `track.js` per car is the thing to do.

The early exit is what keeps this cheap. If it ever regressed to rebuilding
every 5 minutes, the bill would be roughly 30× — so the comparison in
`unchanged()` has a test in `aws/` and should keep one.

---

## 9. Files here

| File | What it is |
|---|---|
| `lambda_handler.py` | The Lambda entry point. Redirects writable paths into `/tmp`, stages fixtures, early-exits on an unchanged bucket. |
| `package.py` | Builds `dist/function.zip` and `dist/layer.zip`. Downloads a manylinux wheel, so it runs fine on Windows. |
| `deploy.py` | Creates or updates everything. `--dry-run`, `--code-only`. |
| `auth-function.js` | CloudFront Function; `__CREDENTIAL__` is substituted at deploy time. |
| `deploy-policy.json` | Least-privilege policy for the deploying user. |
| `check-access.py` | Read-only preflight: can this key deploy, and if not, what is missing. |
| `ACCESS-REQUEST.md` | The message to send to whoever administers the account. |
| `distribution-config.json` | CloudFront template; ids are substituted at deploy time. |

---

## 10. Known limits

- **The password is shared, not per-person.** It cannot tell you who looked,
  and revoking one person means changing it for everyone. The function source
  is also readable by anyone with `cloudfront:GetFunction` on the account, so
  it protects against the public, not against a colleague with AWS access.
  Upgrading to Cognito later changes nothing else in this diagram.
- **The demo machines are published.** `DEMO-FUEL-01`…`05` are generated
  fixtures and are the only thing exercising the fuel reconciliation, because
  no real machine has a fuel sensor fitted yet. They are badged on every screen
  that shows them. To publish real telemetry only, set the function's
  `FIXTURES_PREFIX` environment variable to empty — the dashboard then shows
  four real machines and no fuel data at all.
- **The public side has never touched a real account.** The build half —
  packaging, ingest, decode, publish, the early exit — was exercised end to end
  locally against the real telemetry bucket. The CloudFront half is scripted
  and dry-run clean, but no credentials existed that could create a
  distribution, so its first real run will be its first real run. Expect to
  read one or two errors.
- **The firmware defect is unchanged by any of this.** The gate still fails at
  41.55% valid. Moving to AWS does not fix corrupt input, and the dashboard
  will keep saying so.
