"""Build a nationwide tract and site catalog from public source records."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import time
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from prepare_data import DATA, SNAP, number, select_spread

STATES = dict(zip(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split(),
    "01 02 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56".split(),
))
FIPS = set(STATES.values())
PLACES_URL = "https://data.cdc.gov/resource/yjkw-uj5s.json"
ACS_URL = "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/"
HRSA_URL = "https://gisportal.hrsa.gov/server/rest/services/HealthCareFacilities/PrimaryHealthCareFacilities_FS/MapServer/0/query"
FIELDS = ["stateabbr", "countyname", "countyfips", "tractfips", "geolocation"]
for indicator in ("bphigh", "diabetes", "copd", "casthma"):
    FIELDS.extend([f"{indicator}_crudeprev", f"{indicator}_crude95ci"])


def download(name, url):
    path = SNAP / name
    if path.is_file():
        return path.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": "ReachCare/1.0 (public aggregate research)"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                raw = response.read()
            path.write_bytes(raw)
            return raw
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def source(name, url):
    path = SNAP / name
    return {"url": url, "retrieved_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def places():
    rows = []
    manifest = {}
    for abbr in STATES:
        name = f"places-2025-{abbr.lower()}.json"
        url = PLACES_URL + "?" + urllib.parse.urlencode({"$select": ",".join(FIELDS), "$where": f"stateabbr='{abbr}'",
                                                            "$order": "tractfips", "$limit": 20000})
        state_rows = json.loads(download(name, url))
        if not state_rows:
            raise ValueError(f"No CDC PLACES tracts for {abbr}")
        rows.extend(state_rows)
        manifest[name] = source(name, url)
        print(f"CDC PLACES {abbr}: {len(state_rows)}", flush=True)
    return rows, manifest


def acs(table):
    name = f"acs-2024-{table.lower()}-us-tract.dat"
    path = SNAP / name
    url = ACS_URL + f"acsdt5y2024-{table.lower()}.dat"
    if not path.is_file():
        req = urllib.request.Request(url, headers={"User-Agent": "ReachCare/1.0 (public aggregate research)"})
        partial = path.with_suffix(".part")
        with urllib.request.urlopen(req, timeout=240) as response, partial.open("wb") as out:
            out.write(response.readline())
            for row in response:
                if row.startswith(b"1400000US") and row[9:11].decode() in FIPS:
                    out.write(row)
        partial.replace(path)
    raw = path.read_bytes()
    rows = {row["GEO_ID"].split("US")[1]: row for row in csv.DictReader(io.StringIO(raw.decode()), delimiter="|")}
    info = source(name, url)
    info["subset"] = "Original header and 50-state plus DC census tract rows"
    print(f"ACS {table}: {len(rows)} tracts", flush=True)
    return rows, {name: info}


def libraries(county_names):
    name = "pls-fy2024.zip"
    url = "https://www.imls.gov/sites/default/files/2026-06/pls_fy2024_csv.zip"
    archive = zipfile.ZipFile(io.BytesIO(download(name, url)))
    outlet_name = next(n for n in archive.namelist() if "outlet" in n.lower() and n.lower().endswith(".csv"))
    raw = archive.read(outlet_name)
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = raw.decode("cp1252")
    venues = []
    for row in csv.DictReader(io.StringIO(decoded)):
        abbr = row.get("STABR")
        if abbr not in STATES or row.get("C_OUT_TY") not in {"CE", "BR"}:
            continue
        lat, lon = number(row.get("LATITUDE")), row.get("LONGITUD")
        try:
            lon = float(lon)
        except (ValueError, TypeError):
            continue
        if lat is None or not math.isfinite(lon) or not 17 < lat < 72 or not -180 < lon < -64:
            continue
        county = (row.get("CNTY") or "").title()
        county_id = next((k for k, v in county_names.items() if k.startswith(STATES[abbr]) and v.lower() == county.lower()), None)
        venues.append({"id": "lib-" + row["FSCSKEY"] + "-" + row["FSCS_SEQ"], "name": row["LIBNAME"].title(),
                       "address": row["ADDRESS"].title(), "city": row["CITY"].title(), "county": county,
                       "county_id": county_id, "state": abbr, "lat": lat, "lon": lon, "type": "Public library",
                       "source": "imls_pls_2024", "source_year": 2024, "verification": "discovered"})
    selected = []
    for abbr in STATES:
        selected.extend(select_spread([v for v in venues if v["state"] == abbr], 30))
    print(f"Libraries: {len(venues)} outlets; {len(selected)} planning candidates", flush=True)
    return venues, selected, {name: source(name, url)}


def health_centers():
    sites = []
    manifest = {}
    fields = "OBJECTID,SITE_NM,SITE_ADDRESS,SITE_CITY,SITE_STATE_ABBR,SITE_ZIP_CD,STATE_COUNTY_FIPS_CD,HCC_STATUS_DESC,HCC_TYP_DESC"
    for offset in range(0, 20000, 2000):
        name = f"hrsa-sites-{offset:05}.json"
        url = HRSA_URL + "?" + urllib.parse.urlencode({"where": "1=1", "outFields": fields, "returnGeometry": "true",
                                                       "outSR": 4326, "orderByFields": "OBJECTID", "resultOffset": offset,
                                                       "resultRecordCount": 2000, "f": "json"})
        body = json.loads(download(name, url))
        if "error" in body:
            raise ValueError(body["error"])
        features = body.get("features", [])
        manifest[name] = source(name, url)
        for feature in features:
            row, point = feature["attributes"], feature.get("geometry") or {}
            if row.get("SITE_STATE_ABBR") not in STATES or point.get("x") is None or point.get("y") is None:
                continue
            sites.append({"id": "hrsa-" + str(row["OBJECTID"]), "name": row.get("SITE_NM") or "Health center",
                          "type": row.get("HCC_TYP_DESC") or "Health center", "address": ", ".join(filter(None, [row.get("SITE_ADDRESS"), row.get("SITE_CITY"), row.get("SITE_STATE_ABBR"), row.get("SITE_ZIP_CD")])),
                          "county_id": row.get("STATE_COUNTY_FIPS_CD"), "state": row["SITE_STATE_ABBR"],
                          "lat": point["y"], "lon": point["x"], "source": "hrsa_health_center_sites",
                          "service_verified": False, "eligibility_verified": False, "schedule_verified": False, "verified_at": None})
        print(f"HRSA {offset}: {len(features)}", flush=True)
        if len(features) < 2000:
            break
    return sites, manifest


def build():
    DATA.mkdir(exist_ok=True)
    SNAP.mkdir(exist_ok=True)
    places_rows, manifest = places()
    population, info = acs("B01001"); manifest.update(info)
    transport, info = acs("B08201"); manifest.update(info)
    counties = {r["countyfips"]: r["countyname"] for r in places_rows}
    tracts = []
    issues = []
    for row in places_rows:
        tract_id = row["tractfips"]
        pop, vehicle = population.get(tract_id), transport.get(tract_id)
        under18 = [3, 4, 5, 6, 27, 28, 29, 30]
        total = number(pop.get("B01001_E001")) if pop else None
        under = [number(pop.get(f"B01001_E{i:03}")) for i in under18] if pop else []
        eligible = total - sum(under) if total is not None and all(v is not None for v in under) else None
        moes = [number(pop.get(f"B01001_M{i:03}")) for i in [1] + under18] if pop else []
        population_moe = math.sqrt(sum(v * v for v in moes)) if moes and all(v is not None for v in moes) else None
        households = number(vehicle.get("B08201_E001")) if vehicle else None
        no_vehicle = number(vehicle.get("B08201_E002")) if vehicle else None
        barrier = 100 * no_vehicle / households if households and no_vehicle is not None else None
        num_moe = number(vehicle.get("B08201_M002")) if vehicle else None
        den_moe = number(vehicle.get("B08201_M001")) if vehicle else None
        barrier_moe = 100 * math.sqrt(num_moe ** 2 + (no_vehicle / households) ** 2 * den_moe ** 2) / households if households and no_vehicle is not None and num_moe is not None and den_moe is not None else None
        point = (row.get("geolocation") or {}).get("coordinates")
        if not point:
            issues.append({"tract_id": tract_id, "issue": "Missing representative point"})
            continue
        indicators = {}
        for indicator in ("bphigh", "diabetes", "copd", "casthma"):
            import re
            ci = re.findall(r"[\d.]+", row.get(f"{indicator}_crude95ci") or "")
            indicators[indicator] = {"value": number(row.get(f"{indicator}_crudeprev")),
                                     "lower": float(ci[0]) if len(ci) == 2 else None,
                                     "upper": float(ci[1]) if len(ci) == 2 else None, "source": "cdc_places_2025"}
        flags = [] if eligible is not None and barrier is not None else ["Missing ACS measure; excluded from scored coverage"]
        if flags:
            issues.append({"tract_id": tract_id, "issue": flags[0]})
        tracts.append({"id": tract_id, "state": row["stateabbr"], "county_id": row["countyfips"],
                       "county": row["countyname"], "lat": point[1], "lon": point[0],
                       "eligible_population": round(eligible) if eligible is not None else None,
                       "population_moe": population_moe, "population_source": "acs_2024_B01001",
                       "no_vehicle_pct": barrier, "no_vehicle_moe": barrier_moe, "households": households,
                       "transport_source": "acs_2024_B08201", "indicators": indicators, "flags": flags})
    venues, selected, info = libraries(counties); manifest.update(info)
    care, info = health_centers(); manifest.update(info)
    sources = [
        {"id": "cdc_places_2025", "title": "CDC PLACES 2025", "url": PLACES_URL, "fields": "Tract health indicators and 95% intervals", "quality": "Modeled aggregate estimates"},
        {"id": "acs_2024_B01001", "title": "ACS 2024 five-year population", "url": ACS_URL + "acsdt5y2024-b01001.dat", "fields": "Adults 18 and older, with approximate MOE", "quality": "Survey estimates"},
        {"id": "acs_2024_B08201", "title": "ACS 2024 five-year vehicle access", "url": ACS_URL + "acsdt5y2024-b08201.dat", "fields": "Households without a vehicle, with approximate MOE", "quality": "Survey estimates"},
        {"id": "imls_pls_2024", "title": "IMLS Public Libraries Survey FY2024", "url": "https://www.imls.gov/research-evaluation/surveys/public-libraries-survey-pls", "fields": "Public library locations", "quality": "Candidate hosts; facilities unverified"},
        {"id": "hrsa_health_center_sites", "title": "HRSA Health Center Service Delivery Sites", "url": HRSA_URL, "fields": "Health center locations", "quality": "Service, eligibility, and hours require verification"},
    ]
    state = {"tracts": tracts, "venues": selected, "venue_directory": venues, "existing_services": care,
             "counties": counties, "states": STATES, "sources": sources, "quality_issues": issues,
             "candidate_selection": "30 geographically distributed library candidates per state and DC; full directory retained",
             "created_at": datetime.now(timezone.utc).isoformat()}
    (DATA / "us.json").write_text(json.dumps(state, separators=(",", ":")))
    (DATA / "us-manifest.json").write_text(json.dumps(manifest, indent=2))
    counts = Counter(t["state"] for t in tracts)
    print(f"Prepared {len(tracts)} tracts across 50 states and DC, {len(counties)} counties, {len(venues)} libraries, {len(care)} health centers; {len(issues)} quality flags", flush=True)


if __name__ == "__main__":
    build()
