"""Gemini helpers. Census figures always come from the bundled ACS/PLACES data; Gemini only
parses free-text intake and explains an already computed plan."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ROOT = Path(__file__).resolve().parents[1]
# Tried in order; GEMINI_MODEL in .env goes first. Older 2.5 models are closed to new keys.
FALLBACK_MODELS = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-flash-latest"]


def _load_env():
    path = ROOT / ".env"
    if path.is_file():
        for line in path.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def available():
    _load_env()
    return bool(os.environ.get("GEMINI_API_KEY"))


def generate(prompt, as_json=False):
    _load_env()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    models = [os.environ["GEMINI_MODEL"]] if os.environ.get("GEMINI_MODEL") else []
    models += [m for m in FALLBACK_MODELS if m not in models]
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2}}
    if as_json:
        body["generationConfig"]["responseMimeType"] = "application/json"
    last = "Gemini returned no content"
    for model in models:
        request = urllib.request.Request(ENDPOINT.format(model=model), json.dumps(body).encode(),
                                         {"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.load(response)
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as exc:
            last = f"Gemini returned HTTP {exc.code}"
            if exc.code not in (404, 429, 500, 503):
                break
        except (urllib.error.URLError, TimeoutError):
            last = "Gemini could not be reached"
        except (KeyError, IndexError):
            last = "Gemini returned no content"
    raise RuntimeError(last)


def parse_intake(text, counties):
    prompt = ("Extract free-clinic planning inputs from the hospital's description. Return JSON with only the keys "
              "you can determine: hospital_name (string), county (one of: " + ", ".join(sorted(counties.values())) + "), "
              "radius (15, 30, 45 or 60 driving minutes), travel_target (minutes patients should travel, integer), "
              "clinics (integer 1-5), budget (USD number), cost_per_clinic (USD number), date_start and date_end (YYYY-MM-DD), "
              "health_weight (0-1: how much to favour health need over transport barriers). Do not invent values.\n\n"
              f"Description: {text[:2000]}")
    return json.loads(generate(prompt, as_json=True))


def explain(plan, language="English"):
    compact = {"hospital": plan["hospital"]["name"], "service": plan["service_name"], "radius_minutes": plan["params"]["radius"],
               "travel_target_minutes": plan["params"]["travel_target"], "summary": plan["summary"],
               "clinics": [{k: c[k] for k in ("label", "name", "city", "county", "date", "new_adults", "avg_indicator", "avg_no_vehicle", "stability")}
                           for c in plan["clinics"]]}
    prompt = (f"Write a 3-4 sentence plain-language explanation in {language} for hospital leadership of why these free-clinic "
              "locations were chosen. Use only the numbers provided, state that figures are census-based estimates, and do not "
              "promise turnout or outcomes. Say adults are within reach of a site, never served.\n\n" + json.dumps(compact))
    return generate(prompt).strip()
