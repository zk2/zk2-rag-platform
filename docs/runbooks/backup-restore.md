# Backup and restore

## What has to survive

| Data | Where | Backed up by |
|---|---|---|
| Documents, chunks, vectors, versions, usage | PostgreSQL | RDS automated backups + snapshots |
| Uploaded files | S3 | Versioning, plus the bucket's own lifecycle rules |
| Sessions, rate limits, job queue | Redis | Nothing - deliberately |

Redis is not backed up. Sessions can be re-created by signing in, rate limits
are supposed to be transient, and a lost job queue costs a re-index. Anything
in Redis that could not be rebuilt would be a design error.

## Retention

RDS keeps 14 days in production and 3 outside it (`infra/terraform`). Anything
longer is a manual snapshot with a name that says why it exists.

## Restoring the database

```bash
# 1. Find the snapshot
aws rds describe-db-snapshots --db-instance-identifier zk2-prod-postgres

# 2. Restore to a NEW instance - never over the live one
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier zk2-prod-postgres-restore \
  --db-snapshot-identifier <snapshot-id>

# 3. Point the application at it, in this order:
#    scale the API and worker to zero, change DB_HOST, scale back up
kubectl scale deploy zk2-api zk2-worker --replicas=0
helm upgrade zk2 infra/helm/zk2 -f values-prod.yaml --set config.DB_HOST=<new endpoint>
```

Point-in-time recovery is available within the retention window and is the
right tool when the damage has a known timestamp:

```bash
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier zk2-prod-postgres \
  --target-db-instance-identifier zk2-prod-postgres-pitr \
  --restore-time 2026-09-05T11:00:00Z
```

## After any restore

1. `alembic current` - the restored database may be older than the running code
2. If it is behind, `alembic upgrade head` **before** scaling the API back up
3. Check that vectors survived: `SELECT count(*) FROM source_embeddings;`
   against the number of chunks. A mismatch means re-indexing those sources
4. Uploaded files and database rows are restored independently, so a source row
   whose file is missing will fail ingest with a clear error - re-upload or
   delete it

## Verifying the backup, before you need it

A backup nobody has restored is a hypothesis. Quarterly:

```bash
# restore the newest snapshot into a scratch instance
# run migrations, point a staging release at it, sign in, open a document
# then delete the instance
```

Record how long it took. That number is the recovery time objective, whatever
the document says.
