from __future__ import annotations

import argparse
import base64
import copy
import csv
import gzip
import hmac
import io
import json
import math
import mimetypes
import os
import threading
import time
import traceback
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

from planner.subscribers import Subscribers
from planner import bot, planchat, feeds, gemini, geocode, siting
from planner.model import SERVICES, MODEL_VERSION, default_config, demo_operations, input_hash, run_plan
from planner.store import Store

ROOT = Path(__file__).resolve().parent


class Application:
    def __init__(self, data_dir=None, runtime_dir=None):
        gemini._load_env()
        data_dir = Path(data_dir or ROOT / "data")
        self.data_dir = data_dir
        self.state = json.loads((data_dir / "nc.json").read_text())
        self.routing = json.loads((data_dir / "routing-metadata.json").read_text())
        archive = np.load(data_dir / "routes.npz")
        self.routes = archive["minutes"]
        if list(archive["tract_ids"]) != [t["id"] for t in self.state["tracts"]] or list(archive["site_ids"]) != [s["id"] for s in self.state["venues"] + self.state["existing_services"]]:
            raise ValueError("Routing IDs do not align with the dataset. Rebuild the matrix.")
        self.store = Store(Path(runtime_dir or ROOT / ".runtime") / "planner.sqlite3")
        self.store.seed(self.state, self.routing)
        self.subscribers = Subscribers(Path(runtime_dir or ROOT / ".runtime") / "subscribers.json")
        self.signup_times = []
        self.run_lock = threading.Lock()
        self.feed_lock = threading.Lock()

    def county_centers(self):
        sums = {}
        for t in self.state["tracts"]:
            if t.get("lat") is not None and t.get("eligible_population"):
                w = t["eligible_population"]
                a = sums.setdefault(t["county_id"], [0, 0, 0])
                a[0] += t["lat"] * w; a[1] += t["lon"] * w; a[2] += w
        return {k: [round(a[0] / a[2], 4), round(a[1] / a[2], 4)] for k, a in sums.items() if a[2]}

    def current_state(self):
        state = copy.deepcopy(self.state)
        saved = {s["id"]: s for s in self.store.records("existing_services")}
        state["existing_services"] = [saved.get(s["id"], s) for s in state["existing_services"]]
        return state

    def bootstrap(self):
        state = self.current_state()
        return {"config": default_config(), "services": SERVICES, "counties": state["counties"],
                "tracts": state["tracts"], "venues": state["venues"], "existing_services": state["existing_services"],
                "sources": state["sources"], "quality_issues": state["quality_issues"], "routing": self.routing,
                "candidate_selection": state["candidate_selection"], "venue_operations": {v["id"]: v for v in self.store.records("venue_verifications")},
                "demo_operations": demo_operations(state["venues"]), "runs": self.store.list_runs(),
                "feeds": feeds.states(self.store), "outcomes": self.store.outcomes(), "series": self.store.records("historical_series")}

    def run(self, config, reason="Planner configuration updated"):
        with self.run_lock:
            state = self.current_state()
            operations = {v["id"]: v for v in self.store.records("venue_verifications")}
            series = self.store.records("historical_series")
            signals = feeds.states(self.store)
            weather_blocks, matched_alerts = feeds.weather_blocks(state["venues"], signals, config)
            result = run_plan(state, self.routes, config, operations, series, weather_blocks)
            result["matched_weather_alerts"] = matched_alerts
            result.update(refreshed_at=max((f["retrieved_at"] or "" for f in signals), default="") or None,
                          stale_inputs=[f["title"] for f in signals if f["stale"]], feeds=signals, routing=self.routing)
            snapshot = {"state": state, "config": result["config"], "operations": operations, "observations": series,
                        "road_times_minutes": np.where(np.isfinite(self.routes), self.routes, None).tolist(),
                        "routing": self.routing, "feeds": signals, "weather_blocks": weather_blocks, "model_version": MODEL_VERSION,
                        "numpy_version": np.__version__}
            digest = input_hash(snapshot)
            previous = self.store.list_runs()
            if previous:
                before = self.store.get_run(previous[0]["id"])
                old = {r["site"]["id"]: r for r in before["recommendations"]}
                result["comparison"] = {"previous_id": before["id"], "previous_version": before["version"],
                    "population_change": result.get("schedule_population", 0) - before.get("schedule_population", 0),
                    "score_change": round(result.get("schedule_score", 0) - before.get("schedule_score", 0), 6),
                    "site_changes": [{"site_id": r["site"]["id"], "name": r["site"]["name"], "previous_rank": old.get(r["site"]["id"], {}).get("rank"), "current_rank": r["rank"], "score_change": round(r["score"] - old.get(r["site"]["id"], {}).get("score", 0), 6)} for r in result["recommendations"][:3]],
                    "removed_sites": [r["site"]["name"] for r in before["recommendations"] if r["site"]["id"] in {e["id"] for e in result["excluded"]}]}
            return self.store.save_run(snapshot, result, digest, reason)

    def site(self, params):
        if params.get("lat") is None or params.get("lon") is None:
            params = {**params, **geocode.locate(params.get("hospital_name", ""))}
        plan = siting.site_clinics(self.state, self.routes, params)
        plan["created_at"] = datetime.now(timezone.utc).isoformat()
        with self.run_lock:
            path = Path(self.store.path).parent / "clinic_plans.json"
            plans = json.loads(path.read_text()) if path.is_file() else []
            plan["id"] = f"plan-{len(plans) + 1}"
            plans.append({k: plan[k] for k in ("id", "created_at", "params", "hospital", "service_name", "clinics", "summary")})
            path.write_text(json.dumps(plans[-50:]))
        return plan

    def plans(self):
        path = Path(self.store.path).parent / "clinic_plans.json"
        return json.loads(path.read_text()) if path.is_file() else []

    def import_csv(self, kind, contents):
        if not isinstance(contents, str) or len(contents) > 1000000:
            raise ValueError("CSV must be a text file under 1 MB.")
        rows = list(csv.DictReader(io.StringIO(contents.lstrip("\ufeff"))))
        if not rows or len(rows) > 10000:
            raise ValueError("Upload a CSV with 1–10,000 rows.")
        parsed, errors = [], []
        venues = {v["id"] for v in self.state["venues"]}
        care = {s["id"]: s for s in self.state["existing_services"]}
        def flag(row, key):
            value = row.get(key, "").strip().lower()
            if value not in ["true", "false", "1", "0", "yes", "no", ""]:
                raise ValueError(f"{key}: use true, false, or blank for unknown")
            return None if not value else value in ["true", "1", "yes"]
        def numeric(row, key):
            try:
                value = float(row[key])
            except (KeyError, ValueError):
                raise ValueError(f"{key}: enter a nonnegative number") from None
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{key}: enter a nonnegative number")
            return value
        for index, row in enumerate(rows, 2):
            try:
                if kind == "venues":
                    if row.get("id") not in venues:
                        raise ValueError("Venue ID does not match the public shortlist")
                    item = {"id": row["id"], **{key: flag(row, key) for key in ["details_verified", "host_approved", "accessible", "private_space", "power", "available"]}, "cost": numeric(row, "cost"), "space_throughput": numeric(row, "space_throughput"), "sample": False, "verified_by": row.get("verified_by", "").strip(), "verified_at": row.get("verified_at", "").strip(), "available_dates": [v.strip() for v in row.get("available_dates", "").split(";") if v.strip()]}
                    if not item["verified_by"] or not item["verified_at"]:
                        raise ValueError("Verifier and verification date are required")
                    date.fromisoformat(item["verified_at"])
                    if date.fromisoformat(item["verified_at"]) > date.today():
                        raise ValueError("Verification date cannot be in the future")
                    for day in item["available_dates"]:
                        date.fromisoformat(day)
                elif kind == "services":
                    if row.get("id") not in care or row.get("service") not in SERVICES:
                        raise ValueError("Existing-service ID or service is not recognized")
                    item = {**care[row["id"]], **{key: flag(row, key) for key in ["service_verified", "eligibility_verified", "schedule_verified"]}, "service": row["service"], "verified_by": row.get("verified_by", "").strip(), "verified_at": row.get("verified_at", "").strip(), "schedule_start": row.get("schedule_start", ""), "schedule_end": row.get("schedule_end", ""), "sample": False}
                    date.fromisoformat(item["verified_at"])
                    schedule_start, schedule_end = date.fromisoformat(item["schedule_start"]), date.fromisoformat(item["schedule_end"])
                    if schedule_end < schedule_start:
                        raise ValueError("Provider schedule end must follow its start")
                    if not item["verified_by"] or date.fromisoformat(item["verified_at"]) > date.today():
                        raise ValueError("A verifier and valid past verification date are required")
                elif kind == "series":
                    required = ["signal", "service", "geography_type", "geography_id", "period_start", "period_end", "unit", "source", "published_at", "revision_id"]
                    if any(not row.get(k) for k in required):
                        raise ValueError("Historical-series metadata is incomplete")
                    if row["service"] not in SERVICES or row["geography_type"] not in ["county", "tract"]:
                        raise ValueError("Series must match a configured service and county or tract geography")
                    valid_geo = self.state["counties"] if row["geography_type"] == "county" else {t["id"] for t in self.state["tracts"]}
                    if row["geography_id"] not in valid_geo:
                        raise ValueError("Geography ID does not match; ZIP codes are not converted to tracts")
                    start, end = date.fromisoformat(row["period_start"]), date.fromisoformat(row["period_end"])
                    if end < start:
                        raise ValueError("Series period end precedes its start")
                    denominator = numeric(row, "denominator")
                    if denominator <= 0:
                        raise ValueError("A positive denominator is required")
                    item = {**row, "id": f'{row["signal"]}:{row["geography_id"]}:{row["period_end"]}:{row["revision_id"]}', "value": numeric(row, "value"), "denominator": denominator,
                            "provisional": flag(row, "provisional"), "suppressed": flag(row, "suppressed"), "retrieved_at": datetime.now(timezone.utc).isoformat()}
                    item["recent_rate"] = item["value"] / denominator
                    item["baseline_rate"] = numeric(row, "baseline_rate")
                    item["stale"] = (date.today() - end).days > 60
                    item["valid"] = not item["suppressed"] and item["baseline_rate"] > 0
                else:
                    raise ValueError("CSV kind must be venues, services, or series")
                parsed.append(item)
            except (ValueError, KeyError) as exc:
                errors.append({"row": index, "error": str(exc)})
        if len({r["id"] for r in parsed}) != len(parsed):
            errors.append({"row": None, "error": "Duplicate record IDs; no rows imported"})
        if errors:
            return {"imported": 0, "errors": errors}
        table = {"venues": "venue_verifications", "services": "existing_services", "series": "historical_series"}[kind]
        self.store.update_records(table, parsed)
        # Preserve every raw version, including provisional/revised series observations.
        self.store.observation("csv-" + kind, {"csv": contents, "records": parsed})
        return {"imported": len(parsed), "errors": []}


def _auth_credentials():
    """HTTP Basic credentials, when REACHCARE_PASSWORD is set (used by the hosted demo)."""
    gemini._load_env()
    password = os.environ.get("REACHCARE_PASSWORD", "")
    return (os.environ.get("REACHCARE_USER", "username"), password) if password else None


MESSAGING_PATHS = ("/api/signup", "/api/bot/")


def _same_origin(origin, host):
    scheme = "https" if os.environ.get("VERCEL") else "http"
    return origin == f"{scheme}://{host}"


class Handler(BaseHTTPRequestHandler):
    app: Application

    def authorized(self):
        creds = _auth_credentials()
        if not creds:
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                user, _, password = base64.b64decode(header[6:]).decode().partition(":")
            except (ValueError, UnicodeDecodeError):
                return False
            return hmac.compare_digest(user.encode(), creds[0].encode()) and hmac.compare_digest(password.encode(), creds[1].encode())
        return False

    def deny(self):
        body = b"Sign in to use ReachCare."
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="ReachCare", charset="UTF-8"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def hosted_demo_blocks(self, path):
        """The hosted demo is stateless; texting needs the always-on bridge, so say so instead of silently dropping data."""
        if os.environ.get("VERCEL") and path.startswith(MESSAGING_PATHS):
            self.json_response({"error": "Text alerts and the bot run on the ReachCare bridge, not this hosted demo. Run it locally with ./start.sh."}, 503)
            return True
        return False

    def json_response(self, value, status=200):
        body = json.dumps(value, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.authorized():
            return self.deny()
        url = urlparse(self.path)
        path = url.path
        try:
            if path == "/api/bootstrap":
                self.json_response(self.app.bootstrap())
            elif path == "/api/runs":
                self.json_response(self.app.store.list_runs())
            elif path.startswith("/api/runs/"):
                run_id = path.split("/")[3]
                self.json_response(self.app.store.get_run(run_id, export="export" in parse_qs(url.query)))
            elif path == "/api/feeds":
                self.json_response(feeds.states(self.app.store))
            elif path == "/api/outcomes":
                self.json_response(self.app.store.outcomes())
            elif path == "/api/clinic-options":
                self.json_response({"counties": self.app.state["counties"], "services": {k: v["name"] for k, v in SERVICES.items()},
                                    "radii": list(siting.RADII), "gemini": gemini.available(), "maps": geocode.available(),
                                    })
            elif path == "/api/places/suggest":
                q = parse_qs(url.query)
                self.json_response({"suggestions": geocode.suggest(q.get("q", [""])[0], q.get("session", [None])[0])})
            elif path == "/api/places/details":
                q = parse_qs(url.query)
                self.json_response(geocode.place_details(q.get("id", [""])[0], q.get("session", [None])[0]))
            elif path == "/api/plans":
                file = Path(self.app.store.path).parent / "clinic_plans.json"
                self.json_response(json.loads(file.read_text()) if file.is_file() else [])
            else:
                base = self.app.data_dir if path.startswith("/data/") else ROOT / "web"
                relative = path[6:] if path.startswith("/data/") else path.lstrip("/") or "index.html"
                target = (base / relative).resolve()
                if not target.is_relative_to(base.resolve()) or not target.is_file() or (base == self.app.data_dir and relative != "geography.json"):
                    self.json_response({"error": "Not found"}, 404)
                    return
                body = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", (mimetypes.guess_type(target)[0] or "application/octet-stream") + "; charset=utf-8")
                if len(body) > 50000 and target.suffix in (".json", ".js", ".css", ".html") and "gzip" in self.headers.get("Accept-Encoding", ""):
                    body = gzip.compress(body, 6)
                    self.send_header("Content-Encoding", "gzip")
                    self.send_header("Vary", "Accept-Encoding")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)
        except ValueError as exc:
            self.json_response({"error": str(exc)}, 400)

    def do_POST(self):
        if not self.authorized():
            return self.deny()
        if self.hosted_demo_blocks(urlparse(self.path).path):
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            limit = 20000000 if self.path.startswith("/api/bot/") else 2000000
            if length <= 0 or length > limit:
                raise ValueError(f"Request must be JSON under {limit // 1000000} MB")
            origin = self.headers.get("Origin")
            if origin and not _same_origin(origin, self.headers.get("Host", "")):
                self.json_response({"error": "Cross-origin changes are disabled"}, 403)
                return
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object")
            path = urlparse(self.path).path
            if path == "/api/bot/message":
                try:
                    self.json_response(bot.handle(self.app.plans(), self.app.subscribers, payload))
                except RuntimeError as exc:
                    self.json_response({"error": str(exc)}, 502)
            elif path == "/api/bot/notify":
                plan = next((p for p in self.app.plans() if p["id"] == payload.get("plan_id")), None)
                if not plan:
                    raise ValueError("Unknown plan.")
                dry = bool(payload.get("dry_run"))
                queued = [m for c in plan["clinics"] for m in bot.notifications(self.app.subscribers, c, plan["hospital"], mark=not dry)]
                if not dry:
                    self.app.subscribers.queue(queued)
                self.json_response({"queued": len(queued), "people": len({m["sender"] for m in queued}), "dry_run": dry})
            elif path == "/api/signup":
                now = time.time()
                recent = [t for t in self.app.signup_times if now - t < 60]
                if len(recent) >= 20:
                    raise ValueError("Too many sign-ups right now. Please try again in a minute.")
                self.app.signup_times = recent + [now]
                self.json_response(bot.signup(self.app.subscribers, payload), 201)
            elif path == "/api/bot/requeue":
                self.app.subscribers.queue([m for m in payload.get("messages", []) if isinstance(m, dict) and m.get("sender") and m.get("text")])
                self.json_response({"ok": True})
            elif path == "/api/bot/outbox":
                self.json_response({"messages": self.app.subscribers.drain()})
            elif path == "/api/siting":
                self.json_response(self.app.site(payload), 201)
            elif path == "/api/gemini/parse":
                if not gemini.available():
                    self.json_response({"error": "Set GEMINI_API_KEY to enable AI intake."}, 503)
                    return
                try:
                    self.json_response(gemini.parse_intake(str(payload.get("text", "")), self.app.state["counties"]))
                except (RuntimeError, ValueError) as exc:
                    self.json_response({"error": str(exc)}, 502)
            elif path == "/api/plan-chat":
                try:
                    self.json_response({"reply": planchat.answer(self.app.state, self.app.routes, payload.get("params", {}), payload.get("messages", []))})
                except RuntimeError as exc:
                    self.json_response({"error": str(exc)}, 502)
            elif path == "/api/gemini/explain":
                if not gemini.available():
                    self.json_response({"error": "Set GEMINI_API_KEY to enable AI explanations."}, 503)
                    return
                try:
                    plan = siting.site_clinics(self.app.state, self.app.routes, payload.get("params", {}))
                    self.json_response({"text": gemini.explain(plan, str(payload.get("language", "English"))[:40])})
                except RuntimeError as exc:
                    self.json_response({"error": str(exc)}, 502)
            elif path == "/api/runs":
                self.json_response(self.app.run(payload.get("config", {}), payload.get("change_reason", "Planner configuration updated")), 201)
            elif path == "/api/import":
                self.json_response(self.app.import_csv(payload.get("kind"), payload.get("csv")))
            elif path == "/api/refresh":
                with self.app.feed_lock:
                    updated = feeds.refresh(self.app.store)
                previous = self.app.store.list_runs()
                result = self.app.run(payload.get("config", previous[0]["config"] if previous else default_config()), "Public operational feeds refreshed; cached road times remain fixed") if payload.get("replan", True) else None
                self.json_response({"feeds": updated, "run": result})
            elif path == "/api/forecast":
                venue = next((v for v in self.app.state["venues"] if v["id"] == payload.get("site_id")), None)
                if not venue:
                    raise ValueError("Unknown venue")
                self.json_response(feeds.forecast(self.app.store, venue))
            elif path == "/api/outcomes":
                run_id = payload.get("run_id")
                run = self.app.store.get_run(run_id)
                if payload.get("site_id") not in {v["site"]["id"] for v in run["recommendations"]}:
                    raise ValueError("Outcome venue must belong to that run")
                data = {"site_id": payload["site_id"], "issues": str(payload.get("issues", ""))[:3000], "event_date": payload.get("event_date")}
                date.fromisoformat(data["event_date"])
                for key in ["turnout", "services_completed", "referrals", "followups", "staff_hours"]:
                    value = payload.get(key)
                    if isinstance(value, bool) or not isinstance(value, (float, int)) or value < 0 or not math.isfinite(value):
                        raise ValueError(f"{key} must be a nonnegative number")
                    if key != "staff_hours" and int(value) != value:
                        raise ValueError(f"{key} must be a whole count")
                    data[key] = value
                self.json_response(self.app.store.save_outcome(run_id, data), 201)
            else:
                self.json_response({"error": "Not found"}, 404)
        except (ValueError, TypeError, KeyError) as exc:
            self.json_response({"error": str(exc)}, 400)
        except Exception:
            traceback.print_exc()
            self.json_response({"error": "The run could not complete. Previous runs are preserved."}, 500)

    def log_message(self, format, *args):
        print(format % args, flush=True)


def poll_feeds(app):
    while True:
        with app.feed_lock:
            feeds.refresh(app.store)
        time.sleep(300)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--live-feeds", action="store_true", help="Poll public NWS/NCDOT feeds every five minutes")
    parser.add_argument("--runtime-dir", type=Path, help="Directory for the local SQLite database (default: .runtime)")
    args = parser.parse_args()
    try:
        Handler.app = Application(runtime_dir=args.runtime_dir)
    except FileNotFoundError:
        raise SystemExit("Public data not prepared. Run: python3 scripts/prepare_data.py") from None
    if args.live_feeds:
        threading.Thread(target=poll_feeds, args=(Handler.app,), daemon=True).start()
    print(f"Rural Outreach Planner: http://{args.host}:{args.port}", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
