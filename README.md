# Simple Email Filter for Outlook

A personal Outlook automation project with three related jobs:

- **Junk Guard** processes messages Outlook has already placed in Junk. It uses conservative deterministic checks and a Cloudflare Workers AI fallback, keeping uncertain mail.
- **Historical Cleanup** audits a large existing folder, checkpoints every page, reports retention candidates, and can move a bounded reviewed batch to Deleted Items.
- **Mailbox Retention** lets ordinary mail arrive normally, then identifies categories that may be moved to Deleted Items after an explicit retention period. Retention currently defaults to audit mode.

## Routine operations

The repository owns its deployment procedure. After the initial AWS login, a normal webhook update is:

```bash
git switch main
git pull --ff-only
make deploy-webhook
```

That command checks the local machine and AWS resources, repairs the Python environment, runs tests, backs up the deployed Lambda, builds matching Linux dependencies, updates code without replacing secrets, refreshes Microsoft authentication only when needed, and recreates the secured Graph subscription.

For a large existing Inbox, start with the non-destructive historical audit:

```bash
make mailbox-audit
```

The audit scans or resumes the Inbox, writes private local checkpoints, and produces counts for protected, retained, eligible, and unmatched messages. It moves nothing. Review unmatched senders without another Microsoft request:

```bash
make mailbox-review
```

That review uses the private local snapshot, redacts obvious identifiers from subject patterns, and includes no message IDs, bodies, previews, or attachments. See [Historical mailbox cleanup](docs/MAILBOX_CLEANUP.md).

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
make upgrade-runtime  Upgrade both email Lambdas to Python 3.14
make mailbox-audit    Scan/resume Inbox and build a non-destructive report
make mailbox-report   Print the latest local mailbox report
make mailbox-review   Inspect unmatched senders and redacted subject patterns
make mailbox-apply    Move a bounded reviewed batch to Deleted Items
make mailbox-reset    Delete only the private local cleanup state
```

See [Operations](docs/OPERATIONS.md) for deployment setup, rollback and troubleshooting.

## Safety defaults

- Junk Guard deletes only messages already in Junk and keeps uncertain classifications.
- Webhook notifications must match the stored subscription ID and client state.
- Notifications are processed by exact immutable Outlook message ID.
- Historical cleanup audit mode moves nothing and checkpoints after complete pages.
- Historical cleanup review mode is local-only and redacts obvious subject identifiers.
- Historical cleanup apply mode refuses incomplete scans and checked-in example policies.
- Historical cleanup applies at most 500 messages per run unless explicitly overridden.
- The retention service defaults to audit mode.
- Retention apply mode requires an explicit confirmation value.
- The retention Graph client exposes moves to Deleted Items, not permanent deletion.
- Deployment uses code-only Lambda updates so existing Microsoft and Cloudflare environment variables are preserved.
- Message bodies and attachments are not stored for dashboard activity or cleanup reports.

## Repository map

```text
email_filter/                 shared auth, Graph, policy, review and retention code
handlers/                     Lambda handlers, including the retention sweeper
policies/                     checked-in example policies; personal policies are ignored
scripts/email-filter.sh       local authentication and AWS operations
scripts/lambda-deploy.sh      cached Lambda packaging and deployment
scripts/mailbox-cleanup.sh    resumable historical mailbox operations
mailbox_cleanup.py            historical audit/report/review/apply CLI
webhook_handler.py            deployed Junk Guard webhook
setup_webhook.py              secured Graph subscription setup
setup_token_interactive.py    Microsoft browser authentication and cache refresh
docs/                         architecture, operations, cleanup, policies and roadmap
tests/                        unit tests
```

## Development

Local development targets Python 3.14. CI currently runs on Python 3.11 and 3.14 while compatibility with existing deployments is maintained.

```bash
make bootstrap
make test
```

The webhook package is built inside the official Python Docker image for the deployed Lambda runtime and CPU architecture, so compiled dependencies are not taken from macOS.

## Documentation

- [Operations](docs/OPERATIONS.md)
- [Historical mailbox cleanup](docs/MAILBOX_CLEANUP.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Retention policies](docs/RETENTION_POLICIES.md)
- [Roadmap](docs/ROADMAP.md)

## Current roadmap

The historical audit now handles the existing backlog locally. Next work includes tuning the private policy from the unmatched review, deploying audit-only scheduled retention, mailbox-wide observe-only ingestion for new mail, a privacy-minimised API, and a read-only email dashboard in `scrapbook`.
