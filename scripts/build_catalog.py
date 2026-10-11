"""Materialize the nationwide source catalog as a queryable SQLite database."""
from __future__ import annotations

import json
import gzip
import shutil
import sqlite3
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def build(source=DATA / "us.json", target=DATA / "us.sqlite3"):
    state = json.loads(Path(source).read_text())
    target = Path(target)
    temporary = target.with_suffix(".sqlite3.part")
    temporary.unlink(missing_ok=True)
    db = sqlite3.connect(temporary)
    try:
        db.executescript("""
            CREATE TABLE states (code TEXT PRIMARY KEY, fips TEXT NOT NULL UNIQUE);
            CREATE TABLE counties (id TEXT PRIMARY KEY, state TEXT NOT NULL, name TEXT NOT NULL);
            CREATE TABLE tracts (id TEXT PRIMARY KEY, state TEXT NOT NULL, county_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX tracts_state ON tracts(state);
            CREATE INDEX tracts_county ON tracts(county_id);
            CREATE TABLE libraries (id TEXT PRIMARY KEY, state TEXT NOT NULL, county_id TEXT, selected INTEGER NOT NULL, data TEXT NOT NULL);
            CREATE INDEX libraries_state ON libraries(state);
            CREATE TABLE health_centers (id TEXT PRIMARY KEY, state TEXT NOT NULL, county_id TEXT, data TEXT NOT NULL);
            CREATE INDEX health_centers_state ON health_centers(state);
            CREATE TABLE tract_shapes (id TEXT PRIMARY KEY, state TEXT NOT NULL, geometry BLOB NOT NULL);
            CREATE INDEX tract_shapes_state ON tract_shapes(state);
            CREATE TABLE sources (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        db.executemany("INSERT INTO states VALUES (?,?)", state["states"].items())
        reverse = {fips: code for code, fips in state["states"].items()}
        db.executemany("INSERT INTO counties VALUES (?,?,?)", ((id, reverse[id[:2]], name) for id, name in state["counties"].items()))
        pack = lambda record: json.dumps(record, separators=(",", ":"), allow_nan=False)
        db.executemany("INSERT INTO tracts VALUES (?,?,?,?)", ((t["id"], t["state"], t["county_id"], pack(t)) for t in state["tracts"]))
        selected = {v["id"] for v in state["venues"]}
        db.executemany("INSERT INTO libraries VALUES (?,?,?,?,?)", ((v["id"], v["state"], v["county_id"], int(v["id"] in selected), pack(v)) for v in state["venue_directory"]))
        db.executemany("INSERT INTO health_centers VALUES (?,?,?,?)", ((v["id"], v["state"], v["county_id"], pack(v)) for v in state["existing_services"]))
        db.executemany("INSERT INTO sources VALUES (?,?)", ((s["id"], pack(s)) for s in state["sources"]))
        for abbr in state["states"]:
            path = DATA / f"geography-{abbr.lower()}.json"
            if not path.is_file():
                continue
            features = json.loads(path.read_text())["features"]
            db.executemany("INSERT INTO tract_shapes VALUES (?,?,?)", ((f["properties"]["id"], abbr,
                zlib.compress(pack(f["geometry"]).encode(), 9)) for f in features))
        db.executemany("INSERT INTO metadata VALUES (?,?)", [("created_at", state["created_at"]), ("candidate_selection", state["candidate_selection"])])
        db.commit()
        db.execute("VACUUM")
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(integrity)
    finally:
        db.close()
    temporary.replace(target)
    with target.open("rb") as raw, gzip.open(target.with_suffix(target.suffix + ".gz"), "wb", compresslevel=6) as packed:
        shutil.copyfileobj(raw, packed)
    print(f"Built {target}: {len(state['tracts'])} tracts, {len(state['venue_directory'])} libraries, {len(state['existing_services'])} health centers")


if __name__ == "__main__":
    build()
