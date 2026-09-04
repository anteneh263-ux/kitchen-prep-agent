# Cloud Run deployment

Deployment uses two FastAPI surfaces served by Uvicorn:

- `kitchen_prep/server.py`: private worker invoked by Cloud Scheduler.
- `kitchen_prep/public_server.py`: public read-only plan viewer plus an isolated
  synthetic demo sandbox.

The separation keeps Cloud Run IAM as the worker's authentication boundary and
ensures the public service does not import or expose `run_daily_prep`.

## Container entrypoint

The repository root contains a `Dockerfile`, so `gcloud run deploy --source .`
builds from it rather than falling back to buildpacks. Its entrypoint is:

```
uvicorn kitchen_prep.server:app --host 0.0.0.0 --port ${PORT:-8080}
```

It is declared as `CMD ["sh", "-c", "exec uvicorn ..."]` so Cloud Run's injected
`$PORT` expands and uvicorn runs as PID 1 (clean SIGTERM handling on scale-down).

Notes on the image:

- Base `python:3.12-slim`; dependencies come from `requirements.txt`.
- Only `kitchen_prep/` and `scripts/` are copied. `.env`, `.git`, `.venv`,
  `out/`, caches and tests are excluded by both the explicit `COPY` paths and
  `.dockerignore`.
- `scripts/generate_sales_history.py` runs at build time. The file is gitignored
  but the deterministic fallback forecast reads it, so generating it during the
  build keeps the image self-contained; the generator is seeded, so the result is
  identical on every build. `scripts/verify_data.py` then runs as a build-time
  gate, failing the build on a data-integrity regression.
- The image runs as the unprivileged `appuser`. `/app` stays writable so the
  local JSON store can create `out/` if `KP_STORE` is not set to `firestore`.

## Build & deploy (run manually when ready)

```bash
export PROJECT=your-project-id
export REGION=europe-north1

gcloud run deploy kitchen-prep-agent-web \
  --source . \
  --project="$PROJECT" \
  --region="$REGION" \
  --no-allow-unauthenticated \
  --set-env-vars=KP_STORE=firestore,GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=$REGION,RESTAURANT_LAT=59.91,RESTAURANT_LON=10.75
```

- `GOOGLE_API_KEY` should be provided via Secret Manager, not `--set-env-vars`.
- `--no-allow-unauthenticated`: only the Scheduler service account may invoke it.

## Endpoints

- `POST /runs/daily` — start the idempotent daily run (optional body `{"date": "YYYY-MM-DD", "force": false}`).
- `GET /plans/latest` — latest published plan (simple mobile view).
- `GET /healthz` — liveness.

## Persistence

With `KP_STORE=firestore`, plans, run logs and replay-safe dated inventory live
in Firestore (`daily_plans`, `run_logs`, `inventory_snapshots`). The published
markdown is stored on `daily_plans/{date}`; `inventory_snapshots/{date}` stores
the frozen input and post-consumption output batches. Orders due on a run date
become stable, dated FEFO batches; still-pending orders count toward stock at
delivery to prevent duplicate ordering. For a controlled synthetic-data
migration, set `KP_INVENTORY_EPOCH=YYYY-MM-DD`; that date starts fresh par stock
and records `inventory_basis: epoch_seed_snapshot` without rewriting history.

## Public viewer and isolated demo sandbox

The viewer uses a dedicated service account with `roles/datastore.viewer` only.
It receives no Gemini secret. Production plan routes remain read-only; `/demo`
uses bounded, short-lived process memory and synthetic data only. Because those
sessions are process-local, deploy this sandbox with one instance:

```bash
gcloud run deploy kitchen-prep-viewer \
  --source . \
  --project="$PROJECT" \
  --region="$REGION" \
  --service-account="kitchen-prep-viewer@$PROJECT.iam.gserviceaccount.com" \
  --command=uvicorn \
  --args=kitchen_prep.public_server:app,--host,0.0.0.0,--port,8080 \
  --port=8080 \
  --set-env-vars=KP_STORE=firestore,GOOGLE_CLOUD_PROJECT="$PROJECT" \
  --max-instances=1 \
  --allow-unauthenticated
```

Production routes remain `GET /`, `GET /plans/latest`, `GET /plans` and
`GET /plans/{date}`. The private run endpoint, production action endpoint and
FastAPI schema/documentation routes return 404. Demo POSTs can mutate only the
caller’s expiring in-memory synthetic session; they cannot write Firestore,
call Gemini or contact a real supplier.
