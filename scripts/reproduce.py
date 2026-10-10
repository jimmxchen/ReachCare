"""Reproduce the numeric results of an exported, immutable planning run."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from planner.model import run_plan


def reproduce(export):
    snapshot = export["reproduction_inputs"]
    fresh = run_plan(snapshot["state"], np.array(snapshot["road_times_minutes"], dtype=float), snapshot["config"], snapshot["operations"], snapshot["observations"], snapshot.get("weather_blocks"))
    fields = ["score", "rank", "score_components", "newly_covered_tracts", "approx_population_in_newly_covered_tracts", "event_capacity", "top_n_frequency", "first_frequency", "median_rank", "average_regret", "worst_case_regret"]
    def metrics(run):
        return [{"site_id": r["site"]["id"], **{k: r[k] for k in fields}} for r in run["recommendations"]]
    if metrics(fresh) != metrics(export) or fresh["schedule"] != export["schedule"]:
        raise ValueError("Reproduced metrics differ from saved results")
    return fresh


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("export", type=Path)
    args = parser.parse_args()
    result = reproduce(json.loads(args.export.read_text()))
    print(f"Reproduced {len(result['recommendations'])} rankings, coverage, capacity, schedule, and simulation statistics exactly.")
