# Historical mailbox cleanup

This workflow handles a large existing Outlook folder, such as an Inbox with tens of thousands of messages. It is separate from the scheduled retention Lambda.

## Normal workflow

Routine use is now three commands:

```bash
make mailbox-check
make mailbox-analyze
make mailbox-clean
```

### `make mailbox-check`

This command:

1. repairs the Python 3.14 virtual environment when needed
2. verifies AWS and Microsoft authentication
3. runs unit tests, Python compilation and shell syntax checks
4. records the tested Git commit under the ignored private state directory
5. skips the duplicate test run when the same clean commit is checked again

A dirty working tree is always tested and is never recorded as a reusable successful test stamp.

### `make mailbox-analyze`

This command is non-destructive. It:

1. runs the local checks
2. starts or resumes the Inbox audit only when no complete snapshot exists
3. reuses a complete snapshot without requiring AWS or Microsoft access
4. refreshes the privacy-minimised JSON, CSV and Excel package
5. includes aggregate apply progress when cleanup has already started

The command never rebuilds or replaces an applied plan. When apply results exist, it uses the policy path recorded by that saved plan.

### `make mailbox-clean`

This is the normal reviewed cleanup command. It:

1. runs the cached checks and verifies authentication
2. creates or resumes the complete local snapshot
3. creates the ignored private policy only before apply has started
4. refreshes the analysis package
5. prints the whole-plan status
6. asks once for `MOVE_REVIEWED_MAIL_TO_DELETED_ITEMS`
7. resumes the `bulk`, `newsletters` and `operations` stages
8. restarts bounded stage passes automatically when work remains
9. refreshes exports on success, failure or interruption

The command stops on persistent Graph failures, no progress, authentication failure or the configured pass limit. It does not loop without a bound.

Optional private settings may be stored in:

```text
.mailbox-cleanup/inbox/config.env
```

Example:

```bash
MAILBOX_CLEAN_STAGES=bulk,newsletters,operations
MAILBOX_CLEAN_MAX_PASSES=20
MAILBOX_STAGE_RUN_LIMIT=50000
MAILBOX_GRAPH_WORKERS=4
MAILBOX_OPEN_EXPORT=1
```

## What the audit stores

The audit scans the Inbox in complete Microsoft Graph pages of up to 999 messages, writes a checkpoint after every page and resumes from the saved continuation URL after interruption.

Private state is kept under:

```text
.mailbox-cleanup/inbox/
├── checkpoint.json
├── messages.jsonl
├── plan.jsonl
├── summary.json
├── apply-results.jsonl
├── config.env
├── .tested-commit
└── export/
```

The local snapshot contains message IDs, sender addresses, subjects, timestamps, read state and categories because those fields are required for policy evaluation. It does not contain message bodies, previews or attachments.

Do not upload `messages.jsonl`, `plan.jsonl` or `apply-results.jsonl`.

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

The workbook includes:

- **Overview** — audit totals, policy path and privacy declaration
- **Policy Impact** — matched, protected, retained and selected counts per policy
- **Sender Summary** — one aggregate row per sender
- **Unmatched Senders** — aggregate signals and review flags
- **Subject Patterns** — redacted patterns grouped by sender
- **Data Dictionary** — file descriptions and upload guidance

`apply-progress.json` and `apply-progress.csv` report moved, pending, missing and latest-failure counts by stage and policy. They contain no message IDs, senders, subjects, bodies or previews.

The most useful upload pair remains:

```text
mailbox-analysis.xlsx
mailbox-summary.json
```

`mailbox-summary.json` now embeds the same aggregate apply-progress object.

## Reviewing unmatched mail

Sender names alone are not enough for broad rules. The same sender can carry receipts, security alerts, order updates, recruiter messages and marketing.

```bash
make mailbox-review
```

The review reads the saved snapshot and does not contact Microsoft. It includes sender counts, read state, date ranges, yearly counts, redacted subject patterns and repeated safety signals.

Focused review remains available:

```bash
MAILBOX_REVIEW_SENDER=store-news@amazon.ca make mailbox-review
MAILBOX_REVIEW_DOMAIN=linkedin.com make mailbox-review
```

## Reviewed stages

| Stage | Policies |
|---|---|
| `bulk` | promotions, job alerts, LinkedIn social notifications, ArtStation digests, short-lived digests, Amazon review requests and Uber promotions |
| `newsletters` | reviewed technical and cultural newsletters, entertainment/community feeds, career networks, political updates and financial marketing |
| `operations` | shipment tracking, Uber Eats orders, building notices and deployment alerts |
| `all` | every selected policy in the saved plan |

The high-level cleanup runs the first three stages by default and reports any selected policy that is not assigned to them.

## Adaptive Microsoft Graph apply

The low-level continuous stage runner remains available:

```bash
make mailbox-apply-stage-all
```

Each Microsoft Graph JSON batch contains at most 20 move requests. The runner uses bounded parallel workers and checkpointed chunks.

It handles pressure as follows:

- retries top-level 429 and transient 5xx responses
- follows Microsoft `Retry-After` values when supplied
- retries throttled or omitted per-message batch responses
- saves every successful or missing outcome before continuing
- reduces workers from the configured ceiling when failures remain
- reduces checkpoint size after reaching one worker
- increases checkpoint size and workers again only after repeated clean chunks
- stops at one worker and the minimum chunk if failures persist

Default limits:

```text
MAILBOX_STAGE_LIMIT=5000
MAILBOX_STAGE_RUN_LIMIT=50000 when invoked through make mailbox-clean
MAILBOX_GRAPH_WORKERS=4
MAILBOX_MIN_ADAPTIVE_CHUNK=500
MAILBOX_CLEAN_MAX_PASSES=20
```

These are safeguards, not targets. The runner ends as soon as the selected stage is complete.

## Low-level troubleshooting

The original commands remain available:

```text
make mailbox-audit
make mailbox-report
make mailbox-review
make mailbox-export
make mailbox-prepare-apply
make mailbox-plan
make mailbox-apply-stage
make mailbox-apply-stage-all
make mailbox-apply
make mailbox-reset
```

Preview another stage without contacting Microsoft:

```bash
MAILBOX_APPLY_STAGE=newsletters make mailbox-plan
```

Select exact policy IDs:

```bash
MAILBOX_APPLY_POLICIES=shipment-tracking,uber-order-notifications \
make mailbox-plan
```

Process one bounded chunk:

```bash
MAILBOX_APPLY_STAGE=newsletters \
MAILBOX_STAGE_LIMIT=2000 \
make mailbox-apply-stage
```

All apply commands move mail only to Deleted Items. There is no permanent-delete operation.

## Interrupted work

Audit, export and apply operations are resumable.

- An interrupted audit continues from its last complete Graph page.
- An interrupted apply skips messages already recorded as moved or missing.
- Latest failed outcomes remain pending and are retried.
- `make mailbox-analyze` refreshes the progress package without replacing the plan.
- `make mailbox-clean` resumes the configured stages from their recorded outcomes.

## Starting over

Reset deletes only the private local scan state. It does not change Outlook:

```bash
make mailbox-reset
```

It requires:

```text
RESET_LOCAL_STATE
```

After apply has started, reset is the deliberate way to create a different snapshot or policy plan. Audit and export commands will not silently replace the applied plan.

## Other folders

Inbox is the default. Use a separate state directory for another folder:

```bash
MAILBOX_FOLDER=archive \
MAILBOX_STATE_DIR=.mailbox-cleanup/archive \
make mailbox-audit
```

The high-level commands are optimised for the default Inbox state. Low-level commands remain the clearer choice for one-off alternate-folder work.

## Relationship to scheduled retention

Historical cleanup handles the existing backlog locally and in resumable runs. The scheduled retention Lambda handles new mail later in audit-first operation. Both use the same policy parser and planner so retention decisions remain consistent.
