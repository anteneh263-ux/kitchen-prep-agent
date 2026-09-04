# Design notes — Kitchen Prep Agent

Findings from building the pipeline that shaped the current architecture.

## A double-counting bug in the original replenishment logic

This is the finding that shaped the architecture.

The first version of the replenishment step did what feels intuitive: look at
current stock, subtract today's forecast demand, and order back up to par. Buried
in that is a double count. Today's demand was subtracted **twice** — once
implicitly, because the inventory those requirements consume is still sitting in
the stock figure, and again explicitly as a demand subtraction. The result was
orders that were systematically too large, in a way no eyeball check would
notice: the numbers were plausible, just consistently wrong in one direction.

The corrected implementation splits the two operations and fixes their order:

1. **Consume first.** `pipeline/prep.py` covers today's ingredient requirements
   from valid batches using FEFO, producing the actual per-batch consumption and,
   separately, any uncovered demand as **prep shortfalls** — a today problem for
   the kitchen, never an ordering input.
2. **Then replenish what is left.** `pipeline/replenishment.py` takes the
   **remaining usable inventory after that consumption**, discards batches that
   will expire before the delivery date, and orders the difference up to the par
   level. Today's demand is already gone from the basis, so it cannot be counted
   again.

The planning basis is named explicitly on every plan —
`planning_basis: "today_consumption_plus_par"` — and locked by two tests.
`test_no_double_count.py` uses a case where the correct answer is 50 and the
double-counted answer is 20, so a regression cannot pass quietly.

The wider lesson: this bug is exactly the kind of error an LLM would produce
confidently and explain persuasively, and exactly the kind a human reviewer
would nod along with. It was caught by writing the arithmetic in deterministic
Python and pinning it with a test whose expected value differs numerically from
the wrong answer.

## Other findings

**Ordering a pipeline is a correctness concern, not a style concern.** Consume,
*then* replenish. Reversing those two steps produces the double count above. The
sequence is now documented as a locked rule at the top of
`replenishment.py`.

**Separating "short today" from "order to par" is what makes the output usable.**
They are different problems, for different people, on different timescales.
Merging them into one number is how kitchens end up simultaneously short during
service and over-ordered for next week.

**"Covered today" does not mean "no order needed."** The clearest test case in
the suite: 9 kg of wings covers today's 8.8 kg, so there is no shortfall — but
the leftover 0.2 kg expires before the 2-day-lead delivery arrives, so the
correct order is a full 10 kg to par. Expiry has to be evaluated *against the
delivery date*, not against today.

**Validation bands are more valuable than better prompts.** The ±30%
dishes-per-cover band catches every failure mode that matters — a hallucinated
dish, a missing dish, an order-of-magnitude slip — in one cheap check, and it
does so without any dependence on how the model happens to behave that day.

**Falling back is not the same as failing.** The pivotal design decision was
distinguishing transient model conditions from real errors. Treating everything
as retryable would have masked an invalid API key indefinitely; treating nothing
as retryable would have let a single 429 break a morning. The split is now
codified in `classify_model_error()` and pinned by 13 tests.

**A fallback that hides itself is a liability.** Every plan records
`forecast_source` and `briefing_source`, the mobile page renders **DEGRADERT**
when either used a fallback, and the integration test *fails* if a real API key
produces fallback output. Degradation is observable at every layer.

**Structured JSON in, Python-rendered Markdown out.** Letting the model return
the published document invites it to restate numbers in prose that drifts from
the plan. Constraining it to a validated JSON shape and rendering the Markdown
in Python removes that entire class of error.

**Determinism makes the demo reproducible.** A seeded generator, a fixed demo
date, no wall-clock reads at import, and a socket-blocking test mean the whole
system produces identical output on any machine, offline.
