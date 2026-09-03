# Ader Ultimate Platform

Ader now ships with a platform layer that complements the existing moderation, tickets, economy, analytics, games, dashboard and utility systems.

## New systems

### Security Shield
- Anti-raid join-spike detection.
- Automatic server lockdown when configured thresholds are crossed.
- Manual unlock command.
- Security event log channel.

Commands:
- `/security-status`
- `/security-config`
- `/security-unlock`

### Staff OS
Separate staff points from the normal member XP system.

- 1 staff point per 30 tracked staff messages.
- Staff profiles.
- Tickets handled and ratings fields ready for integration with ticket workflows.
- Staff leaderboard.

Commands:
- `/staff`
- `/staff-leaderboard`

### Achievements
Built-in achievements are stored per guild and user.

Examples:
- First Message
- 100 Messages
- 1,000 Messages
- First Invite
- Staff Star
- Ticket Master

Command:
- `/achievements`

### Automation Engine
Simple IF -> THEN automations are stored in SQLite.

Triggers:
- Member Join
- Message Contains

Actions:
- Give Role
- DM User
- Channel Message
- Timeout

Commands:
- `/automation-create`
- `/automation-list`
- `/automation-delete`

### Invite Intelligence
Ader snapshots invite usage and attributes joins when Discord exposes a changed invite count.

Command:
- `/invite-leaderboard`

### Server Health
A compact operational view for administrators with member count, message volume, joins, leaves, tickets, channels, roles and boosts.

Command:
- `/server-health`

### Ader Currency Network
`ANORIS` is the native Ader currency and remains backed by the existing global balance system.

The market registry includes:
- ANORIS
- CREDITS (Credits PROBOT)
- ADAMC
- FLXCOINS
- VETO

Exchange features:
- Exchange rate registry.
- Quote calculator with configurable fees.
- P2P offers.
- Native ANORIS escrow for offers.
- Cancellation/refund for ANORIS offers.
- Transaction records for external-currency settlements.

Commands:
- `/currency-info`
- `/exchange-rate`
- `/exchange-quote`
- `/exchange-list`
- `/exchange-offer`
- `/exchange-buy`
- `/exchange-cancel`

## External-currency settlement

Ader cannot truthfully move another bot's balance simply because a Discord message says that a payment happened. Real automatic conversion for Credits PROBOT, AdamC, FLXCoins, Veto or another third-party currency requires a provider API or a verified adapter owned/authorized by that provider.

Until such an adapter is configured, external-asset trades are recorded as pending/manual settlement instead of pretending that the other bot's currency was transferred.

This design leaves a clean adapter path for future integrations without compromising the native ANORIS ledger.

## Existing systems kept intact

The platform layer is additive. It does not remove the repository's existing ticket, economy, analytics, giveaway, role, moderation, temporary voice, social alert, dashboard or game systems.
