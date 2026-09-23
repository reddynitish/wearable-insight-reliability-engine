"""FastAPI surface for the reliability engine.

Domain logic lives in engine/; this module only translates HTTP to it. No decision is
made here, so the API can be replaced without revisiting any threshold.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from engine import MODEL_VERSION, POLICY_VERSION, SCOPE_NOTE
from engine.corruptions import SEVERITIES, apply as apply_corruption, describe as describe_corruptions
from engine.engine import evaluate
from engine.policies import all_policies, get_policy, supported_claim_types
from engine.reasons import SPECS, ReasonCode, sort_key
from engine.schemas import DecisionResponse, EvaluationRequest
from engine.synth import GENERATORS, clean_case

app = FastAPI(
    title="Wearable Insight Reliability Engine",
    version=MODEL_VERSION,
    description=(
        "Decides whether the available wearable evidence responsibly supports a proposed "
        "claim. Returns SHOW, SHOW_WITH_WARNING, WAIT_FOR_MORE_DATA, or REJECT with a "
        "traceable evidence record. Not a medical device and not a diagnostic tool."
    ),
)


class Health(BaseModel):
    status: str
    model_version: str
    policy_version: str
    claim_types: list[str]
    scope: str


@app.get("/v1/health", response_model=Health, tags=["meta"])
def health() -> Health:
    return Health(
        status="ok",
        model_version=MODEL_VERSION,
        policy_version=POLICY_VERSION,
        claim_types=supported_claim_types(),
        scope=SCOPE_NOTE,
    )


@app.post("/v1/decisions", response_model=DecisionResponse, tags=["decisions"])
def create_decision(request: EvaluationRequest) -> DecisionResponse:
    """Evaluate one claim against one evidence bundle.

    An unsupported claim type returns 200 with a REJECT decision rather than a 4xx: the
    caller still needs a decision it can branch on, and "this claim is out of scope" is
    one of the four answers.
    """
    return evaluate(request)


@app.get("/v1/claim-types", tags=["meta"])
def claim_types() -> list[dict[str, Any]]:
    """The claim contracts, as the engine actually holds them."""
    out = []
    for policy in all_policies():
        out.append(
            {
                "claim_type": policy.claim_type,
                "mode": policy.mode,
                "description": policy.description,
                "required_signals": [s.value for s in policy.required_signals],
                "supporting_signals": [s.value for s in policy.supporting_signals],
                "max_decision": policy.max_decision.value,
                "requirements": {
                    "min_wear_coverage": policy.min_wear_coverage,
                    "min_overnight_coverage": policy.min_overnight_coverage,
                    "max_measurement_age_hours": policy.max_measurement_age_hours,
                    "max_sync_age_hours": policy.max_sync_age_hours,
                    "min_baseline_days": policy.min_baseline_days,
                    "warn_baseline_days": policy.warn_baseline_days,
                    "max_baseline_sd": policy.max_baseline_sd,
                    "contradict_z": policy.contradict_z,
                    "warn_z": policy.warn_z,
                    "show_z": policy.show_z,
                    "min_absolute_delta": policy.min_absolute_delta,
                },
                "thresholds": {
                    "reject_below": policy.thresholds.reject_below,
                    "warn_above": policy.thresholds.warn_above,
                    "show_above": policy.thresholds.show_above,
                },
                "known_confounders": list(policy.known_confounders),
                "permanent_limitations": [c.value for c in policy.permanent_limitations],
            }
        )
    return out


@app.get("/v1/reason-codes", tags=["meta"])
def reason_codes() -> list[dict[str, Any]]:
    return [
        {
            "code": code.value,
            "dimension": SPECS[code].dimension.value,
            "forces": SPECS[code].outcome.value,
            "decisiveness": SPECS[code].decisiveness,
            "retry_after": SPECS[code].retry_after,
            "required_evidence": list(SPECS[code].required_evidence),
        }
        for code in sorted(ReasonCode, key=sort_key)
    ]


@app.get("/v1/corruptions", tags=["demo"])
def corruptions() -> dict[str, Any]:
    return {
        "severities": list(SEVERITIES),
        "corruptions": [
            {"name": c.name, "dimension": c.dimension, "rationale": c.rationale,
             "degrades_evidence": c.degrades_evidence}
            for c in describe_corruptions()
        ],
    }


@app.get("/v1/demo/case", tags=["demo"])
def demo_case(
    claim_type: str = "RESTING_HEART_RATE_ELEVATED",
    seed: int = 0,
    supportable: bool = True,
    corruption: str | None = None,
    severity: str = "moderate",
) -> dict[str, Any]:
    """A synthetic case and its decision, optionally corrupted.

    This is the before/after demonstration surface: the same evidence, one documented
    failure injected, and the decision that follows. All data is synthetic; no personal
    health data is ever served from this endpoint.
    """
    if claim_type not in GENERATORS:
        return {"error": f"unknown claim_type; have {sorted(GENERATORS)}"}
    case = clean_case(claim_type, seed, supportable=supportable)
    if corruption:
        try:
            case = apply_corruption(case, corruption, severity, seed=seed)  # type: ignore[arg-type]
        except (KeyError, ValueError) as exc:
            return {"error": str(exc)}
    response = evaluate(case.request)
    return {
        "synthetic": True,
        "case": case.manifest_row(),
        "request": case.request.model_dump(mode="json"),
        "decision": response.model_dump(mode="json"),
    }


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> str:
    types = "".join(f"<option>{t}</option>" for t in supported_claim_types())
    corruption_opts = "".join(
        f"<option value='{c.name}'>{c.name} ({c.dimension})</option>"
        for c in describe_corruptions()
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Wearable Insight Reliability Engine</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 :root {{ --bg:#fff; --fg:#141414; --muted:#666; --line:#e2e2e2; --card:#fafafa;
          --show:#0a7d35; --warn:#9a6a00; --wait:#1f5fa9; --reject:#a52020; }}
 @media (prefers-color-scheme: dark) {{
   :root {{ --bg:#141414; --fg:#ececec; --muted:#9a9a9a; --line:#2e2e2e; --card:#1d1d1d;
            --show:#5ad68b; --warn:#e0b04a; --wait:#7db4f0; --reject:#f08a8a; }} }}
 body {{ background:var(--bg); color:var(--fg); font:15px/1.55 -apple-system,
        BlinkMacSystemFont,"Segoe UI",sans-serif; margin:0; padding:24px 16px; }}
 main {{ max-width:860px; margin:0 auto; }}
 h1 {{ font-size:1.5rem; margin:0 0 .25rem; }}
 p.sub {{ color:var(--muted); margin:0 0 1.5rem; }}
 fieldset {{ border:1px solid var(--line); border-radius:8px; padding:12px 14px;
             margin:0 0 16px; }}
 label {{ display:inline-block; margin-right:14px; font-size:.9rem; }}
 select, button {{ font:inherit; padding:5px 8px; border:1px solid var(--line);
                   border-radius:6px; background:var(--card); color:var(--fg); }}
 button {{ cursor:pointer; font-weight:600; }}
 .decision {{ font-size:1.2rem; font-weight:700; }}
 .SHOW {{ color:var(--show); }} .SHOW_WITH_WARNING {{ color:var(--warn); }}
 .WAIT_FOR_MORE_DATA {{ color:var(--wait); }} .REJECT {{ color:var(--reject); }}
 .card {{ background:var(--card); border:1px solid var(--line); border-radius:8px;
          padding:14px; margin-bottom:14px; }}
 table {{ border-collapse:collapse; width:100%; font-size:.88rem; }}
 td,th {{ text-align:left; padding:4px 8px; border-bottom:1px solid var(--line); }}
 code {{ font-size:.85rem; }}
 pre {{ overflow:auto; font-size:.78rem; background:var(--bg); padding:10px;
        border:1px solid var(--line); border-radius:6px; }}
 .note {{ color:var(--muted); font-size:.82rem; }}
</style></head><body><main>
<h1>Wearable Insight Reliability Engine</h1>
<p class="sub">Pick a claim, inject a documented data failure, and watch the decision
change. All evidence here is synthetic.</p>
<fieldset>
 <label>claim <select id="claim">{types}</select></label>
 <label>failure <select id="corruption"><option value="">none</option>{corruption_opts}</select></label>
 <label>severity <select id="severity"><option>mild</option><option selected>moderate</option><option>severe</option></select></label>
 <label>subject <select id="seed"><option>0</option><option>1</option><option>2</option></select></label>
 <label><input type="checkbox" id="supportable" checked> effect really present</label>
 <button id="go">Evaluate</button>
</fieldset>
<div id="out"></div>
<p class="note">Not a medical device. This service reports whether measurements support a
statement, never whether a person has a condition.</p>
<script>
const $ = id => document.getElementById(id);
async function run() {{
  const p = new URLSearchParams({{
    claim_type: $('claim').value, corruption: $('corruption').value,
    severity: $('severity').value, seed: $('seed').value,
    supportable: $('supportable').checked }});
  const r = await fetch('/v1/demo/case?' + p);
  const d = await r.json();
  if (d.error) {{ $('out').innerHTML = '<div class="card">' + d.error + '</div>'; return; }}
  const dec = d.decision, ev = dec.evidence;
  const rows = Object.entries(ev).map(([k, v]) =>
    `<tr><td><code>${{k}}</code></td><td>${{v.toFixed(3)}}</td></tr>`).join('');
  const reasons = dec.reason_codes.length
    ? dec.reason_codes.map(c => `<code>${{c}}</code>`).join(' ') : '<span class="note">none</span>';
  $('out').innerHTML = `
   <div class="card">
     <div class="decision ${{dec.decision}}">${{dec.decision}}</div>
     <p>${{dec.explanation}}</p>
     <p class="note">decision confidence ${{dec.confidence.toFixed(2)}} &middot;
        claim support ${{dec.claim_support_probability.toFixed(2)}}
        (not calibrated) &middot; ground truth
        <code>${{d.case.truth}}</code></p>
     <p>reason codes: ${{reasons}}</p>
   </div>
   <div class="card"><table><tr><th>evidence dimension</th><th>score</th></tr>${{rows}}</table>
     <p class="note">0.5 means exactly at this claim's minimum acceptable level.</p></div>
   <div class="card"><details><summary>decision trace</summary>
     <pre>${{JSON.stringify(dec.trace, null, 2)}}</pre></details></div>`;
}}
$('go').onclick = run; run();
</script></main></body></html>"""
