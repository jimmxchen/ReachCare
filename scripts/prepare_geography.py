"""Preserve tract boundaries for every state and DC as state-sized map files."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from prepare_data import DATA, SNAP, shape_features
from prepare_national import STATES, download, source


def one(abbr, fips):
    name = f"cb_2023_{fips}_tract_500k.zip"
    url = f"https://www2.census.gov/geo/tiger/GENZ2023/shp/{name}"
    features = shape_features(download(name, url), fips)
    if not features:
        raise ValueError(f"No tract boundaries for {abbr}")
    output = DATA / f"geography-{abbr.lower()}.json"
    output.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")))
    return abbr, len(features), {name: source(name, url)}


def build():
    manifest = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(one, abbr, fips): abbr for abbr, fips in STATES.items()}
        for job in as_completed(jobs):
            abbr, count, item = job.result()
            manifest.update(item)
            print(f"{abbr}: {count} boundaries", flush=True)
    (DATA / "us-geography-manifest.json").write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    build()
