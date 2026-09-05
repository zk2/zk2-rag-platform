output "cluster_name" {
  description = "Feed this to: aws eks update-kubeconfig --name"
  value       = module.eks.cluster_name
}

output "database_host" {
  description = "Set as DB_HOST in the Helm values"
  value       = aws_db_instance.postgres.address
}

output "redis_url" {
  description = "Set as REDIS_URL in the Helm values"
  value       = "rediss://${aws_elasticache_replication_group.redis.primary_endpoint_address}:6379/0"
}

output "uploads_bucket" {
  description = "Set as S3_BUCKET in the Helm values"
  value       = aws_s3_bucket.uploads.id
}

output "app_role_arn" {
  description = "Annotate the service account with this for IRSA"
  value       = aws_iam_role.app.arn
}

output "secret_name" {
  description = "What External Secrets should read (externalSecret.remoteKey)"
  value       = aws_secretsmanager_secret.app.name
}
