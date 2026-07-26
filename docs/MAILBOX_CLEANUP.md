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

## Local files and privacy

The scan state is ignored by Git and written with private file permissions where the operating system supports them:

```text
.mailbox-cleanup/inbox/
├── checkpoint.json
├── messages.jsonl
├── plan.jsonl
├── summary.json
└── apply-results.jsonl
```

The local snapshot contains message IDs, sender addresses, subjects, timestamps, read state and categories because those fields are needed to evaluate policies. It does not contain message bodies, previews or attachments.

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

Then rebuild the report using the private policy:

```bash
MAILBOX_POLICY_PATH=policies/personal.json make mailbox-audit
```

When the summary looks correct:

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

Use a separate state directory per folder.

## Relationship to scheduled retention

Historical cleanup handles the existing backlog locally and in bounded runs. The scheduled retention Lambda handles new mail later in audit-first operation. Both use the same policy parser and planner so retention decisions stay consistent.
