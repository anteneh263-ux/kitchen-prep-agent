"""Persistence layer.

Local development uses a JSON-file store under ``out/`` (never committed). A
Firestore backend is provided for Cloud Run; it is lazily imported so the local
environment needs no google-cloud-firestore dependency or credentials.

Collections / concepts:
  - daily_plans/{date}   -> the authoritative plan incl. published markdown field
  - run_logs/{run_id}    -> step-by-step log, written even on failure
  - inventory_snapshots/{date} -> replay-safe input/output batches for each day
  - inventory_adjustments/{id}  -> append-only physical events (receipts, counts)
  - sales_observations/{id}     -> append-only cumulative sales during service
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .. import config


class BaseStore:
    def plan_exists(self, date: str) -> bool: ...
    def get_plan(self, date: str) -> dict | None: ...
    def save_plan(self, date: str, plan: dict, *, overwrite: bool = False) -> dict: ...
    def get_latest_plan(self) -> dict | None: ...
    def list_plans(self, limit: int = 14) -> list[dict]: ...
    def record_plan_action(self, date: str, item_id: str, status: str, occurred_at: str) -> dict | None: ...
    def record_production(self, date: str, record: dict) -> dict | None: ...
    def append_run_log(self, run_id: str, entry: dict) -> None: ...
    def get_or_create_inventory_input(
        self,
        date: str,
        seed_batches: list[dict],
        arrivals: list[dict] | None = None,
        reset_from_seed: bool = False,
        adjust: Callable[[list[dict]], tuple[list[dict], dict]] | None = None,
    ) -> list[dict]: ...
    def save_inventory_output(self, date: str, batches: list[dict]) -> None: ...
    def inventory_snapshot_exists(self, date: str) -> bool: ...
    def get_inventory_snapshot(self, date: str) -> dict | None: ...
    def append_inventory_adjustment(self, adjustment: dict) -> dict: ...
    def list_inventory_adjustments(self, effective_date: str) -> list[dict]: ...
    def record_sales_observation(self, date: str, observation: dict) -> dict: ...
    def list_sales_observations(self, date: str) -> list[dict]: ...


class LocalJsonStore(BaseStore):
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base = Path(base_dir) if base_dir else config.OUT_DIR
        self.plans_dir = self.base / "daily_plans"
        self.logs_dir = self.base / "run_logs"
        self.inventory_dir = self.base / "inventory_snapshots"
        self.adjustments_dir = self.base / "inventory_adjustments"
        self.sales_dir = self.base / "sales_observations"
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.inventory_dir.mkdir(parents=True, exist_ok=True)
        self.adjustments_dir.mkdir(parents=True, exist_ok=True)
        self.sales_dir.mkdir(parents=True, exist_ok=True)

    def _plan_path(self, date: str) -> Path:
        return self.plans_dir / f"{date}.json"

    def plan_exists(self, date: str) -> bool:
        return self._plan_path(date).exists()

    def get_plan(self, date: str) -> dict | None:
        p = self._plan_path(date)
        if not p.exists():
            return None
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)

    def save_plan(self, date: str, plan: dict, *, overwrite: bool = False) -> dict:
        # Idempotent: never overwrite an existing plan for the same date.
        existing = self.get_plan(date)
        if existing is not None and not overwrite:
            return existing
        with open(self._plan_path(date), "w", encoding="utf-8") as fh:
            json.dump(plan, fh, indent=2, ensure_ascii=False)
        # Also persist the published markdown next to it for convenience.
        md = plan.get("briefing_markdown")
        if md:
            with open(self.plans_dir / f"{date}.md", "w", encoding="utf-8") as fh:
                fh.write(md)
        return plan

    def get_latest_plan(self) -> dict | None:
        plans = sorted(self.plans_dir.glob("*.json"))
        if not plans:
            return None
        with open(plans[-1], encoding="utf-8") as fh:
            return json.load(fh)

    def list_plans(self, limit: int = 14) -> list[dict]:
        plans: list[dict] = []
        for path in sorted(self.plans_dir.glob("*.json"), reverse=True)[:limit]:
            with open(path, encoding="utf-8") as fh:
                plans.append(json.load(fh))
        return plans

    def record_plan_action(self, date: str, item_id: str, status: str, occurred_at: str) -> dict | None:
        plan = self.get_plan(date)
        if plan is None:
            return None
        event = {"item_id": item_id, "status": status, "occurred_at": occurred_at}
        plan.setdefault("operational_actions", {})[item_id] = {
            "status": status,
            "updated_at": occurred_at,
        }
        plan.setdefault("action_history", []).append(event)
        return self.save_plan(date, plan, overwrite=True)

    def record_production(self, date: str, record: dict) -> dict | None:
        """Append-only execution log kept on the plan itself."""
        plan = self.get_plan(date)
        if plan is None:
            return None
        plan.setdefault("production_actuals", {})[record["task_id"]] = dict(record)
        plan.setdefault("production_history", []).append(dict(record))
        return self.save_plan(date, plan, overwrite=True)

    def append_run_log(self, run_id: str, entry: dict) -> None:
        with open(self.logs_dir / f"{run_id}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _inventory_path(self, date: str) -> Path:
        return self.inventory_dir / f"{date}.json"

    def get_or_create_inventory_input(
        self,
        date: str,
        seed_batches: list[dict],
        arrivals: list[dict] | None = None,
        reset_from_seed: bool = False,
        adjust: Callable[[list[dict]], tuple[list[dict], dict]] | None = None,
    ) -> list[dict]:
        """Freeze the input for a date; force-runs replay from the same snapshot.

        ``adjust`` runs exactly once, while the input is being frozen, and may
        correct the batches against recorded physical events. Its report is
        stored beside the input so a replay can read it back without
        re-applying anything.
        """
        path = self._inventory_path(date)
        if path.exists():
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)["input_batches"]

        previous_output: list[dict] | None = None
        previous = [p for p in sorted(self.inventory_dir.glob("*.json")) if p.stem < date]
        if previous:
            with open(previous[-1], encoding="utf-8") as fh:
                previous_output = json.load(fh).get("output_batches")
        source = seed_batches if reset_from_seed or previous_output is None else previous_output
        input_batches = [dict(batch) for batch in source]
        input_batches.extend(dict(batch) for batch in (arrivals or []))
        snapshot: dict[str, Any] = {"date": date}
        if adjust is not None:
            input_batches, report = adjust(input_batches)
            snapshot["adjustment_report"] = report
        snapshot["input_batches"] = input_batches
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, indent=2)
        return input_batches

    def inventory_snapshot_exists(self, date: str) -> bool:
        return self._inventory_path(date).exists()

    def get_inventory_snapshot(self, date: str) -> dict | None:
        path = self._inventory_path(date)
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def _adjustments_path(self, effective_date: str) -> Path:
        return self.adjustments_dir / f"{effective_date}.jsonl"

    def append_inventory_adjustment(self, adjustment: dict) -> dict:
        """Append-only: a recorded physical event is never edited or removed."""
        path = self._adjustments_path(adjustment["effective_date"])
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(adjustment, ensure_ascii=False) + "\n")
        return adjustment

    def list_inventory_adjustments(self, effective_date: str) -> list[dict]:
        path = self._adjustments_path(effective_date)
        if not path.exists():
            return []
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def _sales_path(self, date: str) -> Path:
        return self.sales_dir / f"{date}.jsonl"

    def record_sales_observation(self, date: str, observation: dict) -> dict:
        """Append-only: a later observation supersedes an earlier one, never edits it."""
        with open(self._sales_path(date), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(observation, ensure_ascii=False) + "\n")
        return observation

    def list_sales_observations(self, date: str) -> list[dict]:
        path = self._sales_path(date)
        if not path.exists():
            return []
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def save_inventory_output(self, date: str, batches: list[dict]) -> None:
        path = self._inventory_path(date)
        existing: dict = {}
        if path.exists():
            with open(path, encoding="utf-8") as fh:
                existing = json.load(fh)
        existing.update({"date": date, "output_batches": [dict(batch) for batch in batches]})
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(existing, fh, indent=2)


class FirestoreStore(BaseStore):  # pragma: no cover - requires cloud credentials
    def __init__(self, project: str | None = None) -> None:
        from google.cloud import firestore  # lazy import

        self.db = firestore.Client(project=project)

    def plan_exists(self, date: str) -> bool:
        return self.db.collection("daily_plans").document(date).get().exists

    def get_plan(self, date: str) -> dict | None:
        snap = self.db.collection("daily_plans").document(date).get()
        return snap.to_dict() if snap.exists else None

    def save_plan(self, date: str, plan: dict, *, overwrite: bool = False) -> dict:
        ref = self.db.collection("daily_plans").document(date)
        # Idempotent create; if it already exists, return the stored plan.
        existing = ref.get()
        if existing.exists and not overwrite:
            return existing.to_dict()
        if overwrite:
            ref.set(plan)
        else:
            ref.create(plan)  # create() fails if doc exists -> extra idempotency guard
        return plan

    def get_latest_plan(self) -> dict | None:
        from google.cloud import firestore  # lazy import

        q = (
            self.db.collection("daily_plans")
            .order_by("date", direction=firestore.Query.DESCENDING)
            .limit(1)
            .stream()
        )
        for doc in q:
            return doc.to_dict()
        return None

    def list_plans(self, limit: int = 14) -> list[dict]:
        from google.cloud import firestore  # lazy import

        query = (
            self.db.collection("daily_plans")
            .order_by("date", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        return [doc.to_dict() for doc in query]

    def record_plan_action(self, date: str, item_id: str, status: str, occurred_at: str) -> dict | None:
        from google.cloud import firestore  # lazy import

        ref = self.db.collection("daily_plans").document(date)
        transaction = self.db.transaction()

        @firestore.transactional
        def update(txn):
            snap = ref.get(transaction=txn)
            if not snap.exists:
                return None
            plan = snap.to_dict()
            event = {"item_id": item_id, "status": status, "occurred_at": occurred_at}
            plan.setdefault("operational_actions", {})[item_id] = {
                "status": status,
                "updated_at": occurred_at,
            }
            plan.setdefault("action_history", []).append(event)
            txn.set(ref, plan)
            return plan

        return update(transaction)

    def record_production(self, date: str, record: dict) -> dict | None:
        from google.cloud import firestore  # lazy import

        ref = self.db.collection("daily_plans").document(date)
        transaction = self.db.transaction()

        @firestore.transactional
        def update(txn):
            snap = ref.get(transaction=txn)
            if not snap.exists:
                return None
            plan = snap.to_dict()
            plan.setdefault("production_actuals", {})[record["task_id"]] = dict(record)
            plan.setdefault("production_history", []).append(dict(record))
            txn.set(ref, plan)
            return plan

        return update(transaction)

    def append_run_log(self, run_id: str, entry: dict) -> None:
        self.db.collection("run_logs").document(run_id).collection("steps").add(entry)

    def get_or_create_inventory_input(
        self,
        date: str,
        seed_batches: list[dict],
        arrivals: list[dict] | None = None,
        reset_from_seed: bool = False,
        adjust: Callable[[list[dict]], tuple[list[dict], dict]] | None = None,
    ) -> list[dict]:
        from google.cloud import firestore  # lazy import

        ref = self.db.collection("inventory_snapshots").document(date)
        transaction = self.db.transaction()

        @firestore.transactional
        def resolve(txn):
            current = ref.get(transaction=txn)
            if current.exists:
                return current.to_dict()["input_batches"]
            previous_query = (
                self.db.collection("inventory_snapshots")
                .where("date", "<", date)
                .order_by("date", direction=firestore.Query.DESCENDING)
                .limit(1)
            )
            previous = list(previous_query.stream(transaction=txn))
            previous_output = previous[0].to_dict().get("output_batches") if previous else None
            source = seed_batches if reset_from_seed or previous_output is None else previous_output
            input_batches = [dict(batch) for batch in source]
            input_batches.extend(dict(batch) for batch in (arrivals or []))
            snapshot = {"date": date}
            if adjust is not None:
                input_batches, report = adjust(input_batches)
                snapshot["adjustment_report"] = report
            snapshot["input_batches"] = input_batches
            txn.create(ref, snapshot)
            return input_batches

        return resolve(transaction)

    def inventory_snapshot_exists(self, date: str) -> bool:
        return self.db.collection("inventory_snapshots").document(date).get().exists

    def get_inventory_snapshot(self, date: str) -> dict | None:
        snap = self.db.collection("inventory_snapshots").document(date).get()
        return snap.to_dict() if snap.exists else None

    def append_inventory_adjustment(self, adjustment: dict) -> dict:
        """Append-only: a recorded physical event is never edited or removed."""
        ref = self.db.collection("inventory_adjustments").document(adjustment["adjustment_id"])
        ref.create(adjustment)
        return adjustment

    def list_inventory_adjustments(self, effective_date: str) -> list[dict]:
        query = (
            self.db.collection("inventory_adjustments")
            .where("effective_date", "==", effective_date)
            .stream()
        )
        return [doc.to_dict() for doc in query]

    def record_sales_observation(self, date: str, observation: dict) -> dict:
        self.db.collection("sales_observations").add({**observation, "date": date})
        return observation

    def list_sales_observations(self, date: str) -> list[dict]:
        query = self.db.collection("sales_observations").where("date", "==", date).stream()
        return [doc.to_dict() for doc in query]

    def save_inventory_output(self, date: str, batches: list[dict]) -> None:
        self.db.collection("inventory_snapshots").document(date).set(
            {"date": date, "output_batches": [dict(batch) for batch in batches]},
            merge=True,
        )


def get_store() -> BaseStore:
    """Firestore when explicitly configured, otherwise the local JSON store."""
    import os

    if os.environ.get("KP_STORE") == "firestore":  # opt-in only
        return FirestoreStore(project=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    return LocalJsonStore()


def load_seed_batches() -> list[dict[str, Any]]:
    with open(config.BATCHES_PATH, encoding="utf-8") as fh:
        return json.load(fh)["batches"]
