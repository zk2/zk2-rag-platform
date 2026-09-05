# ADR-0013: A shared-key allowance instead of billing

- Status: Accepted
- Date: 2026-09-05

## Context

The plan carried a billing slice: Stripe, plans, subscriptions, quotas. The
product is invite-only and has no paying users, so building a payment flow
would be building for a customer who does not exist yet.

What does exist is a real cost: an organization with no provider keys of its
own runs on the deployment's keys, and those bills land on whoever operates the
deployment.

## Decision

Billing is deferred until there is someone to bill. The accounting it would
have required is built and running:

- every call - completion and embedding alike - writes a `usage_events` row
  with tokens, cost, and **which key paid** (`key_source`)
- an organization may take up to `SYSTEM_KEY_TOKEN_LIMIT` tokens from the
  deployment's keys, after which calls are refused with a message that names
  the fix: add your own key
- a key the organization added has no allowance at all

The limit exists because the shared key is somebody else's, not to meter the
product.

## Consequences

- Operating this deployment has a bounded cost per organization, decided by
  configuration rather than by trust
- When billing does arrive it is additive: `usage_events` already holds what an
  invoice needs, and `key_source` separates what the deployment paid for from
  what the customer did
- Unknown models produce a NULL cost rather than zero, so a missing catalog
  entry shows up as unknown spend rather than free spend
- Someone who wants unlimited use has a self-service answer that costs the
  operator nothing

## Alternatives considered

- **Build Stripe now.** Weeks of work, a webhook surface to secure, and a
  subscription state machine to maintain, for zero revenue
- **No limit at all.** The shared keys are a courtesy; a courtesy without a
  ceiling is an invitation
- **Meter everything, including the customer's own keys.** Pointless: their key
  is already their bill
