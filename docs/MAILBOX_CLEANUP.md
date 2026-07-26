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

## Reviewing and applying

The checked-in example policy is safe for audit but intentionally blocked from apply mode. Copy it to the ignored private path and review it:

```bash
cp policies/personal.example.json policies/personal.json
```

Then edit the private policy and rebuild the report or export:

```bash
MAILBOX_POLICY_PATH=policies/personal.json make mailbox-export
```

When the summary and unmatched review look correct:

```bash
MAILBOX_POLICY_PATH=policies/personal.json make mailbox-apply
```

The command asks you to type:

```text
MOVE_TO_DELETED_ITEMS
```

Each run moves at most 500 planned messages by default. It records an outcome for every message and resumes on the next run. Messages are moved to Deleted Items; there is no permanent-delete operation.

Change the batch cap for one run:

```bash
MAILBOX_POLICY_PATH=policies/personal.json \
MAILBOX_APPLY_LIMIT=1000 \
make mailbox-apply
```

A failed message is eligible for retry on a later run. A message already moved or no longer found is treated as complete so the plan continues.

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
