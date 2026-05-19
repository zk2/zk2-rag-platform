# zk2 API

FastAPI backend. See repo root `README.md` and `PLAN.md` for context.

## Run locally

```bash
# from repo root
make up           # postgres + redis + mailhog
make migrate
make seed
make dev-api
```

API at <http://localhost:8000>, OpenAPI at <http://localhost:8000/docs>.
