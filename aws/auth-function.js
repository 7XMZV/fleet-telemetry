// CloudFront Function — shared-password gate for the BlueICE fleet dashboard.
//
// Runs at the edge on every viewer request, before CloudFront looks at its
// cache. Nothing reaches the S3 bucket without a correct Authorization header.
//
// __CREDENTIAL__ is replaced by deploy.ps1 with base64("user:password").
// Do not commit a real credential into this file.
//
// What this is and is not:
//
//   It is a shared password over HTTPS. That keeps machine positions, fuel
//   figures and site locations off the open internet, and it can be changed in
//   under a minute by republishing the function.
//
//   It is NOT per-person authentication. Everyone uses one credential, so it
//   cannot tell you who looked, and revoking one person means changing the
//   password for everybody. The function's source is also readable by anyone
//   with cloudfront:GetFunction on the AWS account, so treat the password as
//   protection against the public, not against a colleague with AWS access.
//
//   If either of those matters later, the upgrade is Cognito — the bucket,
//   the distribution and the Lambda all stay exactly as they are.

function handler(event) {
  var request = event.request;
  var headers = request.headers;
  var expected = "Basic __CREDENTIAL__";

  if (!headers.authorization || headers.authorization.value !== expected) {
    return {
      statusCode: 401,
      statusDescription: "Unauthorized",
      headers: {
        "www-authenticate": { value: 'Basic realm="BlueICE Fleet Telemetry"' },
        "cache-control": { value: "no-store" }
      }
    };
  }

  // Bare "/" has no object behind it in S3.
  if (request.uri === "/" || request.uri.endsWith("/")) {
    request.uri = "/index.html";
  }
  return request;
}
