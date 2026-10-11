"""Resolve a hospital name to an address and coordinates with the Google Geocoding API."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .gemini import _load_env
from .catalog import STATE_NAMES

ENDPOINT = "https://maps.googleapis.com/maps/api/geocode/json"
_cache = {}


def available():
    _load_env()
    return bool(os.environ.get("GOOGLE_MAPS_API_KEY"))


def locate(name, state_code=None):
    """Return coordinates for a US hospital name, scoped to a selected state when supplied."""
    name = " ".join(str(name).split())[:120]
    if not name:
        raise ValueError("Enter your hospital name.")
    _load_env()
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        raise ValueError("Set GOOGLE_MAPS_API_KEY in .env so hospital names can be located.")
    cache_key = (name.lower(), str(state_code or "").upper())
    if cache_key in _cache:
        return _cache[cache_key]
    try:  # Places text search understands business names far better than address geocoding.
        state_name = STATE_NAMES.get(str(state_code or "").upper(), "")
        found = _places("places:searchText", {"textQuery": ", ".join(v for v in [name, state_name, "USA"] if v), "includedType": "hospital", "strictTypeFiltering": False,
                                              "maxResultCount": 1},
                        mask="places.displayName,places.formattedAddress,places.location")
        top = (found.get("places") or [None])[0]
        if top and top.get("location"):
            result = {"lat": top["location"]["latitude"], "lon": top["location"]["longitude"], "address": top.get("formattedAddress", "")}
            _cache[cache_key] = result
            return result
    except ValueError:
        pass
    state_name = STATE_NAMES.get(str(state_code or "").upper(), "")
    query = urllib.parse.urlencode({"address": ", ".join(v for v in [name, state_name, "USA"] if v), "components": "country:US", "key": key})
    try:
        with urllib.request.urlopen(f"{ENDPOINT}?{query}", timeout=15) as response:
            data = json.load(response)
    except (urllib.error.URLError, TimeoutError):
        raise ValueError("Google Maps could not be reached. Try again.") from None
    status = data.get("status")
    if status == "ZERO_RESULTS" or not data.get("results"):
        raise ValueError(f"We couldn't find \u201c{name}\u201d in the United States. Check the spelling or add the city and state.")
    if status != "OK":
        raise ValueError(f"Google Maps returned {status}. Check the API key and that the Geocoding API is enabled.")
    top = data["results"][0]
    loc = top["geometry"]["location"]
    result = {"lat": loc["lat"], "lon": loc["lng"], "address": top.get("formatted_address", "")}
    _cache[cache_key] = result
    return result


PLACES = "https://places.googleapis.com/v1"


def _places(path, body=None, mask=None):
    _load_env()
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        raise ValueError("Set GOOGLE_MAPS_API_KEY in .env so hospital names can be located.")
    headers = {"X-Goog-Api-Key": key, "Content-Type": "application/json"}
    if mask:
        headers["X-Goog-FieldMask"] = mask
    request = urllib.request.Request(f"{PLACES}/{path}", json.dumps(body).encode() if body is not None else None, headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"Google Places returned HTTP {exc.code}. Enable the Places API (New) for this key.") from None
    except (urllib.error.URLError, TimeoutError):
        raise ValueError("Google Maps could not be reached.") from None


def suggest(text, session=None, state_code=None):
    text = " ".join(str(text).split())[:100]
    if len(text) < 3:
        return []
    suffix = STATE_NAMES.get(str(state_code or "").upper())
    body = {"input": f"{text}, {suffix}" if suffix else text, "includedRegionCodes": ["us"]}
    if session and 8 <= len(str(session)) <= 64:
        body["sessionToken"] = str(session)
    data = _places("places:autocomplete", body)
    out = []
    for s in data.get("suggestions", []):
        p = s.get("placePrediction")
        if p and p.get("placeId"):
            fmt = p.get("structuredFormat", {})
            out.append({"id": p["placeId"], "name": fmt.get("mainText", {}).get("text") or p["text"]["text"],
                        "detail": fmt.get("secondaryText", {}).get("text", ""), "text": p["text"]["text"]})
    return out[:6]


def place_details(place_id, session=None):
    place_id = str(place_id)
    if not place_id.replace("_", "").replace("-", "").isalnum() or len(place_id) > 200:
        raise ValueError("Unknown place.")
    path = f"places/{place_id}" + (f"?sessionToken={urllib.parse.quote(str(session))}" if session and 8 <= len(str(session)) <= 64 else "")
    data = _places(path, mask="id,displayName,formattedAddress,addressComponents,location")
    loc = data.get("location") or {}
    if "latitude" not in loc:
        raise ValueError("That place has no location.")
    state_code = next((part.get("shortText") for part in data.get("addressComponents", []) if "administrative_area_level_1" in part.get("types", [])), None)
    return {"lat": loc["latitude"], "lon": loc["longitude"], "address": data.get("formattedAddress", ""),
            "name": data.get("displayName", {}).get("text", ""), "state": state_code}


def locate_place(text, state_code=None):
    """Find a city, ZIP code or address in the United States.
    Returns {"lat", "lon", "label"} or None."""
    text = " ".join(str(text).split())[:120]
    if not text:
        return None
    try:
        suffix = STATE_NAMES.get(str(state_code or "").upper(), "")
        found = _places("places:searchText", {"textQuery": ", ".join(v for v in [text, suffix, "USA"] if v), "maxResultCount": 1},
                        mask="places.displayName,places.formattedAddress,places.location")
    except ValueError:
        return None
    top = (found.get("places") or [None])[0]
    if not top or not top.get("location"):
        return None
    return {"lat": top["location"]["latitude"], "lon": top["location"]["longitude"],
            "label": top.get("formattedAddress") or top.get("displayName", {}).get("text", text)}
