# ADR-0007: Provider API keys encrypted with Fernet in the database

- Status: Accepted
- Date: 2026-09-04

## Context

Each organization brings its own OpenAI/Anthropic/Gemini keys. They must be
usable by the API and workers at request time, which rules out hashing, and they
must not be readable from a database dump or a stray backup.

## Decision

Keys are encrypted with Fernet (AES-128-CBC plus HMAC) before they are stored in
`llm_providers.api_key_encrypted`. The key is derived from `APP_SECRET_KEY` via
SHA-256, so the secret lives in the environment - k8s Secrets and External
Secrets Operator in production - and never in the database. The API returns a
masked value and a "configured" flag, never the key itself, and every change is
audited.

## Consequences

- A leaked dump alone does not leak provider keys
- `APP_SECRET_KEY` becomes critical: losing it makes stored keys unreadable, and
  rotating it requires a decrypt-and-re-encrypt migration
- Every key use costs a decrypt; negligible next to an LLM call
- Ciphertext is authenticated, so tampering is detected rather than decrypted
  into garbage

## Alternatives considered

- **Plaintext with database-level access control.** One misconfigured backup
  away from disclosure
- **A dedicated secret manager per organization (Vault, AWS Secrets Manager).**
  Better key hygiene, but a hard dependency for a self-hostable project; the
  Fernet layer keeps the door open to move later
- **pgcrypto.** Moves key handling into SQL statements and their logs
