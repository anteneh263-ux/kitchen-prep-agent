# Kitchen Prep Agent

**An autonomous daily kitchen prep and ordering agent.** Every morning at 07:00
it forecasts demand, plans prep, consumes inventory first-expired-first-out,
separates today's shortfalls from tomorrow's orders, flags expiring stock, and
publishes a one-screen briefing the kitchen can read on a phone. No chat, no
prompting, no human in the loop for the routine path.

Built on the Google Gen AI SDK and Gemini, with an optional Google ADK adapter,
Cloud Run, Cloud Scheduler and Firestore.

> **All data in this repository is synthetic and generic.** Restaurant names,
> menu data, recipes, sales history, bookings and inventory values are invented
> for demonstration. No real restaurant, supplier or customer data is used
> anywhere in the project.

---

## Problem

Every restaurant kitchen starts the day with the same three questions, and
answers all of them badly.

**How much do we prep?** Prep quantities are guessed from memory. Guess low and
the kitchen runs out mid-service. Guess high and the surplus goes in the bin.

**What do we order?** Ordering happens by glancing at the walk-in. The person
looking at the shelf usually forgets that today's service is about to eat a
large part of what they are looking at — so they order too little. Or they
count today's demand a second time on top of the par level — so they order too
much. Both errors are invisible until it is too late to fix them.

**What is about to expire?** Nobody tracks batch expiry per item. Stock rotates
by whatever is nearest the door, not by what expires first, and waste is only
discovered when something is opened and thrown out.

These decisions are made at 07:00 by whoever opened the kitchen, under time
pressure, without data. The cost is continuous: food waste, stockouts during
service, emergency supplier runs, and a head chef spending the first hour of
every day doing arithmetic instead of cooking.

## Solution

Kitchen Prep Agent runs the whole morning decision as one unattended,
idempotent job.

At **07:00 Europe/Oslo**, Cloud Scheduler makes an OIDC-authenticated call to the
private Cloud Run worker. FastAPI invokes `run_daily_prep` directly, and the
pipeline uses Gemini through the Google Gen AI SDK:

1. Reads today's expected covers from bookings, plus weather.
2. **Gemini forecasts demand per dish** — with drivers and reasoning.
3. **Validates that forecast** against a locked contract. If it fails, a
   deterministic same-weekday baseline takes over and the plan records that it
   did.
4. Explodes the forecast through recipes into exact ingredient requirements.
5. **Consumes today's requirements from real inventory batches using FEFO**,
   discarding already-expired batches as flagged waste.
6. Reports what today's service is genuinely short of, as prep shortfalls.
7. **Replenishes what is actually left back up to par**, dropping batches that
   will expire before delivery and accounting for open orders. Orders become
   dated FEFO batches when their delivery date arrives.
8. **Gemini prioritises and explains** — a briefing with a summary, ranked
   tasks, recommended actions and warnings, in a validated JSON shape.
9. Renders Markdown in Python, stores the plan, and writes a run log.

By the time the kitchen opens, `GET /` shows the day on a phone.

### The engineering principle

**Gemini handles judgment. Deterministic Python handles arithmetic.**

| Gemini owns | Deterministic Python owns |
| --- | --- |
| Demand forecasting — reading covers, weather and weekday into an expected quantity per dish | All arithmetic: recipe explosion, unit maths, rounding |
| Explanations — why the forecast looks the way it does | Inventory consumption and FEFO batch selection |
| Prioritisation — which prep task matters most this morning | Validation — of the forecast and of the briefing |
| Recommended actions and warnings in plain language | Order quantities, par levels, lead times and delivery dates |

Gemini never computes, edits or overrides a quantity a cook or a supplier acts
on. Its forecast is a *proposal* that must pass validation. Its briefing
receives a **frozen, read-only** plan. This is not a stylistic preference — it
is what makes an autonomous agent safe to leave running against a real supplier
order.

## Features

- **Fully autonomous daily run** — scheduled, authenticated, unattended.
- **Gemini demand forecasting** with per-dish reasoning and named drivers.
- **Validated forecasts with a deterministic fallback** — a bad or missing model
  response degrades the plan's *confidence*, never its *correctness*.
- **Yield-aware requirements** — a recipe quantity is prepared weight; stock and
  orders are purchased weight. The purchased requirement is derived from each
  ingredient's yield factor, and the trim loss is published rather than absorbed.
  Leaving that gap implicit is how a kitchen under-orders every single day.
- **FEFO inventory consumption** — earliest expiry consumed first, per batch.
- **Expiry waste flagging** — batches already expired on the run date are
  removed from availability and surfaced for disposal.
- **Prep shortfalls kept separate from replenishment** — "we are short *today*"
  is a kitchen problem; "order up to par" is a purchasing problem. Conflating
  them is the classic source of ordering errors.
- **Par-level replenishment on post-consumption stock**, with expiring batches
  excluded and open deliveries counted; received orders become dated FEFO
  batches without duplicate ordering.
- **Goods receipt and stock count** — the kitchen records what a delivery
  actually contained and what a count actually found. Partial deliveries and
  counted shortages correct the snapshot chain instead of compounding silently,
  and every variance is published with the plan.
- **Gemini prioritisation and briefing**, validated against a fixed JSON
  contract before publication.
- **Idempotent per date** — Scheduler retries never produce a second plan.
- **Run log preserved on failure** — written in a `finally` block, so a crashed
  run is still auditable.
- **Mobile-friendly server-rendered page** at `GET /` — no build step, no JS.
- **Runs fully offline** — the entire pipeline and test suite work with no API
  key and no network.

## Architecture

The full diagram, the code-level enforcement points and the end-to-end request
flow are in **[`docs/architecture.md`](docs/architecture.md)**.

In brief:

```
Cloud Scheduler (07:00 Europe/Oslo)
        │  HTTPS POST + OIDC token
        ▼
Private Cloud Run worker (kitchen_prep/server.py)
        │  POST /runs/daily
        ▼
FastAPI route POST /runs/daily
        ▼
Orchestrator (kitchen_prep/orchestrator.py)
        │
        ├─ Gemini step 1 ── demand forecast ──▶ VALIDATION GATE ──▶ deterministic baseline on reject
        │
        ├─ Deterministic Python ── recipes ▶ FEFO consumption ▶ prep shortfalls ▶ replenishment to par
        │                          (owns every authoritative quantity)
        │
        └─ Gemini step 2 ── prioritisation + briefing ──▶ CONTRACT CHECK ──▶ deterministic briefing on reject
        ▼
Firestore ── daily_plans · run_logs · inventory_snapshots
        ▲
Public Cloud Run viewer (kitchen_prep/public_server.py)
        ├─ read-only production plans: GET / · GET /plans/*
        └─ isolated synthetic sandbox: /demo (process-local, no Firestore writes)

Optional interactive adapter: Google ADK `root_agent` exposes the same
`run_daily_prep` operation as its only tool for `adk web`; it is not in the
scheduled production request path.
```

## How Gemini Is Used

The scheduled production pipeline calls Gemini (`gemini-3.5-flash`) at exactly
**two** schema-bounded points through the Google Gen AI SDK. The optional ADK
developer adapter uses its own routing model interaction when a user invokes it.

### Step 1 — Demand forecasting

`RealGeminiClient.propose_forecast()` sends expected covers, weekday and weather
and asks for an expected quantity per dish, with a confidence and a short
reasoning string, as JSON.

The response goes straight into `kitchen_prep/pipeline/forecast_validate.py`,
which rejects it if:

- any menu dish is missing from the response,
- an unknown `dish_id` appears,
- `expected_qty` is not an integer, or is negative,
- `expected_covers` is not a positive integer, or
- total dishes-per-cover falls outside **0.724 – 1.344** (a ±30% band around
  the configured ratio of 1.034).

On rejection — or if the model is unavailable — `pipeline/baseline.py` produces
a deterministic forecast from the mean of the last four same-weekday sales,
strictly before the target date. The plan always records which path was taken in
`forecast.forecast_source` (`gemini` or `deterministic_fallback`), and the
mobile page shows the run as **DEGRADERT** (Norwegian for *degraded*) when a
fallback was used.

### Step 2 — Prioritisation and briefing

`RealGeminiClient.propose_briefing()` receives the **completed, frozen plan** and
returns judgment about it: a summary, `priority_task_ids`, per-shortfall
`recommended_action` values with a `requires_human_approval` flag, and warnings.

`contracts.validate_briefing()` enforces the shape. Anything that fails falls
back to a deterministic briefing built from the same plan. Crucially, **Python
renders the published Markdown** from that validated JSON — the model never
returns the published document as free-form text, so it has no opportunity to
restate a number differently from the plan.

### What Gemini is structurally prevented from doing

- In the optional ADK adapter it is exposed **one** tool, `run_daily_prep`, and
  the agent instruction states that tool is the source of truth.
- Its forecast cannot enter the pipeline without passing validation.
- Its briefing sees the plan only after all quantities are final.
- It cannot approve a menu or portion change; the briefing contract carries a
  `requires_human_approval` boolean for exactly that reason.
- Model errors are classified, not swallowed: 408/429/5xx and network failures
  become a documented fallback; 400/401/403/404 and programming errors crash the
  run rather than let it quietly degrade.

## Technology

| Layer | Choice |
| --- | --- |
| Production agent framework | **Google Gen AI SDK** (`google-genai`) — two schema-bounded Gemini judgment steps |
| Optional interactive adapter | **Google ADK** (`google-adk`) — `root_agent` with the same single pipeline tool |
| Model | **Gemini `gemini-3.5-flash`** |
| API | **FastAPI** + **Uvicorn** |
| Compute | **Cloud Run** (`--no-allow-unauthenticated`) |
| Scheduling | **Cloud Scheduler** with OIDC service-account auth |
| Persistence | **Firestore** (`google-cloud-firestore`), with a local JSON store for offline development |
| Weather | **Open-Meteo** (free, no API key) via `requests`, with a deterministic offline stub |
| Tests | **pytest**, with an `integration` marker for the network path |
| Language | Python ≥ 3.11 (`zoneinfo`, dataclasses, no heavyweight dependencies) |

The deterministic core has **no numerical dependencies** — no pandas, no numpy.
Every authoritative calculation is plain, readable, testable Python.

## Data Sources

All files below are **synthetic and generic**, committed under
`kitchen_prep/data/`.

| Source | File | Contents |
| --- | --- | --- |
| Menu and recipes | `menu.json` | 6 generic dishes with per-portion recipes and prep minutes, plus `recipe_basis` declaring that recipe quantities are prepared (edible) weight |
| Ingredient master | `ingredients.json` | 13 generic ingredients with unit, par level, lead time, shelf life, yield factor and a placeholder supplier name |
| Bookings | `bookings.csv` | Expected covers per date, 2026-08-11 → 2026-08-16. Dates outside this window fall back to a deterministic estimate from sales history, marked on the plan as `covers_source` |
| Inventory batches | `inventory_batches.json` | 15 seed batches with quantity and expiry date |
| Sales history | `sales_history.csv` | Generated locally by `scripts/generate_sales_history.py`, seeded and deterministic, covering 2026-06-15 → 2026-08-13 |
| Weather | Open-Meteo API | Live daily forecast for the configured coordinates; a deterministic offline stub is used when no API key is configured |

Sales history is **not committed** (it is in `.gitignore`) — it is regenerated
from a fixed seed, so every clone produces byte-identical data.

The generator deliberately encodes learnable signal: per-dish baselines, weekday
factors (Fri/Sat high, Mon/Tue low), a rain effect that lifts burgers and wings
while dampening salad-based dishes, a heat effect on ribs, and 5–10% noise. The
history ends strictly before any forecastable date, so there is **no future
leakage** into the baseline — `scripts/verify_data.py` asserts this.

## Local Setup

Everything runs offline. **No API key and no network access are required.**

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. Generate the synthetic sales history (deterministic, seeded)
python scripts/generate_sales_history.py

# 2. Verify data integrity (referential checks + no future leakage)
python scripts/verify_data.py

# 3. Run the pipeline for the demo date
python -c "from kitchen_prep.orchestrator import run_daily_prep; import json; \
print(json.dumps(run_daily_prep('2026-08-14'), ensure_ascii=False, indent=2))"
```

The plan JSON and the rendered `.md` briefing are written to
`out/daily_plans/`, and the step log to `out/run_logs/`. The `out/` directory is
gitignored.

### Run the production service or optional ADK adapter

```bash
# FastAPI service on http://localhost:8080
uvicorn kitchen_prep.server:app --port 8080

# Optional ADK developer UI (not the scheduled production path)
adk web
```

Visit `http://localhost:8080/` after a run to see the mobile view.

### Environment variables

Copy `.env.example` to `.env` (never commit `.env`). **Offline development needs
none of these.**

| Variable | Effect |
| --- | --- |
| `GOOGLE_API_KEY` | When set, both Gemini steps call the real model. When unset, model steps use deterministic fallbacks. |
| `GOOGLE_CLOUD_PROJECT` | Firestore project id |
| `GOOGLE_CLOUD_LOCATION` | Cloud region (default in `.env.example`: `europe-north1`) |
| `KP_STORE` | Set to `firestore` to use Firestore; anything else uses the local JSON store |
| `RESTAURANT_LAT` / `RESTAURANT_LON` | Coordinates for the weather lookup (default 59.91 / 10.75) |
| `KP_OUT_DIR` | Overrides the local store root (default: `out/` in the repository root) |

## Testing

```bash
pytest -m "not integration"
```

The offline suite requires no network, no API key and no cloud resources. It
runs on every push and pull request through
[`.github/workflows/ci.yml`](.github/workflows/ci.yml), on Python 3.11 and 3.12,
alongside a build of the deployment image.

```bash
# Real Gemini end to end (requires GOOGLE_API_KEY and network)
pytest -m integration
```

What the suite actually proves:

| Test file | Guarantee |
| --- | --- |
| `test_no_double_count.py` | Today's demand is removed before the par calculation and never counted twice |
| `test_prep_vs_replenishment.py` | Today's shortfalls stay separate from future orders; stock expiring before delivery is excluded from the reorder basis |
| `test_inventory_persistence.py` | Snapshots are replay-safe; deliveries become dated batches; pending orders prevent duplicates; an explicit epoch can start a clean audited chain |
| `test_fefo.py` | Earliest-expiry batches are consumed first; expired stock is flagged, not consumed |
| `test_yield.py` | Purchased requirement is prepared ÷ yield; a missing or absurd yield factor is refused rather than defaulted to 1.0; trim loss is reported separately from expiry waste, and the pork-ribs shortfall it exposes is pinned |
| `test_receiving.py` | A short delivery replaces the assumed arrival; a counted shortage is taken earliest-expiry-first; receipts apply before counts; a forced replay never applies an event twice; a day with no events hands the chain back untouched |
| `test_receiving_api.py` | Both receiving routes accept JSON and HTML form posts; malformed events are refused and not stored; an event recorded after the run rolls forward to the next open day |
| `test_forecast_validate.py` | Missing dishes, unknown ids, non-integer or negative quantities, and out-of-band ratios are all rejected |
| `test_briefing_contract.py` | The briefing JSON contract is enforced |
| `test_gemini_client_errors.py` | 408/429/5xx/network → documented fallback; 400/401/403/404 → the run crashes rather than degrading silently |
| `test_error_policy.py` | The run log is written even when the pipeline fails |
| `test_no_network.py` | The offline path opens **no sockets** — socket access is monkeypatched to fail loudly |
| `test_data_quality.py` | Referential integrity and no future leakage in the generated history |
| `test_pipeline_e2e_local.py` | Full offline run, end to end |
| `test_prep_ids_stable.py` | Prep task ids and ordering are deterministic |
| `test_home_render.py` / `test_server_home.py` | The mobile page renders, including the empty and degraded states |
| `test_covers_fallback.py` | Booking rows win; a date past the booking window gets a positive deterministic estimate using only history strictly before it; same-weekday preferred; empty or non-positive history raises rather than returning zero |
| `test_deployment_config.py` | The image starts the real app on `0.0.0.0` with Cloud Run's `$PORT`; `.env`, `.git`, `.venv` and `out/` stay out of the build context; all four routes answer, including before the first plan exists |
| `test_gemini_integration.py` | *(integration)* With a real key, both steps must use the model — this test **fails** if the system silently degraded to a fallback |

## API Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Mobile-friendly server-rendered HTML view of the latest published plan. Shows run status as **OK**, or **DEGRADERT** when a fallback was used. |
| `GET` | `/plans/latest` | The latest published plan as JSON. Returns `{"detail": "no plans yet"}` when nothing has been published. |
| `POST` | `/inventory/receipts` | Records what a delivery actually contained. Body: `item_id`, `qty_received`, optional `order_by_date` (corrects the assumed arrival), `expiry_date`, `note`, `recorded_by`. Accepts JSON or an HTML form post. |
| `POST` | `/inventory/counts` | Records what a physical count actually found. Body: `item_id`, `counted_qty`, optional `note`, `recorded_by`. Accepts JSON or an HTML form post. |
| `GET` | `/inventory/adjustments` | Physical events recorded for a date (`?date=YYYY-MM-DD`, default today). |
| `POST` | `/runs/daily` | Starts the idempotent daily run. Optional body: `{"date": "YYYY-MM-DD", "force": false}`. The date defaults to today in Europe/Oslo. Returns a summary: date, expected covers, and counts of prep tasks, shortfalls, orders and flagged waste, plus `forecast_source`. |
| `GET` | `/healthz` | Liveness probe — `{"status": "ok"}`. |

```bash
# Trigger a run locally (uvicorn on port 8080)
curl -X POST http://localhost:8080/runs/daily \
  -H 'Content-Type: application/json' -d '{"date":"2026-08-14"}'

# Read the latest plan
curl http://localhost:8080/plans/latest

# Record a short delivery; it applies to the next day that is not yet planned
curl -X POST http://localhost:8080/inventory/receipts \
  -H 'Content-Type: application/json' \
  -d '{"item_id":"beef_patty","qty_received":40,"order_by_date":"2026-08-14"}'

# Record a physical count
curl -X POST http://localhost:8080/inventory/counts \
  -H 'Content-Type: application/json' -d '{"item_id":"tomato","counted_qty":2.5}'
```

## Reference Run

**Reference date: `2026-08-14`** — 80 expected covers. The offline run produces,
from the committed seed inventory:

- **1 flagged waste batch** — pork ribs batch `b06`, expired 2026-08-13, removed
  from availability rather than silently consumed.
- **4 prep shortfalls for today** — beef patties (1 pcs), burger buns (11 pcs),
  pork ribs (0.346 kg) and tomatoes (0.207 kg): a kitchen problem, listed
  separately from ordering. The last two exist *only* because of the yield
  conversion — 6 kg of bone-in ribs is not 6 kg of served ribs, and without it
  the day looks covered right up until service.
- **2.807 kg of trim loss** across six ingredients, published as its own
  category rather than folded into waste.
- **13 replenishment orders** to par, each computed on stock remaining *after*
  today's FEFO consumption, with delivery dates derived from per-ingredient lead
  times.
- `forecast_source: deterministic_fallback` — expected offline, since no API key
  means no model call. With `GOOGLE_API_KEY` set, the same run reports
  `forecast_source: gemini`.

Reproduce it offline in three commands:

```bash
python scripts/generate_sales_history.py
python -c "from kitchen_prep.orchestrator import run_daily_prep; import json; \
print(json.dumps(run_daily_prep('2026-08-14'), ensure_ascii=False, indent=2))"
uvicorn kitchen_prep.server:app --port 8080   # then open http://localhost:8080/
```

The rendered briefing is written to `out/daily_plans/2026-08-14.md`.

## Google Cloud Deployment

The production worker was built from this repository and deployed manually to
Google Cloud Run in `europe-north1`. The reproducible commands live in
[`deploy/cloud_run.md`](deploy/cloud_run.md) and
[`deploy/scheduler.md`](deploy/scheduler.md).

Verified production state on 2026-08-20:

- private worker revision `kitchen-prep-agent-web-00011-vlp`, serving 100%
  of traffic;
- Firestore persistence enabled with `KP_STORE=firestore`;
- Secret Manager injects `GOOGLE_API_KEY` at runtime;
- Cloud Scheduler job `kitchen-prep-daily` is enabled in `europe-west1` at
  `0 7 * * *`, timezone `Europe/Oslo`;
- an authenticated end-to-end run returned `forecast_source: gemini`,
  `forecast_note: gemini_ok`, and `briefing_source: gemini`.

The worker remains private. A separate public viewer runs as
`kitchen-prep-viewer-00010-xlk` with only Firestore viewer permission. It
receives no Gemini secret, and `/runs/daily`, `/docs`, `/redoc` and
`/openapi.json` all return 404. Its interactive `/demo` flow is synthetic,
session-scoped and cannot write production Firestore or contact a supplier.

**Cloud Run worker** — the private container entrypoint is
`uvicorn kitchen_prep.server:app --host 0.0.0.0 --port ${PORT:-8080}`:

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

`GOOGLE_API_KEY` should be supplied via Secret Manager, not `--set-env-vars`.
`KP_INVENTORY_EPOCH=YYYY-MM-DD` is an optional migration control. On that date
only, the pipeline starts a fresh synthetic par-stock snapshot, ignores older
pending orders and publishes `inventory_basis: epoch_seed_snapshot`.

The public service runs `kitchen_prep.public_server:app` under a separate service
account with only `roles/datastore.viewer`. Production plans remain read-only;
`/runs/daily`, production action routes, `/docs`, `/redoc` and `/openapi.json`
are not registered. It has no access to `GOOGLE_API_KEY`. `/demo` uses only
bounded process memory, synthetic inputs and a simulated supplier connector.

**Cloud Scheduler** — one authenticated job at 07:00 Europe/Oslo. The run date is
computed server-side, so no date is passed:

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

`--oidc-service-account-email` makes Scheduler present an OIDC token; because
Cloud Run is deployed `--no-allow-unauthenticated`, only that service account can
invoke the endpoint.

**Firestore** — with `KP_STORE=firestore`, plans, run logs and replay-safe stock
snapshots live in `daily_plans`, `run_logs` and `inventory_snapshots`. The
published Markdown is stored on the dated plan; each inventory document freezes
the dated input and post-consumption output batches.

## Safety and Reliability

This is an agent that would place supplier orders. It is built accordingly.

**No model-authored quantities.** Every number acted upon is computed in Python.
Gemini's forecast is a proposal behind a validation gate; Gemini's briefing sees
a frozen plan. There is no code path where model output becomes an order
quantity.

**Validation with a deterministic fallback, not a retry loop.** A rejected
forecast does not stall the morning — it falls back to the same-weekday baseline
and marks the plan `deterministic_fallback`, which the mobile page surfaces as
**DEGRADERT**. The run always completes; the kitchen always gets a plan; the
degradation is always visible.

**Errors are classified, not swallowed.** `classify_model_error()` treats only
408, 429, 5xx and transport failures as transient. Auth failures, permission
errors, bad requests and programming errors propagate and crash the run. A
misconfigured API key produces a loud failure, not a silent month of fallback
plans.

**Idempotent by date.** The orchestrator checks for an existing plan first.
Scheduler retries, duplicate deliveries and ordinary manual re-invocations all
return the same plan. An explicit `force: true` deliberately replaces the stored
plan and is covered by a regression test.

**Failures stay auditable.** The run log is appended in a `finally` block, so a
crashed run leaves a complete step-by-step record of how far it got.

**Authenticated at the edge.** Cloud Run runs `--no-allow-unauthenticated` and
Scheduler presents an OIDC token scoped to the service URL. There is no
application-level auth to misconfigure because there is no public surface.

**Human approval is explicit.** Menu and portion changes are never automated.
Every shortfall action carries `requires_human_approval`.

**Provably offline.** `test_no_network.py` monkeypatches socket access to raise,
then runs the full pipeline and render, so the offline claim is verifiable
rather than taken on trust.

## Known Limitations

Stated plainly, because a system that hides its edges is not safe to run unattended.

- **Inventory is persisted as replay-safe daily snapshots.** The first run starts
  from the synthetic seed. Each later date freezes the previous output plus
  orders due that day as dated FEFO input batches. A forced rerun reuses the
  frozen input, so it cannot consume or receive twice. Pending deliveries count
  toward stock at delivery and prevent duplicate orders. Receiving discrepancies
  are corrected through recorded goods receipts and stock counts, which apply to
  the next day that is not yet planned — a day that has been planned is never
  rewritten, so a correction entered late shifts forward rather than
  invalidating a plan the kitchen is already working from.
- **Yield factors are static per ingredient.** They come from the ingredient
  master, not from measuring each delivery. A batch of unusually small potatoes
  yields less than the declared factor, and nothing in the system notices; the
  correction path for that is a stock count.
- **Intermediate demand during multi-day lead times is not modelled.** For an
  ingredient with a 3-day lead time, the order covers the gap to par at the
  delivery date, but demand occurring between today and delivery is not
  subtracted. This is a deliberate, documented simplification in
  `pipeline/replenishment.py`.
- **Production plans in the public viewer are deliberately read-only.**
  A viewer cannot trigger the private worker or mutate Firestore. The separate
  `/demo` sandbox supports session-scoped synthetic decisions without exposing
  the Gemini secret or any production mutation endpoint.
- **Bookings cover a fixed synthetic window** (2026-08-11 → 2026-08-16). Dates
  past it no longer fail: covers are estimated deterministically from sales
  history (rounded same-weekday mean, strictly before the target date), and the
  plan records `covers_source` so the estimate is never mistaken for a booking.
  The estimate ignores real demand signal a booking system would carry — large
  parties, closures, events — so it is a safe default, not a substitute for a
  booking feed. A run whose date also predates all sales history still fails
  loudly with `CoversUnavailable` rather than inventing a number.
- **The HTML viewer supports English and Norwegian.** The public viewer
  defaults to English and provides a visible Norwegian switch; the private
  kitchen worker defaults to Norwegian and provides the inverse switch. Stored
  briefing prose remains in the language returned by the validated model step,
  while every authoritative label and quantity in the viewer is localized.
- **Weather is a single daily aggregate** — max temperature, precipitation sum
  and a weather code — not an hourly profile, and not split by service period.
- **The forecast baseline is a 4-week same-weekday mean.** It is intentionally
  simple and explainable; it does not model holidays, local events or trend.
- **No supplier integration.** Replenishment orders are computed and published,
  not transmitted. Placing them is a human step by design.
- **Firestore is not emulated in CI.** `FirestoreStore` requires cloud
  credentials and is excluded from offline coverage; the same snapshot contract
  is exercised through the local JSON backend. The production Firestore path is
  verified manually through authenticated end-to-end Cloud Run executions.

## Repository Structure

```
kitchen-prep-agent/
├── README.md
├── .github/workflows/ci.yml         # Offline suite + image build on every push
├── Dockerfile                       # Cloud Run image (uvicorn on $PORT)
├── .dockerignore
├── docs/
│   ├── architecture.md              # Mermaid diagram + enforcement points
│   └── design-notes.md              # Findings that shaped the pipeline
├── deploy/
│   ├── cloud_run.md                 # Reference deploy commands (manual)
│   └── scheduler.md                 # Reference Scheduler job (manual)
├── kitchen_prep/
│   ├── agent.py                     # ADK root_agent — exposes only run_daily_prep
│   ├── server.py                    # FastAPI: /, /healthz, /runs/daily, /plans/latest
│   ├── orchestrator.py              # run_daily_prep — the single daily pipeline
│   ├── config.py                    # Locked constants: model, band, demo date, paths
│   ├── contracts.py                 # Dataclasses + briefing contract validation
│   ├── gemini/
│   │   ├── client.py                # Real/offline clients + error classification
│   │   ├── forecast_step.py         # Gemini step 1: propose a forecast
│   │   └── briefing_step.py         # Gemini step 2: briefing + deterministic fallback
│   ├── pipeline/
│   │   ├── receiving.py             # Goods receipt + stock count corrections
│   │   ├── forecast_validate.py     # Validation gate (rejects → baseline)
│   │   ├── baseline.py              # Deterministic same-weekday forecast
│   │   ├── ingredients.py           # Recipe explosion + yield → purchased requirements
│   │   ├── fefo.py                  # First-Expired-First-Out primitives
│   │   ├── prep.py                  # Today: consumption, shortfalls, prep tasks
│   │   └── replenishment.py         # Order to par on post-consumption stock
│   ├── data_access/
│   │   ├── store.py                 # LocalJsonStore + FirestoreStore
│   │   ├── bookings.py              # Expected covers
│   │   ├── menu.py                  # Menu + ingredient master
│   │   ├── sales.py                 # Sales history reads (no future leakage)
│   │   └── weather.py               # Open-Meteo + deterministic offline stub
│   ├── render/
│   │   ├── markdown.py              # Plan + briefing → published Markdown
│   │   └── html.py                  # Mobile-friendly page (pure function)
│   └── data/                        # Synthetic data only
│       ├── menu.json
│       ├── ingredients.json
│       ├── bookings.csv
│       └── inventory_batches.json
├── scripts/
│   ├── generate_sales_history.py    # Seeded synthetic history with real signal
│   ├── verify_data.py               # Referential integrity + leakage checks
│   └── run_production_check.py      # Invoke a deployed worker, print safe evidence
├── tests/                           # Offline suite + 1 integration test
├── requirements.txt
├── pyproject.toml
└── .env.example
```

Not committed: `out/` (plans and run logs), `.env`, and
`kitchen_prep/data/sales_history.csv` (regenerated deterministically from seed).

## Privacy

**No real data is used anywhere in this project.**

- All restaurant names, menu data, recipes, sales history, bookings and
  inventory values are **synthetic and generic**, invented for demonstration.
- Supplier names (`MeatCo`, `BakeryCo`, `DairyCo`, `GreenCo`, `DryGoods`,
  `FrozenCo`) are placeholders and do not refer to real companies.
- **No personal data is collected, processed or stored.** Bookings contain only
  a date and a covers count — no names, no contact details, no customer records.
- Sales history is generated locally from a fixed seed. It is not committed and
  is not derived from any real point-of-sale system.
- The only external data request in the system is an anonymous weather lookup to
  Open-Meteo for a pair of coordinates. No API key, no account, no identifiers.
- Secrets are never committed. `.env` is gitignored, `.env.example` ships empty
  values, and `GOOGLE_API_KEY` is expected to come from Secret Manager in
  deployment.
- Firestore holds only generated plans and run logs — operational data, with no
  personal information in it.

## Third-Party Components

The application depends on the open-source packages declared in
`requirements.txt` and `pyproject.toml`, including Google ADK, Google Gen AI SDK,
Google Cloud Firestore, FastAPI, Uvicorn and Requests. Their respective upstream
licenses apply; none of their code is claimed as original project work. Weather
data is obtained from the Open-Meteo API under its published terms. All menu,
booking, sales and inventory data included with this repository is synthetic.
No third-party images, fonts, trademarks or private datasets are bundled.

AI coding assistants were used for implementation, review, diagnostics and
documentation. Runtime AI use is separate and explicit: Gemini is called only
for the two schema-bounded judgment steps documented above.
