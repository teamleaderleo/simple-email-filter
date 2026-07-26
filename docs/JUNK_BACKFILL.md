# Junk notification gap backfill

This workflow repairs a bounded period when the Microsoft token, Graph subscription, Lambda, or classifier was unavailable and messages already placed in Junk were not processed by Junk Guard.

It handles **only messages still in the Junk Email folder**. It does not inspect or delete Inbox mail.

## Audit the gap

Supply timestamps with a UTC offset. For the July 25, 2026 Pacific-time gap discussed during setup:

```bash
JUNK_BACKFILL_START=2026-07-25T08:00:00-07:00 \
JUNK_BACKFILL_END=2026-07-25T11:00:00-07:00 \
make junk-backfill-audit
```

The audit:

1. checks AWS and Microsoft authentication
2. reads the deployed `email-webhook-handler` Lambda configuration
3. reuses its current Cloudflare account, API token, and model without printing or saving the token
4. fetches only messages still in Junk inside the exact time window
5. skips immutable message IDs already recorded by Junk Guard
6. runs the same deterministic rules and Gemma fallback used by the live webhook
7. saves a private local plan under `.junk-backfill/`
8. deletes nothing

The live default model is currently `@cf/google/gemma-4-26b-a4b-it`, unless the Lambda's `CLOUDFLARE_MODEL` environment variable specifies another model.

The terminal shows timestamp, sender, decision, and decision source. The private plan stores message IDs, sender addresses, timestamps, and decisions. It does not store subjects, previews, bodies, attachments, or links.

## Review the plan

```bash
make junk-backfill-report
```

The report shows:

- fetched and classified counts
- DELETE and KEEP totals
- already-processed messages
- decision-source counts
- whether the configured message cap truncated the audit
- apply progress and remaining DELETE decisions

If `truncated` is true, increase the cap and rerun the audit before apply:

```bash
JUNK_BACKFILL_START=2026-07-25T08:00:00-07:00 \
JUNK_BACKFILL_END=2026-07-25T11:00:00-07:00 \
JUNK_BACKFILL_MAX_MESSAGES=1000 \
make junk-backfill-audit
```

Apply refuses a truncated plan.

## Apply saved DELETE decisions

```bash
make junk-backfill-apply
```

The command prints the saved window and pending count, then requires:

```text
DELETE_JUNK_WINDOW
```

Each run processes at most 250 DELETE decisions by default. Before deleting each message it:

1. checks the shared idempotency record again
2. fetches the exact immutable message ID
3. confirms that the message is still in Junk
4. deletes only the saved DELETE decision
5. records the result locally and in the shared 30-day idempotency table

KEEP decisions are never included in apply. Messages that moved out of Junk, disappeared, or were processed after the audit are skipped. Failed items remain pending for a later run.

Change the batch cap for one run:

```bash
JUNK_BACKFILL_APPLY_LIMIT=50 make junk-backfill-apply
```

## Authentication recovery

Graph requests force a fresh silent Microsoft token before the audit or apply run and retry one rejected request after another forced refresh. When the refresh token itself is no longer valid:

```bash
make microsoft-login
```

Then rerun the same audit or apply command.

## Reset local state

```bash
make junk-backfill-reset
```

It requires:

```text
RESET_JUNK_BACKFILL
```

Reset deletes only `.junk-backfill/`. It does not change Outlook or the deployed Lambda.

## Limits and privacy

- Audit default cap: 500 fetched messages
- Apply default cap: 250 DELETE decisions per run
- Audit maximum: 5,000 messages
- Apply maximum: 1,000 decisions per run
- State directory: `.junk-backfill/`, ignored by Git
- No permanent mailbox-wide sweep
- No Inbox processing
- No subject, preview, body, attachment, or link retained in the saved plan
