# Cloud Scheduler (reference — not automated here)

One authenticated job triggers the daily run at **07:00 Europe/Oslo**. The run
date is computed server-side in Europe/Oslo, so no date needs to be passed.

## Create the job (run manually when ready)

```bash
export PROJECT=your-project-id
export SCHEDULER_REGION=europe-west1
export URL=https://<your-cloud-run-url>/runs/daily
export INVOKER_SA=scheduler-invoker@$PROJECT.iam.gserviceaccount.com

gcloud scheduler jobs create http kitchen-prep-daily \
  --project="$PROJECT" \
  --location="$SCHEDULER_REGION" \
  --schedule="0 7 * * *" \
  --time-zone="Europe/Oslo" \
  --http-method=POST \
  --uri="$URL" \
  --headers="Content-Type=application/json" \
  --message-body='{}' \
  --oidc-service-account-email="$INVOKER_SA" \
  --oidc-token-audience="$URL"
```

## Idempotency

Retries are safe: `run_daily_prep` checks `daily_plans/{date}` first and returns
the existing plan instead of creating a duplicate. A Scheduler retry (or a manual
re-invocation) on the same date therefore never produces a second plan.

## Auth

`--oidc-service-account-email` makes Scheduler present an OIDC token; Cloud Run is
deployed `--no-allow-unauthenticated`, so only this service account can invoke the
endpoint.
