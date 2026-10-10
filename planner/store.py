from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tracts (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS candidate_venues (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS existing_services (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS venue_verifications (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS travel_times (version TEXT PRIMARY KEY, metadata TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS resources (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, version INTEGER NOT NULL, created_at TEXT NOT NULL, input_hash TEXT NOT NULL, snapshot TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS run_results (run_id TEXT NOT NULL, site_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(run_id,site_id));
                CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY, feed TEXT NOT NULL, observed_at TEXT, retrieved_at TEXT NOT NULL, success INTEGER NOT NULL, data TEXT NOT NULL, error TEXT);
                CREATE INDEX IF NOT EXISTS observations_feed ON observations(feed,retrieved_at);
                CREATE TABLE IF NOT EXISTS outcomes (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS historical_series (id TEXT PRIMARY KEY, retrieved_at TEXT NOT NULL, data TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def seed(self, state, routing):
        with self.connect() as db:
            for table, key in [("tracts", "tracts"), ("candidate_venues", "venues"), ("existing_services", "existing_services")]:
                for item in state[key]:
                    db.execute(f"INSERT OR IGNORE INTO {table} VALUES (?,?)", (item["id"], json.dumps(item)))
            db.execute("INSERT OR IGNORE INTO travel_times VALUES (?,?)", (routing["sha256"], json.dumps(routing)))

    def records(self, table):
        if table not in ["venue_verifications", "existing_services", "historical_series"]:
            raise ValueError("Invalid table")
        with self.connect() as db:
            return [json.loads(r["data"]) for r in db.execute(f"SELECT data FROM {table} ORDER BY id")]

    def update_records(self, table, records):
        if table not in ["venue_verifications", "existing_services", "historical_series"]:
            raise ValueError("Invalid table")
        with self.connect() as db:
            for item in records:
                if table == "historical_series":
                    db.execute("INSERT OR REPLACE INTO historical_series VALUES (?,?,?)", (item["id"], now(), json.dumps(item)))
                else:
                    db.execute(f"INSERT OR REPLACE INTO {table} VALUES (?,?)", (item["id"], json.dumps(item)))

    def save_run(self, snapshot, result, input_hash, change_reason):
        with self.connect() as db:
            version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM runs").fetchone()[0]
            result.update(id=str(uuid.uuid4()), version=version, created_at=now(), input_hash=input_hash, change_reason=change_reason)
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)", (result["id"], version, result["created_at"], input_hash, json.dumps(snapshot, allow_nan=False), json.dumps(result, allow_nan=False)))
            for recommendation in result["recommendations"]:
                db.execute("INSERT INTO run_results VALUES (?,?,?)", (result["id"], recommendation["site"]["id"], json.dumps(recommendation)))
        return result

    def list_runs(self):
        with self.connect() as db:
            rows = db.execute("SELECT id,version,created_at,input_hash,result FROM runs ORDER BY version DESC LIMIT 100").fetchall()
        return [{"id": r["id"], "version": r["version"], "created_at": r["created_at"], "input_hash": r["input_hash"], "decision": json.loads(r["result"])["decision"], "change_reason": json.loads(r["result"])["change_reason"], "config": json.loads(r["result"])["config"], "top_site": next(iter(json.loads(r["result"])["recommendations"]), {}).get("site", {}).get("name")} for r in rows]

    def get_run(self, run_id, export=False):
        with self.connect() as db:
            row = db.execute("SELECT result,snapshot FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("Planning run not found")
        result = json.loads(row["result"])
        if export:
            result["reproduction_inputs"] = json.loads(row["snapshot"])
        return result

    def observation(self, feed, data, observed_at=None, error=None):
        item = {"id": str(uuid.uuid4()), "feed": feed, "observed_at": observed_at, "retrieved_at": now(), "success": error is None, "data": data, "error": error}
        with self.connect() as db:
            db.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?)", (item["id"], feed, observed_at, item["retrieved_at"], int(item["success"]), json.dumps(data), error))
        return item

    def feed_state(self, feed, ttl=900):
        with self.connect() as db:
            latest = db.execute("SELECT * FROM observations WHERE feed=? ORDER BY retrieved_at DESC LIMIT 1", (feed,)).fetchone()
            successful = db.execute("SELECT * FROM observations WHERE feed=? AND success=1 ORDER BY retrieved_at DESC LIMIT 1", (feed,)).fetchone()
        if not latest:
            return {"feed": feed, "stale": True, "data": None, "error": "Not yet fetched", "retrieved_at": None, "observed_at": None, "snapshot_id": None}
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(successful["retrieved_at"])).total_seconds() if successful else float("inf")
        return {"feed": feed, "stale": not latest["success"] or age > ttl, "data": json.loads(successful["data"]) if successful else None, "error": latest["error"], "retrieved_at": successful["retrieved_at"] if successful else latest["retrieved_at"], "observed_at": successful["observed_at"] if successful else None, "snapshot_id": successful["id"] if successful else None}

    def save_outcome(self, run_id, data):
        self.get_run(run_id)
        item = {"id": str(uuid.uuid4()), "run_id": run_id, "created_at": now(), **data}
        with self.connect() as db:
            db.execute("INSERT INTO outcomes VALUES (?,?,?,?)", (item["id"], run_id, item["created_at"], json.dumps(item)))
        return item

    def outcomes(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM outcomes ORDER BY created_at DESC")]

