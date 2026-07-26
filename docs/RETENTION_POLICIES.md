# Retention policies

A retention policy answers two separate questions:

1. Which messages belong to this category?
2. When does that category stop being useful?

The system deliberately does not decide whether a message may arrive. Outlook remains responsible for Focused/Other, so recent promotions, alerts and artwork remain browseable.

## Modes

### `forever`

Permanent records such as receipts, invoices, order confirmations, refunds, warranties, leases, legal notices, application records and financial account history.

### `days`

Expire every matching message after a fixed age. Useful for routine marketing, shipment tracking and building announcements.

### `latest`

Keep only the newest number of matching messages. Useful for machine-generated status feeds where a rolling history is more useful than a long archive.

### `days_and_latest`

A message expires only when it is both older than the age limit and outside the newest retained set. This conservative rolling mode works well for artwork feeds, job alerts, newsletters, social notifications and deployment logs.

## Rolling groups

By default, `keepLatest` is calculated across the whole policy. That is appropriate for a policy matching one feed.

A policy containing several senders should normally set `groupBy` to `sender`, so each feed receives its own retained set:

```json
{
  "id": "job-alerts",
  "priority": 40,
  "match": {
    "senders": [
      "alerts-one@example.com",
      "alerts-two@example.com"
    ]
  },
  "retention": {
    "mode": "days_and_latest",
    "days": 30,
    "keepLatest": 25,
    "groupBy": "sender"
  }
}
```

This keeps the latest 25 messages from each sender. Without `groupBy`, the policy would keep only the latest 25 messages across both senders combined.

`groupBy` is supported only by `latest` and `days_and_latest`. Its allowed values are `policy` and `sender`; `policy` is the default.

## Precedence

Policies are evaluated by ascending `priority`, then by identifier. The first match wins. Put narrow protective rules before broad sender or domain rules.

```json
{
  "id": "store-receipts",
  "priority": 10,
  "match": {
    "senders": ["orders@example.com"],
    "subjectContains": ["receipt", "invoice"]
  },
  "retention": {"mode": "forever"}
}
```

A broader promotional policy for the same sender can then use a later priority without catching receipts.

Mixed senders should use subject-specific rules before any broad sender policy. The checked-in example does this for Uber: security and trip records are protected, order notifications last 180 days, and explicit promotion subjects last 45 days. Unrecognised subjects from that sender remain unmatched.

## Suggested starting points

| Category | Starting retention |
|---|---:|
| Receipts, invoices, refunds, cancellations, warranties | Forever |
| Order confirmations and trip records | Forever |
| Financial, payment and brokerage account records | Forever |
| Account-security notices | Forever |
| Job application confirmations and status records | Forever |
| Shipment and delivery tracking | 180 days |
| Building package notices | 180 days |
| Routine building announcements | 365 days |
| Lease, legal, management and maintenance records | Forever |
| ArtStation print announcements | 365 days and latest 150 |
| ArtStation daily digests | 45 days |
| Job alerts | 30 days and latest 25 per sender |
| Recruiter-network notifications | 90 days and latest 25 per sender |
| Newsletters | 180 days and latest 20 per sender |
| Entertainment and community notifications | 180 days and latest 30 per sender |
| LinkedIn social notifications | 90 days and latest 50 per sender |
| Deployment alerts | 14 days and latest 20 |
| Financial-product marketing | 90 days and latest 20 per sender |
| General promotions and review requests | 45 days |

Start with audit runs. Tighten retention only after reviewing real counts.

## Reviewed example policy

`policies/personal.example.json` contains high-confidence rules derived from aggregate mailbox audits. It protects purchase, financial, application, account-security and formal property records before applying broader retention to reviewed job, social, newsletter, entertainment, building and promotional feeds.

The example remains blocked from apply mode. Copy it to the ignored private path only after reviewing the updated audit:

```bash
cp policies/personal.example.json policies/personal.json
MAILBOX_POLICY_PATH=policies/personal.json make mailbox-audit
make mailbox-export
```
