"""Public operational feeds. Incidents produce warnings, never invented detours."""
from __future__ import annotations

import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from .catalog import STATE_NAMES

FEEDS = {
    "nws-alerts": {"url": "https://api.weather.gov/alerts/active", "title": "National Weather Service active alerts", "ttl": 900},
}
STATE_FIPS = dict(zip(STATE_NAMES, "01 02 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56".split()))


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "HackNC-Rural-Outreach-Planner/1.0", "Accept": "application/geo+json,application/json"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def refresh(store):
    def update(item):
        name, config = item
        try:
            raw = get_json(config["url"])
            features = raw.get("features")
            if not isinstance(features, list):
                raise ValueError("NWS response lacks alert features")
            values = [{"id": f["id"], **{k: f["properties"].get(k) for k in ["event", "headline", "severity", "effective", "onset", "expires", "ends", "areaDesc", "geocode", "description"]}, "geometry": f.get("geometry")} for f in features]
            data = {"count": len(values), "items": values, "source": config["url"]}
            observed_at = max((v.get("effective") or "" for v in values), default=None)
            store.observation(name, data, observed_at or None)
        except Exception as exc:
            store.observation(name, None, error=str(exc))
    with ThreadPoolExecutor(max_workers=1) as pool:
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
    fips_by_state = STATE_FIPS
    for alert in signal["data"]["items"]:
        counties = set()
        for value in (alert.get("geocode") or {}).get("SAME", []):
            code = str(value)[-5:]
            if len(code) == 5 and code[:2] in set(STATE_FIPS.values()):
                counties.add(code)
        for value in (alert.get("geocode") or {}).get("UGC", []):
            state_abbr, county = str(value)[:2], str(value)[2:]
            if state_abbr in fips_by_state and len(county) == 3 and county.isdigit():
                counties.add(fips_by_state[state_abbr] + county)
        try:
            onset = datetime.fromisoformat(alert.get("onset") or alert["effective"])
            end = datetime.fromisoformat(alert.get("ends") or alert["expires"])
            # County warnings conservatively cover each intersecting local calendar day.
            start_date = onset.date()
            end_date = end.date()
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
