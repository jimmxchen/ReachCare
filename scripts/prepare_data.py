"""Download and preserve public North Carolina inputs. No credentials required."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import struct
import time
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SNAP = DATA / "snapshots"
SOURCES = {}


def fetch(name, url):
    path = SNAP / name
    if not path.exists():
        print(f"Downloading {name}", flush=True)
        request = urllib.request.Request(url, headers={"User-Agent": "HackNC-Rural-Outreach-Planner/1.0 (public aggregate research)"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    raw = response.read()
                path.write_bytes(raw)
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
    raw = path.read_bytes()
    SOURCES[name] = {"url": url, "retrieved_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "sha256": hashlib.sha256(raw).hexdigest()}
    return raw


def acs_table(table):
    name = f"acs-2024-{table.lower()}-nc.dat"
    url = f"https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-{table.lower()}.dat"
    path = SNAP / name
    if not path.exists():
        print(f"Streaming ACS {table}; preserving original NC tract rows", flush=True)
        with urllib.request.urlopen(url, timeout=180) as response, path.with_suffix(".part").open("wb") as out:
            out.write(response.readline())
            for row in response:
                if row.startswith(b"1400000US37"):
                    out.write(row)
        path.with_suffix(".part").replace(path)
    raw = path.read_bytes()
    SOURCES[name] = {"url": url, "retrieved_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "sha256": hashlib.sha256(raw).hexdigest(), "subset": "Original header and NC census tract rows; no values transformed"}
    return {row["GEO_ID"].split("US")[1]: row for row in csv.DictReader(io.StringIO(raw.decode()), delimiter="|")}


def shape_features(raw):
    """Read Census polygon SHP and DBF without a GIS dependency."""
    archive = zipfile.ZipFile(io.BytesIO(raw))
    dbf = archive.read(next(n for n in archive.namelist() if n.endswith(".dbf")))
    count, header_size, record_size = struct.unpack_from("<IHH", dbf, 4)
    fields = []
    offset = 32
    while dbf[offset] != 13:
        field = dbf[offset:offset + 32]
        fields.append((field[:11].split(b"\0")[0].decode(), field[16]))
        offset += 32
    attributes = []
    for index in range(count):
        start = header_size + index * record_size + 1
        row = {}
        for name, length in fields:
            row[name] = dbf[start:start + length].decode("latin1").strip()
            start += length
        attributes.append(row)
    shp = archive.read(next(n for n in archive.namelist() if n.endswith(".shp")))
    offset = 100
    features = []
    for row in attributes:
        length = struct.unpack_from(">I", shp, offset + 4)[0] * 2
        content = shp[offset + 8:offset + 8 + length]
        offset += length + 8
        if row.get("STATEFP") != "37" or len(content) < 44:
            continue
        parts, points = struct.unpack_from("<II", content, 36)
        starts = list(struct.unpack_from(f"<{parts}I", content, 44)) + [points]
        xy = struct.unpack_from(f"<{points * 2}d", content, 44 + parts * 4)
        rings = []
        for p in range(parts):
            ring = [[round(xy[j * 2], 4), round(xy[j * 2 + 1], 4)] for j in range(starts[p], starts[p + 1])]
            # Rings are retained in their supplied order; no synthetic geometry.
            ring = [v for j, v in enumerate(ring) if j == 0 or v != ring[j - 1]]
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            if len(ring) >= 4:
                rings.append(ring)
        features.append({"type": "Feature", "properties": {"id": row["GEOID"], "name": row.get("NAME"), "county_id": row.get("COUNTYFP")}, "geometry": {"type": "Polygon", "coordinates": rings}})
    return features


def number(value):
    try:
        parsed = float(value)
        return parsed if parsed >= 0 and math.isfinite(parsed) else None
    except (ValueError, TypeError):
        return None


def select_spread(items, n):
    if len(items) <= n:
        return items
    remaining = sorted(items, key=lambda v: v["id"])
    selected = [remaining.pop(0)]
    while remaining and len(selected) < n:
        item = max(remaining, key=lambda v: min((v["lon"] - s["lon"]) ** 2 + (v["lat"] - s["lat"]) ** 2 for s in selected))
        selected.append(item)
        remaining.remove(item)
    return selected


def prepare():
    DATA.mkdir(exist_ok=True)
    SNAP.mkdir(exist_ok=True)
    tasks = {
        "places": lambda: json.loads(fetch("places-nc-2025.json", "https://data.cdc.gov/resource/yjkw-uj5s.json?" + urllib.parse.urlencode({"$where": "stateabbr='NC'", "$limit": 5000, "$order": "tractfips"}))),
        "population": lambda: acs_table("B01001"),
        "transport": lambda: acs_table("B08201"),
        "shapes": lambda: shape_features(fetch("cb_2023_37_tract_500k.zip", "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_37_tract_500k.zip")),
        "libraries": lambda: fetch("pls-fy2024.zip", "https://www.imls.gov/sites/default/files/2026-06/pls_fy2024_csv.zip"),
        "care": lambda: fetch("safety-net-2026-03.csv", "https://files.nc.gov/dhhs/2026-03/SafetyNet_2026_03_31.csv?VersionId=jOUztrpAyG3ZQeTrs91c5HA8dp.B0zfq"),
    }
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {name: pool.submit(task) for name, task in tasks.items()}
        results = {name: job.result() for name, job in jobs.items()}
    fetch("places-metadata.json", "https://data.cdc.gov/api/views/yjkw-uj5s.json")
    tract_shapes = {f["properties"]["id"]: f for f in results["shapes"]}
    tracts = []
    issues = []
    county_names = {}
    for row in results["places"]:
        tract_id = row["tractfips"]
        county_names[row["countyfips"]] = row["countyname"]
        pop = results["population"].get(tract_id)
        vehicle = results["transport"].get(tract_id)
        flags = []
        if not pop or not vehicle or tract_id not in tract_shapes:
            issues.append({"tract_id": tract_id, "issue": "Unmatched population, transport, or 2023 boundary"})
        under18_indices = [3, 4, 5, 6, 27, 28, 29, 30]
        if pop:
            total = number(pop["B01001_E001"])
            under = [number(pop[f"B01001_E{i:03}"]) for i in under18_indices]
            eligible = total - sum(under) if total is not None and all(v is not None for v in under) else None
            moes = [number(pop[f"B01001_M{i:03}"]) for i in [1] + under18_indices]
            population_moe = math.sqrt(sum(v * v for v in moes)) if all(v is not None for v in moes) else None
        else:
            eligible = population_moe = None
        households = number(vehicle["B08201_E001"]) if vehicle else None
        no_vehicle = number(vehicle["B08201_E002"]) if vehicle else None
        barrier = 100 * no_vehicle / households if households and no_vehicle is not None else None
        # Conservative ratio MOE approximation; covariance unavailable.
        numerator_moe = number(vehicle["B08201_M002"]) if vehicle else None
        denominator_moe = number(vehicle["B08201_M001"]) if vehicle else None
        barrier_moe = 100 * math.sqrt(numerator_moe ** 2 + (no_vehicle / households) ** 2 * denominator_moe ** 2) / households if households and no_vehicle is not None and numerator_moe is not None and denominator_moe is not None else None
        if eligible is None or barrier is None:
            flags.append("Missing ACS measure; excluded from scored coverage")
        indicators = {}
        for key in ["bphigh", "diabetes", "copd", "casthma"]:
            ci = re.findall(r"[\d.]+", row.get(f"{key}_crude95ci", ""))
            indicators[key] = {"value": number(row.get(f"{key}_crudeprev")), "lower": float(ci[0]) if len(ci) == 2 else None, "upper": float(ci[1]) if len(ci) == 2 else None, "source": "cdc_places_2025"}
        point = row.get("geolocation", {}).get("coordinates")
        if not point:
            issues.append({"tract_id": tract_id, "issue": "Missing representative point"})
            continue
        tracts.append({"id": tract_id, "county_id": row["countyfips"], "county": row["countyname"], "lat": point[1], "lon": point[0], "eligible_population": round(eligible) if eligible is not None else None, "population_moe": population_moe, "population_source": "acs_2024_B01001", "no_vehicle_pct": barrier, "no_vehicle_moe": barrier_moe, "households": households, "transport_source": "acs_2024_B08201", "indicators": indicators, "flags": flags})
    archive = zipfile.ZipFile(io.BytesIO(results["libraries"]))
    print("Library archive:", archive.namelist(), flush=True)
    outlet_name = next(n for n in archive.namelist() if "outlet" in n.lower() and n.lower().endswith(".csv"))
    outlet_raw = archive.read(outlet_name)
    try:
        outlet_text = outlet_raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        outlet_text = outlet_raw.decode("cp1252")
    rows = list(csv.DictReader(io.StringIO(outlet_text)))
    print("Library columns:", list(rows[0]), flush=True)
    venues = []
    for row in rows:
        if row.get("STABR") != "NC" or row.get("C_OUT_TY") not in ["CE", "BR"]:
            continue
        lat, lon = number(row.get("LATITUDE")), row.get("LONGITUD") or row.get("LONGITUDE")
        try:
            lon = float(lon)
        except (ValueError, TypeError):
            continue
        if not lat or not -85 < lon < -75 or not 33 < lat < 37:
            continue
        county = row.get("CNTY", row.get("COUNTY", "")).title()
        county_id = next((key for key, value in county_names.items() if value.lower() == county.lower()), None)
        venues.append({"id": "lib-" + row.get("FSCSKEY", "") + "-" + row.get("FSCS_SEQ", str(len(venues))), "name": row.get("LIBNAME", "Library").title(), "address": row.get("ADDRESS", "").title(), "city": row.get("CITY", "").title(), "county": county, "county_id": county_id, "lat": lat, "lon": lon, "type": "Public library", "source": "imls_pls_2024", "source_year": 2024, "verification": "discovered"})
    # Keep a statewide candidate shortlist geographically distributed across NC.
    selected = select_spread(venues, 30)
    care_rows = list(csv.DictReader(io.StringIO(results["care"].decode("cp1252"))))
    care_candidates = [r for r in care_rows if r["Site Type"] in ["Health Department", "Federally Qualified Health Center", "Rural Health Center"]]
    care = []
    # One seed per selected venue's county; addresses remain unverified for service.
    used = set()
    for venue in selected:
        matches = [r for r in care_candidates if r["County"].lower() == venue["county"].lower()]
        if not matches:
            continue
        row = matches[0]
        address = f'{row["Street Address"]}, {row["City"]}, NC {row["Zip Code"][:5]}'
        if address in used:
            continue
        used.add(address)
        url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?" + urllib.parse.urlencode({"address": address, "benchmark": "Public_AR_Current", "format": "json"})
        cache = "geocode-" + hashlib.sha256(address.encode()).hexdigest()[:12] + ".json"
        try:
            matched = json.loads(fetch(cache, url))["result"]["addressMatches"]
            if not matched:
                issues.append({"site": row["Site Name"], "issue": "Address not geocoded; omitted from routed baseline candidates"})
                continue
            p = matched[0]["coordinates"]
            care.append({"id": f"care-{len(care):03}", "name": row["Site Name"], "type": row["Site Type"], "address": address, "county": row["County"], "lat": p["y"], "lon": p["x"], "source": "nc_safety_net_2026", "service_verified": False, "eligibility_verified": False, "schedule_verified": False, "verified_at": None})
        except Exception as exc:
            issues.append({"site": row["Site Name"], "issue": "Geocoding failed: " + str(exc)})
    sources = [
        {"id": "cdc_places_2025", "title": "CDC PLACES · 2025 release", "year": "2023 BRFSS; modeled tract estimates", "url": "https://data.cdc.gov/500-Cities-Places/PLACES-Census-Tract-Data-GIS-Friendly-Format-2025-/yjkw-uj5s", "fields": "Health indicators and their 95% intervals", "quality": "Modeled aggregate estimates"},
        {"id": "acs_2024_B01001", "title": "Census ACS · 2020–2024", "year": "2024 five-year release", "url": SOURCES["acs-2024-b01001-nc.dat"]["url"], "fields": "Adult population: total less under-18 age groups; root-sum-square MOE approximation", "quality": "Survey estimates; 90% margins of error"},
        {"id": "acs_2024_B08201", "title": "Census ACS · vehicle access", "year": "2024 five-year release", "url": SOURCES["acs-2024-b08201-nc.dat"]["url"], "fields": "B08201 E002 / E001: households without a vehicle; ratio MOE approximation", "quality": "Household proxy for transport barriers"},
        {"id": "census_boundaries_2023", "title": "Census tract boundaries", "year": "2023 cartographic file; 2020 tract geography", "url": "https://www.census.gov/geographies/mapping-files/2023/geo/carto-boundary-file.html", "fields": "Tract boundaries; CDC geometric centroid for routing", "quality": "Centroid travel estimates; not population-weighted"},
        {"id": "imls_pls_2024", "title": "IMLS Public Libraries Survey", "year": "FY 2024; released June 2026", "url": "https://www.imls.gov/research-evaluation/surveys/public-libraries-survey-pls", "fields": "Public library names, addresses, coordinates", "quality": "Discovered candidates; host and facilities unverified"},
        {"id": "nc_safety_net_2026", "title": "NCDHHS Safety Net Sites", "year": "March 2026 directory", "url": "https://www.ncdhhs.gov/divisions/office-rural-health/safety-net-resources/safety-net-sites", "fields": "Care locations; geocoded with Census address service", "quality": "Service, eligibility, and hours require planner verification"},
    ]
    state = {"tracts": tracts, "venues": selected, "venue_directory": venues, "existing_services": care, "counties": county_names, "sources": sources, "quality_issues": issues, "candidate_selection": "30 libraries selected by deterministic geographic spread across the entire state; not an exhaustive venue search", "created_at": datetime.now(timezone.utc).isoformat()}
    (DATA / "nc.json").write_text(json.dumps(state, separators=(",", ":")))
    (DATA / "geography.json").write_text(json.dumps({"type": "FeatureCollection", "features": results["shapes"]}, separators=(",", ":")))
    (DATA / "manifest.json").write_text(json.dumps(SOURCES, indent=2))
    print(f"Prepared {len(tracts)} tracts, {len(county_names)} counties, {len(selected)} venues, {len(care)} baseline candidates; {len(issues)} join/geocoding flags", flush=True)


def routes(base):
    state = json.loads((DATA / "nc.json").read_text())
    tracts = state["tracts"]
    destinations = state["venues"] + state["existing_services"]
    matrix = np.full((len(tracts), len(destinations)), np.nan)
    cached_columns = set()
    old_path = DATA / "routes.npz"
    if old_path.exists():
        old = np.load(old_path)
        if "site_locations" in old.files and list(old["tract_ids"]) == [t["id"] for t in tracts]:
            lookup = {site_id: i for i, site_id in enumerate(old["site_ids"])}
            for column, site in enumerate(destinations):
                previous = lookup.get(site["id"])
                if previous is not None and np.array_equal(old["site_locations"][previous], [site["lon"], site["lat"]]):
                    matrix[:, column] = old["minutes"][:, previous]
                    if np.isfinite(matrix[:, column]).all():
                        cached_columns.add(column)
    missing_columns = [i for i in range(len(destinations)) if i not in cached_columns]
    print(f"Reusing {len(cached_columns)} unchanged destination columns; routing {len(missing_columns)} new columns", flush=True)
    route_cache = SNAP / "routes"
    route_cache.mkdir(exist_ok=True)
    for start in range(0, len(tracts), 65):
        batch = tracts[start:start + 65]
        for end in range(0, len(missing_columns), 30):
            target_columns = missing_columns[end:end + 30]
            targets = [destinations[i] for i in target_columns]
            coordinates = ";".join(f'{p["lon"]:.6f},{p["lat"]:.6f}' for p in batch + targets)
            query = urllib.parse.urlencode({"sources": ";".join(map(str, range(len(batch)))), "destinations": ";".join(map(str, range(len(batch), len(batch) + len(targets))))})
            url = f"{base.rstrip('/')}/table/v1/driving/{coordinates}?{query}"
            path = route_cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
            if path.exists():
                body = json.loads(path.read_text())
            else:
                last_error = None
                for attempt in range(4):
                    try:
                        with urllib.request.urlopen(url, timeout=90) as response:
                            body = json.load(response)
                        if body.get("code") != "Ok":
                            raise ValueError(body)
                        path.write_text(json.dumps(body))
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                        time.sleep(2 ** attempt)
                if last_error:
                    print(f"Route batch {start}/{end} failed: {last_error}; retaining unknown routes", flush=True)
                    continue
                time.sleep(1.05)  # Respect the public demo server's request limit.
            values = np.array([[np.nan if v is None else v / 60 for v in row] for row in body["durations"]])
            matrix[np.ix_(range(start, start + len(batch)), target_columns)] = values
        print(f"Road matrix {min(start + 65, len(tracts))}/{len(tracts)}", flush=True)
    np.savez_compressed(DATA / "routes.npz", minutes=matrix, tract_ids=np.array([t["id"] for t in tracts]), site_ids=np.array([s["id"] for s in destinations]), site_locations=np.array([[s["lon"], s["lat"]] for s in destinations]))
    metadata = {"source": base, "profile": "driving", "retrieved_at": datetime.now(timezone.utc).isoformat(), "tract_point": "CDC geometric centroid; approximate resident journeys", "live_closure_aware": False, "unknown_routes": int(np.isnan(matrix).sum()), "shape": list(matrix.shape), "sha256": hashlib.sha256((DATA / "routes.npz").read_bytes()).hexdigest()}
    (DATA / "routing-metadata.json").write_text(json.dumps(metadata, indent=2))
    print("Routing complete:", metadata, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--routes-only", action="store_true")
    parser.add_argument("--skip-routes", action="store_true")
    parser.add_argument("--routing-url", default="https://router.project-osrm.org")
    options = parser.parse_args()
    if not options.routes_only:
        prepare()
    if not options.skip_routes:
        routes(options.routing_url)
