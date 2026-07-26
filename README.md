# Simple Email Filter for Outlook

A personal Outlook automation project with three related jobs:

- **Junk Guard** processes messages Outlook has already placed in Junk. It uses conservative deterministic checks and a Cloudflare Workers AI fallback, keeping uncertain mail.
- **Historical Cleanup** audits a large existing folder, checkpoints every page, reports retention candidates, and can move reviewed policy stages to Deleted Items in resumable batches.
- **Mailbox Retention** lets ordinary mail arrive normally, then identifies categories that may be moved to Deleted Items after an explicit retention period. Retention currently defaults to audit mode.

## Routine operations

The repository owns its deployment procedure. After the initial AWS login, a normal webhook update is:

```bash
git switch main
git pull --ff-only
make deploy-webhook
```

That command checks the local machine and AWS resources, repairs the Python environment, runs tests, backs up the deployed Lambda, builds matching Linux dependencies, updates code without replacing secrets, refreshes Microsoft authentication only when needed, and recreates the secured Graph subscription.

When Junk Guard was unavailable for a bounded period, audit only the messages still in Junk during that window with the live rules and Gemma classifier:

```bash
JUNK_BACKFILL_START=2026-07-25T08:00:00-07:00 \
JUNK_BACKFILL_END=2026-07-25T11:00:00-07:00 \
make junk-backfill-audit
```

The audit deletes nothing and saves a private local plan. Review it with `make junk-backfill-report`, then apply only saved DELETE decisions with `make junk-backfill-apply`. See [Junk notification gap backfill](docs/JUNK_BACKFILL.md).

For a large existing Inbox, start with the non-destructive historical audit:

```bash
make mailbox-audit
```

The audit scans or resumes the Inbox, writes private local checkpoints, and produces counts for protected, retained, eligible, and unmatched messages. It moves nothing. Review unmatched senders without another Microsoft request:

```bash
make mailbox-review
```

Create a compact analysis package instead of pasting a large report into chat:

```bash
make mailbox-export
```

The export re-evaluates the saved snapshot against the current policy without contacting Microsoft. It writes aggregate JSON, flat CSV files and a multi-sheet Excel workbook under `.mailbox-cleanup/inbox/export/`. The uploadable files contain sender addresses and redacted subject patterns, but no message IDs, bodies, previews, attachments or raw subjects.

After the policy review is complete, prepare the ignored private policy and preview the default staged cleanup:

```bash
make mailbox-prepare-apply
```

Then apply the selected stage in resumable batches of up to 5,000 messages:

```bash
make mailbox-apply-stage
```

Named stages separate high-volume bulk mail, reviewed newsletters, and operational notifications. See [Historical mailbox cleanup](docs/MAILBOX_CLEANUP.md).

Useful commands:

```text
make bootstrap              Create the Python 3.14 development environment
make doctor                 Check AWS login, resources and Lambda configuration
make test                   Run tests and syntax checks
make deploy-webhook         Perform the complete safe webhook update
make setup-webhook          Recreate only the Microsoft Graph subscription
make microsoft-login        Force a Microsoft browser login
make status                 Show deployment status without secrets
make logs-webhook           Follow webhook logs
make upgrade-runtime        Upgrade both email Lambdas to Python 3.14
make junk-backfill-audit    Audit a bounded Junk notification gap with live rules and Gemma
make junk-backfill-report   Print the saved private gap plan and apply status
make junk-backfill-apply    Delete only saved DELETE decisions still in Junk
make junk-backfill-reset    Delete only private local Junk backfill state
make mailbox-audit          Scan/resume Inbox and build a non-destructive report
make mailbox-report         Print the latest local mailbox report
make mailbox-review         Inspect unmatched senders and redacted subject patterns
make mailbox-export         Build uploadable JSON, CSV and Excel analysis files
make mailbox-prepare-apply  Create the private policy and rebuild the local plan
make mailbox-plan           Preview a named stage or exact policy selection
make mailbox-apply-stage    Move up to 5,000 messages from one reviewed stage
make mailbox-apply          Legacy whole-plan bounded apply
make mailbox-reset          Delete only the private local cleanup state
```

See [Operations](docs/OPERATIONS.md) for deployment setup, rollback and troubleshooting.

## Safety defaults

- Junk Guard deletes only messages already in Junk and keeps uncertain classifications.
- Webhook notifications must match the stored subscription ID and client state.
- Notifications are processed by exact immutable Outlook message ID.
- Junk gap backfill is bounded by explicit timestamps and fetches only messages still in Junk.
- Junk gap audit mode deletes nothing and reuses the deployed Lambda's live classifier configuration without printing or saving its API token.
- Junk gap apply uses only saved DELETE decisions, rechecks the exact message and Junk folder, and refuses truncated audits.
- Historical cleanup audit mode moves nothing and checkpoints after complete pages.
- Historical cleanup review mode is local-only and redacts obvious subject identifiers.
- Historical cleanup exports contain aggregate sender data and redacted subject patterns, not raw message-level content.
- Historical cleanup apply mode refuses incomplete scans and checked-in example policies.
- Named apply stages print exact pending counts before asking for confirmation.
- Staged apply moves at most 5,000 messages per run and resumes from recorded outcomes.
- The retention service defaults to audit mode.
- Retention apply mode requires an explicit confirmation value.
- The retention Graph client exposes moves to Deleted Items, not permanent deletion.
- Deployment uses code-only Lambda updates so existing Microsoft and Cloudflare environment variables are preserved.
- Message bodies and attachments are not stored for dashboard activity or cleanup reports.

## Repository map

```text
email_filter/                 shared auth, Graph, policy, review, export, backfill and retention code
handlers/                     Lambda handlers, including the retention sweeper
policies/                     checked-in example policies; personal policies are ignored
scripts/email-filter.sh       local authentication and AWS operations
scripts/lambda-deploy.sh      cached Lambda packaging and deployment
scripts/junk-backfill.sh      bounded Junk notification gap audit/apply operations
scripts/mailbox-cleanup.sh    resumable historical mailbox operations
scripts/mailbox-export.sh     uploadable mailbox analysis package
junk_backfill.py              Junk gap audit/report/apply CLI
mailbox_cleanup.py            historical audit/report/review/plan/apply CLI
mailbox_export.py             local JSON/CSV/XLSX export CLI
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
- [Junk notification gap backfill](docs/JUNK_BACKFILL.md)
- [Historical mailbox cleanup](docs/MAILBOX_CLEANUP.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Retention policies](docs/RETENTION_POLICIES.md)
- [Roadmap](docs/ROADMAP.md)

## Current roadmap

The Junk Guard webhook, bounded gap replay, historical audit, analysis export and staged apply workflow now cover both notification outages and the existing backlog. Next work includes deploying audit-only scheduled retention, mailbox-wide observe-only ingestion for new mail, a privacy-minimised API, and a read-only email dashboard in `scrapbook`.
