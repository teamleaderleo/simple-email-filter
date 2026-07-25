# Architecture

The project is evolving from a Junk-folder classifier into two cooperating systems.

## Junk Guard

Junk Guard handles messages that Outlook has already placed in Junk. It may act quickly, but only for high-confidence phishing, scams, casino spam, malware and deceptive account alerts. The current handlers continue to provide this behaviour while shared code is extracted incrementally.

## Mailbox Retention

Mailbox Retention lets ordinary mail arrive, remain visible in Outlook's Focused or Other view, and expire later according to explicit policies.

```text
New message
    │
    ├── Outlook delivers and classifies Focused / Other
    │
    └── ingestion records a policy category and optional expiry
                         │
                         ▼
                 daily retention sweep
                         │
                         ├── audit by default
                         └── move expired mail to Deleted Items
```

The retention service never permanently deletes a message. Apply mode moves selected messages to Deleted Items and requires a separate confirmation environment variable.

## Repository boundaries

### `simple-email-filter`

Owns Microsoft Graph credentials, webhook processing, policy evaluation, expiry actions and privacy-minimised activity records.

### `scrapbook`

Owns the authenticated web interface. It will consume a narrow private API and must never receive Microsoft access tokens, raw message bodies or attachments.

## Privacy model

Activity records may include policy identifiers, timestamps, actions, aggregate counts and one-way message identifier hashes when message-level idempotency is needed.

Activity records should avoid message bodies, previews, attachments, full Outlook links, Microsoft tokens and exact subjects by default.

## Safety invariants

1. Audit is the default mode.
2. Apply requires `RETENTION_APPLY_CONFIRMATION=MOVE_TO_DELETED_ITEMS`.
3. The service exposes no permanent-delete operation.
4. Every policy must constrain sender, sender domain or subject.
5. First-match precedence allows narrow permanent-record policies to protect messages from broader expiry policies.
6. Deleted Items is excluded from retention planning.
7. Apply refuses checked-in example policy files.
8. Personal policy files should be supplied at deployment time rather than committed to a public repository.
