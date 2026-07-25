# Roadmap

The project will move in small, reviewable steps. Existing Junk filtering remains operational while new shared components are introduced.

## Phase 1 — Retention foundation

- [x] Document the Junk Guard and Mailbox Retention split.
- [x] Add a validated JSON policy model.
- [x] Add first-match retention planning with `forever`, `days`, `latest` and `days_and_latest` modes.
- [x] Add an audit-first Lambda sweeper.
- [x] Batch moves to Deleted Items with retry handling.
- [x] Add unit tests for policy validation and expiry behaviour.
- [ ] Deploy an audit-only scheduled run and compare counts with the one-off cleanup report.

## Phase 2 — Shared runtime

- [ ] Move existing token-cache code into the shared authentication module.
- [ ] Move Graph request, pagination and retry logic into the shared client.
- [ ] Migrate the current Junk webhook without changing its behaviour.
- [ ] Validate webhook `clientState` and use immutable message identifiers.
- [ ] Fetch the exact message referenced by each notification.

## Phase 3 — Mailbox ingestion and activity

- [ ] Subscribe to mailbox-wide message creation.
- [ ] Classify arrivals without moving or deleting them.
- [ ] Store privacy-minimised policy-hit and retention-run events.
- [ ] Add idempotency and lifecycle handling for missed or removed subscriptions.
- [ ] Add dry-run comparison reports when policies change.

## Phase 4 — Private API and dashboard

- [ ] Expose authenticated summary, policy, activity and upcoming-expiry endpoints.
- [ ] Add a read-only `/dashboard/email` page to `scrapbook`.
- [ ] Display retention health, active rules, recent runs and upcoming expiries.
- [ ] Keep Microsoft credentials and raw email contents out of the website.
- [ ] Add policy editing only after read-only operation proves reliable.

## Later ideas

- User flags or Outlook categories that permanently protect individual messages.
- Per-sender retention suggestions derived from aggregate read behaviour.
- A reversible quarantine period before Deleted Items.
- Local CLI commands for rank, audit, apply and policy validation.
- Exportable aggregate history without message content.
