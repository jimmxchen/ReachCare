# Rural Outreach Planner

A HackNC MVP for planning blood-pressure screening across **all 100 North Carolina counties**. It ranks feasible venues by need-weighted improvement in road-time access, tests ranking sensitivity, and preserves each planning run for review.

## Run locally

Python 3.11 or newer and NumPy are required. The prepared public dataset and routing matrix are included; no API key or frontend build is needed.

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 server.py --live-feeds
```

Open **http://127.0.0.1:8000**. Without `--live-feeds`, public conditions can still be refreshed manually. Runs, confirmations, observations, imports, and outcomes are stored in `.runtime/planner.sqlite3`, which is excluded from Git. Keep that directory to retain your work.

NWS needs no API key. NCDOT's previous TIMS endpoint was retired in May 2026. Its replacement [DriveNC events API](https://drivenc.gov/help/endpoint/event) requires a developer key. Configure `DRIVENC_API_KEY` in your local environment before starting the server; never put it in a CSV or commit it. If no key is configured, the interface explicitly marks road events unavailable. The key is omitted from saved source URLs, exports, and error messages.

For an independent preview or evaluation database:

```sh
python3 server.py --port 8001 --runtime-dir /private/tmp/hacknc-preview
```

## Planning workflow

1. Choose statewide or county scope, dates, travel target, event count, budget, staff, equipment, and policy weights.
2. Explore **Sample operations** to see ranked plans. The community indicators, geography, addresses, and road times are public data; host permissions, service verification, dates, costs, and station throughput are explicitly labeled samples.
3. For a real campaign, select **Planner-confirmed operations**. In **Venues & care**, enter or import dated host and venue confirmations, then verify existing blood-pressure services, eligibility, and a schedule covering the campaign window. Unknown requirements exclude a venue. Unknown baseline access is excluded from confirmed access-gain metrics.
4. Compare score components, newly covered tracts and adult population, capacity, costs, simulation frequencies, and regret. Select multiple events to build a schedule with unique coverage, a shared budget, and separate dates for one vehicle.
5. Review **Live conditions**. Refreshing conditions creates a new version; previous inputs remain inspectable in **Run history**. Download an hourly NWS forecast for a venue when needed.
6. Export the complete JSON inputs and results, or an HTML event brief that can be printed to PDF. Record aggregate event outcomes after delivery; past recommendations remain unchanged.

CSV templates for venue confirmations, existing-service verification, and historical series are available in the interface. Imports validate all rows before saving. Only the prepared venue and care IDs are accepted; adding coordinates requires rebuilding the routing matrix.

## Included statewide data

Prepared on October 10, 2026:

| Input | Coverage and vintage |
| --- | --- |
| CDC PLACES | 2025 release; 2,647 NC tracts across 100 counties; blood-pressure estimates and confidence intervals |
| Census ACS | 2024 five-year B01001 adult population and B08201 no-vehicle households, with margins of error |
| Census geography | 2023 cartographic tract boundaries and representative coordinates |
| IMLS library outlets | FY2024; 397 fixed library outlets; 30 candidates selected deterministically for geographic spread |
| NCDHHS safety-net directory | March 2026; 27 geocoded discovery records for baseline-service review |
| OSRM driving matrix | 2,647 tracts × 57 destinations; 150,879 cached journeys; no missing routes in this snapshot |

Ten tracts lack required joined population or transport inputs and are excluded from scoring and simulation. Three care addresses could not be geocoded and are flagged. Source URLs, release dates, retrieval dates, join decisions, and checksums are available in **Data & methods**, `data/nc.json`, and `data/manifest.json`.

The 30-venue shortlist and 27 care records are **not an exhaustive statewide inventory**. All state tracts are evaluated against that shortlist. Confirmed deployment requires a reviewed inventory of relevant care and local venue inputs.

## How scores work

The first service is screening for adults aged 18 and older. Modeled diagnosed hypertension is a geographic priority proxy; it does not estimate undiagnosed patients or turnout.

```text
health_index = clip((high_blood_pressure_percent - 15) / 40, 0, 1)
barrier_index = clip(no_vehicle_household_percent / 20, 0, 1)
priority = health_weight × health_index + (1 - health_weight) × barrier_index
tract_weight = eligible_adult_population × priority

new_access = verified_baseline_minutes > target
             AND candidate_minutes <= target
score = sum(tract_weight for tracts with new_access)
```

Fixed benchmark ranges do not change with the venue list. Missing indicators and unknown baseline or route times never become zero. Exact score ties use lower cost, then venue ID. Capacity is the minimum of staff × services per hour × clinical hours, equipment throughput, and clinical-station throughput. Geographic reach, capacity, and observed turnout are separate quantities.

Multi-event selection greedily adds unique weighted coverage within budget and available dates. It is an approximation, not a guarantee of the statewide optimum. The test suite compares it with an exact solution on a small case.

Each run uses at least 1,000 seeded scenarios and a second, broader weight-concentration scenario set. Simulations vary bounded Dirichlet policy weights, uncertainty derived from CDC intervals and ACS margins of error, shared county shocks, and road times. Confirmed feasibility and costs remain fixed. Results include first-place and top-N frequency, median rank, average and worst-case regret, and broader-scenario frequency. Frequency describes ranking sensitivity, not attendance probability. A top-N stability badge is suppressed when N cannot distinguish the candidate set.

No blood-pressure demand time series is bundled. Trend adjustment defaults to zero. A versioned, service-matched county or tract CSV can supply a reviewed comparison baseline; valid adjustments are bounded to ±20%. Suppressed, sparse, stale, or unrelated series do not change blood-pressure priorities.

## Live conditions and limits

- NWS alerts and the configured DriveNC / NCDOT feed are polled every five minutes with `--live-feeds`. Failed refreshes retain the last successful observation with a visible stale warning. DriveNC returns published traffic events; their reported dates need review before applying them to an event journey.
- Incidents are review warnings. The cached OSRM matrix is not closure-aware and does not model real-time detours, traffic, or vehicle travel from a hospital depot.
- Weather checks match warning county and North Carolina calendar dates. Tornado-warning date exclusion is an explicit optional policy. Alerts otherwise prompt review; stale alerts never silently establish safe conditions.
- Tract representative-point journeys approximate residents' travel. ACS population and vehicle measures, PLACES health estimates, and tract boundaries have different vintages; provenance and exclusions are retained.
- Costs, throughput, and staff rates require operational confirmation. The model does not approve, publish, or book an event.
- This is a local prototype with no user authentication. It stores aggregate information; do not import patient-level records. A hospital deployment needs approved data handling, access controls, clinical workflow review, and closure-aware routing.

## Verify and reproduce

```sh
python3 -m unittest discover -s tests -v
node --check web/app.js
python3 scripts/reproduce.py /path/to/exported-run.json
```

The export contains the exact tract inputs, service/venue verification, routing matrix, weather policy and matched blocks, historical observations, seed, model version, NumPy version, and input hash. Reproduction checks rankings, coverage, capacity, the schedule, and sensitivity statistics against the saved result. Python 3.13.7 and NumPy 2.3.2 were used during development.

Tests cover the PRD worked example, missing data and routes, hard exclusions, unique multi-event coverage and budget, deterministic seeds, exact export reproduction, immutable versions and outcomes, stale feeds, service-matched trends, weather matching, CSV atomicity, interface defaults, and statewide routing alignment. The 1,000- versus 5,000-draw check differed by at most 1.04 percentage points for the default leading three sites.

## Refresh public baseline data

```sh
python3 scripts/prepare_data.py --skip-routes
python3 scripts/prepare_data.py --routes-only
```

Downloads are cached in `data/snapshots`; remove or archive a specific cached source before intentionally fetching a new release. The routing step reuses a column only when destination IDs, coordinates, and tract order match. Public OSRM requests are throttled. For larger inventories, supply an institution-managed OSRM endpoint with `--routing-url` and review its capacity and routing assumptions. Restart the server after rebuilding data.

## Code map and next services

| File | Responsibility |
| --- | --- |
| `planner/model.py` | Service catalog, validation, deterministic scores, capacity, schedules, simulations |
| `planner/store.py` | SQLite persistence, immutable runs, feed observations, outcomes |
| `planner/feeds.py` | NWS/NCDOT snapshots, hourly forecasts, declared weather policy |
| `server.py` | Local HTTP API, CSV validation, input snapshots and version comparison |
| `web/` | Map, campaign configuration, comparison, verification, exports and outcomes |
| `scripts/prepare_data.py` | Public-data preparation, provenance and cached road routing |
| `scripts/reproduce.py` | Independent numeric replay of an exported run |

Blood-pressure screening is the only selectable service in this release. Adding a service begins with `SERVICES` in `planner/model.py`: define its eligible population, defensible indicator, fixed benchmarks, staff and equipment requirements, and verified service inventory. Update population eligibility, intake fields, operational rules, and tests before exposing it. Additional disease indicators in the source data do not by themselves establish a supported service.

The implementation follows `PRD.md` with the requested statewide scope. ChatGPT's HackNC project was located in the browser, but its conversation history was not accessible through the available connector or UI; no unseen conversation content was assumed.
