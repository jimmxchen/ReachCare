"""Hospital-centred free-clinic siting.

Weighted maximum-coverage selection over need-weighted census tracts. Parameters are
chosen by the planner, not learned, so the whole recommendation is reproducible.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np

from .model import SERVICES

# Straight-line miles per driving minute. Roughly 35 mph of effective straight-line
# progress once road circuity and rural speeds are folded in.
MILES_PER_MINUTE = 0.58
EARTH_MILES = 3958.8
RADII = (15, 30, 45, 60)
WEIGHT_GRID = (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)
MAX_CLINICS = 10
# Cost assumptions for one clinic day: clinical labor plus supplies, space and outreach.
STAFF_HOURLY_COST = 55
EVENT_FIXED_COST = 400


def _haversine(lat, lon, lats, lons):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat, lon, lats, lons))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_MILES * np.arcsin(np.sqrt(a))


def validate(params):
    p = {"service": "blood-pressure", "hospital_name": "Hospital", "radius": 30, "travel_target": 30,
         "days": 3, "staff_count": 3, "clinical_hours": 4, "services_per_staff_hour": 5,
         "health_weight": .7, "use_existing_care": True}
    p.update({k: v for k, v in params.items() if v is not None})
    if p["service"] not in SERVICES:
        raise ValueError("Select a supported service.")
    for key in ("lat", "lon"):
        if not isinstance(p.get(key), (int, float)) or isinstance(p.get(key), bool) or not math.isfinite(p[key]):
            raise ValueError("Choose the hospital location on the map or by county.")
    if not (33.5 <= p["lat"] <= 37.0 and -84.8 <= p["lon"] <= -75.0):
        raise ValueError("The hospital location must be inside North Carolina.")
    ranges = {"radius": (15, 60), "travel_target": (5, 60), "days": (1, MAX_CLINICS), "health_weight": (0, 1),
              "staff_count": (1, 100), "clinical_hours": (.5, 12), "services_per_staff_hour": (.1, 100)}
    for key, (lo, hi) in ranges.items():
        v = p[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
            raise ValueError(f"{key.replace('_', ' ')} must be between {lo} and {hi}.")
    if p["radius"] not in RADII:
        raise ValueError("Driving radius must be 15, 30, 45 or 60 minutes.")
    for key in ("staff_count", "days"):
        if int(p[key]) != p[key]:
            raise ValueError(f"{key.replace('_', ' ')} must be a whole number.")
        p[key] = int(p[key])
    if p["staff_count"] < SERVICES[p["service"]]["minimum_staff"]:
        raise ValueError(f"{SERVICES[p['service']]['name']} needs at least {SERVICES[p['service']]['minimum_staff']} clinical staff.")
    p["hospital_name"] = str(p["hospital_name"]).strip()[:120] or "Hospital"
    start = date.fromisoformat(p.get("date_start") or (date.today() + timedelta(days=7)).isoformat())
    end = date.fromisoformat(p.get("date_end") or (start + timedelta(days=28)).isoformat())
    if end < start:
        raise ValueError("The latest date must be on or after the earliest date.")
    weekdays = sum(1 for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() < 5)
    if p["days"] > weekdays:
        raise ValueError(f"Your date range has only {weekdays} weekday{'s' if weekdays != 1 else ''}. Widen it or reduce the number of days.")
    p["date_start"], p["date_end"] = start.isoformat(), end.isoformat()
    p["use_existing_care"] = bool(p["use_existing_care"])
    p["address"] = str(p.get("address") or "")[:200]
    return p


def clinic_dates(start, end, count):
    """Spread events across the window on weekdays."""
    span = (end - start).days
    dates = []
    for i in range(count):
        d = start + timedelta(days=round(span * (i + .5) / count)) if count > 1 else start + timedelta(days=span // 2)
        while d.weekday() >= 5 and d < end:
            d += timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        dates.append(max(d, start))
    return dates


def _greedy(cover, weight, limit, order):
    chosen, covered = [], np.zeros(cover.shape[1], dtype=bool)
    for _ in range(limit):
        gains = (cover & ~covered) @ weight
        best = max(range(len(gains)), key=lambda i: (round(float(gains[i]), 9), -order[i]))
        if gains[best] <= 0:
            break
        chosen.append(best)
        covered |= cover[best]
    return chosen


def site_clinics(state, routes, params):
    p = validate(params)
    service = SERVICES[p["service"]]
    lo, hi = service["benchmark"]
    tracts = [t for t in state["tracts"] if t.get("eligible_population") is not None and t.get("no_vehicle_pct") is not None
              and t["indicators"].get(service["indicator"], {}).get("value") is not None and t.get("lat") is not None]
    lats = np.array([t["lat"] for t in tracts])
    lons = np.array([t["lon"] for t in tracts])
    pop = np.array([t["eligible_population"] for t in tracts], dtype=float)
    health = np.clip((np.array([t["indicators"][service["indicator"]]["value"] for t in tracts]) - lo) / (hi - lo), 0, 1)
    barrier = np.clip(np.array([t["no_vehicle_pct"] for t in tracts]) / 20, 0, 1)

    hospital_radius = p["radius"] * MILES_PER_MINUTE
    target_radius = p["travel_target"] * MILES_PER_MINUTE
    in_area = _haversine(p["lat"], p["lon"], lats, lons) <= hospital_radius
    if not in_area.any():
        raise ValueError("No census tracts fall inside that radius. Try a larger driving radius.")

    # Existing safety-net access, using the cached OSRM road times (not straight-line distance).
    baseline = np.zeros(len(tracts), dtype=bool)
    if p["use_existing_care"]:
        index = {t["id"]: i for i, t in enumerate(state["tracts"])}
        rows = [index[t["id"]] for t in tracts]
        care_cols = slice(len(state["venues"]), routes.shape[1])
        with np.errstate(invalid="ignore"):
            baseline = np.nanmin(np.where(np.isfinite(routes[rows][:, care_cols]), routes[rows][:, care_cols], np.inf), axis=1) <= p["travel_target"]

    candidates = [v for v in state["venue_directory"] if v.get("lat") is not None
                  and _haversine(p["lat"], p["lon"], np.array([v["lat"]]), np.array([v["lon"]]))[0] <= hospital_radius]
    if not candidates:
        raise ValueError("No candidate host sites fall inside that radius. Try a larger driving radius.")
    c_lat = np.array([v["lat"] for v in candidates])
    c_lon = np.array([v["lon"] for v in candidates])
    reach = np.array([_haversine(v["lat"], v["lon"], lats, lons) <= target_radius for v in candidates])
    cover = reach & in_area & ~baseline
    order = list(range(len(candidates)))
    order.sort(key=lambda i: candidates[i]["id"])
    rank = {i: r for r, i in enumerate(order)}
    rank_list = [rank[i] for i in range(len(candidates))]

    cost = p["staff_count"] * p["clinical_hours"] * STAFF_HOURLY_COST + EVENT_FIXED_COST
    capacity = int(p["staff_count"] * p["clinical_hours"] * p["services_per_staff_hour"])
    limit = p["days"]

    weight = pop * (p["health_weight"] * health + (1 - p["health_weight"]) * barrier)
    chosen = _greedy(cover, weight, limit, rank_list)
    if not chosen:
        raise ValueError("Every tract in this area already has access within the travel target, or no host site reaches an unserved tract.")

    # Robustness: how often each pick appears across the policy-weight range.
    frequency = {i: 0 for i in chosen}
    for w in WEIGHT_GRID:
        alt = _greedy(cover, pop * (w * health + (1 - w) * barrier), limit, rank_list)
        for i in alt:
            if i in frequency:
                frequency[i] += 1

    dates = clinic_dates(date.fromisoformat(p["date_start"]), date.fromisoformat(p["date_end"]), len(chosen))
    covered = np.zeros(len(tracts), dtype=bool)
    clinics = []
    for n, i in enumerate(chosen):
        new = cover[i] & ~covered
        covered |= new
        v = candidates[i]
        w = weight[new]
        tr = [tracts[j] for j in np.flatnonzero(new)]
        clinics.append({
            "rank": n + 1, "label": f"Clinic {n + 1}", "id": v["id"], "name": v["name"], "address": v.get("address"),
            "city": v.get("city"), "county": v.get("county"), "lat": v["lat"], "lon": v["lon"], "date": dates[n].isoformat(),
            "service": p["service"], "capacity": capacity, "new_adults": int(pop[new].sum()), "need_weight": float(w.sum()), "new_tracts": int(new.sum()),
            "avg_indicator": round(float(np.average([t["indicators"][service["indicator"]]["value"] for t in tr], weights=pop[new])), 1) if new.any() else None,
            "avg_no_vehicle": round(float(np.average([t["no_vehicle_pct"] for t in tr], weights=pop[new])), 1) if new.any() else None,
            "distance_from_hospital_miles": round(float(_haversine(p["lat"], p["lon"], np.array([v["lat"]]), np.array([v["lon"]]))[0]), 1),
            "stability": {"chosen_in": frequency[i], "of": len(WEIGHT_GRID)},
        })

    def community(j):
        t = tracts[j]
        return {"tract": t["id"], "county": t["county"], "adults": int(pop[j]), "indicator_pct": round(t["indicators"][service["indicator"]]["value"], 1),
                "no_vehicle_pct": round(t["no_vehicle_pct"], 1), "priority": round(float(health[j] * p["health_weight"] + barrier[j] * (1 - p["health_weight"])), 2),
                "need_weight": round(float(weight[j]))}
    reached = np.flatnonzero(covered)
    left = np.flatnonzero(in_area & ~baseline & ~covered)
    counties = {}
    for j in np.flatnonzero(in_area):
        c = counties.setdefault(tracts[j]["county"], {"county": tracts[j]["county"], "adults": 0, "newly_in_reach": 0, "existing_access": 0, "unserved": 0})
        c["adults"] += int(pop[j])
        c["newly_in_reach" if covered[j] else "existing_access" if baseline[j] else "unserved"] += int(pop[j])
    communities = {
        "prioritized": [community(j) for j in reached[np.argsort(-weight[reached])][:10]],
        "left_out": [community(j) for j in left[np.argsort(-weight[left])][:8]],
        "counties": sorted(counties.values(), key=lambda c: -c["adults"])[:8],
    }
    total_pop = float(pop[in_area].sum())
    total_weight = float(weight[in_area].sum())
    status = np.where(covered, "new", np.where(baseline & in_area, "existing", "unserved"))
    return {
        "params": p, "service_name": service["name"], "indicator_label": service["short_label"],
        "hospital": {"name": p["hospital_name"], "lat": p["lat"], "lon": p["lon"], "address": p.get("address")},
        "radius_miles": round(hospital_radius, 1), "target_miles": round(target_radius, 1),
        "clinics": clinics, "communities": communities,
        "summary": {
            "tracts_in_area": int(in_area.sum()), "adults_in_area": int(total_pop),
            "adults_with_existing_access": int(pop[in_area & baseline].sum()),
            "adults_newly_served": int(pop[covered].sum()),
            "adults_still_unserved": int(pop[in_area & ~baseline & ~covered].sum()),
            "share_of_need_addressed": round(float(weight[covered].sum() / total_weight), 3) if total_weight else 0,
            "candidate_sites": len(candidates), "estimated_cost": round(len(chosen) * cost), "days_requested": p["days"], "cost_per_clinic_day": round(cost),
            "capacity_per_clinic_day": capacity, "total_capacity": capacity * len(chosen), 
        },
        "tracts": [[t["id"], round(float(health[j] * p["health_weight"] + barrier[j] * (1 - p["health_weight"])), 3), str(status[j])]
                   for j, t in enumerate(tracts) if in_area[j]],
        "method": {"miles_per_minute": MILES_PER_MINUTE, "staff_hourly_cost": STAFF_HOURLY_COST, "event_fixed_cost": EVENT_FIXED_COST, "weights_tested": list(WEIGHT_GRID),
                   "note": "Hospital radius and clinic reach use straight-line distance converted at "
                           f"{MILES_PER_MINUTE} mi per driving minute; existing-care access uses cached road times."},
    }
