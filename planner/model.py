from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
from datetime import date, timedelta

import numpy as np

MODEL_VERSION = "1.0.0"
SERVICES = {
    "blood-pressure": {
        "name": "Blood-pressure screening", "indicator": "bphigh",
        "indicator_label": "Adults reporting diagnosed high blood pressure",
        "eligible_group": "Adults 18 and older", "benchmark": [15, 55],
        "minimum_staff": 2, "equipment": "Blood-pressure monitors",
        "interpretation": "Modeled diagnosed prevalence is a geographic priority proxy; it does not estimate undiagnosed patients or screening demand.",
        "short_label": "High blood pressure",
    },
    "diabetes": {
        "name": "Diabetes screening", "indicator": "diabetes",
        "indicator_label": "Adults reporting diagnosed diabetes",
        "eligible_group": "Adults 18 and older", "benchmark": [4, 24],
        "minimum_staff": 2, "equipment": "Glucose meters or A1C analyzers",
        "interpretation": "Modeled diagnosed prevalence is a geographic priority proxy; it does not estimate undiagnosed patients or screening demand.",
        "short_label": "Diabetes",
    },
    "copd": {
        "name": "COPD and lung-health screening", "indicator": "copd",
        "indicator_label": "Adults reporting diagnosed COPD",
        "eligible_group": "Adults 18 and older", "benchmark": [2, 15],
        "minimum_staff": 2, "equipment": "Handheld spirometers",
        "interpretation": "Modeled diagnosed prevalence is a geographic priority proxy; it does not estimate undiagnosed patients or screening demand.",
        "short_label": "COPD",
    },
    "asthma": {
        "name": "Asthma education and support", "indicator": "casthma",
        "indicator_label": "Adults reporting current asthma",
        "eligible_group": "Adults 18 and older", "benchmark": [8.5, 14.5],
        "minimum_staff": 2, "equipment": "Peak-flow meters and inhaler demonstration kits",
        "interpretation": "Modeled current-asthma prevalence is a geographic priority proxy; it does not estimate care need or demand.",
        "short_label": "Asthma",
    },
}


def default_config(today=None):
    today = today or date.today()
    return {"service": "blood-pressure", "region": "", "travel_target": 30,
            "events": 1, "budget": 5000, "date_start": (today + timedelta(days=7)).isoformat(),
            "date_end": (today + timedelta(days=35)).isoformat(), "staff_count": 3,
            "services_per_staff_hour": 5, "clinical_hours": 4, "equipment_limit": 120,
            "equipment_available": True, "vehicle_available": True, "health_weight": .7,
            "weight_min": .2, "weight_max": .8, "concentration": 60,
            "seed": 2026, "simulations": 1000, "top_n": 3, "robust_threshold": .8,
            "travel_variation": .1, "trend_strength": 0, "mode": "demo",
            "weather_block_events": [],
            "excluded_services": [], "venue_overrides": {}}


def validate_config(config, counties):
    merged = default_config()
    merged.update(config)
    if merged["service"] not in SERVICES:
        raise ValueError("Select a configured service before running a plan.")
    if merged["region"] != "US" and merged["region"] not in counties and merged["region"] not in STATE_CODES:
        raise ValueError("Region must be the United States, a state or district, or a county FIPS ID.")
    numeric = {"travel_target": (5, 120), "events": (1, 5), "budget": (0, 1000000),
               "staff_count": (0, 100), "services_per_staff_hour": (.1, 100),
               "clinical_hours": (.5, 12), "equipment_limit": (0, 10000),
               "health_weight": (0, 1), "weight_min": (0, 1), "weight_max": (0, 1),
               "concentration": (1, 1000), "seed": (0, 2 ** 32 - 1),
               "simulations": (1000, 10000), "top_n": (1, 30), "robust_threshold": (0, 1),
               "travel_variation": (0, .3), "trend_strength": (0, .2)}
    for key, (lower, upper) in numeric.items():
        value = merged[key]
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not lower <= value <= upper:
            raise ValueError(f"{key.replace('_', ' ')} must be between {lower} and {upper}.")
    for key in ["events", "staff_count", "seed", "simulations", "top_n"]:
        if int(merged[key]) != merged[key]:
            raise ValueError(f"{key} must be an integer.")
        merged[key] = int(merged[key])
    if not merged["weight_min"] <= merged["health_weight"] <= merged["weight_max"] or merged["weight_min"] >= merged["weight_max"]:
        raise ValueError("Health weight must lie inside the approved weight bounds.")
    # Both complementary weights must satisfy these same approved bounds.
    if not merged["weight_min"] <= 1 - merged["health_weight"] <= merged["weight_max"]:
        raise ValueError("Transport weight must also lie inside the approved weight bounds.")
    for key in ["equipment_available", "vehicle_available"]:
        if not isinstance(merged[key], bool):
            raise ValueError(f"{key} must be true or false.")
    try:
        start, end = date.fromisoformat(merged["date_start"]), date.fromisoformat(merged["date_end"])
    except (TypeError, ValueError):
        raise ValueError("Enter valid start and end dates.") from None
    if end < start or (end - start).days > 180:
        raise ValueError("The date window must be ordered and at most 180 days.")
    if merged["mode"] not in ["demo", "confirmed"]:
        raise ValueError("Select demo or confirmed operations.")
    if not isinstance(merged["venue_overrides"], dict) or not isinstance(merged["excluded_services"], list):
        raise ValueError("Invalid venue or existing-service updates.")
    for venue_id, operation in merged["venue_overrides"].items():
        if not isinstance(operation, dict):
            raise ValueError("Venue updates must contain structured operational fields.")
        for key in ["details_verified", "host_approved", "accessible", "private_space", "power", "available"]:
            if key in operation and operation[key] is not None and not isinstance(operation[key], bool):
                raise ValueError(f"{key} must be confirmed, unavailable, or unknown.")
        for key in ["cost", "space_throughput"]:
            if key in operation and (isinstance(operation[key], bool) or not isinstance(operation[key], (int, float)) or not math.isfinite(operation[key]) or operation[key] < 0):
                raise ValueError(f"Venue {key} must be a nonnegative number.")
        for day in operation.get("available_dates", []):
            date.fromisoformat(day)
    if not isinstance(merged["weather_block_events"], list) or any(event not in ["Tornado Warning", "Flash Flood Warning", "Severe Thunderstorm Warning"] for event in merged["weather_block_events"]):
        raise ValueError("Select supported weather event types for the operational policy.")
    return merged


STATE_CODES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())


def travel_minutes_matrix(tracts, sites):
    """Approximate travel minutes from coordinates using the documented straight-line factor."""
    if not tracts or not sites:
        return np.empty((len(tracts), len(sites)))
    lat1 = np.radians(np.array([t["lat"] for t in tracts], dtype=float))[:, None]
    lon1 = np.radians(np.array([t["lon"] for t in tracts], dtype=float))[:, None]
    lat2 = np.radians(np.array([s["lat"] for s in sites], dtype=float))[None, :]
    lon2 = np.radians(np.array([s["lon"] for s in sites], dtype=float))[None, :]
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    miles = 7917.6 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    return miles / .58


def demo_operations(venues):
    operations = {}
    for index, venue in enumerate(venues):
        operations[venue["id"]] = {
            "details_verified": index % 9 != 8, "host_approved": index % 9 != 8,
            "accessible": True, "private_space": True, "power": index % 13 != 12,
            "available": index % 11 != 10, "cost": 600 + (index % 7) * 125,
            "space_throughput": 48 + (index % 5) * 12, "available_dates": [],
            "verified_by": "Sample operations for demonstration", "verified_at": None,
            "sample": True,
        }
    return operations


def feasibility(venue, op, config):
    reasons = []
    for key, label in [("details_verified", "Venue details"), ("host_approved", "Host approval"),
                       ("accessible", "Accessible entrance"), ("private_space", "Private clinical space"),
                       ("power", "Power"), ("available", "Venue availability")]:
        if op.get(key) is not True:
            reasons.append(f"{label} {'not confirmed' if op.get(key) is None else 'not available'}")
    if config["mode"] == "confirmed" and (op.get("sample") or not op.get("verified_at") or not op.get("verified_by")):
        reasons.append("Real host/operations verification is required")
    if config["staff_count"] < SERVICES[config["service"]]["minimum_staff"]:
        reasons.append("At least two clinical staff are required for this campaign policy")
    if not config["vehicle_available"]:
        reasons.append("Mobile vehicle unavailable")
    if not config["equipment_available"]:
        reasons.append("Required screening equipment unavailable")
    cost = op.get("cost")
    if cost is None:
        reasons.append("Event cost not confirmed")
    elif cost > config["budget"]:
        reasons.append("Event exceeds the total budget")
    space = op.get("space_throughput")
    if space is None:
        reasons.append("Clinical station throughput not confirmed")
    throughput = math.floor(config["clinical_hours"] * config["staff_count"] * config["services_per_staff_hour"])
    capacity = min(throughput, config["equipment_limit"], space or 0)
    if capacity <= 0:
        reasons.append("No deliverable service capacity")
    days = op.get("available_dates", [])
    if not days and op.get("sample"):
        start, end = date.fromisoformat(config["date_start"]), date.fromisoformat(config["date_end"])
        days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    blocked_days = op.get("weather_block_dates", [])
    days = sorted({d for d in days if config["date_start"] <= d <= config["date_end"] and d not in blocked_days})
    if blocked_days and not days:
        reasons.append("Available dates blocked by the declared, matched weather policy")
    if not days:
        reasons.append("No confirmed available date in the campaign window")
    return reasons, capacity, days


def normalize(values, bounds):
    return np.clip((values - bounds[0]) / (bounds[1] - bounds[0]), 0, 1)


def baselines(matrix, relevant_columns):
    if not relevant_columns:
        return np.full(matrix.shape[0], np.nan), np.full(matrix.shape[0], -1)
    times = matrix[:, relevant_columns]
    safe = np.where(np.isfinite(times), times, np.inf)
    closest = np.argmin(safe, axis=1)
    best = safe[np.arange(len(safe)), closest]
    ids = np.array(relevant_columns)[closest]
    ids[~np.isfinite(best)] = -1
    best[~np.isfinite(best)] = np.nan
    return best, ids


def coverage_matrix(baseline, times, threshold):
    return (np.isfinite(baseline) & (baseline > threshold))[:, None] & np.isfinite(times) & (times <= threshold)


def sample_weights(rng, config, draws, concentration):
    approved = np.array([config["health_weight"], 1 - config["health_weight"]])
    if np.any(approved == 0):
        return np.tile(approved, (draws, 1))
    accepted = []
    count = 0
    for _ in range(500):
        batch = rng.dirichlet(concentration * approved, max(200, 2 * (draws - count)))
        batch = batch[np.all((batch >= config["weight_min"]) & (batch <= config["weight_max"]), axis=1)]
        accepted.append(batch)
        count += len(batch)
        if count >= draws:
            return np.concatenate(accepted)[:draws]
    raise ValueError("Approved weight bounds are too narrow to sample. Widen the bounds.")


def simulate(config, tracts, health, barrier, eligible, baseline, times, costs, multipliers, concentration=None, draws=None):
    """Seeded policy sensitivity with shared county shocks and estimated travel times."""
    draws = draws or config["simulations"]
    concentration = concentration or config["concentration"]
    rng = np.random.default_rng(config["seed"])
    weights = sample_weights(rng, config, draws, concentration)
    indicator = SERVICES[config["service"]]["indicator"]
    bounds = SERVICES[config["service"]]["benchmark"]
    health_sd = np.array([(t["indicators"][indicator]["upper"] - t["indicators"][indicator]["lower"]) / 3.92 if t["indicators"][indicator].get("upper") is not None and t["indicators"][indicator].get("lower") is not None else 0 for t in tracts])
    barrier_sd = np.array([(t.get("no_vehicle_moe") or 0) / 1.645 for t in tracts])
    population_sd = np.array([(t.get("population_moe") or 0) / 1.645 for t in tracts])
    county_values = sorted({t["county_id"] for t in tracts})
    county_index = np.array([county_values.index(t["county_id"]) for t in tracts])
    scores = np.empty((draws, times.shape[1]))
    # 21 discrete route factors: a documented sensitivity grid, not an inferred traffic forecast.
    route_factors = np.linspace(1 - config["travel_variation"], 1 + config["travel_variation"], 21)
    for start in range(0, draws, 100):
        length = min(100, draws - start)
        shared = rng.normal(size=(length, len(county_values)))[:, county_index]
        h_noise = .5 * shared + math.sqrt(.75) * rng.normal(size=(length, len(tracts)))
        b_noise = .5 * shared + math.sqrt(.75) * rng.normal(size=(length, len(tracts)))
        h = normalize(np.clip(health + h_noise * health_sd, 0, 100), bounds)
        b = normalize(np.clip(barrier + b_noise * barrier_sd, 0, 100), [0, 20])
        e = np.maximum(0, eligible + rng.normal(size=(length, len(tracts))) * population_sd)
        e[:, ~np.array([t["eligible_population"] is not None and t["no_vehicle_pct"] is not None and t["indicators"][indicator]["value"] is not None for t in tracts])] = 0
        w = weights[start:start + length]
        base_weight = e * (h * w[:, :1] + b * w[:, 1:])
        # Optional service-matched trends vary by bounded strength, and never affect unrelated services.
        trend_fraction = rng.uniform(0, 1, (length, 1)) if config["trend_strength"] else np.ones((length, 1))
        value = base_weight * (1 + (multipliers - 1) * trend_fraction)
        factor_indices = rng.integers(0, len(route_factors), length)
        block = np.empty((length, times.shape[1]))
        for factor_index in np.unique(factor_indices):
            selected = np.flatnonzero(factor_indices == factor_index)
            factor = route_factors[factor_index]
            coverage = coverage_matrix(baseline * factor, times * factor, config["travel_target"])
            block[selected] = value[selected] @ coverage.astype(float)
        scores[start:start + length] = block
    # Cost and confirmed hard constraints stay fixed: no simulation can approve a venue.
    order = np.empty(scores.shape, dtype=int)
    for index, score in enumerate(scores):
        order[index] = np.lexsort((np.arange(len(costs)), costs, -score))
    ranks = np.empty_like(order)
    np.put_along_axis(ranks, order, np.broadcast_to(np.arange(1, scores.shape[1] + 1), order.shape), axis=1)
    regret = scores.max(axis=1, keepdims=True) - scores
    return {"top_n_frequency": np.mean(ranks <= config["top_n"], axis=0),
            "first_frequency": np.mean(ranks == 1, axis=0), "median_rank": np.median(ranks, axis=0),
            "average_regret": np.mean(regret, axis=0), "scores": scores}


def trend_multipliers(tracts, config, observations):
    multipliers = np.ones(len(tracts))
    details = []
    if not config["trend_strength"]:
        return multipliers, details
    relevant = [s for s in observations if s.get("service") == config["service"] and s.get("valid") and not s.get("stale") and not s.get("suppressed")]
    for index, tract in enumerate(tracts):
        matches = [s for s in relevant if (s["geography_type"] == "county" and s["geography_id"] == tract["county_id"]) or (s["geography_type"] == "tract" and s["geography_id"] == tract["id"])]
        if not matches:
            continue
        observation = matches[-1]
        trend = (observation["recent_rate"] - observation["baseline_rate"]) / max(observation["baseline_rate"], .000001)
        multipliers[index] = np.clip(1 + config["trend_strength"] * np.clip(trend, -1, 1), .8, 1.2)
        if observation not in details:
            details.append(observation)
    return multipliers, details


def greedy_schedule(coverage, weights, candidates, config, capacity_by_id):
    covered = np.zeros(coverage.shape[0], dtype=bool)
    remaining_budget = config["budget"]
    selected, used_dates, remaining = [], set(), list(range(len(candidates)))
    for _ in range(config["events"]):
        choices = []
        for index in remaining:
            candidate = candidates[index]
            available = [d for d in candidate["available_dates"] if d not in used_dates]
            if candidate["cost"] <= remaining_budget and available:
                gain = float(weights @ (coverage[:, index] & ~covered))
                choices.append((-gain, candidate["cost"], candidate["id"], index, available[0]))
        if not choices:
            break
        neg_gain, _, _, index, selected_date = min(choices)
        if -neg_gain <= 0:
            break
        candidate = candidates[index]
        selected.append({"site_id": candidate["id"], "name": candidate["name"], "date": selected_date, "incremental_score": round(-neg_gain, 6), "event_capacity": capacity_by_id[candidate["id"]], "cost": candidate["cost"]})
        covered |= coverage[:, index]
        remaining_budget -= candidate["cost"]
        used_dates.add(selected_date)
        remaining.remove(index)
    return selected, covered


def run_plan(state, routes, raw_config, operations=None, observations=None, weather_blocks=None):
    config = validate_config(raw_config, state["counties"])
    service = SERVICES[config["service"]]
    indicator = service["indicator"]
    region = config["region"]
    whole_region = region == "US" or region == state.get("state_code")
    tracts = [t for t in state["tracts"] if whole_region or t["county_id"] == region]
    tract_indices = [i for i, t in enumerate(state["tracts"]) if whole_region or t["county_id"] == region]
    matrix = routes[np.array(tract_indices)] if routes is not None else None
    venue_count = len(state["venues"])
    existing = copy.deepcopy(state["existing_services"])
    for site in existing:
        if config["mode"] == "demo":
            site.update(service_verified=True, eligibility_verified=True, schedule_verified=True, service=config["service"], sample=True)
    relevant = [i for i, s in enumerate(existing) if s["id"] not in config["excluded_services"] and s.get("service", config["service"]) == config["service"] and all(s.get(k) is True for k in ["service_verified", "eligibility_verified", "schedule_verified"]) and (config["mode"] == "demo" or (s.get("verified_at") and not s.get("sample") and s.get("schedule_start", "9999") <= config["date_start"] and s.get("schedule_end", "0000") >= config["date_end"]))]
    if matrix is not None:
        baseline, baseline_ids = baselines(matrix, [venue_count + i for i in relevant])
    else:
        baseline = np.full(len(tracts), np.nan)
        baseline_ids = np.full(len(tracts), -1)
        care = [(i, existing[i]) for i in relevant if existing[i].get("lat") is not None and existing[i].get("lon") is not None]
        for start in range(0, len(care), 128):
            block = care[start:start + 128]
            distances = travel_minutes_matrix(tracts, [site for _, site in block])
            nearest = np.argmin(distances, axis=1)
            values = distances[np.arange(len(tracts)), nearest]
            update = ~np.isfinite(baseline) | (values < baseline)
            baseline[update] = values[update]
            site_indices = np.array([venue_count + index for index, _ in block])
            baseline_ids[update] = site_indices[nearest[update]]
    ops = demo_operations(state["venues"]) if config["mode"] == "demo" else copy.deepcopy(operations or {})
    for venue_id, overrides in config["venue_overrides"].items():
        ops.setdefault(venue_id, {}).update(overrides)
    for venue_id, days in (weather_blocks or {}).items():
        ops.setdefault(venue_id, {})["weather_block_dates"] = days
    excluded, feasible, indices, capacities = [], [], [], {}
    for index, venue in enumerate(state["venues"]):
        op = ops.get(venue["id"], {})
        reasons, capacity, days = feasibility(venue, op, config)
        if reasons:
            excluded.append({"id": venue["id"], "name": venue["name"], "county": venue["county"], "reasons": reasons, "verification": "prospect"})
        else:
            feasible.append({**venue, "cost": op["cost"], "available_dates": days, "operation": op})
            indices.append(index)
            capacities[venue["id"]] = capacity
    health_raw = np.array([t["indicators"][indicator]["value"] if t["indicators"][indicator]["value"] is not None else np.nan for t in tracts])
    barrier_raw = np.array([t["no_vehicle_pct"] if t["no_vehicle_pct"] is not None else np.nan for t in tracts])
    eligible = np.array([t["eligible_population"] if t["eligible_population"] is not None else np.nan for t in tracts], dtype=float)
    valid = np.isfinite(health_raw) & np.isfinite(barrier_raw) & np.isfinite(eligible)
    clipped = np.isfinite(health_raw) & ((health_raw < service["benchmark"][0]) | (health_raw > service["benchmark"][1])) | np.isfinite(barrier_raw) & ((barrier_raw < 0) | (barrier_raw > 20))
    health, barrier, population = np.nan_to_num(health_raw), np.nan_to_num(barrier_raw), np.where(valid, eligible, 0)
    multipliers, trends = trend_multipliers(tracts, config, observations or [])
    h_component = population * normalize(health, service["benchmark"]) * config["health_weight"]
    b_component = population * normalize(barrier, [0, 20]) * (1 - config["health_weight"])
    weights = (h_component + b_component) * multipliers
    times = matrix[:, indices] if matrix is not None else travel_minutes_matrix(tracts, feasible)
    coverage = coverage_matrix(baseline, times, config["travel_target"]) & valid[:, None]
    costs = np.array([v["cost"] for v in feasible])
    result = {
        "model_version": MODEL_VERSION, "config": config, "service": service,
        "scope": {"region": "United States" if region == "US" else state.get("state_name", region) if region == state.get("state_code") else state["counties"][region], "tracts": len(tracts), "counties": len({t["county_id"] for t in tracts}), "eligible_population": int(population.sum()), "scorable_tracts": int(valid.sum()), "unknown_baseline_tracts": int(np.isnan(baseline).sum()), "unknown_route_cells": 0 if matrix is None else int(np.isnan(times).sum())},
        "excluded": excluded, "candidates": len(feasible), "recommendations": [], "schedule": [],
        "selected_tracts": [], "trends": trends, "sources": state["sources"],
        "candidate_selection": state["candidate_selection"], "expected_attendance": None,
        "data_quality": state.get("quality_issues", []) + [{"tract_id": tracts[i]["id"], "issue": "Required indicator missing; excluded from score"} for i in np.flatnonzero(~valid)] + [{"tract_id": tracts[i]["id"], "issue": "Indicator outside fixed benchmark bounds; normalized value clipped"} for i in np.flatnonzero(clipped)],
        "warnings": ["Geographic reach, clinical capacity, and turnout are different measures.", "Travel times are coordinate-based estimates, not turn-by-turn directions or live traffic.", "No matching history: turnout is unavailable.", "Benchmark bounds are fixed policy choices: high blood pressure 15–55%; no-vehicle households 0–20%."]}
    if config["mode"] == "demo":
        result["warnings"].insert(0, "DEMO: venue approvals, operating dates, staff rates, costs, and existing-service verification are sample inputs. These are not approved events.")
    if (~valid).any():
        result["warnings"].append(f"{int((~valid).sum())} tracts lack a required indicator and are excluded from the score; missing values are not treated as zero need.")
    if len(feasible) <= config["top_n"]:
        result["warnings"].append("Top-N frequency cannot discriminate when all candidates fit inside N; compare first-place frequency and regret.")
    tract_details = []
    all_sites = state["venues"] + existing
    for i, tract in enumerate(tracts):
        tract_details.append({"id": tract["id"], "county": tract["county"], "eligible_population": tract["eligible_population"], "health_pct": tract["indicators"][indicator]["value"], "no_vehicle_pct": tract["no_vehicle_pct"], "priority": round(float(weights[i] / population[i]), 5) if population[i] else None, "baseline_minutes": round(float(baseline[i]), 2) if np.isfinite(baseline[i]) else None, "baseline_site": all_sites[baseline_ids[i]]["id"] if baseline_ids[i] >= 0 else None, "baseline_name": all_sites[baseline_ids[i]]["name"] if baseline_ids[i] >= 0 else None, "scorable": bool(valid[i])})
    result["tract_details"] = tract_details
    if not feasible:
        result["decision"] = "No feasible plans"
        result["simulation_design"] = {"seed": config["seed"], "draws": 0, "reason": "Feasibility checks excluded every candidate"}
        return result
    scores = weights @ coverage
    order = sorted(range(len(feasible)), key=lambda j: (-scores[j], costs[j], feasible[j]["id"]))
    # Simulation columns share the venue-ID tie-break used in deterministic rankings.
    stable = sorted(range(len(feasible)), key=lambda j: feasible[j]["id"])
    sim = simulate(config, tracts, health, barrier, population, baseline, times[:, stable], costs[stable], multipliers)
    broad = simulate(config, tracts, health, barrier, population, baseline, times[:, stable], costs[stable], multipliers, concentration=max(1, config["concentration"] / 3))
    sim_index = {original: position for position, original in enumerate(stable)}
    stress_coverages = [coverage_matrix(baseline * f, times * f, config["travel_target"]) & valid[:, None] for f in [1 - config["travel_variation"], 1 + config["travel_variation"]]]
    stress_scores = np.array([weights @ cov for cov in stress_coverages])
    stress_regret = stress_scores.max(axis=1, keepdims=True) - stress_scores
    for rank, j in enumerate(order, 1):
        venue = feasible[j]
        k = sim_index[j]
        newly = np.flatnonzero(coverage[:, j])
        h_score = float(h_component @ coverage[:, j])
        b_score = float(b_component @ coverage[:, j])
        top_frequency = float(sim["top_n_frequency"][k])
        result["recommendations"].append({
            "site": {key: venue[key] for key in ["id", "name", "county", "address", "city", "lat", "lon", "source"]},
            "date_window": [config["date_start"], config["date_end"]], "proposed_date": venue["available_dates"][0],
            "service": config["service"], "rank": rank, "score": round(float(scores[j]), 6),
            "score_components": {"health": round(h_score, 6), "transport": round(b_score, 6), "trend": round(float(scores[j]) - h_score - b_score, 6)},
            "newly_covered_tracts": [tracts[i]["id"] for i in newly],
            "approx_population_in_newly_covered_tracts": int(population[newly].sum()),
            "event_capacity": capacities[venue["id"]], "clinical_throughput": math.floor(config["clinical_hours"] * config["staff_count"] * config["services_per_staff_hour"]),
            "space_throughput": venue["operation"]["space_throughput"], "equipment_throughput": config["equipment_limit"],
            "estimated_cost": venue["cost"], "required_staff": config["staff_count"], "required_equipment": service["equipment"],
            "top_n_frequency": round(top_frequency, 6), "first_frequency": round(float(sim["first_frequency"][k]), 6),
            "median_rank": float(sim["median_rank"][k]), "average_regret": round(float(sim["average_regret"][k]), 6),
            "worst_case_regret": round(float(stress_regret[:, j].max()), 6),
            "broader_top_n_frequency": round(float(broad["top_n_frequency"][k]), 6),
            "robust": bool(scores[j] > 0 and top_frequency >= config["robust_threshold"] and len(feasible) > config["top_n"]),
            "feasibility": "Demo feasible" if config["mode"] == "demo" else "Planner verified; event approval pending",
            "open_checks": ["Hospital approval before scheduling", "Host confirmation before scheduling"] + (["Replace sample operational inputs and provider verification"] if config["mode"] == "demo" else []),
            "verification": venue["operation"], "expected_attendance": None,
        })
    schedule, selected = greedy_schedule(coverage, weights, feasible, config, capacities)
    result["schedule"] = schedule
    result["selected_tracts"] = [tracts[i]["id"] for i in np.flatnonzero(selected)]
    result["schedule_score"] = round(float(weights @ selected), 6)
    result["schedule_population"] = int(population[selected].sum())
    result["schedule_cost"] = sum(s["cost"] for s in schedule)
    result["schedule_capacity"] = sum(s["event_capacity"] for s in schedule)
    result["optimizer"] = "Greedy unique weighted coverage; one mobile unit and one event per date; total-budget constraint"
    result["decision"] = "Robust alternatives" if any(r["robust"] for r in result["recommendations"]) else "Inconclusive"
    if float(scores.max()) <= 0:
        result["decision"] = "No confirmed access gain"
    result["simulation_design"] = {
        "seed": config["seed"], "draws": config["simulations"], "broader_draws": config["simulations"],
        "weight_distribution": "Dirichlet(concentration × policy weights), rejected outside approved bounds",
        "concentration": config["concentration"], "broader_concentration": max(1, config["concentration"] / 3),
        "bounds": [config["weight_min"], config["weight_max"]], "top_n": config["top_n"], "threshold": config["robust_threshold"],
        "measured_uncertainty": "Bounded normal: CDC interval width / 3.92; ACS 90% MOE / 1.645. Shared county component with loading 0.5. Approximate sensitivity distributions, not CDC posteriors.",
        "population_uncertainty": "Adults: total less under-18 groups; MOE uses root-sum-square approximation; covariance unavailable.",
        "travel_distribution": f"21 equally likely shared route factors from {1 - config['travel_variation']:.2f} to {1 + config['travel_variation']:.2f}",
        "cost_policy": "Confirmed costs and all hard feasibility checks fixed; costs are budget constraints, not a score penalty",
        "stress_scenarios": ["All driving times at lower declared bound", "All driving times at upper declared bound"],
        "interpretation": "Stability under stated scenarios; not probability of turnout or objective optimality",
    }
    return result


def input_hash(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
