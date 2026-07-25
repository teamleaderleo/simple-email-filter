# Retention policies

A retention policy answers two separate questions:

1. Which messages belong to this category?
2. When does that category stop being useful?

The system deliberately does not decide whether a message may arrive. Outlook remains responsible for Focused/Other, so recent promotions, alerts and artwork remain browseable.

## Modes

### `forever`

Permanent records such as receipts, invoices, order confirmations, refunds, warranties, leases and legal notices.

### `days`

Expire every matching message after a fixed age. Useful for routine marketing, shipment tracking and building announcements.

### `latest`

Keep only the newest number of matching messages. Useful for machine-generated status feeds where a rolling history is more useful than a long archive.

### `days_and_latest`

A message expires only when it is both older than the age limit and outside the newest retained set. This conservative rolling mode works well for ArtStation prints, job alerts and deployment logs.

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

## Suggested starting points

| Category | Starting retention |
|---|---:|
| Receipts, invoices, refunds, warranties | Forever |
| Order confirmations | Forever |
| Shipment and delivery tracking | 180 days |
| Building package notices | 180 days |
| Routine building announcements | 365 days |
| Lease, legal and formal property records | Forever |
| ArtStation print announcements | 365 days and latest 150 |
| ArtStation daily digests | 60 days |
| Job alerts | 30 days and latest 25 per feed |
| Deployment alerts | 14 days and latest 20 |
| General promotions | 30–60 days |

Start with audit runs. Tighten retention only after reviewing real counts.
