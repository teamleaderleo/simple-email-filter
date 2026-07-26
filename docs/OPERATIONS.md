# Operations

The repository owns the deployment procedure. Routine updates should not require copying individual AWS, Docker or Microsoft Graph commands out of a chat.

## Routine webhook update

From a current checkout:

```bash
git switch main
git pull --ff-only
make deploy-webhook
```

`make deploy-webhook` performs the full sequence:

1. verifies Git, AWS CLI, Docker, ZIP, cURL and Make
2. creates or repairs a Python 3.14 virtual environment
3. installs the AWS CRT dependency required by `aws login` credentials
4. verifies the AWS account, region, Lambdas, DynamoDB table and API Gateway
5. checks that the deployed Lambda still has the expected environment-variable names
6. runs the complete test suite and syntax checks
7. downloads a rollback copy of the currently deployed webhook ZIP
8. detects the live Lambda runtime and CPU architecture
9. reuses matching Linux dependencies from the local build cache, installing them only when the runtime, architecture or requirements change
10. updates Lambda code without replacing environment variables
11. checks the cached Microsoft token and opens browser authentication only when it has expired
12. exercises the exact Microsoft Graph validation-token handshake against API Gateway
13. recreates the secured Microsoft Graph subscription using the API Gateway URL discovered from AWS
14. verifies the subscription record without printing its client-state secret

Backups are written under `backups/<timestamp>/` and ignored by Git.

## First use on a Mac

Sign in to AWS once:

```bash
aws login --profile email
```

Then:

```bash
make bootstrap
make doctor
make deploy-webhook
```

The defaults are:

```text
AWS_PROFILE=email
AWS_REGION=us-east-2
```

Override them for one command when needed:

```bash
AWS_PROFILE=another-profile AWS_REGION=ca-central-1 make doctor
```

## Commands

```text
make bootstrap        Create the Python 3.14 virtual environment and install tools
make doctor           Validate local tools, AWS access and deployed resources
make test             Run tests and syntax checks
make deploy-webhook   Cached build, backup, deploy and Graph subscription setup
make setup-webhook    Refresh authentication when needed and recreate only the subscription
make microsoft-login  Force a Microsoft browser login
make status           Show deployment and subscription status without secrets
make logs-webhook     Follow the webhook Lambda logs
make upgrade-runtime  Upgrade both email Lambdas to Python 3.14
```

## Python versions

Local development targets Python 3.14. CI runs the suite on both Python 3.11 and 3.14 while the deployed Lambdas are transitioned deliberately.

The normal deployment command builds against the runtime currently configured on the webhook Lambda, preventing a compiled-package mismatch. Runtime upgrades are deliberately separate:

```bash
make upgrade-runtime
```

That command creates rollback backups, asks once for confirmation, builds matching Python 3.14 packages, upgrades both `email-webhook-handler` and `email-subscription-manager`, validates the endpoint and recreates the Graph subscription. Existing Lambda environment variables are not replaced.

## Build cache

Lambda dependencies are cached under:

```text
.build-cache/lambda/<runtime>-<architecture>-<requirements-hash>/
```

The first build for a runtime and CPU architecture pulls the Docker image and installs dependencies. Later deployments reuse that directory and only copy the handler source and create the ZIP. Changing `requirements-webhook.txt`, the runtime or the architecture creates a new cache entry automatically.

Clear all cached build files when troubleshooting a dependency package:

```bash
rm -rf .build-cache
```

The next deployment will rebuild them.

## Microsoft authentication

The Microsoft refresh token is stored in the `email-filter-tokens` DynamoDB table. `make deploy-webhook` checks it before subscription creation.

When the cache can no longer refresh a Graph access token, the command opens the Microsoft browser login and writes the renewed cache back to DynamoDB. You can force that step with:

```bash
make microsoft-login
```

The non-secret Azure application client ID is recovered from the existing Lambda and added to the ignored local `.env` file when it is missing. Cloudflare credentials are never copied into `.env` by the operations script.

## Webhook validation failures

Before asking Microsoft Graph to create a subscription, the setup command sends the same validation request Graph uses: an HTTP `POST` with a `validationToken` query parameter. The endpoint must return that token unchanged with HTTP 200 and a `text/plain` content type.

When the probe fails, setup stops before creating a subscription and prints recent CloudWatch logs for `email-webhook-handler`. A 502 response normally indicates that API Gateway could not obtain a valid Lambda response, commonly because the deployed package is stale, incomplete or failed during module initialisation. Run the complete repair path:

```bash
git pull --ff-only
make deploy-webhook
```

## Rollback

List local backups:

```bash
find backups -name 'email-*.zip' -print
```

Restore a selected package:

```bash
aws lambda update-function-code \
  --profile email \
  --region us-east-2 \
  --function-name email-webhook-handler \
  --zip-file fileb://backups/YYYYMMDD-HHMMSS/email-webhook-handler.zip
```

Then wait for the update:

```bash
aws lambda wait function-updated \
  --profile email \
  --region us-east-2 \
  --function-name email-webhook-handler
```

## Security notes

The deploy commands use code-only Lambda updates and do not send an `--environment` argument. This prevents the existing Microsoft and Cloudflare settings from being erased.

`make doctor` warns when the AWS profile is authenticated as the root user. Root access is not blocked so an existing personal deployment can be repaired, but routine work should move to a non-root administrator identity.

## Retention service

Mailbox-retention code is present but its scheduled audit deployment remains separate. It should receive the same treatment: a checked-in command that packages a private policy, deploys in audit mode and verifies aggregate counts before apply mode is available.
