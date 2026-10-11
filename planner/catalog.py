"""Read the nationwide tract and site catalog by state."""
from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
import threading
import zlib
from contextlib import closing
from pathlib import Path

STATE_NAMES = {
    "AL":"Alabama","AK":"Alaska","AZ":"Arizona","AR":"Arkansas","CA":"California","CO":"Colorado","CT":"Connecticut","DE":"Delaware","DC":"District of Columbia","FL":"Florida","GA":"Georgia","HI":"Hawaii","ID":"Idaho","IL":"Illinois","IN":"Indiana","IA":"Iowa","KS":"Kansas","KY":"Kentucky","LA":"Louisiana","ME":"Maine","MD":"Maryland","MA":"Massachusetts","MI":"Michigan","MN":"Minnesota","MS":"Mississippi","MO":"Missouri","MT":"Montana","NE":"Nebraska","NV":"Nevada","NH":"New Hampshire","NJ":"New Jersey","NM":"New Mexico","NY":"New York","NC":"North Carolina","ND":"North Dakota","OH":"Ohio","OK":"Oklahoma","OR":"Oregon","PA":"Pennsylvania","RI":"Rhode Island","SC":"South Carolina","SD":"South Dakota","TN":"Tennessee","TX":"Texas","UT":"Utah","VT":"Vermont","VA":"Virginia","WA":"Washington","WV":"West Virginia","WI":"Wisconsin","WY":"Wyoming"
}


class NationalCatalog:
    def __init__(self, archive, runtime_dir):
        self.archive = Path(archive)
        self.path = Path(runtime_dir) / "us.sqlite3"
        self.lock = threading.Lock()

    def _connect(self):
        with self.lock:
            if not self.path.is_file() or self.path.stat().st_mtime < self.archive.stat().st_mtime:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(".part")
                with gzip.open(self.archive, "rb") as packed, temporary.open("wb") as raw:
                    shutil.copyfileobj(packed, raw)
                temporary.replace(self.path)
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)

    def states(self):
        with closing(self._connect()) as db:
            return [{"code": code, "name": STATE_NAMES.get(code, code), "fips": fips,
                     "tracts": db.execute("SELECT COUNT(*) FROM tracts WHERE state=?", (code,)).fetchone()[0],
                     "libraries": db.execute("SELECT COUNT(*) FROM libraries WHERE state=?", (code,)).fetchone()[0],
                     "health_centers": db.execute("SELECT COUNT(*) FROM health_centers WHERE state=?", (code,)).fetchone()[0]}
                    for code, fips in db.execute("SELECT code,fips FROM states ORDER BY code").fetchall()]

    def planner_state(self, state):
        state = str(state).upper()
        with closing(self._connect()) as db:
            if db.execute("SELECT 1 FROM states WHERE code=?", (state,)).fetchone() is None:
                raise ValueError("Choose a state or district in the United States")
            tracts = [json.loads(r[0]) for r in db.execute("SELECT data FROM tracts WHERE state=? ORDER BY id", (state,))]
            venues = [json.loads(r[0]) for r in db.execute("SELECT data FROM libraries WHERE state=? AND selected=1 ORDER BY id", (state,))]
            directory = [json.loads(r[0]) for r in db.execute("SELECT data FROM libraries WHERE state=? ORDER BY id", (state,))]
            care = [json.loads(r[0]) for r in db.execute("SELECT data FROM health_centers WHERE state=? ORDER BY id", (state,))]
            counties = {id: name for id, name in db.execute("SELECT id,name FROM counties WHERE state=? ORDER BY id", (state,))}
            sources = [json.loads(r[0]) for r in db.execute("SELECT data FROM sources ORDER BY id")]
        issues = [{"tract_id": t["id"], "issue": flag} for t in tracts for flag in t.get("flags", [])]
        return {"state_code": state, "state_name": STATE_NAMES.get(state, state), "tracts": tracts,
                "venues": venues, "venue_directory": directory, "existing_services": care,
                "counties": counties, "sources": sources, "quality_issues": issues,
                "candidate_selection": "30 geographically distributed public library candidates, selected per state; full state directory is available"}

    def counties(self):
        with closing(self._connect()) as db:
            return {id: name for id, name in db.execute("SELECT id,name FROM counties ORDER BY state,id")}

    def records(self, table, state, limit=1000, offset=0):
        if table not in {"tracts", "libraries", "health_centers"}:
            raise ValueError("Unknown national catalog table")
        if not isinstance(state, str) or len(state) != 2 or not state.isalpha():
            raise ValueError("Choose a two-letter state code")
        if not 1 <= limit <= 2000 or offset < 0:
            raise ValueError("Invalid catalog page")
        with closing(self._connect()) as db:
            if db.execute("SELECT 1 FROM states WHERE code=?", (state.upper(),)).fetchone() is None:
                raise ValueError("Unknown state code")
            total = db.execute(f"SELECT COUNT(*) FROM {table} WHERE state=?", (state.upper(),)).fetchone()[0]
            rows = db.execute(f"SELECT data FROM {table} WHERE state=? ORDER BY id LIMIT ? OFFSET ?",
                              (state.upper(), limit, offset)).fetchall()
        return {"state": state.upper(), "total": total, "limit": limit, "offset": offset,
                "records": [json.loads(row[0]) for row in rows]}

    def geography(self, state):
        if not isinstance(state, str) or len(state) != 2 or not state.isalpha():
            raise ValueError("Choose a two-letter state code")
        with closing(self._connect()) as db:
            rows = db.execute("SELECT id,geometry FROM tract_shapes WHERE state=? ORDER BY id", (state.upper(),)).fetchall()
        if not rows:
            raise ValueError("Unknown state code")
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"id": row[0]}, "geometry": json.loads(zlib.decompress(row[1]))}
            for row in rows]}
