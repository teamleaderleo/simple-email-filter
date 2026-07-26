# Simple Email Filter for Outlook

A personal Outlook automation project with three related jobs:

- **Junk Guard** processes messages Outlook has already placed in Junk. It uses conservative deterministic checks and a Cloudflare Workers AI fallback, keeping uncertain mail.
- **Historical Cleanup** audits a large existing folder, checkpoints every page, reports retention candidates, and can move reviewed policy stages to Deleted Items in resumable batches.
- **Mailbox Retention** lets ordinary mail arrive normally, then identifies categories that may be moved to Deleted Items after an explicit retention period. Retention currently defaults to audit mode.

## Normal mailbox commands

Routine mailbox work uses three commands:

```bash
make mailbox-check
make mailbox-analyze
make mailbox-clean
```

`make mailbox-check` repairs the Python environment, verifies AWS and Microsoft authentication, and runs the repository checks once for each Git commit. Repeated commands on the same tested commit skip the duplicate test run.

`make mailbox-analyze` starts or resumes a non-destructive Inbox audit when no complete snapshot exists, then refreshes the privacy-minimised JSON, CSV and Excel package under `.mailbox-cleanup/inbox/export/`. Once cleanup has started, it preserves the saved plan and adds aggregate apply-progress files instead of refusing to export.

`make mailbox-clean` is the normal reviewed cleanup command. It:

1. runs the cached health checks
2. creates or resumes the complete local snapshot
3. creates the ignored private policy only when apply has not started
4. refreshes the analysis export
5. prints the exact whole-plan status
6. asks for one confirmation
7. resumes the reviewed `bulk`, `newsletters` and `operations` stages
8. adapts Graph concurrency and checkpoint size when Microsoft throttles requests
9. refreshes the export on success, failure or interruption

Successful and missing outcomes are checkpointed after every chunk. The command may restart a bounded pass automatically, but it stops on persistent failures, no progress, authentication failure or its configured pass limit. All moves go to Deleted Items; there is no permanent-delete operation.

Optional private defaults can be placed in:

```text
.mailbox-cleanup/inbox/config.env
```

For example:

```bash
MAILBOX_CLEAN_STAGES=bulk,newsletters,operations
MAILBOX_GRAPH_WORKERS=4
MAILBOX_STAGE_RUN_LIMIT=50000
MAILBOX_OPEN_EXPORT=1
```

The low-level audit, plan, stage and reset commands remain available for diagnosis and targeted work.

## Deployment operations

The repository owns its deployment procedure. After the initial AWS login, a normal webhook update is:

```bash
git switch main
git pull --ff-only
make deploy-webhook
```

That command checks the local machine and AWS resources, repairs the Python environment, runs tests, backs up the deployed Lambda, builds matching Linux dependencies, updates code without replacing secrets, refreshes Microsoft authentication only when needed, and recreates the secured Graph subscription.

When Junk Guard was unavailable for a bounded period, audit only the messages still in Junk during that window with the live rules and Gemma classifier. Timestamps carry their own offset; this example is July 26, 8:00–11:00 a.m. Beijing time:

```bash
JUNK_BACKFILL_START=2026-07-26T08:00:00+08:00 \
JUNK_BACKFILL_END=2026-07-26T11:00:00+08:00 \
make junk-backfill-audit
```

The audit deletes nothing and saves a private local plan. Review it with `make junk-backfill-report`, then apply only saved DELETE decisions with `make junk-backfill-apply`. See [Junk notification gap backfill](docs/JUNK_BACKFILL.md).

## Analysis package

The export directory contains:

```text
.mailbox-cleanup/inbox/export/
├── mailbox-analysis.xlsx
├── mailbox-summary.json
├── sender-summary.csv
├── policy-impact.csv
├── unmatched-senders.csv
├── subject-patterns.csv
├── unmatched-review.json
├── apply-progress.json
├── apply-progress.csv
├── manifest.json
└── README.txt
```

The package contains aggregate sender addresses and redacted subject patterns, but no message IDs, bodies, previews, attachments or raw subjects. The apply-progress files contain aggregate counts only and include no senders or subjects.

Useful low-level commands:

```text
make bootstrap                Create the Python 3.14 development environment
make doctor                   Check deployment prerequisites, including Docker and AWS resources
make test                     Run tests and syntax checks
make deploy-webhook           Perform the complete safe webhook update
make setup-webhook            Recreate only the Microsoft Graph subscription
make microsoft-login          Force a Microsoft browser login
make status                   Show deployment status without secrets
make logs-webhook             Follow webhook logs
make upgrade-runtime          Upgrade both email Lambdas to Python 3.14
make junk-backfill-audit      Audit a bounded Junk notification gap with live rules and Gemma
make junk-backfill-report     Print the saved private gap plan and apply status
make junk-backfill-apply      Delete only saved DELETE decisions still in Junk
make junk-backfill-reset      Delete only private local Junk backfill state
make mailbox-audit            Scan/resume Inbox and build a non-destructive report
make mailbox-report           Print the latest local mailbox report
make mailbox-review           Inspect unmatched senders and redacted subject patterns
make mailbox-export           Refresh analysis and aggregate apply-progress files
make mailbox-prepare-apply    Create the private policy and rebuild the local plan
make mailbox-plan             Preview a named stage or exact policy selection
make mailbox-apply-stage      Move one bounded chunk from a reviewed stage
make mailbox-apply-stage-all  Resume one stage with adaptive checkpointed chunks
make mailbox-apply            Legacy whole-plan bounded apply
make mailbox-reset            Delete only the private local cleanup state
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
- Progress exports contain aggregate stage and policy counts only.
- Historical cleanup apply mode refuses incomplete scans and checked-in example policies.
- One-command cleanup preserves an existing applied plan and never silently replans it.
- Adaptive apply retries Graph 429 and transient failures, follows retry delays, reduces pressure when necessary and increases it again only after clean chunks.
- Every apply path moves to Deleted Items rather than permanently deleting mail.
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
scripts/mailbox-cleanup.sh    low-level resumable historical mailbox operations
scripts/mailbox-apply-stage-all.sh  adaptive single-stage runner
scripts/mailbox-ops.sh        high-level check, analyze and clean commands
scripts/mailbox-export.sh     uploadable mailbox analysis package
junk_backfill.py              Junk gap audit/report/apply CLI
mailbox_cleanup.py            historical audit/report/review/plan/apply CLI
mailbox_export.py             local JSON/CSV/XLSX and progress export CLI
webhook_handler.py            deployed Junk Guard webhook
setup_webhook.py              secured Graph subscription setup
setup_token_interactive.py    Microsoft browser authentication and cache refresh
docs/                         architecture, operations, cleanup, policies and roadmap
tests/                        unit tests
```

## Development

Local development targets Python 3.14. CI is configured for Python 3.11 and 3.14; when the GitHub Actions allowance is unavailable, run the same repository checks locally.

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

The Junk Guard webhook, bounded gap replay, historical audit, progress-aware analysis export and adaptive staged apply workflow now cover notification outages and the existing backlog. Next work includes deploying audit-only scheduled retention, mailbox-wide observe-only ingestion for new mail, a privacy-minimised API, and a read-only email dashboard in `scrapbook`.
