"""Server-rendered bilingual dashboard for the latest kitchen plan."""
from __future__ import annotations

from datetime import datetime
from html import escape

from ..data_access import menu as menu_da
from ..pipeline import prep as prep_pipe
from ..units import ingredient_unit, portion_label, unit_label


def _run_status(plan: dict, language: str) -> tuple[str, str]:
    actions = plan.get("operational_actions", {})
    unresolved = [
        item
        for item in plan.get("prep_shortfalls", [])
        if actions.get(str(item.get("item_id")), {}).get("status") != "resolved"
    ]
    if unresolved:
        return ("ACTION REQUIRED" if language == "en" else "HANDLING KREVES"), "action"
    sources = {plan["forecast"]["forecast_source"], plan.get("briefing_source", "")}
    degraded = "deterministic_fallback" in sources
    if degraded:
        return ("READY WITH LIMITATIONS" if language == "en" else "KLAR MED BEGRENSNINGER"), "degraded"
    return ("READY" if language == "en" else "KLAR"), "ready"


def _human_id(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _dish_name(dish_id: str) -> str:
    return str(menu_da.menu_by_id().get(dish_id, {}).get("name") or _human_id(dish_id))


def _item_name(item_id: str, language: str = "en") -> str:
    norwegian = {
        "beef_patty": "Burgerkjøtt",
        "burger_bun": "Hamburgerbrød",
        "pork_ribs": "Svineribbe",
    }
    if language == "no" and item_id in norwegian:
        return norwegian[item_id]
    return _human_id(item_id)


def _number(value: object) -> str:
    """Render operational quantities without machine-like trailing zeroes."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _duration(minutes: object, language: str) -> str:
    try:
        total = round(float(minutes))
    except (TypeError, ValueError):
        return str(minutes)
    if total < 60:
        return f"{total} min"
    hours, remainder = divmod(total, 60)
    if language == "en":
        return f"{hours} hr {remainder} min" if remainder else f"{hours} hr"
    return f"{hours} t {remainder} min" if remainder else f"{hours} t"


def _reasoning_text(reasoning: str, language: str, fallback: bool) -> str:
    if language == "no" and fallback:
        return "Deterministisk baseline basert på de siste tilsvarende ukedagene, normalisert per gjest."
    return reasoning


def _display_timestamp(value: str, language: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value
    months = (
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        if language == "en"
        else ["jan.", "feb.", "mars", "apr.", "mai", "juni", "juli", "aug.", "sep.", "okt.", "nov.", "des."]
    )
    return f"{parsed.day} {months[parsed.month - 1]} {parsed.year} · {parsed:%H:%M}"


def _display_date(value: str, language: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value
    months = (
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        if language == "en"
        else ["jan.", "feb.", "mars", "apr.", "mai", "juni", "juli", "aug.", "sep.", "okt.", "nov.", "des."]
    )
    return f"{parsed.day} {months[parsed.month - 1]}"


def _source_label(value: str, language: str) -> str:
    labels = {
        "gemini": "Gemini",
        "deterministic_fallback": "Deterministic fallback" if language == "en" else "Deterministisk fallback",
    }
    return labels.get(value, _human_id(value))


def _technical_label(value: str, language: str) -> str:
    labels = {
        "bookings_csv": "Bookings file" if language == "en" else "Bookingfil",
        "expected_covers": "Expected covers" if language == "en" else "Forventet belegg",
        "today_consumption_plus_par": "Consumption + par level" if language == "en" else "Forbruk + par-nivå",
        "date_input_output_snapshot": "Daily snapshot chain" if language == "en" else "Daglig snapshot-kjede",
        "epoch_seed_snapshot": "Fresh inventory epoch" if language == "en" else "Ny lagerepoke",
        "seed_inventory": "Seed inventory" if language == "en" else "Startlager",
        "gemini_ok": "Gemini proposal validated" if language == "en" else "Gemini-forslag validert",
        "approved": "Approved" if language == "en" else "Godkjent",
        "resolved": "Resolved" if language == "en" else "Løst",
        "reopened": "Reopened" if language == "en" else "Gjenåpnet",
    }
    if value in labels:
        return labels[value]
    if value.startswith("fallback:"):
        reason = value.split(":", 2)[1].replace("_", " ")
        return f"Fallback · {reason.capitalize()}"
    return _human_id(value)


_STYLE = """
:root {
  color-scheme: light;
  --ink: #14201a; --muted: #5c6b62; --line: #dde6e0; --paper: #ffffff;
  --canvas: #eef3ef; --brand: #16513a; --brand-2: #1f6a49; --brand-soft: #e4f0ea;
  --good: #147a3e; --warn: #9a5406; --danger: #a32828;
  --radius: 16px; --radius-sm: 11px;
  --shadow-sm: 0 1px 2px rgba(20,50,35,.06), 0 1px 1px rgba(20,50,35,.04);
  --shadow: 0 10px 34px rgba(20,50,35,.12);
  --ring: 0 0 0 3px #1f6a4940;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  margin: 0; background: var(--canvas); color: var(--ink); line-height: 1.5;
  -webkit-font-smoothing: antialiased; }
.shell { width: min(1080px, calc(100% - 2rem)); margin: 0 auto; padding: 1.25rem 0 3rem; }
a { color: inherit; }
:focus-visible { outline: none; box-shadow: var(--ring); border-radius: 8px; }

.topbar { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin-bottom: 1.15rem; }
.brand { display: flex; align-items: center; gap: .7rem; font-weight: 800; letter-spacing: -.02em; }
.mark { display: grid; place-items: center; width: 2.35rem; height: 2.35rem; border-radius: .75rem;
  color: white; background: linear-gradient(150deg, var(--brand-2), var(--brand)); font-size: 1.2rem;
  box-shadow: var(--shadow-sm); }
.language { color: var(--brand); border: 1px solid #b5cec0; border-radius: 999px; padding: .42rem .85rem;
  text-decoration: none; font-size: .82rem; font-weight: 700; background: white; transition: background .15s, border-color .15s; }
.language:hover { background: var(--brand-soft); border-color: #98bfa9; }

.hero { position: relative; min-height: 21rem; display: flex; align-items: flex-end; color: white;
  background: #103724 url('/assets/food-hero.webp') center/cover no-repeat;
  border-radius: 20px; padding: clamp(1.35rem, 4vw, 2.35rem); box-shadow: var(--shadow); overflow: hidden; }
.hero::before { content: ""; position: absolute; inset: 0; background:
  linear-gradient(90deg, rgba(7,29,19,.96) 0%, rgba(9,38,25,.86) 38%, rgba(9,38,25,.28) 70%, rgba(9,38,25,.08) 100%); pointer-events: none; }
.hero::after { content: ""; position: absolute; inset: 0; background:
  linear-gradient(0deg, rgba(3,18,11,.6), transparent 45%); pointer-events: none; }
.hero-row { position: relative; display: flex; justify-content: space-between; align-items: flex-start;
  gap: 1rem; flex-wrap: wrap; width: 100%; z-index: 1; }
.hero-copy { max-width: 34rem; padding-top: 2.5rem; }
.hero-kicker { display: inline-block; margin-top: .7rem; color: #b9e6c6; font-size: .78rem; font-weight: 850;
  text-transform: uppercase; letter-spacing: .08em; }
.eyebrow { margin: 0 0 .35rem; opacity: .82; text-transform: uppercase; letter-spacing: .12em; font-size: .72rem; font-weight: 800; }
h1 { margin: 0; font-size: clamp(1.7rem, 5vw, 2.7rem); line-height: 1.08; letter-spacing: -.035em; }
.hero p { margin: .6rem 0 0; color: #d7e8de; font-variant-numeric: tabular-nums; }
.status { position: absolute; right: 0; top: 0; display: inline-flex; align-items: center; gap: .5rem; border-radius: 999px; padding: .5rem .85rem;
  background: #ffffff1c; border: 1px solid #ffffff45; font-size: .76rem; font-weight: 800; letter-spacing: .05em; }
.dot { width: .58rem; height: .58rem; border-radius: 50%; background: #6fe295; box-shadow: 0 0 0 4px #6fe29528; }
.status.degraded { background: #ffb55f1f; border-color: #ffcf9a66; }
.status.degraded .dot { background: #ffc178; box-shadow: 0 0 0 4px #ffc17826; }
.status.action { background: #ff777722; border-color: #ffb3b377; }
.status.action .dot { background: #ff8d8d; box-shadow: 0 0 0 4px #ff8d8d2b; }
.value-strip { display: grid; grid-template-columns: repeat(3, 1fr); gap: .8rem; margin: 1.1rem 0; }
.value-card { display: grid; grid-template-columns: auto 1fr; gap: .75rem; align-items: start; padding: 1rem 1.05rem;
  background: var(--paper); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow-sm); }
.value-icon { display: grid; place-items: center; width: 2rem; height: 2rem; border-radius: .65rem;
  color: var(--brand); background: var(--brand-soft); font-weight: 900; }
.value-card strong { display: block; font-size: .85rem; }
.value-card span:last-child { display: block; margin-top: .12rem; color: var(--muted); font-size: .74rem; }
.section-nav { position: sticky; top: .6rem; z-index: 10; display: flex; gap: .45rem; margin: 1rem 0;
  padding: .45rem; overflow-x: auto; background: color-mix(in srgb, var(--paper) 92%, transparent);
  border: 1px solid var(--line); border-radius: 999px; box-shadow: var(--shadow-sm); backdrop-filter: blur(14px); }
.section-link { flex: 1 0 auto; padding: .5rem .75rem; border-radius: 999px; color: var(--muted);
  text-align: center; text-decoration: none; font-size: .74rem; font-weight: 800; transition: color .15s, background .15s; }
.section-link:hover { color: var(--brand); background: var(--brand-soft); }
.service-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .8rem; margin: 1.1rem 0; }
.summary-card { padding: 1rem 1.05rem; background: var(--paper); border: 1px solid var(--line);
  border-radius: var(--radius); box-shadow: var(--shadow-sm); }
.summary-card .label { display: block; color: var(--muted); font-size: .68rem; font-weight: 800;
  text-transform: uppercase; letter-spacing: .07em; }
.summary-card strong { display: block; margin-top: .2rem; font-size: 1.15rem; font-variant-numeric: tabular-nums; }
.impact-strip { display: flex; align-items: center; gap: .8rem; margin: -0.15rem 0 1rem; padding: .85rem 1rem;
  color: #16482f; background: #edf8f1; border: 1px solid #bddbc8; border-radius: var(--radius); box-shadow: var(--shadow-sm); }
.impact-strip strong { flex: 0 0 auto; font-size: .78rem; text-transform: uppercase; letter-spacing: .07em; }
.impact-strip span { font-size: .82rem; }
.demo-invite { display:flex; align-items:center; justify-content:space-between; gap:1rem; margin:1rem 0;
  padding:1rem 1.1rem; color:white; background:linear-gradient(135deg,var(--brand),var(--brand-2));
  border-radius:var(--radius); box-shadow:var(--shadow-sm); }
.demo-invite strong { display:block; font-size:.9rem; }.demo-invite span { display:block; color:#d8ebe0; font-size:.76rem; }
.demo-invite .button { flex:0 0 auto; margin:0; color:var(--brand); background:white; }
.agent-difference { margin: 1rem 0; padding: 1.1rem 1.2rem; background: linear-gradient(135deg, var(--brand-soft), var(--paper));
  border: 1px solid #b9d7c5; border-radius: var(--radius); box-shadow: var(--shadow-sm); }
.agent-difference h2 { margin: 0; font-size: 1rem; }
.agent-difference > p { margin: .3rem 0 .75rem; color: var(--muted); font-size: .82rem; }
.agent-points { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .65rem; }
.agent-point { padding: .75rem .8rem; background: color-mix(in srgb, var(--paper) 84%, transparent);
  border: 1px solid var(--line); border-radius: .75rem; }
.agent-point strong { display: block; font-size: .78rem; }
.agent-point span { display: block; margin-top: .15rem; color: var(--muted); font-size: .72rem; }
.agent-activity { margin: 1rem 0; background: var(--paper); border: 1px solid var(--line);
  border-radius: var(--radius); box-shadow: var(--shadow-sm); overflow: hidden; }
.agent-activity ol { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 0; padding: .7rem 1.15rem 1rem 2.8rem; }
.agent-activity li { padding: .4rem .75rem .4rem .15rem; color: var(--muted); font-size: .78rem; }
.agent-activity li::marker { color: var(--brand); font-weight: 850; }
.why-action { margin-top: .45rem; }
.why-action summary { color: var(--brand); cursor: pointer; font-size: .72rem; font-weight: 800; }
.calculation-chain { margin-top: .45rem; padding: .65rem .75rem; color: var(--ink); background: var(--brand-soft);
  border-radius: .65rem; font-size: .72rem; font-variant-numeric: tabular-nums; }
.calculation-chain div + div { margin-top: .2rem; }
.calculation-chain .recommendation { margin-top: .55rem; padding-top: .45rem; border-top: 1px solid #b9d7c5; font-weight: 750; }
.critical-action { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 1rem;
  margin: 1rem 0; padding: 1.1rem 1.2rem; color: #651c1c; background: #fff6f6;
  border: 1px solid #e5b3b3; border-left: 5px solid var(--danger); border-radius: var(--radius); box-shadow: var(--shadow-sm); }
.critical-icon { display: grid; place-items: center; width: 2.35rem; height: 2.35rem; border-radius: 50%;
  color: white; background: var(--danger); font-weight: 900; }
.critical-action h2 { margin: 0; font-size: 1.05rem; }
.critical-action p { margin: .2rem 0 0; font-size: .84rem; }
.critical-action--approved { color: #67400c; background: #fff9ed; border-color: #e7c889; border-left-color: var(--warn); }
.critical-action--approved .critical-icon { background: var(--warn); }
.critical-action--resolved { color: #115c31; background: #f3fbf6; border-color: #b9d8c5; border-left-color: var(--good); }
.critical-action--resolved .critical-icon { background: var(--good); }
.action-links { display: flex; flex-wrap: wrap; gap: .45rem; }
.action-links form { margin: 0; }
.action-state { display: inline-block; margin-top: .4rem; padding: .2rem .5rem; border-radius: 999px;
  color: var(--brand); background: var(--brand-soft); font-size: .68rem; font-weight: 850; text-transform: uppercase; letter-spacing: .04em; }
.button { display: inline-flex; align-items: center; justify-content: center; min-height: 2.35rem; padding: .55rem .85rem;
  border: 0; border-radius: .65rem; color: white; background: var(--brand); text-decoration: none; font: inherit; font-size: .76rem; font-weight: 800; cursor: pointer; }
.button:hover { background: var(--brand-2); }
.button--secondary { color: var(--brand); background: white; border: 1px solid #b5cec0; }
.button--secondary:hover { background: var(--brand-soft); }
.intake { display: grid; gap: .8rem; padding: 0 1.15rem 1.1rem; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }
.intake form { display: grid; gap: .55rem; align-content: start; padding: .9rem; border: 1px solid var(--line);
  border-radius: .8rem; background: var(--canvas); margin: 0; }
.intake h3 { margin: 0; font-size: .82rem; font-weight: 850; }
.intake .hint { margin: 0; color: var(--muted); font-size: .72rem; line-height: 1.4; }
.intake label { display: grid; gap: .25rem; color: var(--muted); font-size: .66rem; font-weight: 850;
  text-transform: uppercase; letter-spacing: .04em; }
.intake input, .intake select { min-height: 2.3rem; padding: .4rem .55rem; border: 1px solid var(--line);
  border-radius: .55rem; background: var(--paper); color: var(--ink); font: inherit; font-size: .8rem; }
.intake .button { margin-top: .15rem; }
.station-head { display: flex; align-items: baseline; justify-content: space-between; gap: .6rem;
  padding: .55rem 1.15rem; background: var(--brand-soft); color: var(--brand);
  font-size: .7rem; font-weight: 850; text-transform: uppercase; letter-spacing: .07em; }
.station-head .load { color: var(--muted); font-weight: 700; letter-spacing: .02em; }
.draw { display: block; margin-top: .1rem; color: var(--muted); font-size: .72rem; font-weight: 600; }
.yield-tag { display: inline-block; padding: .12rem .45rem; border-radius: 999px; font-size: .66rem;
  font-weight: 850; letter-spacing: .03em; color: #7a5a12; background: #fdf3dc; }
@media (prefers-color-scheme: dark) { .yield-tag { color: #ffdfa6; background: #3a2f1f; } }
.variance-tag { display: inline-block; padding: .12rem .45rem; border-radius: 999px; font-size: .66rem;
  font-weight: 850; letter-spacing: .03em; }
.variance-tag--short { color: #8a2b2b; background: #fbeaea; }
.variance-tag--over { color: #7a5a12; background: #fdf3dc; }
@media (prefers-color-scheme: dark) { .variance-tag--short { color: #ffc0c0; background: #3a2323; }
  .variance-tag--over { color: #ffdfa6; background: #3a2f1f; } }
.trace-details { margin-top: 1rem; border: 1px solid var(--line); border-radius: var(--radius); background: var(--paper); box-shadow: var(--shadow-sm); }
.trace-details > summary { cursor: pointer; list-style: none; padding: 1rem 1.15rem; font-size: .88rem; font-weight: 850; }
.trace-details > summary::-webkit-details-marker { display: none; }
.trace-details > summary::after { content: "+"; float: right; color: var(--brand); font-size: 1.15rem; }
.trace-details[open] > summary::after { content: "−"; }
.trace-content { padding: 0 1rem 1rem; }

.cards { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .8rem; margin: 1.1rem 0; }
.card, .panel { background: var(--paper); border: 1px solid var(--line); border-radius: var(--radius); }
.card { padding: 1.05rem 1.1rem; box-shadow: var(--shadow-sm); position: relative;
  transition: transform .12s ease, box-shadow .12s ease; }
.card:hover { transform: translateY(-1px); box-shadow: var(--shadow); }
.card .label { color: var(--muted); font-size: .72rem; font-weight: 800; text-transform: uppercase; letter-spacing: .07em; }
.card .value { display: block; margin-top: .2rem; font-size: 1.7rem; line-height: 1.1; font-weight: 850;
  letter-spacing: -.03em; font-variant-numeric: tabular-nums; }
.card--alert { border-color: #e7c3c3; }
.card--alert::before { content: ""; position: absolute; left: 0; top: .9rem; bottom: .9rem; width: 3px;
  border-radius: 3px; background: var(--danger); }
.card--alert .value { color: var(--danger); }

.run-flow { margin: 1.1rem 0; }
.flow-list { list-style: none; display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); margin: 0; padding: 0; }
.flow-step { position: relative; min-width: 0; padding: 1rem .9rem; border-right: 1px solid var(--line); }
.flow-step:last-child { border-right: 0; }
.flow-top { display: flex; align-items: center; gap: .45rem; margin-bottom: .4rem; }
.flow-number { display: grid; place-items: center; flex: 0 0 auto; width: 1.45rem; height: 1.45rem;
  border-radius: 50%; color: white; background: var(--brand); font-size: .68rem; font-weight: 850; }
.flow-label { color: var(--muted); font-size: .65rem; font-weight: 850; text-transform: uppercase; letter-spacing: .06em; }
.flow-value { display: block; font-size: .82rem; font-weight: 800; overflow-wrap: anywhere; }
.flow-detail { display: block; margin-top: .18rem; color: var(--muted); font-size: .7rem; overflow-wrap: anywhere; }
.flow-step--fallback .flow-number { background: var(--warn); }
.grid { display: grid; grid-template-columns: 1.35fr .9fr; gap: 1rem; align-items: start; }
.stack { display: grid; gap: 1rem; }
.panel { overflow: hidden; box-shadow: var(--shadow-sm); }
.panel-head { padding: 1.05rem 1.15rem .8rem; border-bottom: 1px solid var(--line); }
.panel-head h2 { margin: 0; font-size: 1rem; letter-spacing: -.01em; }
.panel-head p { margin: .2rem 0 0; color: var(--muted); font-size: .8rem; }
.rows { list-style: none; padding: 0; margin: 0; }
.row { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: .85rem;
  padding: .8rem 1.15rem; border-bottom: 1px solid var(--line); transition: background .12s; }
.row:last-child { border-bottom: 0; }
.row:hover { background: #f6faf7; }
.rank { display: grid; place-items: center; width: 1.85rem; height: 1.85rem; border-radius: .6rem; color: var(--brand);
  background: var(--brand-soft); font-size: .78rem; font-weight: 850; font-variant-numeric: tabular-nums; }
.name { font-weight: 750; }
.sub { color: var(--muted); font-size: .78rem; margin-top: .1rem; font-variant-numeric: tabular-nums; }
.qty { font-weight: 850; white-space: nowrap; font-variant-numeric: tabular-nums; }
.alert .rank { color: var(--warn); background: #f7ecdd; }
.alert .qty { color: var(--warn); }
.danger .rank { color: var(--danger); background: #f6dede; }
.empty { color: var(--muted); padding: 1.05rem 1.15rem; margin: 0; font-style: italic; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: .84rem; }
th, td { text-align: left; padding: .74rem .85rem; border-bottom: 1px solid var(--line); white-space: nowrap; }
td { font-variant-numeric: tabular-nums; }
td:first-child { font-variant-numeric: normal; }
th { color: var(--muted); font-size: .68rem; text-transform: uppercase; letter-spacing: .06em; }
tbody tr:hover td { background: #f6faf7; }
tr:last-child td { border-bottom: 0; }
.source { display: flex; justify-content: space-between; gap: .75rem; padding: .78rem 1.15rem;
  border-bottom: 1px solid var(--line); font-size: .8rem; }
.source:last-child { border-bottom: 0; }
.source span:first-child { color: var(--muted); }
.source strong { text-align: right; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.driver-list { display: flex; flex-wrap: wrap; gap: .45rem; padding: 1.05rem 1.15rem; }
.chip { display: inline-block; border-radius: 999px; padding: .3rem .6rem; background: #edf2ee; color: #3f5147;
  font-size: .72rem; text-decoration: none; transition: background .12s; }
a.chip:hover { background: var(--brand-soft); }
a.chip[aria-current="page"] { background: var(--brand); color: #fff; }
.forecast-row { padding: .82rem 1.15rem; border-bottom: 1px solid var(--line); }
.forecast-row:last-child { border-bottom: 0; }
.forecast-top { display: flex; justify-content: space-between; gap: .75rem; }
.reason { color: var(--muted); font-size: .76rem; margin-top: .2rem; }
.confidence { color: var(--brand); font-size: .68rem; font-weight: 850; text-transform: uppercase; letter-spacing: .04em; margin-top: .12rem; }
.briefing-summary { margin: 0; padding: 1rem 1.15rem; border-bottom: 1px solid var(--line); }
.priority-panel { display: grid; grid-template-columns: 1fr 1.35fr; margin: 1.1rem 0; }
.priority-lead { padding: 1.25rem; background: linear-gradient(145deg, var(--brand-soft), transparent); }
.priority-lead .eyebrow { color: var(--brand); opacity: 1; }
.priority-lead h2 { margin: 0; font-size: 1.3rem; letter-spacing: -.025em; }
.priority-lead p { margin: .5rem 0 0; color: var(--muted); font-size: .82rem; }
.priority-items { display: grid; grid-template-columns: repeat(3, 1fr); border-left: 1px solid var(--line); }
.priority-item { min-width: 0; padding: 1.15rem 1rem; border-right: 1px solid var(--line); }
.priority-item:last-child { border-right: 0; }
.priority-item .rank { margin-bottom: .6rem; }
.priority-item .name { font-size: .82rem; }
.priority-item .qty { display: block; margin-top: .3rem; color: var(--brand); font-size: 1.05rem; }
.action-badge { display: inline-block; margin-top: .35rem; border-radius: 999px; padding: .2rem .5rem;
  color: var(--warn); background: #f7ecdd; font-size: .65rem; font-weight: 850; text-transform: uppercase; letter-spacing: .04em; }
.warning-list { margin: 0; padding: .85rem 1.15rem .95rem 2.2rem; color: var(--warn); font-size: .78rem; }
.warning-list li + li { margin-top: .35rem; }
.footer { color: var(--muted); text-align: center; font-size: .72rem; margin-top: 1.25rem; }
@media (max-width: 780px) { .hero { min-height: 25rem; background-position: 62% center; }
  .hero::before { background: linear-gradient(90deg, rgba(7,29,19,.95), rgba(7,29,19,.72)); }
  .status { position: static; margin-top: 1rem; } .hero-copy { padding-top: 0; }
  .value-strip, .service-summary { grid-template-columns: repeat(2, 1fr); } .priority-panel { grid-template-columns: 1fr; }
  .agent-points, .agent-activity ol { grid-template-columns: 1fr; }
  .demo-invite { align-items:flex-start; flex-direction:column; }
  .critical-action { grid-template-columns: auto 1fr; } .action-links { grid-column: 1 / -1; }
  .priority-items { border-left: 0; border-top: 1px solid var(--line); }
  .cards { grid-template-columns: repeat(2, 1fr); } .grid { grid-template-columns: 1fr; }
  .flow-list { grid-template-columns: 1fr; } .flow-step { border-right: 0; border-bottom: 1px solid var(--line); }
  .flow-step:last-child { border-bottom: 0; } }
@media (max-width: 430px) { .shell { width: min(100% - 1rem, 1080px); } .hero { border-radius: 16px; }
  .service-summary { grid-template-columns: 1fr 1fr; }
  .cards { gap: .55rem; } .card { padding: .85rem .9rem; } .card .value { font-size: 1.5rem; }
  .priority-items { grid-template-columns: 1fr; } .priority-item { display: grid; grid-template-columns: auto 1fr auto;
    gap: .7rem; align-items: center; border-right: 0; border-bottom: 1px solid var(--line); }
  .priority-item:last-child { border-bottom: 0; } .priority-item .rank { margin: 0; }
  .priority-item .qty { margin: 0; } .row { padding: .74rem .9rem; } }
@media (prefers-color-scheme: dark) { :root { color-scheme: dark; --ink: #e9f0eb; --muted: #a4b3ab;
  --line: #2f4239; --paper: #16221b; --canvas: #0e150f; --brand-soft: #21402f; --brand: #6fbf93;
  --shadow-sm: 0 1px 2px rgba(0,0,0,.4); --shadow: 0 10px 34px rgba(0,0,0,.5); }
  .language { background: #16221b; border-color: #3f5f4d; color: #a9dfbf; }
  .chip { background: #243228; color: #c4d0c8; } a.chip:hover { background: #2c4a38; }
  .row:hover, tbody tr:hover td { background: #1b2a21; }
  .card--alert { border-color: #5a2f2f; } .card--alert .value { color: #f0a1a1; }
  .critical-action { color: #ffd1d1; background: #2d1c1c; border-color: #673636; }
  .critical-action--approved { color: #ffe0a6; background: #302719; border-color: #675432; }
  .critical-action--resolved { color: #b9f0cc; background: #17291e; border-color: #315a40; }
  .alert .rank, .action-badge { background: #3a2f1f; } .danger .rank { background: #3a2323; } }
""".strip()


def _empty(message: str) -> str:
    return f'<p class="empty">{escape(message)}</p>'


_ADJUSTMENT_LABELS = {
    "receipt": ("Goods receipt", "Varemottak"),
    "count": ("Stock count", "Opptelling"),
}


def _variance_tag(variance_qty: float, unit: str, language: str) -> str:
    """A signed variance, tagged so a shortage is never mistaken for a surplus."""
    en = language == "en"
    if variance_qty < 0:
        kind, word = "short", ("short" if en else "manko")
    else:
        kind, word = "over", ("over" if en else "overskudd")
    return (
        f'<span class="variance-tag variance-tag--{kind}">'
        f'{escape(_number(abs(variance_qty)))} {escape(unit)} {escape(word)}</span>'
    )


def _receiving_block(plan: dict, language: str, interactive: bool) -> str:
    """Goods receipt and stock count: what the kitchen physically saw.

    The planner assumes deliveries arrive in full and that theoretical
    consumption is what happened. This panel is where that assumption is
    corrected, and where corrections already applied to this plan are shown.
    """
    en = language == "en"
    applied = plan.get("inventory_adjustments", []) or []
    variance_by_id = {
        str(item.get("adjustment_id")): item for item in (plan.get("stock_variances", []) or [])
    }

    if applied:
        rows = []
        for adjustment in applied:
            kind = str(adjustment.get("type", ""))
            label_en, label_no = _ADJUSTMENT_LABELS.get(kind, (kind, kind))
            item_id = str(adjustment.get("item_id", ""))
            unit = unit_label(adjustment.get("unit"), language)
            variance = variance_by_id.get(str(adjustment.get("adjustment_id")))
            tag = (
                _variance_tag(float(variance["variance_qty"]), unit, language)
                if variance
                else f'<span class="sub">{"as planned" if en else "som planlagt"}</span>'
            )
            note = str(adjustment.get("note") or "")
            recorded_by = str(adjustment.get("recorded_by") or "")
            detail = " · ".join(part for part in (recorded_by, note) if part)
            rows.append(
                f'<li class="row"><div><div class="name">{escape(label_en if en else label_no)}: '
                f'{escape(_item_name(item_id, language))}</div>'
                f'<div class="sub">{escape(detail)}</div></div>'
                f'<span class="qty">{escape(_number(adjustment.get("qty", 0)))} {escape(unit)}<br>{tag}</span></li>'
            )
        applied_block = f'<ul class="rows">{"".join(rows)}</ul>'
    else:
        applied_block = _empty(
            "No physical corrections were recorded for this plan."
            if en
            else "Ingen fysiske korrigeringer er registrert for denne planen."
        )

    if not interactive:
        return applied_block

    options = "".join(
        f'<option value="{escape(item_id)}">{escape(_item_name(item_id, language))} '
        f'({escape(unit_label(meta.get("unit"), language))})</option>'
        for item_id, meta in sorted(menu_da.ingredients_by_id().items())
    )
    lang_field = f'<input type="hidden" name="lang" value="{language}">'
    forms = f'''<div class="intake">
<form method="post" action="/inventory/receipts">{lang_field}
<h3>{"Record a goods receipt" if en else "Registrer varemottak"}</h3>
<p class="hint">{"What the delivery actually contained. Give the order date to correct an assumed arrival." if en else "Hva leveransen faktisk inneholdt. Oppgi bestillingsdato for å korrigere en antatt leveranse."}</p>
<label>{"Ingredient" if en else "Ingrediens"}<select name="item_id" required>{options}</select></label>
<label>{"Quantity received" if en else "Mottatt mengde"}<input type="number" name="qty_received" min="0" step="0.001" required></label>
<label>{"Order date (optional)" if en else "Bestillingsdato (valgfritt)"}<input type="date" name="order_by_date"></label>
<label>{"Expiry date (optional)" if en else "Utløpsdato (valgfritt)"}<input type="date" name="expiry_date"></label>
<label>{"Note" if en else "Merknad"}<input type="text" name="note" maxlength="200"></label>
<button class="button" type="submit">{"Record receipt" if en else "Registrer mottak"}</button></form>
<form method="post" action="/inventory/counts">{lang_field}
<h3>{"Record a stock count" if en else "Registrer opptelling"}</h3>
<p class="hint">{"What a physical count actually found. The counted quantity becomes the truth for that ingredient." if en else "Hva en fysisk telling faktisk fant. Den talte mengden blir fasit for ingrediensen."}</p>
<label>{"Ingredient" if en else "Ingrediens"}<select name="item_id" required>{options}</select></label>
<label>{"Counted quantity" if en else "Talt mengde"}<input type="number" name="counted_qty" min="0" step="0.001" required></label>
<label>{"Note" if en else "Merknad"}<input type="text" name="note" maxlength="200"></label>
<button class="button" type="submit">{"Record count" if en else "Registrer opptelling"}</button></form></div>
<p class="hint" style="padding:0 1.15rem 1.1rem">{"A planned day is frozen: a correction recorded now applies to the next day that has not been planned yet." if en else "En planlagt dag er frosset: en korrigering som registreres nå, gjelder fra den første dagen som ennå ikke er planlagt."}</p>'''
    return applied_block + forms


def _trim_block(plan: dict, language: str) -> str:
    """Trim loss: the gap between prepared weight and purchased weight.

    Kept apart from expiry waste on purpose. This loss is planned and
    unavoidable; an expired batch is neither, and a kitchen acts on the two
    completely differently.
    """
    en = language == "en"
    losses = plan.get("trim_loss", []) or []
    if not losses:
        return _empty(
            "No ingredient in today's plan loses weight to trimming."
            if en
            else "Ingen ingrediens i dagens plan taper vekt på rensing."
        )

    rows = []
    for item in losses:
        item_id = str(item.get("item_id", ""))
        unit = unit_label(item.get("unit"), language)
        percent = round(float(item.get("yield_factor", 1)) * 100)
        rows.append(
            f'<li class="row"><div><div class="name">{escape(_item_name(item_id, language))}</div>'
            f'<div class="sub">{escape(_number(item.get("prepared", 0)))} {escape(unit)} '
            f'{"on the plate needs" if en else "på tallerkenen krever"} '
            f'{escape(_number(item.get("purchased", 0)))} {escape(unit)} '
            f'{"purchased" if en else "innkjøpt"}</div></div>'
            f'<span class="qty"><span class="yield-tag">−{escape(_number(item.get("trim_loss", 0)))} '
            f'{escape(unit)}</span><br><span class="sub">{percent}% {"yield" if en else "utbytte"}</span></span></li>'
        )
    return f'<ul class="rows">{"".join(rows)}</ul>'


def _station_block(plan: dict, language: str) -> str:
    """Component prep, grouped by the station that does the work.

    A dish task tells a manager what the day looks like. This tells a cook what
    to pick up: one job per ingredient, aggregated across every dish that uses
    it, with the quantity to produce and the quantity to draw from stock.
    """
    en = language == "en"
    tasks = plan.get("station_tasks", []) or []
    if not tasks:
        return _empty(
            "Nothing in today's plan needs component prep."
            if en
            else "Ingenting i dagens plan krever komponentprep."
        )

    stations = plan.get("stations") or [{"station": task["station"]} for task in tasks]
    blocks = []
    for station in stations:
        station_id = str(station.get("station", ""))
        rows = []
        for task in sorted(
            (t for t in tasks if t.get("station") == station_id),
            key=lambda t: t.get("priority", 0),
        ):
            item_id = str(task.get("item_id", ""))
            unit = unit_label(task.get("unit"), language)
            rows.append(
                f'<li class="row"><span class="rank">{task.get("priority", "")}</span>'
                f'<div><div class="name">{escape(str(task.get("action") or ""))} — '
                f'{escape(_item_name(item_id, language))}</div>'
                f'<div class="sub">{escape(_duration(task.get("prep_minutes"), language))}</div></div>'
                f'<span class="qty">{escape(_number(task.get("prepare_qty", 0)))} {escape(unit)}'
                f'<span class="draw">{"draw" if en else "hent"} '
                f'{escape(_number(task.get("draw_qty", 0)))} {escape(unit)}</span></span></li>'
            )
        if not rows:
            continue
        minutes = station.get("prep_minutes")
        load = (
            f'{len(rows)} {"jobs" if len(rows) != 1 and en else "job" if en else "oppgaver" if len(rows) != 1 else "oppgave"}'
            + (f' · {escape(_duration(minutes, language))}' if minutes is not None else "")
        )
        blocks.append(
            f'<div class="station-head"><span>{escape(prep_pipe.station_label(station_id, language))}</span>'
            f'<span class="load">{load}</span></div><ul class="rows">{"".join(rows)}</ul>'
        )
    return "".join(blocks)


def render_home(
    plan: dict | None,
    language: str = "no",
    available_plans: list[dict] | None = None,
    interactive: bool = False,
    demo_url: str | None = None,
) -> str:
    language = "en" if language == "en" else "no"
    en = language == "en"
    switch = (
        '<a class="language" href="?lang=no" lang="no">Norsk</a>'
        if en else '<a class="language" href="?lang=en" lang="en">English</a>'
    )
    brand = '<div class="brand"><span class="mark">K</span><span>Kitchen Prep Agent</span></div>'

    if plan is None:
        message = "No plan has been published yet." if en else "Ingen plan er publisert ennå."
        return f"""<!doctype html><html lang="{language}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Kitchen Prep Agent</title>
<style>{_STYLE}</style></head><body><main class="shell"><nav class="topbar">{brand}{switch}</nav>
<section class="panel">{_empty(message)}</section></main></body></html>"""

    status, status_kind = _run_status(plan, language)
    degraded = status_kind == "degraded"
    prep = sorted(plan.get("prep_tasks", []), key=lambda task: task["priority"])
    shortfalls = plan.get("prep_shortfalls", [])
    orders = plan.get("replenishment_orders", [])
    waste = plan.get("waste_flagged", [])
    forecast = plan.get("forecast", {})

    prep_rows = "".join(
        f'<li class="row"><span class="rank">{task["priority"]}</span><div><div class="name">{escape(_dish_name(task["dish_id"]))}</div>'
        f'<div class="sub" title="ID: {escape(task["dish_id"])}">{escape(_duration(task["prep_minutes"], language))}</div></div>'
        f'<span class="qty">{escape(_number(task["qty"]))} {escape(portion_label(language))}</span></li>'
        for task in prep
    )
    prep_block = f'<ul class="rows">{prep_rows}</ul>' if prep else _empty("No prep tasks." if en else "Ingen prep-oppgaver.")

    briefing = plan.get("briefing", {})
    forecast_by_dish = {
        str(item.get("dish_id")): item
        for item in forecast.get("dishes", [])
        if item.get("dish_id")
    }

    def shortfall_row(item: dict) -> str:
        item_id = str(item["item_id"])
        unit = ingredient_unit(item_id, language)
        recipe_lines = []
        for dish in menu_da.load_menu():
            recipe_qty = dish.get("recipe", {}).get(item_id)
            forecast_item = forecast_by_dish.get(str(dish.get("id")))
            if recipe_qty is None or not forecast_item:
                continue
            portions = forecast_item.get("expected_qty", 0)
            contribution = float(portions) * float(recipe_qty)
            recipe_lines.append(
                f'<div>{escape(_number(portions))} {escape(portion_label(language))} '
                f'× {escape(_number(recipe_qty))} {escape(unit)} = {escape(_number(contribution))} {escape(unit)} '
                f'({escape(str(dish.get("name", dish.get("id", ""))))})</div>'
            )
        calculation = "".join(recipe_lines)
        calculation += (
            f'<div>{escape(_number(item["required"]))} {escape(unit)} {"required" if en else "behov"} '
            f'− {escape(_number(item["available"]))} {escape(unit)} {"usable inventory" if en else "brukbart lager"} '
            f'= {escape(_number(item["shortfall"]))} {escape(unit)} {"service shortfall" if en else "mangel før service"}</div>'
        )
        approval_action = next(
            (
                action
                for action in briefing.get("shortfall_actions", [])
                if action.get("item_id") == item_id
            ),
            {},
        )
        recommendation = str(approval_action.get("recommended_action", "")).strip()
        if recommendation and not en:
            recommendation = (
                f'Skaff {_number(item.get("shortfall", 0))} {unit} '
                f'{_item_name(item_id, language)} før service.'
            )
        approval = bool(approval_action.get("requires_human_approval"))
        recommendation_block = ""
        if recommendation:
            recommendation_block += (
                f'<div class="reason"><strong>{"Recommended action" if en else "Anbefalt tiltak"}:</strong> '
                f'{escape(recommendation)}</div>'
            )
        if approval:
            recommendation_block += (
                f'<span class="action-badge">{"Approval required" if en else "Krever godkjenning"}</span>'
            )
        return (
            f'<li class="row alert"><span class="rank">!</span><div><div class="name">{escape(_item_name(item_id, language))}</div>'
            f'<div class="sub">{("Available" if en else "Tilgjengelig")}: {escape(_number(item["available"]))} {escape(unit)} · '
            f'{("Required" if en else "Behov")}: {escape(_number(item["required"]))} {escape(unit)}</div>{recommendation_block}'
            f'<details class="why-action"><summary>{"Why this action?" if en else "Hvorfor dette tiltaket?"}</summary>'
            f'<div class="calculation-chain">{calculation}</div></details></div>'
            f'<span class="qty">{("Missing" if en else "Mangler")} {escape(_number(item["shortfall"]))} {escape(unit)}</span></li>'
        )

    short_rows = "".join(shortfall_row(item) for item in shortfalls)
    short_block = f'<ul class="rows">{short_rows}</ul>' if shortfalls else _empty("No shortfalls today." if en else "Ingen mangler i dag.")

    order_rows = "".join(
        f'<tr><td><strong>{escape(_item_name(order["item_id"], language))}</strong></td>'
        f'<td>{escape(_number(order["order_qty"]))} {escape(unit_label(order["unit"], language))}</td>'
        f'<td>{escape(order["supplier"])}</td><td><time datetime="{escape(order["delivery_date"])}">'
        f'{escape(_display_date(order["delivery_date"], language))}</time></td></tr>'
        for order in orders
    )
    order_block = (
        f'<div class="table-wrap"><table><thead><tr><th>{"Item" if en else "Vare"}</th><th>{"Additional order" if en else "Ekstra bestilling"}</th>'
        f'<th>{"Supplier" if en else "Leverandør"}</th><th>{"Delivery" if en else "Levering"}</th></tr></thead><tbody>{order_rows}</tbody></table></div>'
        if orders else _empty("No replenishment orders." if en else "Ingen bestillinger.")
    )

    waste_rows = "".join(
        f'<li class="row danger"><span class="rank">×</span><div><div class="name">{escape(_item_name(item["item_id"], language))}</div>'
        f'<div class="sub">{escape(item["batch_id"])} · {("Expired" if en else "Utløpt")} <time datetime="{escape(item["expiry_date"])}">{escape(_display_date(item["expiry_date"], language))}</time></div></div>'
        f'<span class="qty">{escape(_number(item["qty"]))} {escape(ingredient_unit(item["item_id"], language))}</span></li>'
        for item in waste
    )
    waste_block = f'<ul class="rows">{waste_rows}</ul>' if waste else _empty("No expired waste." if en else "Ingen utgått svinn.")

    briefing_summary = str(briefing.get("summary", "")).strip()
    if not en:
        briefing_summary = (
            f'{_number(plan.get("expected_covers", 0))} gjester forventet. '
            f'{len(shortfalls)} {"mangel" if len(shortfalls) == 1 else "mangler"} krever oppfølging.'
        )

    warning_items = "".join(f'<li>{escape(str(warning))}</li>' for warning in briefing.get("warnings", []) if str(warning).strip()) if en else "".join(
        f'<li>Kasser utgått parti {escape(str(item.get("batch_id", "")))}.</li>' for item in waste
    )
    briefing_trace_block = (
        (f'<p class="briefing-summary">{escape(briefing_summary)}</p>' if briefing_summary else "")
        + (f'<ul class="warning-list">{warning_items}</ul>' if warning_items else "")
    ) or _empty("No recorded briefing." if en else "Ingen registrert briefing.")
    approval_count = sum(
        1
        for action in briefing.get("shortfall_actions", [])
        if action.get("requires_human_approval")
    )
    drivers = forecast.get("drivers", [])
    driver_block = "".join(f'<span class="chip">{escape(_human_id(str(driver)))}</span>' for driver in drivers)
    forecast_rows = "".join(
        f'<div class="forecast-row"><div class="forecast-top"><span class="name">{escape(_dish_name(item["dish_id"]))}</span>'
        f'<span class="qty">{escape(str(item["expected_qty"]))}</span></div>'
        f'<div class="confidence">{escape(str(item.get("confidence", "medium")))}</div>'
        f'<div class="reason">{escape(_reasoning_text(str(item.get("reasoning", "")), language, forecast.get("forecast_source") == "deterministic_fallback"))}</div></div>'
        for item in forecast.get("dishes", [])
    )

    status_class = f"status {status_kind}" if status_kind != "ready" else "status"
    forecast_note = plan.get("forecast_note", "")
    forecast_source = str(forecast.get("forecast_source", "unknown"))
    briefing_source = str(plan.get("briefing_source", "unknown"))
    forecast_label = _source_label(forecast_source, language)
    briefing_label = _source_label(briefing_source, language)
    validation_fallback = forecast_source == "deterministic_fallback"
    validation_value = ("Fallback activated" if en else "Fallback aktivert") if validation_fallback else ("Proposal accepted" if en else "Forslag godkjent")
    fefo_count = len(plan.get("fefo_consumption", []))
    covers_source = str(plan.get("covers_source", "expected_covers"))
    planning_basis = str(plan.get("planning_basis", "today_consumption_plus_par"))
    generated_raw = str(plan.get("generated_at", ""))
    generated_display = _display_timestamp(generated_raw, language)
    flow_steps = [
        (("Inputs" if en else "Datagrunnlag"), f'{plan["expected_covers"]} {"guests" if en else "gjester"}', _technical_label(covers_source, language), False),
        (("AI forecast" if en else "AI-prognose"), forecast_label, f'{len(forecast.get("dishes", []))} {"dish predictions" if en else "rettsprognoser"}', validation_fallback),
        (("Validation" if en else "Validering"), validation_value, _technical_label(str(forecast_note or "—"), language), validation_fallback),
        (("Deterministic math" if en else "Deterministisk beregning"), f'FEFO · {fefo_count} {"batch uses" if en else "batchuttak"}', _technical_label(planning_basis, language), False),
        (("Published plan" if en else "Publisert plan"), briefing_label, generated_display, briefing_source == "deterministic_fallback"),
    ]
    flow_items = "".join(
        f'<li class="flow-step{" flow-step--fallback" if fallback else ""}"><div class="flow-top"><span class="flow-number">{number}</span>'
        f'<span class="flow-label">{escape(label)}</span></div><strong class="flow-value">{escape(value)}</strong>'
        f'<span class="flow-detail">{escape(detail)}</span></li>'
        for number, (label, value, detail, fallback) in enumerate(flow_steps, 1)
    )
    history_items = []
    for historic in available_plans or []:
        historic_date = str(historic.get("date", ""))
        if not historic_date:
            continue
        selected = historic_date == str(plan.get("date"))
        href = f"?lang={language}&amp;date={escape(historic_date)}"
        label = f'{_display_date(historic_date, language)} · {historic.get("expected_covers", "—")} {"guests" if en else "gjester"}'
        raw_label = f'{historic_date} · {historic.get("expected_covers", "—")} {"guests" if en else "gjester"}'
        current_page = ' aria-current="page"' if selected else ""
        history_items.append(
            f'<a class="chip" href="{href}" aria-label="{escape(raw_label)}" title="{escape(historic_date)}"'
            f'{current_page}>{escape(label)}</a>'
        )
    history_block = "".join(history_items)
    action_history_rows = "".join(
        f'<li class="row"><span class="rank">✓</span><div><div class="name">{escape(_item_name(str(event.get("item_id", "")), language))}</div>'
        f'<div class="sub"><time datetime="{escape(str(event.get("occurred_at", "")))}">{escape(_display_timestamp(str(event.get("occurred_at", "")), language))}</time></div></div>'
        f'<span class="qty">{escape(_technical_label(str(event.get("status", "")), language))}</span></li>'
        for event in reversed(plan.get("action_history", []))
    )
    plan_date = _display_date(str(plan.get("date", "")), language)
    unresolved_count = sum(
        1
        for item in shortfalls
        if plan.get("operational_actions", {}).get(str(item.get("item_id")), {}).get("status")
        != "resolved"
    )
    if unresolved_count:
        status_explanation = (
            f'{unresolved_count} {"service risk requires" if unresolved_count == 1 else "service risks require"} human attention.'
            if en else
            f'{unresolved_count} {"servicerisiko krever" if unresolved_count == 1 else "servicerisikoer krever"} menneskelig oppfølging.'
        )
        if validation_fallback:
            status_explanation += (
                " Forecast model unavailable; verified reserve model used."
                if en else
                " Prognosemodellen er utilgjengelig; kontrollert reservemodell er brukt."
            )
    elif degraded:
        status_explanation = (
            "Forecast model unavailable; verified reserve model used."
            if en else
            "Prognosemodellen er utilgjengelig; kontrollert reservemodell er brukt."
        )
    else:
        status_explanation = "All primary systems are available." if en else "Alle primærsystemer er tilgjengelige."
    demand_context_step = (
        "Applied weekday and weather context to the Gemini demand proposal"
        if en and not validation_fallback
        else "Brukte ukedag og værkontekst i Gemini-forslaget"
        if not validation_fallback
        else "Read weekday and weather context; safe fallback used same-weekday history"
        if en
        else "Leste ukedag og værkontekst; sikker fallback brukte historikk fra samme ukedag"
    )
    service_alerts = (
        f'{len(shortfalls)} service alert' if len(shortfalls) == 1 else f'{len(shortfalls)} service alerts'
    ) if en else (
        f'{len(shortfalls)} servicevarsel' if len(shortfalls) == 1 else f'{len(shortfalls)} servicevarsler'
    )
    approval_requests = (
        f'{approval_count} approval request' if approval_count == 1 else f'{approval_count} approval requests'
    ) if en else (
        f'{approval_count} godkjenningsforespørsel'
        if approval_count == 1 else f'{approval_count} godkjenningsforespørsler'
    )
    activity_steps = [
        (
            f"Scheduled daily at 07:00; this plan was generated {generated_display}"
            if en else f"Planlagt daglig kl. 07:00; denne planen ble generert {generated_display}"
        ),
        (
            f'Read simulated POS history; resolved {_number(plan["expected_covers"])} expected covers from '
            f'{_technical_label(covers_source, language)}'
        ) if en else (
            f'Leste simulert POS-historikk; fastsatte {_number(plan["expected_covers"])} forventede gjester fra '
            f'{_technical_label(covers_source, language)}'
        ),
        demand_context_step,
        f'Forecasted {len(forecast.get("dishes", []))} menu items'
        if en else f'Prognostiserte {len(forecast.get("dishes", []))} menyretter',
        f'Expanded recipes into {len(plan.get("ingredient_requirements", {}))} ingredient requirements'
        if en else f'Utvidet oppskrifter til {len(plan.get("ingredient_requirements", {}))} råvarebehov',
        "Reconciled demand with dated FEFO inventory and scheduled deliveries"
        if en else "Avstemte behov mot datert FEFO-lager og planlagte leveranser",
        f'Published the prep plan with {service_alerts} and {approval_requests}'
        if en else f'Publiserte prep-planen med {service_alerts} og {approval_requests}',
    ]
    agent_activity_items = "".join(f'<li>{escape(step)}</li>' for step in activity_steps)
    demo_invite = (
        f'<section class="demo-invite"><div><strong>{"Want to see the agent act live?" if en else "Vil du se agenten handle live?"}</strong>'
        f'<span>{"Run an isolated 90-second sandbox with simulated data." if en else "Kjør en isolert 90-sekunders sandbox med simulerte data."}</span></div>'
        f'<a class="button" href="{escape(demo_url)}">{"Try interactive demo" if en else "Prøv interaktiv demo"}</a></section>'
        if demo_url else ""
    )
    agent_difference_block = f'''<section class="agent-difference" aria-labelledby="agent-difference-title"><h2 id="agent-difference-title">{"Why this isn’t a spreadsheet" if en else "Hvorfor dette ikke er et regneark"}</h2>
<p>{"A spreadsheet waits for an operator. This agent runs the full planning workflow and publishes an auditable result." if en else "Et regneark venter på en operatør. Denne agenten kjører hele planleggingsflyten og publiserer et reviderbart resultat."}</p>
<div class="agent-points"><div class="agent-point"><strong>{"Deterministic engine" if en else "Deterministisk motor"}</strong><span>{"Calculates recipe demand, FEFO consumption, shortages, lead times and par replenishment." if en else "Beregner oppskriftsbehov, FEFO-forbruk, mangler, ledetider og lagerpåfylling til par."}</span></div>
<div class="agent-point"><strong>{"AI agent" if en else "AI-agent"}</strong><span>{"Uses the validated context to forecast, prioritise conflicts, propose actions and explain the result." if en else "Bruker validert kontekst til prognose, prioritering av konflikter, tiltak og forklaring."}</span></div>
<div class="agent-point"><strong>{"Human approval" if en else "Menneskelig godkjenning"}</strong><span>{"Shortfall recommendations are flagged for operator review; recorded decisions appear in the audit trail." if en else "Anbefalinger om mangler merkes for operatørkontroll; registrerte beslutninger vises i revisjonssporet."}</span></div></div></section>'''
    if shortfalls and interactive:
        critical = shortfalls[0]
        critical_item = _item_name(str(critical.get("item_id", "")), language)
        critical_item_id = str(critical.get("item_id", ""))
        critical_qty = _number(critical.get("shortfall", 0))
        action_status = str(plan.get("operational_actions", {}).get(critical_item_id, {}).get("status", "pending"))
        status_labels = {
            "pending": "Pending" if en else "Venter",
            "approved": "Approved" if en else "Godkjent",
            "resolved": "Resolved" if en else "Løst",
            "reopened": "Reopened" if en else "Gjenåpnet",
        }
        action_state = status_labels.get(action_status, _human_id(action_status))
        critical_class = "critical-action"
        critical_icon = "!"
        if action_status == "approved":
            critical_class += " critical-action--approved"
        elif action_status == "resolved":
            critical_class += " critical-action--resolved"
            critical_icon = "✓"
        if interactive:
            if action_status == "resolved":
                action_controls = f'''<form method="post" action="/plans/{escape(str(plan.get('date', '')))}/actions/{escape(critical_item_id)}/reopened?lang={language}"><button class="button button--secondary" type="submit">{"Reopen" if en else "Gjenåpne"}</button></form>'''
            else:
                action_controls = f'''<form method="post" action="/plans/{escape(str(plan.get('date', '')))}/actions/{escape(critical_item_id)}/approved?lang={language}"><button class="button" type="submit">{"Approve" if en else "Godkjenn"}</button></form>
<form method="post" action="/plans/{escape(str(plan.get('date', '')))}/actions/{escape(critical_item_id)}/resolved?lang={language}"><button class="button button--secondary" type="submit">{"Mark resolved" if en else "Marker som løst"}</button></form>'''
        critical_heading = (("Resolved" if en else "Markert som løst") if action_status == "resolved" else ("Action required" if en else "Handling kreves"))
        critical_block = f'''<section class="{critical_class}" id="critical-actions" aria-labelledby="critical-title">
<span class="critical-icon">{critical_icon}</span><div><h2 id="critical-title">{critical_heading}: {"Source" if en and action_status != "resolved" else "Skaff" if not en and action_status != "resolved" else ""} {escape(critical_qty)} {escape(ingredient_unit(critical_item_id, language))} {escape(critical_item)}</h2>
<p>{"Resolve before service. Review the calculated order and approval recommendation." if en else "Må løses før service. Kontroller bestillingsforslaget og anbefalingen før godkjenning."}</p><span class="action-state">Status: {escape(action_state)}</span></div>
<div class="action-links">{action_controls}</div></section>'''
    elif unresolved_count:
        risk_label = (
            "service risk requires review"
            if unresolved_count == 1 and en
            else "service risks require review"
            if en
            else "servicerisiko krever vurdering"
            if unresolved_count == 1
            else "servicerisikoer krever vurdering"
        )
        critical_block = f'''<section class="critical-action" id="critical-actions" aria-labelledby="critical-title">
<span class="critical-icon">!</span><div><h2 id="critical-title">{unresolved_count} {risk_label}</h2>
<p>{"Review each shortage and its recommended action before service." if en else "Vurder hver mangel og det anbefalte tiltaket før service."}</p></div>
<div class="action-links"><a class="button button--secondary" href="#orders">{"View future replenishment" if en else "Se fremtidig lagerpåfylling"}</a><a class="button" href="#service-risks">{"Review today’s risks" if en else "Vurder dagens risikoer"}</a></div></section>'''
    else:
        critical_block = f'''<section class="critical-action" id="critical-actions" style="border-left-color:var(--good);border-color:#b9d8c5;background:#f3fbf6;color:var(--ink)">
<span class="critical-icon" style="background:var(--good)">✓</span><div><h2>{"No unresolved service risks" if en else "Ingen uløste servicerisikoer"}</h2><p>{"The plan is ready for service." if en else "Planen er klar for service."}</p></div></section>'''
    receiving_block = _receiving_block(plan, language, interactive)
    trim_block = _trim_block(plan, language)
    station_block = _station_block(plan, language)
    next_step = (
        "Resolve shortfall" if en else "Løs mangel"
    ) if unresolved_count else "Start prep"
    return f"""<!doctype html>
<html lang="{language}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kitchen Prep — {escape(plan['date'])}</title><style>{_STYLE}</style></head><body><main class="shell">
<nav class="topbar">{brand}{switch}</nav>
<header class="hero"><div class="hero-row"><div class="hero-copy"><p class="eyebrow">{"Operations overview" if en else "Operativ oversikt"}</p>
<h1>{"Today’s kitchen plan" if en else "Dagens kjøkkenplan"}</h1>
<p>{escape(status_explanation)}<br>Plan for <time datetime="{escape(str(plan.get('date', '')))}">{escape(plan_date)}</time> · {plan['expected_covers']} {"guests" if en else "gjester"}<br>
{"Last updated" if en else "Sist oppdatert"}: <time datetime="{escape(generated_raw)}">{escape(generated_display)}</time></p></div>
<span class="{status_class}"><span class="dot"></span>{escape(status)}</span></div></header>

<section class="service-summary" aria-label="{"Service summary" if en else "Serviceoversikt"}">
<div class="summary-card"><span class="label">Plan for</span><strong>{escape(plan_date)}</strong></div>
<div class="summary-card"><span class="label">{"Expected guests" if en else "Forventede gjester"}</span><strong>{escape(_number(plan['expected_covers']))}</strong></div>
<div class="summary-card"><span class="label">{"Unresolved risks" if en else "Uløste risikoer"}</span><strong>{unresolved_count}</strong></div>
<div class="summary-card"><span class="label">{"Next step" if en else "Neste handling"}</span><strong>{next_step}</strong></div>
</section>

<section class="impact-strip" aria-label="{"Plan impact" if en else "Planeffekt"}"><strong>{"Impact" if en else "Effekt"}</strong><span>{len(shortfalls)} {"service risk surfaced before prep" if len(shortfalls) == 1 and en else "service risks surfaced before prep" if en else "servicerisiko avdekket før prep" if len(shortfalls) == 1 else "servicerisikoer avdekket før prep"} · {len(waste)} {"expired batch isolated" if len(waste) == 1 and en else "expired batches isolated" if en else "utgått batch isolert" if len(waste) == 1 else "utgåtte batcher isolert"} · {len(orders)} {"future replenishment proposal prepared" if len(orders) == 1 and en else "future replenishment proposals prepared" if en else "forslag til fremtidig lagerpåfylling klargjort"}. {"Calculated from this published plan." if en else "Beregnet fra denne publiserte planen."}</span></section>

{demo_invite}

{critical_block}

<nav class="section-nav" aria-label="Quick navigation">
<a class="section-link" href="#service-risks">{"Today’s risks" if en else "Dagens risikoer"}</a>
<a class="section-link" href="#prep-plan">{"Prep plan" if en else "Prep-plan"}</a>
<a class="section-link" href="#station-prep">{"Stations" if en else "Stasjoner"}</a>
<a class="section-link" href="#orders">{"Orders" if en else "Bestillinger"}</a>
<a class="section-link" href="#inventory-receiving">{"Receiving" if en else "Varemottak"}</a>
<a class="section-link" href="#trim-loss">{"Trim loss" if en else "Rensetap"}</a>
<a class="section-link" href="#forecast">{"Forecast" if en else "Prognose"}</a>
<a class="section-link" href="#traceability">{"Traceability" if en else "Sporbarhet"}</a></nav>

<div class="stack">
<section class="panel" id="service-risks"><header class="panel-head"><h2>{"Today’s service risks" if en else "Dagens servicerisikoer"}</h2><p>{"Each shortage includes one recommendation and its approval boundary." if en else "Hver mangel vises med ett anbefalt tiltak og krav til godkjenning."}</p></header>{short_block}</section>
<section class="panel" id="prep-plan"><header class="panel-head"><h2>{"Today’s prep" if en else "Dagens prep"}</h2><p>{"Work in priority order" if en else "Utfør i prioritert rekkefølge"}</p></header>{prep_block}</section>
<section class="panel" id="station-prep"><header class="panel-head"><h2>{"Component prep by station" if en else "Komponentprep per stasjon"}</h2><p>{"One job per ingredient, aggregated across every dish that uses it. Produce the first quantity; draw the second from stock." if en else "Én jobb per ingrediens, slått sammen på tvers av alle rettene som bruker den. Lag den første mengden; hent den andre fra lageret."}</p></header>{station_block}</section>
<section class="panel" id="orders"><header class="panel-head"><h2>{"Future replenishment" if en else "Fremtidig lagerpåfylling"}</h2><p>{"Calculated to restore par stock after scheduled arrivals. These deliveries do not resolve today’s service shortfalls." if en else "Beregnet for å fylle lageret til par-nivå etter planlagte leveranser. Disse leveransene løser ikke dagens mangler."}</p></header>{order_block}</section>
<section class="panel" id="inventory-receiving"><header class="panel-head"><h2>{"Goods receipt and stock count" if en else "Varemottak og opptelling"}</h2><p>{"Physical reality corrects the plan: partial deliveries and counted stock are recorded here and applied to the next planning day." if en else "Fysisk virkelighet korrigerer planen: delleveranser og talt lager registreres her og brukes fra neste planleggingsdag."}</p></header>{receiving_block}</section>
<section class="panel" id="forecast"><header class="panel-head"><h2>{"Demand forecast" if en else "Etterspørselsprognose"}</h2><p>{"Expected quantities for today’s service" if en else "Forventede mengder for dagens service"}</p></header>
{f'<div class="driver-list">{driver_block}</div>' if driver_block else f'<p class="empty">{"No forecast drivers are available because the reserve model was used." if en else "Ingen prognosedrivere er tilgjengelige fordi reservemodellen ble brukt."}</p>'}{forecast_rows}</section>
<section class="panel" id="trim-loss"><header class="panel-head"><h2>{"Trim loss" if en else "Renseskjæringstap"}</h2><p>{"A recipe quantity is what reaches the plate. This is what must be bought to get there, and the difference is planned loss — not spoilage." if en else "En oppskriftsmengde er det som havner på tallerkenen. Dette er hva som må kjøpes inn for å komme dit, og differansen er planlagt tap — ikke svinn."}</p></header>{trim_block}</section>
<section class="panel"><header class="panel-head"><h2>{"Waste requiring attention" if en else "Svinn som krever kontroll"}</h2><p>{"Expired stock is excluded before consumption is calculated" if en else "Utgått lager er fjernet før forbruk beregnes"}</p></header>{waste_block}</section>
</div>

<details class="trace-details" id="traceability"><summary>{"Traceability and technical details" if en else "Sporbarhet og tekniske detaljer"}</summary><div class="trace-content">
{agent_difference_block}
<section class="agent-activity" aria-labelledby="agent-activity-title"><header class="panel-head"><h2 id="agent-activity-title">{"AI Operations Agent — completed 7 actions" if en else "AI-driftsagent — fullførte 7 handlinger"}</h2><p>{"Recorded orchestration for this published run" if en else "Registrert orkestrering for denne publiserte kjøringen"}</p></header><ol>{agent_activity_items}</ol></section>
<section class="panel"><header class="panel-head"><h2>{"Agent briefing record" if en else "Registrert agentbriefing"}</h2><p>{"Summary and warnings retained for audit" if en else "Sammendrag og varsler beholdt for revisjon"}</p></header>{briefing_trace_block}</section>
<section class="panel run-flow" id="agent-run" aria-labelledby="run-flow-title" data-technical="{escape(' | '.join((covers_source, forecast_source, str(forecast_note or '—'), planning_basis, briefing_source)))}"><header class="panel-head">
<h2 id="run-flow-title">{"Autonomous run" if en else "Autonom kjøring"}</h2><p>{"Trace from operational inputs to the published plan" if en else "Spor fra driftsdata til publisert plan"}</p></header><ol class="flow-list">{flow_items}</ol></section>
<div class="grid"><div class="stack">
{f'<section class="panel"><header class="panel-head"><h2>{"Plan history" if en else "Planhistorikk"}</h2><p>{"Read-only archived plans" if en else "Skrivebeskyttede arkiverte planer"}</p></header><div class="driver-list">{history_block}</div></section>' if history_block else ''}
<section class="panel"><header class="panel-head"><h2>{"Run integrity" if en else "Kjøringsintegritet"}</h2><p>{"Model paths and audit status" if en else "Modellbaner og revisjonsstatus"}</p></header>
<div class="source"><span>{"Forecast source" if en else "Prognosekilde"}</span><strong title="{escape(forecast_source)}">{escape(forecast_label)}</strong></div>
<div class="source"><span>{"Briefing source" if en else "Briefingkilde"}</span><strong title="{escape(briefing_source)}">{escape(briefing_label)}</strong></div>
<div class="source"><span>{"Inventory basis" if en else "Lagergrunnlag"}</span><strong title="{escape(str(plan.get('inventory_basis', 'seed_inventory')))}">{escape(_technical_label(str(plan.get('inventory_basis', 'seed_inventory')), language))}</strong></div>
<div class="source"><span>{"Diagnostic" if en else "Diagnostikk"}</span><strong title="{escape(str(forecast_note or '—'))}">{escape(_technical_label(str(forecast_note or '—'), language))}</strong></div></section>
{f'<section class="panel"><header class="panel-head"><h2>{"Action history" if en else "Handlingshistorikk"}</h2><p>{"Recorded operator decisions" if en else "Registrerte operatørbeslutninger"}</p></header><ul class="rows">{action_history_rows}</ul></section>' if action_history_rows else ''}
</div></div></div></details>
<footer class="footer">{"Operational plan with a complete audit trail" if en else "Operativ plan med fullstendig revisjonsspor"}</footer>
</main></body></html>"""
