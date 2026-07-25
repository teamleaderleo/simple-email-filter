# Project documentation

The root README stays concise. Design, policy and operational details live here instead of becoming one enormous setup document.

- [Operations](OPERATIONS.md) — one-command testing, AWS deployment, Microsoft authentication, logs and rollback.
- [Architecture](ARCHITECTURE.md) — Junk Guard, Mailbox Retention, repository boundaries, privacy and safety invariants.
- [Retention policies](RETENTION_POLICIES.md) — matching, precedence, retention modes and conservative starting values.
- [Roadmap](ROADMAP.md) — staged implementation from retention audits through the private `scrapbook` dashboard.

## Current implementation status

The deployed Junk Guard has secured exact-message webhook handling. Routine operations are consolidated behind Make targets so code updates preserve live environment variables and recreate the Graph subscription automatically.

The retention foundation includes:

- validated JSON policies
- permanent, age-based and rolling retention modes
- first-match protection precedence
- a mailbox metadata scanner
- audit mode by default
- guarded moves to Deleted Items
- aggregate activity records
- unit tests and pull-request CI

The retention sweeper is not wired into a live schedule yet. Its first deployment should run in audit mode and compare counts with the earlier one-off mailbox cleanup before enabling any moves.
