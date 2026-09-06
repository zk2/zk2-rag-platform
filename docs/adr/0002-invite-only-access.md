# ADR-0002: Invite-only access, no self-signup

- Status: Accepted
- Date: 2026-09-04

## Context

The system is a public deployment holding a database, an LLM budget and
third-party API keys. Open registration on such a deployment
means anyone can burn tokens, upload arbitrary documents and probe the platform.

## Decision

`POST /auth/signup` does not exist. Access flows one way only:

    /request-access -> access_requests (pending) -> super-admin approves
      -> single-use invite with a 7-day TTL -> user sets a password
      -> User + Organization + Membership(owner) are created

`users.is_super_admin` is set by the seed script alone and cannot be set through
any API. OAuth logins for unknown emails are redirected to the access-request
form rather than auto-creating an account.

## Consequences

- Every account exists because a human approved it, and every approval is in the
  audit log with IP and user agent
- Access requests are rate-limited per email and per IP; invite tokens are
  single-use and stored as hashes
- Demo access requires a manual step - accepted, because a self-serve sandbox
  was explicitly out of scope
- Load and e2e testing must create users through fixtures, not the public API

## Alternatives considered

- **Open signup with email verification.** Verification proves an inbox exists,
  not that the account should
- **A shared demo account.** Anyone who finds it gets a shared, mutable tenant
