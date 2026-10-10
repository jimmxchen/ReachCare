# Product Requirements Document: Rural Outreach Planner

**Status:** Draft for HackNC  
**Date:** October 10, 2026  
**Primary user:** Hospital and health-system outreach planners  
**Product type:** Decision-support web application

## 1. Product summary

Rural Outreach Planner helps a hospital decide **which service to bring, where to hold it, and when to schedule it** for a mobile clinic or outreach day. It combines public health and population data with verified care locations, potential venues, road travel times, and the hospital's operational constraints. Its central output is a ranked, explainable set of feasible event plans.

The core calculations and rankings are deterministic. A simulation layer tests whether rankings remain stable when reasonable assumptions change. A language model may summarize a completed recommendation, but it does not calculate scores, choose sites, or invent missing facts.

The initial product serves planning teams. Publishing approved events and allowing patient booking are later extensions.

## 2. Problem and opportunity

Rural outreach teams have limited staff, vehicles, time, and funding. Public datasets can identify possible health and travel-access gaps, but they do not directly answer where a specific hospital should place its next event. Existing location lists also do not establish whether a site provides the relevant service or has appointments available. Venue addresses do not establish permission, accessibility, privacy, power, or event capacity.

The planning decision therefore requires three kinds of evidence in one place:

1. **Community indicators:** where relevant need and access barriers may be concentrated.
2. **Service and travel geography:** what relevant care already exists and how long residents might travel.
3. **Operational feasibility:** which venues, dates, staff, and equipment the hospital can actually use.

North Carolina's 2026 NC ROOTS request for applications calls for regional gap assessments, coordination with existing mobile units, and outreach for high-priority chronic-disease and cancer services. This provides a concrete local use case for the product. [Source: NCDHHS NC ROOTS RFA](https://www.ncdhhs.gov/rfa-2026-ncroots/download)

## 3. Goals and success measures

### Product goals

- Give planners a short list of **feasible site–date–service plans**, rather than only a map of need.
- Improve access to a chosen service for high-priority communities within a specified road-travel-time target.
- Make every ranking reproducible and understandable, including its data sources, weights, hard constraints, and uncertainty.
- Help teams compare one-event and multi-event schedules without counting the same community's coverage more than once.
- Capture actual event results so later planning can be tested against outcomes.

### MVP success criteria

- A planner can configure one service and one geographic region, upload or edit operational inputs, and receive the top three feasible plans.
- Every recommended plan shows its score breakdown, newly covered tracts, approximate newly covered population, event capacity, and unverified assumptions.
- Re-running the same data and configuration with the same simulation seed produces the same results.
- A venue that violates a hard requirement is never presented as schedulable.
- The interface separates **geographic reach**, **service capacity**, and **expected turnout**. It does not present a turnout estimate until sufficient historical event data are available.

### Pilot outcome measures

After a real deployment, evaluate actual events using turnout, services delivered, referrals made, follow-ups completed, staff-hours per service, and how forecast rankings compare with observed results. Geographic reach is a planning metric; these observed measures determine whether the tool improves outreach.

## 4. Users and jobs

| User | Main job | Needed decision |
| --- | --- | --- |
| Hospital outreach manager | Allocate a mobile unit and staff across communities | Which event plan best meets our access goal within budget? |
| Clinical operations lead | Check staffing, equipment, workflow, and capacity | Can we deliver this service safely at this venue and time? |
| Community partner or venue host | Confirm local suitability and availability | Can we host this event, and what conditions apply? |
| Analyst or program evaluator | Review methods and results | Which inputs drove the ranking, and did the event perform as expected? |

## 5. Scope

### Hackathon MVP

- One North Carolina region, one selected outreach service, and roughly 15–30 candidate venues.
- Public, aggregate data for community indicators and initial care/venue discovery.
- A hospital-provided spreadsheet or form for venue verification and event resources; sample operational data may be used for a demonstration.
- Road-time access calculation, deterministic ranking, uncertainty simulations, comparison view, and downloadable plan.
- A live operations layer using planner updates, National Weather Service forecasts/alerts, and North Carolina road incidents; changing inputs can trigger a new, versioned recommendation.
- No patient-level records or patient booking.

### Later phases

- Multiple services and a multi-week calendar with staff and vehicle scheduling.
- Import of de-identified hospital outreach history and service utilization summaries.
- Attendance forecasting after sufficient local outcome data are collected.
- Partner confirmation workflow and approved-event publishing.
- Patient-facing event discovery and booking after an event is approved.

### Outside the MVP

- Clinical diagnosis, patient triage, treatment recommendations, or automated referrals.
- Claims that a public-health estimate represents an identified individual or an exact count of people who will attend.
- Automatically treating every listed clinic as a verified provider of the chosen service.
- Automatically scheduling an event at a discovered venue without host and hospital approval.

## 6. Inputs and data provenance

| Input | Minimum fields | Source and handling |
| --- | --- | --- |
| Campaign | Service, eligible group, planning region, travel-time target, number of events, goal, date window | Planner form; required before ranking |
| Health and community indicators | Census tract ID, service-relevant measure, estimate, interval where available, data year | [CDC PLACES tract data](https://data.cdc.gov/500-Cities-Places/PLACES-Census-Tract-Data-GIS-Friendly-Format-2025-/yjkw-uj5s); modeled estimates, not observed patient counts |
| Population and transport | Tract ID, eligible population estimate, no-vehicle or other selected access measure, margin of error where available | [Census ACS 5-year data](https://api.census.gov/data/2024/acs/acs5.html); match geography and record vintage |
| Geographic boundaries | Tract ID and geometry | [Census tract boundary files](https://www.census.gov/geographies/mapping-files/2023/geo/carto-boundary-file.html) |
| Existing care | Site ID, address, site type, service offered, target-population eligibility, service schedule, verification date | Seed from [NC Safety Net Sites](https://www.ncdhhs.gov/divisions/office-rural-health/safety-net-resources/safety-net-sites); service and schedule must be verified by staff |
| Candidate venues | Venue ID, name, address, coordinates, venue type, host, verification status | Seed from public directories such as [NC DPI's school directory](https://www.dpi.nc.gov/districts-schools/district-operations/financial-and-business-services/demographics-and-finances/eddie) and [IMLS library outlet data](https://www.imls.gov/research-evaluation/surveys/public-libraries-survey-pls), plus hospital/community partners |
| Venue feasibility | Available dates, host approval, accessible entrance, parking, private space, power, connectivity if needed, physical limit, estimated cost | Hospital or host confirmation; unknown values remain unknown |
| Event resources | Staff roles/count, service rate per staff member, clinical hours, setup time, equipment, vehicle availability, total budget | Hospital operations input |
| Historical events (optional) | Site, service, date, staff, appointments, walk-ins, completed services, referrals, follow-ups | Hospital-provided aggregate or de-identified history; not required for MVP |

Each imported field retains a source, release or verification date, and quality flag. Public venue records generate **candidates**. A venue progresses through `discovered → details verified → host approved → schedulable`; only schedulable venues may appear as confirmed event plans.

### Dynamic inputs and refresh cadence

The product combines slow-changing baseline indicators with recent operational and health signals. A live signal affects only a decision it can support; weather, for example, changes event feasibility rather than the estimated prevalence of a chronic condition.

| Dynamic input | Availability and cadence | Decision it can change |
| --- | --- | --- |
| Venue, staff, vehicle, and equipment status | Hospital/host updates as changes occur | Whether a site–date plan is feasible; immediate reranking |
| Pending referrals, waitlist totals, missed follow-ups, and prior event demand by area and service | Hospital partner; aggregate or de-identified, potentially daily | Recent service-specific demand, after geographic quality checks |
| Road closures and incidents | Public [NCDOT TIMS incident API](https://tims.ncdot.gov/tims/V2/webservices), updated as incidents change | Route warning or travel-time recalculation when a compatible live-routing method is available |
| Hourly forecasts and active alerts | Public [National Weather Service API](https://www.weather.gov/documentation/services-web-api) | Outdoor-event suitability and near-term schedule risk |
| Asthma/COPD emergency-department trends | Public [NC DETECT dashboard](https://ncdetect.org/asthma-copd-dashboard/), with monthly trends updated around the 15th | Optional recent context for a relevant respiratory service; not a general chronic-care demand feed |
| Respiratory-virus trends | Public [NC respiratory dashboard](https://www.dph.ncdhhs.gov/programs/epidemiology/communicable-disease/respiratory-diseases/dashboard), updated weekly | Optional seasonal context for relevant services |

Detailed near-real-time NC DETECT records require authorized access and are outside the public-data MVP. Public respiratory trends and weather should not be used as substitutes for service-specific demand for a blood-pressure or diabetes event. Every dynamic value has an observation time, fetch time, and stale-data state.

### Historical series for priority decisions

The application should preserve observations by time period rather than overwrite each feed with its latest value. Useful public series include monthly [NC DETECT asthma/COPD ED visit trends](https://ncdetect.org/asthma-copd-dashboard/) at ZIP, county, and state levels since 2017; downloadable [NC respiratory dashboard data](https://www.dph.ncdhhs.gov/programs/epidemiology/communicable-disease/respiratory-diseases/dashboard/data-behind-dashboards) updated weekly; and [NC overdose surveillance](https://www.dph.ncdhhs.gov/programs/chronic-disease-and-injury/injury-and-violence-prevention-branch/north-carolina-overdose-epidemic-data) updated monthly. These series inform only matching outreach services. Relevant hospital-provided series, such as weekly pending referrals or missed follow-ups by service and area, can inform other services if a partner supplies them.

Public dashboard history does not imply a stable automated API. Verify an export path for the selected pilot series; a versioned CSV import is an acceptable MVP method.

Road and weather feeds primarily support operational history and retrospective evaluation. The [NWS alerts API](https://www.weather.gov/documentation/services-web-api) exposes only a short recent alert window, so the application should save dated snapshots if it needs a longer local history. NCDOT exposes incident queries by active date, but historical availability should be checked before relying on it; save snapshots for reproducible runs. Annual [CDC PLACES releases](https://www.cdc.gov/places/faqs/index.html) are a slow-changing geographic baseline and should not be treated as a monthly demand series.

Store each time-series point with `signal`, `service`, `geography_type`, `geography_id`, `period_start`, `period_end`, `value`, `denominator`, `unit`, `published_at`, `retrieved_at`, `source`, `provisional`, and `revision_id`. Keep original downloaded snapshots so revised provisional data can be distinguished from what a planner knew at the time.

## 7. Core workflow and functional requirements

### FR-1: Configure an outreach campaign

The planner selects a service, region, eligible group, travel-time target, number of events, preferred date window, and available resources. The interface explains which community indicator is used for the chosen service and allows the planner to adjust policy weights within documented bounds.

**Acceptance:** The run cannot start without a service, region, at least one candidate venue, and the resources needed to calculate service capacity.

### FR-2: Ingest and normalize data

The system joins tract-level public data by tract identifier, validates units and vintages, geocodes site addresses, removes duplicate care locations, and stores source metadata. It reports missing values and failed joins. Mobile-unit headquarters or temporary listings must not be assumed to be current service stops.

**Acceptance:** An analyst can inspect the source and vintage of every indicator used in a recommendation; invalid or unmatched records are flagged.

### FR-3: Calculate current and proposed access

For each tract, calculate road travel time to the closest **verified, relevant** existing service and each candidate venue. For the MVP, a tract representative point may be used; the interface labels travel-time estimates as approximate, especially for large rural tracts. A routing matrix, such as the [OSRM table service](https://project-osrm.org/docs/v5.24.0/api/), can be cached to make repeated rankings fast.

**Acceptance:** The user can inspect which existing service establishes each tract's baseline, and can exclude an incorrectly classified service.

### FR-4: Apply feasibility and capacity rules

Hard constraints cover host approval, accessibility, necessary facilities, date availability, staffing, equipment, vehicle availability, and budget. Calculate clinical event capacity from staff, service rate, and clinical hours, limited by any applicable venue or equipment limit. Physical room occupancy is not treated as clinical throughput.

**Acceptance:** The tool explains why each excluded site failed; a discovered but unapproved site can appear in a prospecting view but not in a confirmed schedule.

### FR-5: Rank feasible sites algorithmically

Calculate a transparent, service-specific priority index, road-time access gain, and need-weighted coverage score. Exclude infeasible plans before ranking. For a multi-event plan, count each geographic unit's access gain once. Keep geographic population reached and event capacity separate from the score. The complete definitions, equations, and interpretation are in Section 8.

**Acceptance:** Same inputs produce the same ranking; adding a second event does not double-count a tract; the score breakdown can be reproduced from exported inputs.

### FR-6: Test ranking robustness

Run seeded simulations that vary policy weights within hospital-approved ranges and perturb uncertain indicators within defensible bounds. Keep confirmed hard constraints fixed. For each feasible site, calculate first-place frequency, top-*N* frequency, median rank, and score loss relative to the best site in each simulation. Section 8 defines the sampling plan and tests.

Default decision rule for the MVP: a site is labeled **robustly recommended** if it ranks in the top three in at least 80% of simulations. Both `N` and the percentage are configurable. If no site passes, label the result **inconclusive** and show the leading alternatives and the assumptions that change their order. Run results include the simulation seed and distributions used. Simulations test sensitivity; validation requires observed event outcomes.

**Acceptance:** The same seed gives identical frequency results; the interface never describes simulation frequency as the probability that patients will attend.

### FR-7: Compare and explain plans

Show the top three feasible plans on a map and comparison table. Each plan shows venue, service, date window, required staff and equipment, event capacity, estimated cost, tracts newly within the travel-time target, approximate population in those tracts, score breakdown, robustness statistics, and outstanding confirmations. Show the tradeoff when a planner changes weights or constraints.

**Acceptance:** A planner can answer “Why did Site A outrank Site B?” from visible numeric factors and a map without relying on a generated summary.

### FR-8: Export and review

Export a one-page event brief and machine-readable results containing assumptions, sources, selected sites, coverage, resources, ranking metrics, and unresolved checks. A human planner approves the event outside the optimizer before scheduling or publishing it.

**Acceptance:** An exported plan contains enough information to reproduce the result and distinguish verified facts from modeled estimates.

### FR-9: Record event outcomes

Provide a simple post-event form for aggregate turnout, services completed, referrals, follow-ups, and operational issues. Use these data to evaluate site selection and, in later versions, calibrate attendance forecasts.

**Acceptance:** Entering event outcomes does not silently alter past recommendations or overwrite their original inputs.

### FR-10: Refresh and replan as conditions change

Refresh eligible dynamic feeds at an appropriate cadence and create a new run when a meaningful input changes. Show the previous plan beside the new plan with a numeric reason for any rank change, such as a closed access road, newly unavailable staff, or a confirmed venue. Do not automatically cancel or publish an event. If a feed fails, retain its last-known value with a visible stale warning; never interpret missing data as zero incidents or zero demand.

**Acceptance:** A simulated venue closure or staff change removes infeasible plans and produces a versioned new recommendation; the prior run remains inspectable.

### FR-11: Use relevant time series to adjust priorities

When a service has a compatible historical series, show its recent level against a seasonal or longer-term baseline, with geography, denominator, and provisional status. Allow a bounded, documented trend adjustment to the underlying geographic priority score; default to no adjustment when the series is absent, too sparse, suppressed, or unrelated to the service. A spike in ED use is a signal for review, not proof of unmet need. Include trend assumptions in the robustness simulations.

**Acceptance:** A planner can see the historical chart and exact trend contribution to a site's score; changing a respiratory series has no effect on an unrelated screening campaign.

## 8. Mathematical model, simulations, and validation

This section specifies every quantitative method used by the MVP. None of the scores is a clinical diagnosis, an estimate of undiagnosed patients, or a forecast of event turnout. Record all formulas, indicator definitions, parameter values, routing data, and software versions with each run.

### 8.1 Units and notation

| Symbol | Meaning |
| --- | --- |
| `i` | A geographic demand unit, initially a census tract |
| `j` | One candidate event: a venue, date/time, and fixed service |
| `k` | An existing care site verified to offer that service to the target group |
| `E_i` | Estimated number of residents eligible for the service in unit `i` |
| `T` | Planner-selected maximum resident road travel time, in minutes |
| `τ(i,j)` | Estimated road travel time from a representative point in unit `i` to event `j` |
| `d_i^0` | Shortest road travel time from unit `i` to any verified existing relevant service |
| `q_i` | Dimensionless, service-specific priority index between 0 and 1 |
| `W_i` | `E_i × q_i`, a need-weighted population index, not a patient count |
| `x_j` | Binary decision to schedule event `j` |
| `y_i` | Binary indication that the selected plan newly covers unit `i` |

Use one service per MVP optimization. Align the service's actual eligibility rule with `E_i` and the chosen health indicator; when a public indicator's age group differs, label it as a proxy and do not turn its percentage into an exact case count. Geography identifiers and dates are explicit. If a time series is available only at county level, apply it only as county-level context; do not display it as a tract-specific observation.

### 8.2 Indicator normalization and priority index

Select at most two or three service-relevant indicators for the MVP: a health or preventive-service measure, a transport/access-barrier measure, and optionally a verified program-demand measure. Orient each so larger means greater priority. Normalize to `[0,1]` using **fixed, documented benchmark bounds** for the pilot region or a stated reference population; do not silently rescale when the candidate-venue list changes. Clip out-of-range observations and flag them. Missing indicators are flagged and handled by a declared fallback; they are never silently set to zero.

```text
normalized_high_priority(v) = clip((v - lower_benchmark)
                                   / (upper_benchmark - lower_benchmark), 0, 1)
normalized_priority_for_low_values(v) = 1 - normalized_high_priority(v)
```

The first form applies when a higher raw value means greater priority; the second applies when a lower raw value means greater priority, such as screening completion. Require `upper_benchmark > lower_benchmark`. Store the benchmark and direction for each indicator.

For approved weights `w_h`, `w_b`, and optional `w_d`, each nonnegative and summing to 1:

```text
q_i = w_h × health_index_i
    + w_b × barrier_index_i
    + w_d × recent_demand_index_i

W_i = E_i × q_i
```

The selected indicators, bounds, and weights express program priorities and must be reviewed by the hospital. `W_i` has no claim to represent the number of residents who need or will use a service. Publish unweighted population alongside the index. If no defensible health indicator exists for a service, omit the disease-specific component and label the result as an access-only scenario rather than inventing a proxy.

### 8.3 Road-time access and geographic coverage

Compute a route-time matrix from a tract representative point to each verified existing service and candidate venue. Prefer a population-weighted point if available; otherwise use a representative point with a warning for large rural tracts. Road time, not straight-line distance, determines coverage. An unavailable route is `unknown`, never zero minutes.

```text
d_i^0 = min over verified existing sites k of τ(i,k)
a_ij = 1 if d_i^0 > T and τ(i,j) ≤ T; otherwise 0

newly_covered_population(S) = Σ_i E_i × 1[Σ_(j in S) a_ij ≥ 1]
weighted_coverage(S) = Σ_i W_i × 1[Σ_(j in S) a_ij ≥ 1]
```

An existing site's service, eligibility, and relevant operating schedule must be verified before it contributes to `d_i^0`. If none is verified, label baseline access **unknown** rather than assuming no care exists. Provide a separate prospecting map for units with unknown baseline access; exclude them from the confirmed newly-covered metric until reviewed. Display the travel-time threshold and mapped representative point with each result.

### 8.4 Operational feasibility and capacity

Before scoring, reject candidate events that fail required host approval, accessibility, privacy, power/equipment, date, staff, vehicle, or budget checks. Unknown required conditions make a venue a prospect, not a confirmed plan. Compute service capacity from operations data:

```text
clinical_throughput_j = floor(clinical_hours_j × Σ_role staff_count_(j,role)
                              × services_per_staff_hour_(role))
capacity_j = min(clinical_throughput_j, equipment_throughput_j,
                 space_throughput_j)
```

`space_throughput` refers to clinical stations and usable time, not fire-code occupancy. If a throughput limit is unknown, flag it for confirmation; do not use an invented number. Capacity describes how many services an event *could* deliver. It does not cap the number of residents in its geographic catchment or predict turnout. A later calibrated attendance model may connect reach to capacity.

For an upcoming event, a road incident affects the route only after its location and active time are matched to a travel path. If closure-aware routing is unavailable, show a route warning and request review instead of guessing a new travel time. A weather alert is matched to the event place and time; whether it blocks an event is an explicit hospital policy, with the alert type and rule saved. These live checks change feasibility or route estimates, not the tract's health indicator.

### 8.5 Single-event ranking and multi-event optimization

For one event, rank each feasible `j` by `weighted_coverage({j})`. Report cost, van travel, and capacity beside the score. For the MVP, cost and vehicle limits are hard constraints; any additional cost penalty must be explicit and user-controlled. Break exact score ties with a documented stable rule, such as lower cost followed by venue ID.

For `K` events, maximize unique weighted coverage with binary decisions:

```text
maximize    Σ_i W_i × y_i
subject to  y_i ≤ Σ_j a_ij × x_j                 for every i
            Σ_j x_j ≤ K
            Σ_j cost_j × x_j ≤ total_budget
            staff, vehicle, venue, date constraints
            x_j, y_i ∈ {0,1}
```

Because each `y_i` is binary, an area reached by multiple events is credited once. A greedy maximum-coverage algorithm is sufficient for a small hackathon candidate set; an integer program can handle more complex calendars later. Report the selected method and, where practical, compare the greedy result with an exact solution on small synthetic cases. Repeated events in one area may have value, but require a separate cadence objective rather than double-counting access gain.

An optional, planner-approved equity constraint can require `Σ_(i in high_barrier_units) y_i ≥ minimum_units`, so a high-population solution cannot completely bypass the most isolated areas. The chosen threshold and any loss in total weighted coverage must be shown.

**Worked example:** With a 30-minute target, suppose tract A has `E=1,000`, `q=0.8`, and a 40-minute baseline; tract B has `E=500`, `q=0.6`, and a 45-minute baseline. Venue 1 brings only A within 30 minutes, so its weighted-coverage score is `800` and its newly covered population is `1,000`. Venue 2 brings only B within 30 minutes, so its score is `300` and its newly covered population is `500`. Scheduling both yields score `1,100` and newly covered population `1,500`, regardless of each event's separate clinical capacity. These figures describe geographic coverage, not expected attendance.

### 8.6 Time-series trend adjustment

Only use a historical series if its service, geography, unit, and denominator match the campaign's purpose. Examples include monthly asthma/COPD emergency-visit **rates** for respiratory outreach and weekly aggregate pending referrals for the corresponding hospital service. Compare a recent rolling window with the same seasonal window in prior years when enough history exists; otherwise compare with a clearly labeled longer-term baseline. For a geographic series `g`:

```text
rate_(g,t) = observed_count_(g,t) / valid_denominator_(g,t)
trend_g = (recent_rate_g - seasonal_baseline_rate_g)
          / max(seasonal_baseline_rate_g, small_positive_floor)

multiplier_g = clip(1 + trend_strength × clip(trend_g, -1, 1), 0.8, 1.2)
W_i^trend = W_i × multiplier_(geography containing i)
```

The `0.8–1.2` range is a proposed pilot guardrail, not a learned clinical threshold. The planner approves `trend_strength` and can set it to zero. Suppressed, sparse, stale, or revised observations are flagged; without a valid comparable series, use multiplier `1`. A county-level multiplier is identical for tracts in that county and cannot choose among sites *within* that county by itself. Emergency-department trends can reflect care-seeking and coding changes, so show the original series and trend contribution separately.

### 8.7 Robustness simulations

Run at least 1,000 seeded scenarios for each planning run in the hackathon version. Distinguish three kinds of variation:

1. **Priority choices:** sample nonnegative weights that sum to 1 within hospital-approved ranges. A concrete default is `weights ~ Dirichlet(concentration × approved_weights)`, rejecting draws outside the approved bounds; store the concentration and bounds, and repeat the analysis with a broader concentration. These samples represent plausible *preferences*, not random clinical truths.
2. **Measured uncertainty:** perturb public estimates using their published intervals and ACS margins of error where available. For a percentage with point estimate `p` and reported 95% bounds `L,U`, a simple sensitivity approximation is a bounded normal draw centered on `p` with standard deviation `(U-L)/3.92`; clip to the valid range. Include a shared county-level component as well as local variation so nearby tracts do not unrealistically move independently. State that this approximation is not a posterior distribution supplied by CDC, especially when the interval is asymmetric.
3. **Operational scenarios:** vary travel times within a documented range, such as ±10%, venue cost, and optional trend strength. Keep confirmed hard requirements fixed; an unapproved venue never becomes feasible merely through a random draw. Test a separately labeled scenario if a verified service might close or a route becomes unusable.

For each scenario, rerank all feasible candidate plans and record:

```text
top_N_frequency(j) = number of scenarios with rank(j) ≤ N / scenarios_run
first_frequency(j) = number of scenarios with rank(j) = 1 / scenarios_run
regret_(j,b) = best_score_in_scenario_b - score_(j,b)
```

Also show median rank, average regret, worst-case regret across declared stress scenarios, and the number of candidates. A top-three frequency of 80% is a **stability result under the stated simulations**, not an 80% probability that the venue is objectively best or that patients will attend. The default `top 3 in ≥80%` badge from FR-6 is a proposed review threshold; expose the `N`, percentage, weight ranges, and simulation design. Show the recommendation as inconclusive when no plan passes, and show near ties even when a plan does pass. A site with high top-three frequency but large regret should be reviewed rather than automatically favored.

If the number of feasible candidates is at most `N`, top-*N* frequency cannot discriminate among them; use first-place frequency, regret, and side-by-side score components instead. If several candidates pass the review threshold, retain the deterministic score order and show the full robustness results rather than declaring a unique simulation winner.

### 8.8 Testing and validation plan

| Test | Required check |
| --- | --- |
| Data contracts | Valid geography IDs, matching service definitions, plausible rates, nonnegative times/counts, denominators present, source dates recorded, duplicates flagged, no silent ZIP-to-tract conversion |
| Small worked cases | Hand-calculate a two-tract/two-site example and compare the exact score, baseline access, capacity, exclusions, and ranking with the implementation |
| Invariants | Infeasible plans never rank; one tract contributes at most once to a multi-event plan; adding a feasible event cannot reduce raw coverage; no route is not treated as zero minutes; same inputs and seed give the same output |
| Weight sensitivity | Test approved weight endpoints, remove one indicator at a time, and identify which assumptions reverse the top two plans |
| Data and route stress | Test lower/upper indicator estimates, shared regional shocks, travel-time perturbations, different access thresholds, stale feeds, and a road-closure scenario |
| Simulation convergence | Compare top-*N* frequencies at 1,000 and 5,000 draws with fixed seeds; if a leading site's frequency changes by more than 3 percentage points, increase draws and report the instability |
| Optimizer check | On small synthetic instances, compare greedy multi-event selection with an exact enumeration or integer-program optimum; record any gap |
| Historical backtest | When real event history exists, rerun the model using only data available before each past event and compare predictions with later turnout, completed services, and follow-ups |

Historical attendance alone does not prove a site caused higher access: marketing, weather, staffing, and service mix differ. Backtests should stratify or adjust for those factors where data permit. Before enough real events exist, report simulations as robustness evidence, not outcome validation. Any later attendance forecast needs its own held-out calibration and error analysis before appearing as an expected-visit number.

## 9. Output contract

A recommendation card must contain:

| Field | Meaning |
| --- | --- |
| `site`, `date_window`, `service` | Proposed operational plan, with approval status |
| `rank`, `score`, `score_components` | Reproducible result under the selected policy weights |
| `newly_covered_tracts` | Tracts moved within the travel-time target compared with verified baseline services |
| `approx_population_in_newly_covered_tracts` | Geographic reach estimate; not the number expected to attend |
| `event_capacity` | Maximum services supported by entered staffing, time, venue, and equipment limits |
| `expected_attendance` | Omitted or marked unavailable until calibrated with relevant event history |
| `top_n_frequency`, `median_rank`, `regret` | Robustness results across documented simulations |
| `feasibility`, `open_checks` | Verified operational facts and unresolved conditions |
| `data_vintage`, `sources` | Provenance of the estimates and site information |
| `refreshed_at`, `stale_inputs`, `change_reason` | Freshness and explanation when a live update changes the recommendation |
| `trend_period`, `trend_baseline`, `trend_adjustment` | Service-specific historical signal and its bounded contribution, when applicable |

Any optional language-model summary must be generated from these structured fields. The numeric card and export remain available if the summary fails or is disabled.

## 10. Technical approach for the MVP

1. Prepare tract data and boundaries, then normalize indicators and join by tract ID.
2. Maintain separate tables for `tracts`, `existing_services`, `candidate_venues`, `venue_verifications`, `resources`, `travel_times`, `runs`, and `run_results`.
3. Cache road travel-time matrices from tracts to verified services and candidate venues.
4. Apply hard constraints and score feasible single-site plans. For multiple events, use a maximum-coverage selection method or integer optimization.
5. Run seeded robustness simulations and persist the inputs, scoring configuration, and results.
6. Poll selected operational, weather, and road feeds; store dated snapshots and explain any material change.
7. Add a service-specific historical series when one is available, with a chart and bounded trend contribution.
8. Present the map, ranked list, comparison table, and export in a lightweight web interface.

A Python data pipeline with a small relational or analytical database and a map-capable web UI is sufficient for the hackathon. The exact framework is an implementation choice; reproducible calculations and inspectable data matter more than a particular library.

## 11. Data and decision limitations

- CDC PLACES provides **modeled** small-area estimates with uncertainty. Its indicators should guide geographic planning, not identify patients or prove unmet clinical demand. Small-area comparisons should consider confidence intervals. [Source: CDC methodology](https://www.cdc.gov/places/methodology/index.html) and [FAQ](https://www.cdc.gov/places/faqs/index.html)
- A clinic directory establishes a location and site type. Relevant services, eligibility, schedules, and capacity require verification.
- Public venue directories establish possible locations. Availability, permission, clinical suitability, and physical limits require host or hospital confirmation.
- Tract-level travel times approximate residents' journeys; rural tracts can be geographically large. The tool should expose the representative point and allow local correction.
- Driving time is an incomplete access measure for residents without a vehicle. The no-vehicle indicator flags additional barriers, and later versions should consider walk, transit, or partner-provided transport options where those exist. Distance to a provider does not establish available appointments.
- Policy weights express the hospital's priorities. Robustness simulations reveal sensitivity to those choices; they do not make the choices objectively correct.
- The MVP uses public aggregate and sample operational data. Patient-level information is unnecessary for its core decision.
- Public live feeds chiefly improve near-term feasibility. A current, service-specific demand signal usually requires a hospital or public-health partner. The system must not claim that a road or weather update proves a change in chronic disease burden.
- Public health series may be revised, suppressed in small areas, or affected by changes in care seeking and coding. Compare rates or proportions with appropriate denominators, show uncertainty, and avoid treating a short spike as a stable shift in need.

## 12. Delivery milestones

| Milestone | Demonstrable result |
| --- | --- |
| Data foundation | One region's tract indicators, population, verified existing services, and candidate venues loaded with provenance |
| Access model | Road-time map and baseline-versus-candidate coverage for one service |
| Planner | Feasibility rules and top-three deterministic recommendations |
| Robustness and explanation | Simulation frequencies, sensitivity view, numeric reasons, and comparison map |
| Demo and evaluation | Exported event brief and tests for reproducibility, infeasible-site exclusion, and no double-counting |

## 13. Open product decisions

These choices should be made with a hospital or public-health partner before using the ranking operationally:

1. Which **single service and region** should anchor the first pilot?
2. Which existing providers can be verified as offering that service to the target population?
3. What travel-time target and equity priorities reflect the program's goals?
4. Which venue requirements are absolute, and who can confirm them?
5. What historical event data, if any, can be shared in aggregate for outcome validation?
