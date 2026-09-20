# The public side — three administrator steps

The build half runs inside the `blueice-fleet-deploy-role`: a Lambda decodes
new telemetry every 5 minutes and writes `index.html`, `data.js` and `track.js`
into `s3://blueice-fleet-site/`. That bucket stays private and nothing is
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

| Field | Value |
|---|---|
| `Origins.Items[0].OriginAccessControlId` | `E25UBTALZV3Q69` (already created) |
| `Origins.Items[0].DomainName` | `blueice-fleet-site.s3.eu-north-1.amazonaws.com` |
| `DefaultCacheBehavior.FunctionAssociations.Items[0].FunctionARN` | the ARN printed by step 1 |

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
aws s3api put-bucket-policy --bucket blueice-fleet-site \
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
