# Project documentation

The root README remains focused on the currently deployed Junk-folder filter. New design and implementation work lives here so setup instructions do not become one enormous document.

- [Architecture](ARCHITECTURE.md) — Junk Guard, Mailbox Retention, repository boundaries, privacy and safety invariants.
- [Retention policies](RETENTION_POLICIES.md) — matching, precedence, retention modes and conservative starting values.
- [Roadmap](ROADMAP.md) — staged implementation from retention audits through the private `scrapbook` dashboard.

## Current implementation status

The retention foundation now includes:

- validated JSON policies
- permanent, age-based and rolling retention modes
- first-match protection precedence
- a mailbox metadata scanner
- audit mode by default
- guarded moves to Deleted Items
- aggregate activity records
- unit tests and pull-request CI

It is not wired into the existing production deployment scripts yet. The first live deployment should run in audit mode and compare its counts with the earlier one-off mailbox cleanup before enabling any moves.
