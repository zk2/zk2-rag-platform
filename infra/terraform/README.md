# Terraform example: zk2 on AWS

A worked example of what this application needs in a cloud account. It is an
example, not somebody's production state: sizes are small, deletion protection
is off outside prod, and the state backend is left to be configured.

What it does model faithfully is the shape:

- **VPC** across three availability zones, everything stateful in private
  subnets. One NAT gateway outside production, three in it - three NATs cost
  more than the cluster does
- **EKS** with a managed node group and IRSA enabled, so pods get AWS access
  through a role rather than a key in a secret
- **RDS Postgres 16**, encrypted, private, with backups and Performance
  Insights. Multi-AZ is a variable, on in production
- **ElastiCache Redis** with encryption in transit and at rest. The job queue
  lives here, so production gets a replica and automatic failover
- **S3** for uploads: public access blocked, SSE on, versioning on - a reindex
  that overwrites the wrong object should be recoverable
- **IAM** scoped to one Kubernetes service account: the role can touch its own
  bucket and its own secret, and nothing else

Terraform writes only the generated database password into Secrets Manager.
Provider API keys are added out of band, because they should not pass through
Terraform state.

## Using it

```bash
cd envs/dev
cp backend.hcl.example backend.hcl   # then edit it
terraform init -backend-config=backend.hcl
terraform plan
terraform apply

# Wire the outputs into the Helm values
terraform output
aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)"
```

Then `helm upgrade --install zk2 ../../helm/zk2 -f ../../helm/zk2/values-prod.yaml`
with `DB_HOST`, `REDIS_URL`, `S3_BUCKET` and the service-account role
annotation taken from the outputs.

## What is deliberately absent

- **pgvector's extension** is created by the application's first migration, not
  here: `CREATE EXTENSION vector` belongs with the schema it serves
- **cert-manager, ExternalDNS, the ingress controller and External Secrets** are
  cluster add-ons; install them with their own charts, and the app chart
  expects them to exist
- **Backups beyond RDS snapshots.** Retention is set here; a restore runbook is
  documentation, not Terraform

## Verified

`terraform init -backend=false && terraform validate && terraform fmt -check`
is what `make tf-validate` runs.
