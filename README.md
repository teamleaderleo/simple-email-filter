# Simple Email Filter for Outlook

A personal Outlook automation project with two related jobs:

- **Junk Guard** processes messages Outlook has already placed in Junk. It uses conservative deterministic checks and a Cloudflare Workers AI fallback, keeping uncertain mail.
- **Mailbox Retention** lets ordinary mail arrive normally, then identifies categories that may be moved to Deleted Items after an explicit retention period. Retention currently defaults to audit mode.

## Routine operations

The repository owns its deployment procedure. After the initial AWS login, a normal webhook update is:

```bash
git switch main
git pull --ff-only
make deploy-webhook
```

That command checks the local machine and AWS resources, repairs the Python environment, runs tests, backs up the deployed Lambda, builds matching Linux dependencies, updates code without replacing secrets, refreshes Microsoft authentication only when needed, and recreates the secured Graph subscription.

Useful commands:

```text
make bootstrap        Create the Python 3.14 development environment
make doctor           Check AWS login, resources and Lambda configuration
make test             Run tests and syntax checks
make deploy-webhook   Perform the complete safe webhook update
make setup-webhook    Recreate only the Microsoft Graph subscription
make microsoft-login  Force a Microsoft browser login
make status           Show deployment status without secrets
make logs-webhook     Follow webhook logs
make upgrade-runtime  Explicitly upgrade the webhook Lambda to Python 3.14
```

See [Operations](docs/OPERATIONS.md) for first-time setup, rollback and troubleshooting.

## Safety defaults

- Junk Guard deletes only messages already in Junk and keeps uncertain classifications.
- Webhook notifications must match the stored subscription ID and client state.
- Notifications are processed by exact immutable Outlook message ID.
- The retention service defaults to audit mode.
- Retention apply mode requires an explicit confirmation value.
- The retention Graph client exposes moves to Deleted Items, not permanent deletion.
- Deployment uses code-only Lambda updates so existing Microsoft and Cloudflare environment variables are preserved.
- Message bodies and attachments are not stored for dashboard activity.

## Repository map

```text
email_filter/              shared auth, Graph, policy and retention code
handlers/                  Lambda handlers, including the retention sweeper
policies/                  checked-in example policies; personal policies are ignored
scripts/email-filter.sh    consolidated macOS/AWS operations
webhook_handler.py         deployed Junk Guard webhook
setup_webhook.py           secured Graph subscription setup
setup_token_interactive.py Microsoft browser authentication and cache refresh
docs/                      architecture, operations, policies and roadmap
tests/                     unit tests
```

## Development

Local development targets Python 3.14. CI currently runs on Python 3.11 and 3.14 while the live Lambda runtime is transitioned deliberately.

```bash
make bootstrap
make test
```

The webhook package is built inside the official Python Docker image for the deployed Lambda runtime and CPU architecture, so compiled dependencies are not taken from macOS.

## Documentation

- [Operations](docs/OPERATIONS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Retention policies](docs/RETENTION_POLICIES.md)
- [Roadmap](docs/ROADMAP.md)

## Current roadmap

The immediate operational milestone is a stable Junk Guard deployment with one-command updates. Next work includes an audit-only scheduled retention deployment, mailbox-wide observe-only ingestion, a privacy-minimised API, and a read-only email dashboard in `scrapbook`.
