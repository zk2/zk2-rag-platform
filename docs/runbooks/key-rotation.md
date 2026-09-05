# Rotating credentials

## Provider API keys (per organization)

Self-service and safe: Settings -> Providers, paste the new key. The old key is
overwritten, encrypted with Fernet, and never returned by the API. Calls after
the save use the new key; calls in flight finish on the old one.

## The deployment's own provider keys

These are the keys lent to organizations that have none
([ADR-0013](../adr/0013-shared-key-allowance.md)).

1. Add the new key to the secret manager alongside the old one
2. `kubectl rollout restart deploy zk2-api zk2-worker` - External Secrets syncs
   on its own schedule, and a restart makes the moment explicit
3. Revoke the old key at the provider
4. Watch `zk2_llm_errors_total` for a few minutes

## APP_SECRET_KEY - read this before touching it

`APP_SECRET_KEY` does two jobs: it signs access tokens, and it derives the
Fernet key that encrypts provider keys and MCP headers in the database
([ADR-0007](../adr/0007-encrypted-provider-keys.md)).

**Changing it without re-encrypting makes every stored provider key
unreadable.** Organizations would silently fall back to the deployment's keys
or fail outright.

The safe sequence:

1. Announce a maintenance window - this one is not zero-downtime
2. Dump the encrypted columns:
   `llm_providers.api_key_encrypted`, `mcp_servers.headers_encrypted`
3. Decrypt them with the **old** key, re-encrypt with the new one, write back
4. Deploy the new `APP_SECRET_KEY`
5. Every session is invalidated by the change, which is expected: users sign in
   again

If the old key is already lost, there is no recovery. Clear the encrypted
columns and ask each organization to re-enter its keys.

## Database password

1. Rotate in RDS (or `terraform apply` with a new generated password)
2. Update the secret manager entry
3. `kubectl rollout restart deploy zk2-api zk2-worker`

The migration job runs on the next release, not on restart, so a rotation does
not touch the schema.

## After any rotation

Check the audit log: key changes are recorded there, and the row is the record
that the rotation happened.

```sql
SELECT created_at, action, target, actor_user_id
FROM audit_log
WHERE action LIKE '%provider%' OR action LIKE '%key%'
ORDER BY created_at DESC LIMIT 20;
```
