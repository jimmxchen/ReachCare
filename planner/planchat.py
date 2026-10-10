"""Planner Q&A over a computed plan. Gemini explains; every number it may quote comes from `context()`."""
from __future__ import annotations

import json

from . import gemini, siting

_cache = {}
STAFF_OPTIONS = (2, 3, 4, 5, 6)


def _scenario(state, routes, params, label, **change):
    try:
        plan = siting.site_clinics(state, routes, {**params, **change})
    except ValueError as exc:
        return {"change": label, "result": f"no valid plan ({exc})"}
    s = plan["summary"]
    return {"change": label, "adults_newly_in_reach": s["adults_newly_served"], "share_of_need_reached_pct": round(s["share_of_need_addressed"] * 100),
            "clinic_days": len(plan["clinics"]), "sites": [c["name"] for c in plan["clinics"]],
            "estimated_cost": s["estimated_cost"], "total_service_capacity": s["total_capacity"]}


def context(state, routes, params):
    key = json.dumps(params, sort_keys=True)
    if key in _cache:
        return _cache[key]
    plan = siting.site_clinics(state, routes, params)
    p = plan["params"]
    scenarios = [_scenario(state, routes, p, "current plan")]
    for t in (15, 20, 30, 45, 60):
        if t != p["travel_target"]:
            scenarios.append(_scenario(state, routes, p, f"patient travel target {t} min", travel_target=t))
    for d in (1, 2, 3, 4, 5):
        if d != p["days"]:
            scenarios.append(_scenario(state, routes, p, f"{d} clinic day{'s' if d > 1 else ''}", days=d))
    for r in siting.RADII:
        if r != p["radius"]:
            scenarios.append(_scenario(state, routes, p, f"hospital driving radius {r} min", radius=r))
    for n in STAFF_OPTIONS:
        if n != p["staff_count"]:
            scenarios.append(_scenario(state, routes, p, f"{n} clinical staff", staff_count=n))
    scenarios.append(_scenario(state, routes, p, "ignore existing safety-net clinics", use_existing_care=not p["use_existing_care"]))
    out = {
        "hospital": plan["hospital"], "service": plan["service_name"], "health_indicator": plan["indicator_label"],
        "inputs": {k: p[k] for k in ("radius", "travel_target", "days", "staff_count", "clinical_hours", "services_per_staff_hour",
                                     "health_weight", "use_existing_care", "date_start", "date_end")},
        "summary": plan["summary"], "clinics": [{k: c[k] for k in ("label", "name", "address", "city", "county", "date", "new_adults",
                                                                   "capacity", "avg_indicator", "avg_no_vehicle", "stability")} for c in plan["clinics"]],
        "communities": plan["communities"], "what_if_scenarios": scenarios,
        "method": {"need_weight": "adults 18+ x (health_weight x health_index + (1 - health_weight) x transport_barrier_index)",
                   "selection": "greedy maximum coverage: each clinic day picks the host site adding the most need-weighted adults not yet covered",
                   "assumptions": plan["method"]["note"] + f" Cost per clinic day = staff x hours x ${plan['method']['staff_hourly_cost']} + ${plan['method']['event_fixed_cost']}."},
    }
    if len(_cache) > 40:
        _cache.clear()
    _cache[key] = out
    return out


SYSTEM = (
    "You are ReachCare's planning analyst, helping a hospital planner decide where to run free health clinics. "
    "Answer ONLY from the JSON plan data below. Quote numbers exactly as given; never invent figures, communities or sites. "
    "Explain why communities were prioritized using the need factors (health indicator rate, households without a vehicle, adult population) "
    "and show trade-offs between travel time, population coverage and resources (staff, days, cost) using the what_if_scenarios. "
    "'Reach' counts adults living near a site, not expected turnout; say so if relevant. Note that distances are straight-line approximations "
    "and indicators are modeled estimates when it matters. If the data cannot answer something, say what is missing and what the planner could change. "
    "Say adults are 'within reach' or 'newly in reach', never 'served'. Never use internal field names such as health_weight or need_weight; "
    "say 'health need' and 'transport barriers'. The health-versus-transport weighting is fixed at 70/30 and cannot be changed in the tool. "
    "Only suggest changing inputs the planner actually has: service, hospital driving radius, patient travel target, clinical staff, clinical hours, "
    "services per staff hour, number of clinic days, dates, and the existing-clinics option. "
    "Give no medical advice. Be concise: a short paragraph or a few bullets, plain language, no markdown headings. Answer only what was asked."
)


def answer(state, routes, params, messages):
    if not gemini.available():
        raise RuntimeError("Set GEMINI_API_KEY in .env to ask questions about this plan.")
    turns = [m for m in messages if isinstance(m, dict) and m.get("role") in ("user", "assistant") and str(m.get("text", "")).strip()][-10:]
    if not turns or turns[-1]["role"] != "user":
        raise ValueError("Ask a question about the plan.")
    convo = "\n".join(f"{'Planner' if m['role'] == 'user' else 'Analyst'}: {str(m['text'])[:800]}" for m in turns)
    data = json.dumps(context(state, routes, params), separators=(",", ":"))
    return gemini.generate(f"{SYSTEM}\n\nPLAN DATA:\n{data}\n\nCONVERSATION:\n{convo}\nAnalyst:").strip()
