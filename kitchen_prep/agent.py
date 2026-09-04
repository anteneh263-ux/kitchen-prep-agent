"""ADK agent definition.

Only ``run_daily_prep`` is exposed as a tool. The agent is a thin wrapper: all
authoritative computation happens in the deterministic pipeline; the model does
not compute or change quantities. Menu/portion changes are NOT automated and
require human approval.
"""
from __future__ import annotations

from . import config
from .orchestrator import run_daily_prep as _run_daily_prep


def run_daily_prep(date: str = "") -> dict:
    """Run the idempotent daily prep + ordering pipeline for ``date`` (YYYY-MM-DD).

    If ``date`` is empty, uses today's date in Europe/Oslo. Returns the plan,
    including prep tasks, prep shortfalls, replenishment orders, flagged waste and
    the published markdown briefing.
    """
    return _run_daily_prep(date or None)


# Built lazily/at import; requires google-adk to be installed.
from google.adk.agents import Agent  # noqa: E402

root_agent = Agent(
    name="kitchen_prep_agent",
    model=config.MODEL_ID,
    description="Autonomous daily kitchen prep and ordering agent.",
    instruction=(
        "You coordinate a restaurant's daily prep and ordering. Call the "
        "run_daily_prep tool to produce the authoritative plan. Never invent or "
        "change quantities; the tool is the source of truth. Menu or portion "
        "changes require human approval."
    ),
    tools=[run_daily_prep],
)
