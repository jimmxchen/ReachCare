"""Public operational feeds. Incidents produce warnings, never invented detours."""
from __future__ import annotations

import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote_plus, urlencode
from zoneinfo import ZoneInfo

FEEDS = {
    "nws-alerts": {"url": "https://api.weather.gov/alerts/active?area=NC", "title": "National Weather Service alerts", "ttl": 900},
    "ncdot-incidents": {"url": "https://www.drivenc.gov/api/v2/get/event", "title": "DriveNC / NCDOT road events", "ttl": 300},
}


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "HackNC-Rural-Outreach-Planner/1.0", "Accept": "application/geo+json,application/json"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def refresh(store):
    def update(item):
        name, config = item
        developer_key = ""
        try:
            url = config["url"]
            if name == "ncdot-incidents":
                developer_key = os.environ.get("DRIVENC_API_KEY", "").strip()
                if not developer_key:
                    raise ValueError("DriveNC developer key required. Set DRIVENC_API_KEY locally and restart. Setup: https://drivenc.gov/help/endpoint/event")
                url += "?" + urlencode({"key": developer_key, "format": "json"})
            raw = get_json(url)
            if name == "nws-alerts":
                features = raw.get("features")
                if not isinstance(features, list):
                    raise ValueError("NWS response lacks alert features")
                values = [{"id": f["id"], **{k: f["properties"].get(k) for k in ["event", "headline", "severity", "effective", "onset", "expires", "ends", "areaDesc", "geocode", "description"]}, "geometry": f.get("geometry")} for f in features]
                data = {"count": len(values), "items": values, "source": config["url"]}
                observed_at = max((v.get("effective") or "" for v in values), default=None)
            else:
                values = raw if isinstance(raw, list) else raw.get("incidents", raw.get("results"))
                if not isinstance(values, list):
                    raise ValueError("DriveNC did not return a road-event list; check developer-key access")
                data = {"count": len(values), "items": values, "source": config["url"], "route_match": "Not matched to cached paths; review warning only"}
                updated = [v.get("LastUpdated") for v in values if isinstance(v.get("LastUpdated"), (int, float))]
                observed_at = datetime.fromtimestamp(max(updated), timezone.utc).isoformat() if updated else None
            store.observation(name, data, observed_at or None)
        except Exception as exc:
            # The documented endpoint uses a query key. Keep it out of errors,
            # saved observations, and exported input snapshots.
            message = str(exc)
            if developer_key:
                message = message.replace(developer_key, "[redacted]").replace(quote_plus(developer_key), "[redacted]")
            store.observation(name, None, error=message)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(update, FEEDS.items()))
    return states(store)


def states(store):
    return [{**store.feed_state(name, config["ttl"]), "title": config["title"], "source": config["url"]} for name, config in FEEDS.items()]


def forecast(store, venue):
    name = "forecast-" + venue["id"]
    try:
        point = get_json(f'https://api.weather.gov/points/{venue["lat"]:.4f},{venue["lon"]:.4f}')
        hourly_url = point["properties"]["forecastHourly"]
        # URLs are supplied by NWS and constrained to its public service.
        if not hourly_url.startswith("https://api.weather.gov/"):
            raise ValueError("Unexpected forecast URL")
        raw = get_json(hourly_url)
        store.observation(name, {"periods": raw["properties"]["periods"], "source": hourly_url}, raw["properties"].get("updateTime"))
    except Exception as exc:
        store.observation(name, None, error=str(exc))
    return store.feed_state(name, 3600)


def weather_blocks(venues, signals, config):
    """Match a declared warning policy to both event county and active date."""
    signal = next((s for s in signals if s["feed"] == "nws-alerts"), None)
    blocks, matched = {}, []
    if not signal or signal["stale"] or not signal.get("data"):
        return blocks, matched
    for alert in signal["data"]["items"]:
        counties = {str(value)[-5:] for value in (alert.get("geocode") or {}).get("SAME", []) if str(value)[-5:].startswith("37")}
        counties |= {"37" + value[3:] for value in (alert.get("geocode") or {}).get("UGC", []) if value.startswith("NCC") and len(value) == 6}
        try:
            onset = datetime.fromisoformat(alert.get("onset") or alert["effective"])
            end = datetime.fromisoformat(alert.get("ends") or alert["expires"])
            # County warnings conservatively cover each intersecting local calendar day.
            local_zone = ZoneInfo("America/New_York")
            start_date = onset.astimezone(local_zone).date() if onset.tzinfo else onset.date()
            end_date = end.astimezone(local_zone).date() if end.tzinfo else end.date()
        except (KeyError, TypeError, ValueError):
            continue
        for venue in venues:
            if venue.get("county_id") not in counties:
                continue
            dates = []
            current = max(start_date, date.fromisoformat(config.get("date_start", date.today().isoformat())))
            last = min(end_date, date.fromisoformat(config.get("date_end", date.today().isoformat())))
            while current <= last:
                dates.append(current.isoformat())
                current += timedelta(days=1)
            if dates:
                blocked = alert["event"] in config.get("weather_block_events", [])
                matched.append({"site_id": venue["id"], "event": alert["event"], "dates": dates, "blocked": blocked, "alert_id": alert["id"], "match": "County warning area and local calendar date"})
                if blocked:
                    blocks.setdefault(venue["id"], []).extend(dates)
    return blocks, matched
