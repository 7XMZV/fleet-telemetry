Hi,

I have built the fleet telemetry dashboard for the machine data landing in
`s3://your-telemetry-bucket/device-logs/`. It decodes the device logs
and shows each machine's position, working vs idling time, and fuel
reconciliation on a web page.

It currently runs on my laptop, which means it stops working the day I leave.
I would like to move it into the BlueICE AWS account so it keeps running
without me.

**What I need:** a new IAM user — suggested name `blueice-deploy` — with the
attached policy `deploy-policy.json` attached to it, and an access key for it.

**What that policy allows,** and nothing else:

- read `s3://your-telemetry-bucket` (the telemetry we already have)
- create and write one new bucket, `your-site-bucket`
- create one Lambda function, `blueice-fleet-build`, and its execution role
- create one EventBridge schedule and one CloudFront distribution
- write its own CloudWatch logs

It cannot delete telemetry, cannot touch any other bucket, and cannot read or
change anything else in the account.

**What it will cost:** roughly $1–3 per month. The function does nothing on
most runs — it compares S3 ETags and exits in about a second — and only
rebuilds when a new log file actually arrives.

Thanks,
Hamzas