# Raw manifests

These are rendered from the Helm chart, not maintained by hand:

```bash
make k8s-manifests
```

They exist for two audiences - someone who wants to read what the chart
produces without installing Helm, and a cluster where Helm is not available and
`kubectl apply -f` is the whole deployment story. The chart in
`../helm/zk2` is the source of truth; a change made here is lost on the next
render.

The rendered file uses the production values, so it references secrets that
External Secrets Operator is expected to sync. Without that operator, create
the `zk2-zk2-secrets` Secret yourself with at least `APP_SECRET_KEY` and
`DB_PASSWORD`.
