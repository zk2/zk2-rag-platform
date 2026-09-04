# ADR-0005: Own authentication instead of a hosted identity provider

- Status: Accepted
- Date: 2026-09-04

## Context

The system needs password login, magic links, OAuth, TOTP, sessions with
revocation, RBAC across organizations and an audit trail. Auth0, Clerk or
WorkOS would deliver most of that in an afternoon.

## Decision

Authentication is implemented in-project: argon2id password hashing, short-lived
HS256 access tokens, opaque refresh tokens rotated on every use with their
hashes in Redis, single-use magic links stored as hashes, TOTP with recovery
codes, and an audit log entry for every security-relevant action.

## Consequences

- Security work is visible in the repository, which is the point of a portfolio
  project - and the reason this ADR exists at all
- Every mistake here is ours: rotation, replay, timing, lockout, token lifetime
  over a long-lived WebSocket
- No per-MAU bill and no vendor lock-in
- The area needs above-average test coverage; auth is the one module that had
  integration tests from the very first commit

## Alternatives considered

- **Auth0 / Clerk / WorkOS.** Less code and better defaults, but it hides
  exactly the expertise this project exists to show
- **Django allauth or similar.** Wrong framework for this stack
