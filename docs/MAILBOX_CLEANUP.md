# Historical mailbox cleanup

This workflow is for a large existing Outlook folder, such as an Inbox with tens of thousands of messages. It is separate from the scheduled retention Lambda.

The default command is non-destructive:

```bash
git switch main
git pull --ff-only
make mailbox-audit
```

## What the audit does

`make mailbox-audit`:

1. checks the existing AWS and Microsoft authentication
2. scans the Inbox in complete Microsoft Graph pages of up to 999 messages
3. writes a checkpoint after every page
4. resumes from the saved Graph continuation URL after interruption
5. stores a private local snapshot under `.mailbox-cleanup/inbox/`
6. evaluates the shared retention policies
7. writes a local plan and summary
8. moves no messages

The report includes:

- total scanned, read and unread counts
- matched and unmatched counts
- messages protected forever
- messages currently kept by a temporary retention rule
- messages eligible to move to Deleted Items
- counts by year and policy
- the largest senders and domains
- the largest unmatched senders and domains, which are the best candidates for new rules

Print the latest report without contacting Microsoft:

```bash
make mailbox-report
```

## Reviewing unmatched mail

Sender names alone are not enough for broad rules. The same sender can carry receipts, security alerts, order updates, recruiter messages, and marketing. Use the private local review command before adding a policy:

```bash
make mailbox-review
```

It reads the existing local snapshot and does not contact Microsoft. The output includes:

- unmatched counts per sender
- read and unread counts
- first and last received dates
- counts by year
- redacted subject patterns
- keyword signals for security, finance, purchase records, delivery, property/legal, job applications, and promotions
- a manual-review flag when repeated potentially important signals appear

The output does not contain message IDs, bodies, previews or attachments. Obvious email addresses, URLs, long identifiers, UUIDs and numbers in subjects are replaced before display.

Review a single sender:

```bash
MAILBOX_REVIEW_SENDER=store-news@amazon.ca make mailbox-review
```

Review a whole domain:

```bash
MAILBOX_REVIEW_DOMAIN=linkedin.com make mailbox-review
```

Change the number of senders or subject patterns shown:

```bash
MAILBOX_REVIEW_TOP=40 \
MAILBOX_REVIEW_SAMPLES=8 \
make mailbox-review
```

The review command uses the policy path recorded in the latest audit, so unmatched results stay consistent with that plan.

## Exporting an analysis package

Use the export command instead of pasting a large JSON report into chat:

```bash
make mailbox-export
```

The export is local-only. It rebuilds the audit plan from the saved snapshot using the current policy file, so policy edits are reflected without another Microsoft Graph scan. It writes:

```text
.mailbox-cleanup/inbox/export/
├── mailbox-analysis.xlsx
├── mailbox-summary.json
├── sender-summary.csv
├── policy-impact.csv
├── unmatched-senders.csv
├── subject-patterns.csv
├── unmatched-review.json
├── manifest.json
└── README.txt
```

`mailbox-analysis.xlsx` contains these sheets:

- **Overview** — audit totals, policy path and privacy declaration
- **Policy Impact** — matched, protected, retained and selected counts per policy
- **Sender Summary** — one aggregate row per sender
- **Unmatched Senders** — aggregate signals and review flags
- **Subject Patterns** — redacted patterns grouped by sender
- **Data Dictionary** — file descriptions and upload guidance

The workbook has frozen headers, filters, sensible column widths and percentage formatting. CSV files use UTF-8 with a byte-order mark so Excel opens sender names and symbols correctly.

The export contains sender addresses, domains, aggregate counts and redacted subject patterns. It contains no message IDs, bodies, previews, attachments or raw subjects. The most useful upload pair is:

```text
mailbox-analysis.xlsx
mailbox-summary.json
```

Change the output path or number of patterns retained per unmatched sender:

```bash
MAILBOX_EXPORT_DIR="$HOME/Desktop/mailbox-analysis" \
MAILBOX_EXPORT_SAMPLES=8 \
make mailbox-export
```

Export refuses incomplete scans because rolling retention rules require the full folder history. It also refuses to rebuild a plan after message moves have started in that state directory.

## Local files and privacy

The scan state is ignored by Git and written with private file permissions where the operating system supports them:

```text
.mailbox-cleanup/inbox/
├── checkpoint.json
├── messages.jsonl
├── plan.jsonl
├── summary.json
├── export/
└── apply-results.jsonl
```

The local snapshot contains message IDs, sender addresses, subjects, timestamps, read state and categories because those fields are needed to evaluate policies. It does not contain message bodies, previews or attachments. Do not upload `messages.jsonl`, `plan.jsonl` or `apply-results.jsonl`; upload files from the `export/` directory instead.

## Interrupted scans

Run the same command again:

```bash
make mailbox-audit
```

The scanner resumes from its last completed page. A crash after writing a page but before updating the checkpoint can duplicate that page in the JSONL file; report generation deduplicates by immutable message ID.

To test with only a few pages while preserving a resumable checkpoint:

```bash
bash scripts/mailbox-cleanup.sh audit --max-pages 3
```

Apply mode refuses an incomplete scan because rolling rules such as `keepLatest` require the complete folder history.

## Preparing the reviewed apply plan

The checked-in example policy is intentionally blocked from apply mode. Prepare an ignored private copy and rebuild the plan from the saved snapshot with one command:

```bash
make mailbox-prepare-apply
```

This command:

1. creates `policies/personal.json` from the reviewed example when the private file does not exist
2. leaves an existing private policy untouched
3. rebuilds `plan.jsonl` and `summary.json` locally without contacting Microsoft Graph
4. records the private policy path in the summary
5. prints the default `bulk` stage preview

The private policy is ignored by Git. Edit it directly when personal overrides are needed, then rerun `make mailbox-prepare-apply`.

## Previewing named apply stages

The plan is divided into reviewed groups so tens of thousands of messages do not have to be applied as one undifferentiated operation:

| Stage | Policies |
|---|---|
| `bulk` | retail and restaurant promotions, job alerts, LinkedIn social notifications, ArtStation digests, short-lived digests, Amazon review requests and Uber promotions |
| `newsletters` | reviewed technical and cultural newsletters, entertainment/community feeds, career networks, political updates and financial marketing |
| `operations` | shipment tracking, Uber Eats orders, building notices and deployment alerts |
| `all` | every selected policy in the plan |

Preview the default `bulk` stage without contacting Microsoft:

```bash
make mailbox-plan
```

Preview another stage:

```bash
MAILBOX_APPLY_STAGE=newsletters make mailbox-plan
```

Select exact policy ids instead of a named stage:

```bash
MAILBOX_APPLY_POLICIES=shipment-tracking,uber-order-notifications \
make mailbox-plan
```

The preview reports totals, pending messages, moved messages, messages no longer found, the most recent failed attempts and counts by policy. It also reports pending work across the whole plan.

## Applying a stage

After reviewing the preview:

```bash
make mailbox-apply-stage
```

The command prints the same stage preview and asks you to type:

```text
MOVE_TO_DELETED_ITEMS
```

A staged run moves at most 5,000 messages by default. It records an outcome for every message, retries earlier failures on a later run and resumes the same selection when the command is repeated. A message already moved or no longer found is treated as complete.

Continue the selected stage by rerunning the same command:

```bash
make mailbox-apply-stage
```

Change the stage or batch cap for one run:

```bash
MAILBOX_APPLY_STAGE=newsletters \
MAILBOX_STAGE_LIMIT=2000 \
make mailbox-apply-stage
```

The older `make mailbox-apply` command remains available for the whole plan and uses a 500-message default cap. Named stages are easier to review and track.

All apply commands move mail only to Deleted Items. There is no permanent-delete operation.

## Starting over

The reset command deletes only the private local scan state. It does not change Outlook:

```bash
make mailbox-reset
```

It requires the exact confirmation:

```text
RESET_LOCAL_STATE
```

After any apply run, reset is the deliberate way to build a new snapshot. The audit command will not silently replace a state directory containing apply results.

## Other folders

Inbox is the default. A well-known folder name or Graph folder ID can be supplied:

```bash
MAILBOX_FOLDER=archive \
MAILBOX_STATE_DIR=.mailbox-cleanup/archive \
make mailbox-audit
```

Use a separate state directory per folder. Export uses the same state directory and writes its own `export/` child directory.

## Relationship to scheduled retention

Historical cleanup handles the existing backlog locally and in bounded runs. The scheduled retention Lambda handles new mail later in audit-first operation. Both use the same policy parser and planner so retention decisions stay consistent.
