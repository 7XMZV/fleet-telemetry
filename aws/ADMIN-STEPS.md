# The public side — three administrator steps

> ## STATUS: DONE. The dashboard is live.
>
> **https://dXXXXXXXXXXXXX.cloudfront.net** — account `123456789012`,
> distribution serving `your-site-bucket`, shared-password gate in front.
>
> Verified 21 September 2026, unauthenticated, from outside the account:
>
> | Check | Result |
> |---|---|
> | Gate is live | `401` with `WWW-Authenticate: Basic realm="BlueICE Fleet Telemetry"` |
> | Function is **published**, not merely created | `X-Cache: FunctionGeneratedResponse from cloudfront` |
> | Gate covers every path, not just `/` | `/`, `/index.html`, `/data.js`, `/track.js`, `/_state/state.json`, `/favicon.ico` and an unknown path all return `401` |
> | Edge location serves the Gulf | `X-Amz-Cf-Pop: MCT50-P3` (Muscat) — `PriceClass_200` behaving as intended |
> | Bucket policy applied | the dashboard renders once the credential is supplied, so CloudFront can read the bucket |
>
> The three steps below are kept as the record of how it was built, and as the
> procedure if it ever has to be rebuilt. **To change the password, see
> [Rotating the password](#rotating-the-password) at the end — not step 1.**


The build half runs inside the `blueice-fleet-deploy-role`: a Lambda decodes
new telemetry every 5 minutes and writes `index.html`, `data.js` and `track.js`
into `s3://your-site-bucket/`. That bucket stays private and nothing is
reachable from the internet.

These three steps put it online. All three need permissions the deploy role
does not have, so they are yours. Everything you need is in this folder.

**Do them in this order.** Step 3 names the distribution, so it cannot be
written until step 2 has created one.

---

## 1. The password gate

`auth-function.js` is a CloudFront Function. It runs at the edge on every
request and returns 401 unless the viewer supplies the shared credential.

Replace `__CREDENTIAL__` with base64 of `user:password`. To generate it:

```bash
python -c "import base64;print(base64.b64encode(b'blueice:CHOSEN-PASSWORD').decode())"
```

Save the substituted copy as **`auth-function.built.js`** — that is the filename
the next command reads. Keep `auth-function.js` with the `__CREDENTIAL__`
placeholder intact, so the credential is never committed alongside the source.

Then:

```bash
aws cloudfront create-function --name blueice-fleet-auth \
  --function-config 'Comment=BlueICE shared-password gate,Runtime=cloudfront-js-2.0' \
  --function-code fileb://auth-function.built.js
```

```bash
aws cloudfront publish-function --name blueice-fleet-auth --if-match <ETag-from-above>
```

**Publish matters.** A created-but-unpublished function is not attached to live
traffic, so the site would be open while looking configured.

### What this is, and is not

It is a shared password over HTTPS. It keeps machine positions, site locations
and fuel figures off the open internet, and it can be changed in a minute by
republishing the function.

It is **not** per-person authentication. Everyone uses one credential, so it
cannot tell you who looked, and revoking one person means changing it for
everybody. The function source is also readable by anyone with
`cloudfront:GetFunction` on the account. Treat it as protection against the
public, not against a colleague with AWS access.

If that is not enough, the upgrade is Cognito, and nothing else in the design
changes.

---

## 2. The distribution

`distribution-config.json` is ready. Three things to substitute:

`distribution-with-tags.json` wraps it with tags and is what the command below
reads. Only **one** field still needs substituting, in that file:

| Field | Value |
|---|---|
| `DistributionConfig.DefaultCacheBehavior.FunctionAssociations.Items[0].FunctionARN` | the ARN printed by step 1 |

The other two are already filled in and verified:

| Field | Value |
|---|---|
| `OriginAccessControlId` | `EXXXXXXXXXXXXX` — confirmed live, name `your-site-bucket-oac`, signing `always` |
| `DomainName` | `your-site-bucket.s3.eu-north-1.amazonaws.com` |

```bash
aws cloudfront create-distribution-with-tags \
  --distribution-config-with-tags file://distribution-with-tags.json
```

Two settings in that config are deliberate and worth keeping:

- **`Compress: true`** — `track.js` is 8.6 MB and gzips to 0.78 MB. On a site
  connection that is the difference between usable and not.
- **`PriceClass_200`** — includes the Middle East. `PriceClass_100` would serve
  Oman out of Europe.

---

## 3. The bucket policy

**Last.** Until the distribution exists there is no ARN to name, and a policy
naming one that does not exist locks the bucket to nobody.

`bucket-policy.json` in this folder — substitute `DIST_ID` with the id from
step 2:

```bash
aws s3api put-bucket-policy --bucket your-site-bucket \
  --policy file://bucket-policy.json
```

This grants `s3:GetObject` to the CloudFront service principal, and only when
the request comes from that one distribution. It does not make the bucket
public and does not grant anything to any other principal.

---

## Checking it worked

```bash
curl -I https://<distribution-domain>/
```

Expect **401** with a `WWW-Authenticate` header. That means the gate is live.
Then open it in a browser, enter the credential, and you should get the
dashboard.

If you get **403** instead, the bucket policy (step 3) has not applied.
If you get **200 without being asked for a password**, the function was created
but not published — go back to the end of step 1.

---

## What the build half is doing meanwhile

Every 5 minutes, regardless of whether the public side exists:

- lists `s3://your-telemetry-bucket/device-logs/` and compares ETags
  against its own state file; if nothing changed it exits in about a second
- if something changed: downloads, decodes, validates, deduplicates, rebuilds
  and writes the three files into the site bucket

A full rebuild takes about 2 minutes. Cost is roughly $1–3/month, nearly all of
it the no-op invocations.

To watch it:

```bash
aws logs tail /aws/lambda/blueice-fleet-build --follow
```

---

## Rotating the password

The shared credential should be changed when someone who knew it leaves, if it
has been sent over an insecure channel, or on any routine schedule the company
prefers. It takes about two minutes and nothing else in the system changes.

**Generate the new credential without it touching your shell history:**

```bash
python make-credential.py
```

It prompts hidden, writes `auth-function.built.js`, and prints the base64
string. `auth-function.js` keeps its `__CREDENTIAL__` placeholder, and
`auth-function.built.js` is git-ignored.

**Then publish it, by whichever route you have access to.**

*Console* — CloudFront → Functions → `blueice-fleet-auth` → **Edit code**,
replace the string after `Basic ` with the new base64 → **Save changes** →
**Publish** tab → **Publish function**. Saving alone does nothing to live
traffic; publishing is what applies it.

*CLI* — needs `cloudfront:DescribeFunction`, `UpdateFunction` and
`PublishFunction`, which the `blueice-fleet-deploy-role` does **not** have.
Run these from this folder:

```bash
aws cloudfront describe-function --name blueice-fleet-auth --query ETag --output text
```

```bash
aws cloudfront update-function --name blueice-fleet-auth --if-match ETAG_FROM_ABOVE --function-config 'Comment=BlueICE shared-password gate,Runtime=cloudfront-js-2.0' --function-code fileb://auth-function.built.js
```

```bash
aws cloudfront publish-function --name blueice-fleet-auth --if-match ETAG_FROM_UPDATE
```

**Afterwards**, confirm the old credential is dead and the gate still stands:

```bash
python -c "import urllib.request,urllib.error;
try: urllib.request.urlopen('https://dXXXXXXXXXXXXX.cloudfront.net/')
except urllib.error.HTTPError as e: print(e.code, e.headers.get('WWW-Authenticate'))"
```

Expect `401 Basic realm="BlueICE Fleet Telemetry"`. Then delete the built file:

```bash
rm auth-function.built.js
```
