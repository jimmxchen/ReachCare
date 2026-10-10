# ReachCare

**Decision support and community outreach for free health clinics in North Carolina.**

ReachCare helps a hospital decide *where* to put a free clinic, shows the answer on a map of North Carolina, lets the planner interrogate the recommendation in plain language, and then reaches the community the clinic is for: residents sign up for alerts, or text and talk to ReachCare in their own language over iMessage.

Built for HackNC.

---

## Contents

1. [What it does](#what-it-does)
2. [Product tour](#product-tour)
3. [Architecture](#architecture)
4. [The siting model](#the-siting-model)
5. [Data sources](#data-sources)
6. [AI and third-party services](#ai-and-third-party-services) (APIs, models, prompts, guardrails)
7. [Frameworks and libraries](#frameworks-and-libraries)
8. [Project structure](#project-structure)
9. [Setup and running](#setup-and-running)
10. [Configuration reference](#configuration-reference)
11. [HTTP API reference](#http-api-reference)
12. [Testing](#testing)
13. [Privacy, consent and safety](#privacy-consent-and-safety)
14. [Limitations and honest caveats](#limitations-and-honest-caveats)
15. [Roadmap](#roadmap)
16. [Legacy planner engine](#legacy-planner-engine)
17. [Credits and attribution](#credits-and-attribution)

---

## What it does

| Audience | Capability |
| --- | --- |
| **Hospital planners** | Enter a hospital, a driving radius (15/30/45/60 min), a service, a patient travel target, staffing and clinic days. Get recommended host sites, suggested dates, capacity and estimated cost on a zoomable NC map. |
| **Hospital planners** | Ask a Gemini-powered analyst *why* communities were prioritized and explore trade-offs between travel time, population coverage and resources. |
| **Residents** | Sign up on a web page to be texted when a free clinic is scheduled nearby. |
| **Residents** | Text or send a **voice note in any language** over iMessage; ask when/where the next free clinic is. A text gets a text reply, and a voice note gets a **voice note back in the same language**. |

Supported services: **blood-pressure**, **diabetes**, **COPD / lung health** and **asthma** screening. Each uses a different CDC PLACES health indicator.

---

## Product tour

| Page | URL | Purpose |
| --- | --- | --- |
| **Plan a clinic** | `/` | Hospital intake form with Google Places autocomplete. Submitting opens a full-screen NC map with results. |
| **Results pop-up** | (overlay on `/`) | Map (zoom, pan, reset), metrics, clinic cards, subscriber announcement panel and the planner chat. |
| **How it works** | `/#/docs` | In-app documentation of the data, formulas, algorithm, robustness check and limits. |
| **Ask ReachCare** | `/bot.html` | Browser tester for the same text and voice bot that runs on iMessage. |
| **Get alerts** | `/join.html` | Public resident sign-up page (name, mobile number, location, interests, language, consent). |

### Planner flow

1. Type a hospital name and pick a suggestion (address and coordinates are resolved by Google Places).
2. Choose driving radius, service, patient travel target, staff, clinical hours, services per staff hour, number of clinic days and a date window.
3. Click **Find optimal clinic location**.
4. Review the map, the clinic cards, estimated cost and capacity. Ask the planner chat follow-up questions.
5. If residents have signed up nearby, **Text subscribers** queues an announcement for the matching people.

### Resident flow

- **Web:** fill in `/join.html`; a welcome text arrives by iMessage in the chosen language.
- **iMessage:** the bot onboards new numbers conversationally (name, city or ZIP, interests), acknowledges that they will be texted when a clinic is scheduled, and answers questions afterward. Keywords: `STOP`, `START`, `UPDATE`, `HELP`.

---

## Architecture

```
                         ┌─────────────────────────────────────────────┐
  Hospital planner ────► │  Browser UI  (web/: HTML + CSS + vanilla JS)  │
  Resident (web)   ────► │  /  /bot.html  /join.html  /#/docs            │
                         └───────────────────┬─────────────────────────┘
                                             │ JSON over HTTP
                         ┌───────────────────▼─────────────────────────┐
                         │  Python server (server.py, stdlib http.server) │
                         │                                               │
                         │  siting.py     weighted max-coverage model    │
                         │  planchat.py   what-if scenarios + Gemini Q&A │
                         │  bot.py        onboarding, intents, replies   │
                         │  subscribers.py opt-in list + outbox          │
                         │  gemini.py / elevenlabs.py / geocode.py       │
                         └───────┬───────────────┬──────────────┬────────┘
                                 │               │              │
                      Google Places API   Google Gemini    ElevenLabs
                      (autocomplete,      (explain, chat,   (Scribe STT,
                       details, search)    intent, translate) multilingual TTS)
                                 ▲
                         ┌───────┴─────────────────────────────┐
  iPhone (iMessage) ◄──► │ Photon Spectrum  ◄──►  bot/photon.mjs │  (Node.js bridge)
                         └─────────────────────────────────────┘
```

- **One Python process** serves the UI, the JSON API and all domain logic. There is no web framework.
- **A small Node.js bridge** (`bot/photon.mjs`) connects Photon's Spectrum SDK to the Python API. It forwards each inbound text or voice memo to `/api/bot/message`, sends the reply back, and polls `/api/bot/outbox` every 10 s to deliver queued announcements.
- **State** lives in the git-ignored `.runtime/` directory: `clinic_plans.json` (saved plans), `subscribers.json` (opt-in list, conversation state, outbox) and `planner.sqlite3` (legacy planner runs).

---

## The siting model

The recommendation is a **transparent optimization**, not a trained machine-learning model. Every output traces to a census estimate and a stated assumption, and the same inputs always give the same answer.

### 1. Need score per census tract

```text
health_index  = clip((indicator_percent − low) / (high − low), 0, 1)
barrier_index = clip(no_vehicle_household_percent / 20, 0, 1)
priority      = w · health_index + (1 − w) · barrier_index        (w = 0.70)
need_weight   = adults_18_plus × priority
```

Fixed benchmark ranges per service keep scores stable when candidate sites change:

| Service | CDC PLACES indicator | low – high |
| --- | --- | --- |
| Blood-pressure screening | Diagnosed high blood pressure | 15 – 55 % |
| Diabetes screening | Diagnosed diabetes | 4 – 24 % |
| COPD / lung health | Diagnosed COPD | 2 – 15 % |
| Asthma care | Current asthma | 8.5 – 14.5 % |

### 2. Service area and existing access

- The hospital's **driving radius** is converted to straight-line distance at **0.58 miles per minute** (about 8.7, 17.4, 26.1 and 34.8 miles for 15/30/45/60 min). Tracts whose center is inside the circle are in scope.
- A tract counts as **already served** if one of the 27 geocoded NCDHHS safety-net sites is within the patient travel target by **cached OSRM road time**. This can be switched off in the form.

### 3. Greedy maximum coverage

Candidate host sites are the **397 IMLS public-library outlets** inside the circle. A site reaches a tract when the tract center is within the travel target of the site.

```text
covered = ∅
repeat k times (k = number of clinic days):
    pick the site maximizing  Σ need_weight(t)  for t in reach(site) ∖ covered
    stop when the gain is zero
    covered ← covered ∪ reach(site)
```

Greedy selection carries the standard (1 − 1/e) ≈ 63 % approximation guarantee for maximum coverage. Ties break on site ID so results are repeatable.

### 4. Capacity, cost and schedule

```text
capacity per clinic day = staff × clinical_hours × services_per_staff_hour
estimated cost per day  = staff × clinical_hours × $55 + $400        (placeholder constants)
```

Clinic days are spread evenly across the date window on weekdays, largest reach first.

### 5. Robustness

The selection is re-run for seven health-vs-transport weights (0.2 … 0.8). Each clinic card reports how many of the seven runs chose it ("Chosen in 7 of 7 priority mixes").

> **Reach is not turnout.** "Adults newly in reach" counts residents who live near a site and are not already near an existing clinic. It does not predict attendance, and capacity is reported separately.

---

## Data sources

| Dataset | Vintage | Used for |
| --- | --- | --- |
| **U.S. Census ACS 5-year**, tables B01001 (sex by age) and B08201 (household size by vehicles available) | 2024 | Adult population and households without a vehicle, by tract |
| **Census cartographic tract boundaries** | 2023 | Map polygons and tract representative points |
| **CDC PLACES**, census-tract data | 2025 release | Modeled prevalence of high blood pressure, diabetes, COPD and asthma |
| **IMLS Public Libraries Survey** | FY2024 | 397 library outlets used as candidate host sites |
| **NC DHHS safety-net clinic directory** | March 2026 | 27 geocoded existing care sites |
| **OSRM** (OpenStreetMap road network) | precomputed | Driving times, 2,647 tracts × 57 destinations |

Of 2,647 tracts, **2,637** have the inputs required for scoring. Source URLs, checksums and join decisions are recorded in `data/manifest.json` and `data/nc.json`.

> **All census and health figures are read from bundled files. They are never generated by an AI model.**

Rebuild data with `python3 scripts/prepare_data.py --skip-routes` and `--routes-only` (public OSRM requests are throttled; supply `--routing-url` for your own OSRM).

---

## AI and third-party services

### Summary

| Service | Product / model | Where it is used | Code |
| --- | --- | --- | --- |
| **Google Gemini API** | `gemini-3.5-flash`, falling back to `gemini-3.1-flash-lite`, then `gemini-flash-latest` (override with `GEMINI_MODEL`) | Plan explanation, planner chat, chatbot intent extraction, reply translation | `planner/gemini.py`, `planner/planchat.py`, `planner/bot.py` |
| **ElevenLabs** | Scribe speech-to-text (`scribe_v2`, falling back to `scribe_v1`); text-to-speech `eleven_multilingual_v2` | Voice-note transcription with language detection, spoken replies | `planner/elevenlabs.py` |
| **Google Places API (New)** | `places:autocomplete`, `places/{id}` details, `places:searchText` | Hospital autocomplete, address resolution, locating cities and ZIP codes in chat | `planner/geocode.py` |
| **Google Geocoding API** (legacy) | `maps/api/geocode/json` | Fallback for resolving a typed name when Places search returns nothing | `planner/geocode.py` |
| **Photon Spectrum** | `spectrum-ts` SDK, iMessage provider | Receive texts and voice memos, send replies, start new chats for announcements | `bot/photon.mjs` |
| **ffmpeg** | local binary | Converts ElevenLabs MP3 to AAC `.m4a` so iMessage plays it as a voice note | `planner/elevenlabs.py` |
| **Claude Code** (Anthropic) | AI coding assistant | Used during development to write and refactor code and tests | n/a |

### Where Gemini is called

All calls go through one helper, `gemini.generate()`, using the REST endpoint `v1beta/models/{model}:generateContent` with the key in the `x-goog-api-key` header. Temperature is `0.2`. If a model returns 404, 429, 500 or 503, the next model in the fallback list is tried.

| # | Feature | Mode | Input given to Gemini | Output |
| --- | --- | --- | --- | --- |
| 1 | **Plan explanation** (results screen) | text | Plan summary and clinic list only | 3–4 sentence plain-language summary |
| 2 | **Planner chat** ("Ask about this plan") | text | Full plan context: inputs, summary, clinics, top prioritized and left-out tracts, county breakdown, and a table of precomputed what-if scenarios | Short grounded answer |
| 3 | **Bot understanding** (iMessage / web bot) | JSON mode (`responseMimeType: application/json`) | The user's message and the current onboarding stage | `{intent, name, place, services, language}` |
| 4 | **Bot reply translation** | text | The English reply text | Reply in the person's language (numbers, names, addresses and keywords preserved) |
| 5 | Free-text intake parsing | JSON mode | A sentence about the plan | Form fields (endpoint exists, not shown in the current UI) |

### Where ElevenLabs is called

- **Speech-to-text:** `POST /v1/speech-to-text` (multipart). Returns transcript text and the detected language code (ISO 639-3, e.g. `spa`). The language drives the reply language.
- **Text-to-speech:** `POST /v1/text-to-speech/{voice_id}?output_format=mp3_44100_128` with `eleven_multilingual_v2`. Output is converted to `.m4a` (AAC, 64 kbps) with ffmpeg. If ffmpeg is missing, MP3 is sent as an audio attachment.

### Where Google Places is called

- **`/api/places/suggest`** proxies `places:autocomplete` (US, restricted to a North Carolina bounding box) with a session token.
- **`/api/places/details`** fetches location and formatted address (field-masked).
- **Typed names without a selection** go through `places:searchText`, then the Geocoding API as a fallback.
- **Cities and ZIP codes in chat and sign-up** use `places:searchText` restricted to North Carolina.
- API keys never reach the browser; the Python server proxies every Google, Gemini and ElevenLabs call.

### Guardrails on AI output

- **Grounding:** the planner chat is told to answer *only* from the supplied plan JSON and to quote numbers exactly. What-if numbers are computed by the siting model, not by Gemini, so the model never does the arithmetic.
- **No invented data:** Gemini never supplies census figures, never chooses sites and never changes the recommendation.
- **Vocabulary:** replies say adults are "within reach", never "served", because reach is not turnout.
- **Scope:** the chat may only suggest inputs the tool really has (radius, travel target, staff, hours, days, dates, existing-clinics option).
- **Medical questions:** the bot declines medical advice, points to a doctor or 911, and offers to find a clinic.
- **Graceful degradation:** with no Gemini key, the app falls back to rule-based intent matching and built-in English summaries. Planning and the map need no AI at all.
- **Greetings:** "yo", "hey" and similar are classified as greetings, never as a name.

### What is *not* AI

The siting recommendation, the need scores, the what-if scenarios, subscriber matching and notification targeting are all deterministic Python and NumPy.

---

## Frameworks and libraries

| Layer | Technology | Notes |
| --- | --- | --- |
| **Server language** | Python 3.11+ (developed on 3.13) | Standard-library `http.server`, `json`, `sqlite3`, `urllib`, `threading` |
| **Numerics** | NumPy ≥ 2.1, < 3 | Vectorized distance, coverage and weighting (only Python dependency) |
| **Front end** | HTML, CSS, vanilla JavaScript (ES modules) | No framework, no build step; hand-drawn SVG map; browser `MediaRecorder` for voice capture in the bot tester |
| **iMessage bridge** | Node.js 20+, `spectrum-ts` ^12.10.1 | Photon Spectrum SDK; ESM; loads `.env` with `node --env-file` |
| **Persistence** | JSON files and SQLite | `.runtime/` directory, git-ignored |
| **Audio** | ffmpeg | AAC `.m4a` voice replies |
| **Routing data** | OSRM over OpenStreetMap | Precomputed matrix in `data/routes.npz` |
| **Tests** | Python `unittest` | 33 tests |
| **Image tooling** | Pillow (development only) | Used once to cut transparent logo assets |

There is no database server, ORM, web framework, bundler or CSS framework.

---

## Project structure

```text
.
├── server.py                 HTTP server, routing, application wiring
├── start.sh                  Starts the Python server and the iMessage bridge together
├── requirements.txt          Python dependencies (NumPy)
├── .env.example              Template for local secrets (copy to .env)
├── planner/
│   ├── siting.py             Hospital-centred clinic siting (maximum coverage)
│   ├── planchat.py           What-if scenarios and Gemini analyst chat
│   ├── bot.py                Conversation engine, onboarding, signup, notifications
│   ├── subscribers.py        Opt-in list, conversation state, outbox (JSON file)
│   ├── gemini.py             Gemini REST client with model fallback
│   ├── elevenlabs.py         Speech-to-text, text-to-speech, ffmpeg conversion
│   ├── geocode.py            Google Places / Geocoding client
│   ├── model.py              Service catalog and legacy planning model
│   ├── feeds.py              Legacy NWS / DriveNC feeds
│   └── store.py              Legacy SQLite persistence
├── web/
│   ├── index.html, app.js    Planner UI, map, results, planner chat, docs page
│   ├── bot.html              Browser tester for the bot
│   ├── join.html             Resident sign-up page
│   ├── style.css             Shared design system
│   └── *.png                 Logo mark, lockup and favicon
├── bot/
│   ├── photon.mjs            Photon Spectrum ⇄ ReachCare bridge
│   └── package.json
├── data/                     Census, PLACES, library, clinic and routing data
├── scripts/                  Data preparation and run reproduction
└── tests/test_planner.py     Unit and integration tests
```

---

## Setup and running

### Prerequisites

- Python 3.11 or newer
- Node.js 20 or newer (only for iMessage)
- ffmpeg (optional; needed for `.m4a` voice replies)
- API keys, as needed: Google Cloud (Places API New), Gemini, ElevenLabs, and Photon project credentials

### Install

```sh
git clone <this repo> && cd hacknc-main
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r requirements.txt

cp .env.example .env          # then add your keys
cd bot && npm install && cd ..
```

### Run

```sh
./start.sh
```

This starts the web app at **http://127.0.0.1:8000** and the iMessage bridge. Press `Ctrl+C` to stop both.

Run only the web app (no iMessage):

```sh
python3 server.py                       # default port 8000
python3 server.py --port 8001 --runtime-dir /tmp/reachcare-preview
```

What works without each key:

| Missing key | Effect |
| --- | --- |
| `GOOGLE_MAPS_API_KEY` | Hospital lookup, autocomplete and city/ZIP lookup are unavailable (clear error shown) |
| `GEMINI_API_KEY` | No AI explanations or planner chat; bot uses rule-based matching and English replies |
| `ELEVENLABS_API_KEY` | Voice notes are rejected with an error; text works normally |
| Photon credentials | The web app, sign-up page and browser bot tester still work; iMessage does not |

### Try it without a phone

- **Planner:** open `/`, type a hospital, choose a suggestion, and run.
- **Bot:** open `/bot.html`. Type or record a voice note. **Start over** resets the demo conversation to a brand-new person.
- **Sign-up:** open `/join.html`.

---

## Configuration reference

| Variable | Required for | Description |
| --- | --- | --- |
| `GOOGLE_MAPS_API_KEY` | Planner, bot, sign-up | Key with **Places API (New)** enabled (Geocoding API optional fallback) |
| `GEMINI_API_KEY` | AI features | Google AI Studio key |
| `GEMINI_MODEL` | optional | Force a specific model instead of the built-in fallback order |
| `ELEVENLABS_API_KEY` | Voice | ElevenLabs API key |
| `ELEVENLABS_VOICE_ID` | optional | Voice for spoken replies (default: premade "Sarah") |
| `PHOTON_PROJECT_ID` | iMessage | Photon Spectrum project ID |
| `PHOTON_PROJECT_SECRET` | iMessage | Photon Spectrum project secret |
| `PHOTON_PROMO_CODE` | n/a | Stored for reference only; not read by the code |
| `REACH_URL` | optional | Where the bridge finds the Python server (default `http://127.0.0.1:8000`) |
| `DRIVENC_API_KEY` | legacy | NCDOT DriveNC events key for the legacy planner's live feeds |

Secrets are read from `.env` (git-ignored) or the process environment and are never written to disk elsewhere or returned by the API.

---

## HTTP API reference

All endpoints return JSON. POST endpoints reject cross-origin browser requests.

### Planning

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/clinic-options` | Services, radii and which integrations are configured |
| GET | `/api/places/suggest?q=&session=` | Hospital name autocomplete (Places) |
| GET | `/api/places/details?id=&session=` | Resolve a suggestion to coordinates and address |
| POST | `/api/siting` | Run the siting model; saves the plan; returns the full result |
| GET | `/api/plans` | Saved plans (the bot answers from these) |
| POST | `/api/plan-chat` | Planner Q&A: `{params, messages[]}` → `{reply}` |
| POST | `/api/gemini/explain` | Plain-language plan summary |
| POST | `/api/gemini/parse` | Free-text intake → form fields |

`POST /api/siting` body:

```json
{
  "hospital_name": "Mission Hospital",
  "service": "blood-pressure",
  "radius": 30,
  "travel_target": 30,
  "staff_count": 3,
  "clinical_hours": 4,
  "services_per_staff_hour": 5,
  "days": 3,
  "date_start": "2026-10-17",
  "date_end": "2026-11-14",
  "use_existing_care": true
}
```

### Messaging and subscribers

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/signup` | Add a consenting resident to the list and queue a welcome text |
| POST | `/api/bot/message` | `{sender, text}` or `{sender, audio_b64, mime}` → reply (+ voice reply for voice input) |
| POST | `/api/bot/notify` | `{plan_id, dry_run?}` queue announcements for matching subscribers |
| POST | `/api/bot/outbox` | Drain queued messages (used by the bridge) |
| POST | `/api/bot/requeue` | Return undelivered messages to the queue |

### Legacy planner

`/api/bootstrap`, `/api/runs`, `/api/runs/{id}`, `/api/import`, `/api/refresh`, `/api/forecast`, `/api/feeds` and `/api/outcomes` belong to the original planner engine (see below).

---

## Testing

```sh
python3 -m unittest discover -s tests -v
node --check web/app.js
node --check bot/photon.mjs
```

33 tests cover the siting model (inside-radius, uniqueness, determinism, validation, geocoding), planner chat context and what-ifs, bot onboarding, voice-in/voice-out, signup validation and notification targeting, plus the original planner engine (exact-solution comparison, reproducibility, data alignment and feed handling). External services (Google, Gemini, ElevenLabs) are mocked in tests.

---

## Privacy, consent and safety

- **Opt-in only.** Web sign-up requires an explicit consent checkbox; the first iMessage tells people they can reply `STOP`. `STOP` unsubscribes immediately and `START` resumes.
- **No double texting.** The same clinic is never announced to the same person twice; signing up twice sends one welcome.
- **Data stored per resident:** name, phone number or iMessage ID, city or ZIP, interests, language and consent timestamp, in `.runtime/subscribers.json`. **No voice recordings, message transcripts or health details are stored.** Voice notes are held in memory only while being processed.
- **Aggregate data only** is used for planning; no patient-level records.
- **Not medical advice.** The bot declines clinical questions and directs people to a clinician or 911.
- **Third-party processing:** message text and voice audio are sent to ElevenLabs and Google (Gemini, Places) to provide the feature. Plan context sent to Gemini contains aggregate figures and clinic locations only.
- **Secrets** live in `.env` and are never exposed to the browser.

---

## Limitations and honest caveats

- **Distances are approximations.** Hospital radius and clinic reach use straight-line distance converted at a fixed speed, not real drive times from the hospital. Existing-clinic access does use real cached road times.
- **Reach, not turnout.** The model identifies who lives near a site. It does not forecast attendance.
- **Health data are modeled estimates** of *diagnosed* prevalence, and they miss undiagnosed cases.
- **Host sites are libraries.** The 397 library outlets are a candidate list, not a verified inventory; confirm host approval, space, power and accessibility.
- **Existing care is incomplete.** The 27-record directory is not exhaustive or service-specific, so a nearby clinic may not offer the chosen service.
- **Cost figures are placeholders** ($55 per staff hour, $400 per clinic day).
- **Plans are suggestions.** The bot answers from saved plans, and nothing yet marks a plan as confirmed by the hospital.
- **Local deployment.** No authentication or rate limiting beyond a basic sign-up throttle; do not expose it publicly as-is.
- **Photon delivery.** Announcements start chats by phone number; behavior and limits depend on your Photon plan.
- **AI can be wrong.** Gemini answers are grounded and instructed to cite only supplied numbers, but they should be reviewed by a person before acting.

---

## Roadmap

- Hospital "confirm plan" step so only approved clinics are announced
- Real road-network drive times from the hospital's address
- Hosting so the web app and iMessage bridge run continuously
- Verified, service-specific directory of existing clinics and venue types beyond libraries
- Import of a hospital's own clinic and site lists
- More services and configurable priority weights
- Calibration against observed turnout from pilot events
- Authentication and role-based access for planners

---

## Legacy planner engine

ReachCare grew out of an earlier "Rural Outreach Planner" MVP. Its engine is still in the repository and still served by the API, though the current UI no longer surfaces it:

- `planner/model.py`: venue scoring against a precomputed OSRM travel-time matrix, capacity limits, multi-event scheduling, and 1,000+ seeded simulations with Dirichlet policy weights
- `planner/store.py`: SQLite storage of immutable, versioned runs
- `planner/feeds.py`: NWS alerts and NCDOT DriveNC road events
- `scripts/reproduce.py`: independent numeric replay of an exported run

Product requirements for that engine are in `PRD.md`. The service catalog in `planner/model.py` is shared with the current siting model.

---

## Credits and attribution

- **Data:** U.S. Census Bureau (ACS, TIGER/cartographic boundaries), CDC PLACES, Institute of Museum and Library Services, NC DHHS, OpenStreetMap contributors and the OSRM project.
- **Services:** Google (Gemini, Maps Platform), ElevenLabs, Photon.
- **Development:** built for HackNC with the assistance of Claude Code (Anthropic) for code generation, refactoring and test writing. All product decisions, data choices and review were made by the team.
- **License and data terms:** check each data provider's and API provider's terms before reuse; this repository does not relicense third-party data.
