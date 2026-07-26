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

A message expires only when it is both older than the age limit and outside the newest retained set. This conservative rolling mode works well for ArtStation prints, job alerts, LinkedIn notifications and deployment logs.

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

Mixed senders should use subject-specific rules before any broad sender policy. The checked-in example does this for Uber: security notices are protected, order notifications last 180 days, and explicit promotion subjects last 45 days. Unrecognised subjects from that sender remain unmatched.

## Suggested starting points

| Category | Starting retention |
|---|---:|
| Receipts, invoices, refunds, cancellations, warranties | Forever |
| Order confirmations | Forever |
| Financial and brokerage account records | Forever |
| Job application confirmations and status records | Forever |
| Shipment and delivery tracking | 180 days |
| Building package notices | 180 days |
| Routine building announcements | 365 days |
| Lease, legal, management and maintenance records | Forever |
| ArtStation print announcements | 365 days and latest 150 |
| ArtStation daily digests | 45 days |
| Job alerts | 30 days and latest 25 per feed |
| LinkedIn social notifications | 90 days and latest 50 |
| Deployment alerts | 14 days and latest 20 |
| General promotions | 45 days |

Start with audit runs. Tighten retention only after reviewing real counts.

## Reviewed example policy

`policies/personal.example.json` contains high-confidence rules derived from an aggregate mailbox audit. It protects purchase, financial, application and formal property records before applying broader retention to known job, social, building and promotional feeds.

The example remains blocked from apply mode. Copy it to the ignored private path only after reviewing the updated audit:

```bash
cp policies/personal.example.json policies/personal.json
MAILBOX_POLICY_PATH=policies/personal.json make mailbox-audit
```
