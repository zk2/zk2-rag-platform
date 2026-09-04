# ADR-0006: Bot versions stored as full snapshots

- Status: Accepted
- Date: 2026-09-04

## Context

A bot is a system prompt plus model settings plus retrieval parameters. Users
edit it, and later need to see what changed, roll back, and eventually run two
versions against each other in an A/B experiment.

## Decision

Editing a bot never mutates the active configuration. Any change that touches a
meaningful field inserts a new `bot_versions` row holding the complete
configuration, and `bots.current_version_id` moves to it. Rollback is a pointer
change; diff is computed between two snapshots.

## Consequences

- History accumulates for free, and every conversation can name the exact
  configuration that produced it
- Rollback cannot half-apply, because a version is never partially built
- A/B variants and pipeline versions reuse the same shape: an experiment points
  at version ids
- Rows grow with edit frequency; snapshots are small, and pruning can wait

## Alternatives considered

- **A change log of field diffs.** Compact, but reconstructing a configuration
  means replaying history, and a broken replay silently corrupts a bot
- **Audit-log-only history.** Good for forensics, useless for rollback
