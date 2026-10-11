"""Add more CDC PLACES tract measures to the packaged national catalog (data/us.sqlite3.gz).

Reads the existing catalog, downloads the extra crude-prevalence columns for every state from the
CDC PLACES API, merges them into each tract's `indicators`, and rewrites the archive. Raw responses
are cached in data/snapshots so a rerun is offline. Safe to run repeatedly.
"""
from __future__ import annotations

import gzip
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_national import PLACES_URL, STATES, download  # noqa: E402
from prepare_data import DATA  # noqa: E402

# PLACES measure id -> service it supports
EXTRA = ("obesity", "highchol", "chd", "stroke", "arthritis", "depression")
SOURCE = "cdc_places_2025"


def interval(text):
    found = re.findall(r"[\d.]+", text or "")
    return (float(found[0]), float(found[1])) if len(found) == 2 else (None, None)


def fetch():
    columns = ["tractfips"] + [f"{m}_{k}" for m in EXTRA for k in ("crudeprev", "crude95ci")]
    rows = {}
    for abbr in STATES:
        url = PLACES_URL + "?" + urllib.parse.urlencode({"$select": ",".join(columns), "$where": f"stateabbr='{abbr}'",
                                                           "$order": "tractfips", "$limit": 20000})
        for row in json.loads(download(f"places-extra-2025-{abbr.lower()}.json", url)):
            rows[row["tractfips"]] = row
        print(f"CDC PLACES extra {abbr}: ok", flush=True)
    return rows


def main():
    archive = DATA / "us.sqlite3.gz"
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "us.sqlite3"
        with gzip.open(archive, "rb") as packed, path.open("wb") as raw:
            shutil.copyfileobj(packed, raw)
        extra = fetch()
        db = sqlite3.connect(path)
        updated = missing = 0
        values = {m: [] for m in EXTRA}
        for tract_id, blob in db.execute("SELECT id,data FROM tracts").fetchall():
            tract, row = json.loads(blob), extra.get(tract_id)
            if row is None:
                missing += 1
                continue
            for m in EXTRA:
                try:
                    value = float(row.get(f"{m}_crudeprev"))
                except (TypeError, ValueError):
                    value = None
                lower, upper = interval(row.get(f"{m}_crude95ci"))
                tract["indicators"][m] = {"value": value, "lower": lower, "upper": upper, "source": SOURCE}
                if value is not None:
                    values[m].append(value)
            db.execute("UPDATE tracts SET data=? WHERE id=?", (json.dumps(tract, separators=(",", ":")), tract_id))
            updated += 1
        db.commit()
        db.execute("VACUUM")
        db.close()
        temporary = archive.with_suffix(".part")
        with path.open("rb") as raw, gzip.open(temporary, "wb", compresslevel=9) as packed:
            shutil.copyfileobj(raw, packed)
        temporary.replace(archive)
    print(f"updated {updated} tracts, {missing} without PLACES rows")
    for m, v in values.items():
        v.sort()
        pick = lambda q: v[min(len(v) - 1, int(q * len(v)))]
        print(f"{m}: n={len(v)} p5={pick(.05)} p50={pick(.5)} p95={pick(.95)} min={v[0]} max={v[-1]}")


if __name__ == "__main__":
    main()
