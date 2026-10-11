"""Text and voice-memo question answering: "when/where is the next free clinic?"

Text in -> text out. Voice memo in -> voice memo out, in the language that was spoken.
"""
from __future__ import annotations

import base64
import json
import math
from datetime import date

from datetime import datetime, timezone

from . import elevenlabs, gemini, geocode
from .model import SERVICES

LANGUAGES = {"eng": "English", "spa": "Spanish", "fra": "French", "deu": "German", "por": "Portuguese", "ita": "Italian",
             "zho": "Chinese", "cmn": "Mandarin Chinese", "yue": "Cantonese", "vie": "Vietnamese", "ara": "Arabic",
             "rus": "Russian", "hin": "Hindi", "kor": "Korean", "jpn": "Japanese", "hat": "Haitian Creole",
             "tgl": "Tagalog", "pol": "Polish", "urd": "Urdu", "ben": "Bengali", "guj": "Gujarati", "ukr": "Ukrainian",
             "tur": "Turkish", "fas": "Persian", "hmn": "Hmong", "swh": "Swahili"}
ISO2 = {"en": "eng", "es": "spa", "fr": "fra", "de": "deu", "pt": "por", "it": "ita", "zh": "zho", "vi": "vie", "ar": "ara",
        "ru": "rus", "hi": "hin", "ko": "kor", "ja": "jpn", "ht": "hat", "tl": "tgl", "pl": "pol", "ur": "urd"}
KEYWORDS = {"blood-pressure": ["blood pressure", "hypertension", "presión", "presion", "tension"],
            "diabetes": ["diabet", "sugar", "glucose", "azúcar", "azucar"],
            "copd": ["copd", "lung", "breath", "pulmon", "pulmón"],
            "asthma": ["asthma", "asma"]}


def _miles(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 7917.6 * math.asin(math.sqrt(a))


def language_name(code):
    code = (code or "eng").lower()
    return LANGUAGES.get(ISO2.get(code, code), code)


def understand(text):
    """Return {"service": key|None, "place": str|None, "language": iso639-3}."""
    lowered = text.lower()
    fallback = {"service": next((k for k, words in KEYWORDS.items() if any(w in lowered for w in words)), None),
                "place": None, "language": "eng"}
    if not gemini.available():
        return fallback
    names = "; ".join(f'"{k}" = {v["name"]}' for k, v in SERVICES.items())
    prompt = ("A person texted a free-clinic finder. Return JSON with keys: service (one of the keys below or null if they did "
              "not name a health service), place (the city, town, county or ZIP code in North Carolina they mention, or null), "
              f"language (ISO 639-3 code of the language the message is written in). Services: {names}.\n\nMessage: {text[:600]}")
    try:
        data = json.loads(gemini.generate(prompt, as_json=True))
        service = data.get("service") if data.get("service") in SERVICES else fallback["service"]
        return {"service": service, "place": data.get("place") or None, "language": str(data.get("language") or "eng")[:3].lower()}
    except (RuntimeError, ValueError, TypeError):
        return fallback


def upcoming(plans, service, where, today=None):
    today = (today or date.today()).isoformat()
    seen, found = set(), []
    for plan in plans:
        for c in plan.get("clinics", []):
            key = (c["id"], c["date"], c["service"])
            if key in seen or c["date"] < today or (service and c["service"] != service):
                continue
            seen.add(key)
            miles = _miles(where["lat"], where["lon"], c["lat"], c["lon"]) if where else None
            found.append({**c, "hospital": plan.get("hospital", {}).get("name"), "miles": miles})
    if where:
        return sorted(found, key=lambda c: (c["miles"] > 25, c["date"], c["miles"]))
    return sorted(found, key=lambda c: c["date"])


def _pretty(iso):
    d = date.fromisoformat(iso)
    return f"{d.strftime('%A, %B')} {d.day}"


def facts_for(matches, service, where, asked_place):
    svc = SERVICES[service]["name"] if service else "free clinic"
    if not matches:
        return {"found": False, "service": svc}
    top = matches[0]
    return {"found": True, "far": top.get("miles") is not None and top["miles"] > 60, "service": SERVICES[top["service"]]["name"], "date": _pretty(top["date"]), "name": top["name"],
            "address": ", ".join(p for p in [top.get("address"), top.get("city")] if p), "county": top.get("county"),
            "hosted_by": top.get("hospital"), "miles_away": round(top["miles"]) if top.get("miles") is not None else None,
            "also": [{"name": m["name"], "date": _pretty(m["date"])} for m in matches[1:3]],
            "asked_about_place": asked_place, "location_understood": bool(where)}


def english_reply(f, proposed=False):
    if not f["found"]:
        if proposed:
            return f"I don't see an upcoming proposed {f['service']} clinic in this browser demo. Create a plan in Plan a clinic first."
        return f"I don't see an upcoming free {f['service']} clinic scheduled yet. Please check back soon."
    if f.get("far"):
        text = f"I don't see a {'proposed ' if proposed else 'free '}{f['service']} clinic within 60 miles. The closest one is on"
    else:
        text = f"The next {'proposed ' if proposed else 'free '}{f['service']} clinic is on"
    text += f" {f['date']} at {f['name']}, {f['address']}"
    text += f" ({f['miles_away']} miles away)." if f.get("miles_away") is not None else "."
    if f.get("hosted_by"):
        text += f" Hosted with {f['hosted_by']}."
    if f["also"] and not f.get("far"):
        text += " Also: " + "; ".join(f"{a['name']} on {a['date']}" for a in f["also"]) + "."
    if not f["location_understood"]:
        text += " Tell me your city or ZIP code and I'll find the closest one."
    if proposed:
        text += " This is a proposed plan, not a confirmed event."
    return text


def compose(f, language):
    base = english_reply(f)
    if language_name(language) == "English" or not gemini.available():
        return base
    try:
        return gemini.generate(
            f"Rewrite this message in {language_name(language)} for a text message. Translate the weekday and month names, but keep "
            f"every number, clinic name, hospital name and street address exactly as written. Reply with only the message.\n\n{base}").strip()
    except RuntimeError:
        return base


PERSONA = (
    "You are ReachCare, a friendly assistant that helps North Carolinians find FREE community health clinics "
    "(blood-pressure, diabetes, and COPD/lung screenings, plus asthma education and support) hosted by local hospitals. "
    "You are not a doctor and never give medical advice."
)
GREETINGS = {"hi", "hello", "hey", "yo", "sup", "hola", "hii", "heya", "howdy", "yo!", "hi!", "hello!", "hey!", "start", "help", "?"}
STOP_WORDS = {"stop", "unsubscribe", "cancel", "quit", "end", "alto", "parar"}
SERVICE_LIST = "blood pressure, diabetes, lung health (COPD), asthma education and support"
PROMPTS = {
    "name": "What's your name?",
    "place": "What city or ZIP code are you in? I'll use it to find clinics near you.",
    "services": f"Which free clinics are you interested in? Reply with any of: {SERVICE_LIST}. Or reply ALL.",
}


def _parse(text, stage):
    """Classify one message. Gemini when available; keyword rules otherwise."""
    lowered = " ".join(text.lower().split())
    words = lowered.replace("?", " ").replace("!", " ").replace(",", " ").split()
    rule = {"intent": "other", "name": None, "place": None, "services": [], "language": None}
    if lowered in STOP_WORDS:
        rule["intent"] = "stop"
    elif lowered in ("start", "subscribe", "resume", "unstop"):
        rule["intent"] = "start"
    elif lowered in ("update", "change", "edit", "restart", "reset"):
        rule["intent"] = "update"
    elif lowered in ("help", "?", "menu"):
        rule["intent"] = "help"
    elif lowered in GREETINGS or (words and words[0] in GREETINGS and
                                  (len(words) <= 2 or "how are you" in lowered or "how's it going" in lowered)):
        rule["intent"] = "greeting"
    elif any(w in lowered for w in ("when", "where", "next", "clinic", "cuando", "cuándo", "dónde", "donde", "clínica", "clinica")):
        rule["intent"] = "ask_clinic"
    elif any(w in lowered for w in ("should i", "my symptoms", "pain", "medicine", "medication", "dose", "diagnos", "chest", "emergency")):
        rule["intent"] = "medical"
    rule["services"] = [k for k, ws in KEYWORDS.items() if any(w in lowered for w in ws)]
    if "all" in words or "any" in words or "todas" in words or "todos" in words:
        rule["services"] = list(SERVICES)
    if stage == "name" and rule["intent"] in ("other",) and 1 <= len(words) <= 4 and all(w.isalpha() for w in words):
        cleaned = [w for w in words if w not in ("my", "name", "is", "im", "i'm", "i", "am", "this", "it's", "its", "me", "llamo", "soy")]
        if cleaned:
            rule["name"] = " ".join(cleaned).title()
            rule["intent"] = "provide_info"
    if stage == "place" and rule["intent"] in ("other", "ask_clinic") and len(text) <= 60:
        rule["place"] = text.strip()
        rule["intent"] = "provide_info"
    if stage == "services" and rule["services"]:
        rule["intent"] = "provide_info"
    if stage is None and rule["intent"] == "greeting":
        return rule
    if not gemini.available():
        return rule
    prompt = (PERSONA + " Classify the person's latest message. Return JSON with keys: "
              "intent (one of: greeting, ask_clinic, provide_info, stop, start, update, help, medical, other), "
              "name (the person's first name if they gave it, else null), "
              "place (a North Carolina city, town, county or ZIP code if they gave one, else null), "
              f"services (list of any of {list(SERVICES)} they are interested in; all of them if they say all/any), "
              "language (ISO 639-3 code of the message language, or null if the message is too short to tell). "
              "Greetings like 'yo', 'hey', 'hi' are intent greeting, never a name. "
              f"We are currently asking them for: {stage or 'nothing'}.\n\nMessage: {text[:500]}")
    try:
        data = json.loads(gemini.generate(prompt, as_json=True))
    except (RuntimeError, ValueError, TypeError):
        return rule
    services = [s for s in (data.get("services") or []) if s in SERVICES] or rule["services"]
    intent = data.get("intent") if data.get("intent") in ("greeting", "ask_clinic", "provide_info", "stop", "start", "update", "help", "medical", "other") else rule["intent"]
    if rule["intent"] in ("stop", "start", "update"):
        intent = rule["intent"]  # exact keywords always win
    name = (data.get("name") or "").strip()[:40] or None
    if name and name.lower() in GREETINGS:
        name = None
    return {"intent": intent, "name": name or (rule["name"] if intent != "greeting" else None),
            "place": data.get("place") or rule["place"], "services": services,
            "language": (data.get("language") or None)}


def _service_text(services):
    names = [SERVICES[s]["name"].split(" (")[0].lower() for s in services if s in SERVICES]
    return ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1] if names else "free clinic"


def _clinic_answer(plans, profile, parsed, text, proposed=False):
    service = parsed["services"][0] if len(parsed["services"]) == 1 else (profile.get("services") or [None])[0] if len(profile.get("services") or []) == 1 else None
    where = geocode.locate_place(parsed["place"]) if parsed["place"] else None
    if not where and profile.get("lat") is not None:
        where = {"lat": profile["lat"], "lon": profile["lon"], "label": profile.get("place")}
    matches = upcoming(plans, service, where)
    facts = facts_for(matches, service, where, parsed["place"])
    reply = english_reply(facts, proposed)
    if facts["found"] and gemini.available():
        prompt = (PERSONA + " Answer the person's clinic question in 1-3 friendly sentences. "
                  "Use only the supplied clinic facts. Do not invent a date, venue, address, distance, or availability. "
                  "If no clinic was found, clearly say so. "
                  + ("Every clinic here is a proposal, not a confirmed event. Say that clearly. " if proposed else "")
                  + "Do not give medical advice. Reply in English; translation happens separately.\n\n"
                  + f"Question: {text[:500]}\nFacts: {json.dumps(facts)}\n"
                  + f"Factual fallback answer: {reply}")
        try:
            generated = gemini.generate(prompt).strip()
            if generated:
                reply = generated + (" This is a proposed plan, not a confirmed event." if proposed else "")
        except RuntimeError:
            pass
    return reply, matches[0] if matches else None


def _browser_conversation(text, proposed):
    """Answer conversational browser messages without enrolling someone in alerts."""
    fallback = ("Hi! I'm doing well, thanks for asking. " if "how are you" in text.lower() else "Hi! ")
    fallback += ("I can help you explore proposed free clinic plans in North Carolina. What would you like to know?"
                 if proposed else "I can help you find free clinics in North Carolina. What would you like to know?")
    if not gemini.available():
        return fallback
    prompt = (PERSONA + " You are chatting in a browser. Reply naturally to the person's message in 1-2 sentences. "
              "If they ask how you are, answer politely. You can help with North Carolina free clinic plans. "
              "No clinic facts are provided here, so do not invent locations, dates, or availability. "
              "Do not ask for a name, phone number, or subscription. Reply in English; translation happens separately. "
              + ("The hosted browser only shows proposed plans, not confirmed events. " if proposed else "")
              + f"\n\nPerson: {text[:500]}\nReachCare:")
    try:
        return gemini.generate(prompt).strip() or fallback
    except RuntimeError:
        return fallback


def converse(plans, store, sender, text, language_hint=None, preview=False):
    """Return (english_reply, language, match). Mutates the sender's profile."""
    browser = sender.startswith("web-")
    profile = store.get(sender)
    first_contact = profile is None
    if first_contact:
        profile = {"name": None, "place": None, "lat": None, "lon": None, "services": [], "language": "eng",
                   "stage": "chat" if browser else "name", "stopped": False, "created_at": datetime.now(timezone.utc).isoformat()}
    if browser:
        profile["stage"] = "chat"  # Existing browser sessions may have the old onboarding stage.
    stage = None if browser or profile["stage"] == "done" else profile["stage"]
    parsed = _parse(text, stage)
    language = language_hint or profile.get("language") or "eng"
    if not language_hint and parsed.get("language") and len(text.split()) >= 4:
        language = parsed["language"]
    profile["language"] = language
    intent = parsed["intent"]
    match = None

    def finish(reply):
        store.put(sender, profile)
        return reply, language, match

    if browser:
        if intent == "medical":
            return finish("I can't give medical advice. Please contact a clinician about symptoms or medicines. If it's an emergency, call 911.")
        if intent == "ask_clinic":
            reply, match = _clinic_answer(plans, profile, parsed, text, preview)
            return finish(reply)
        if intent == "greeting":
            return finish(("Hi! I'm doing well, thanks for asking. " if "how are you" in text.lower() else "Hi! ") +
                          ("I can help you explore proposed free clinic plans in North Carolina. What would you like to know?"
                           if preview else "I can help you find free clinics in North Carolina. What would you like to know?"))
        return finish(_browser_conversation(text, preview))

    if intent == "stop":
        profile["stopped"] = True
        if preview:
            return finish("This browser demo doesn't send text alerts. Reply START to continue the conversation.")
        return finish("You're unsubscribed and won't get text updates. Reply START anytime to turn them back on.")
    if intent == "start" and profile["stopped"]:
        profile["stopped"] = False
        if preview:
            return finish("Welcome back! Ask me about a proposed clinic plan.")
        return finish("Welcome back! I'll text you when a free clinic is scheduled near you.")
    if intent == "update":
        profile.update(stage="name", stopped=False)
        return finish("Sure, let's update your info. " + PROMPTS["name"])
    if intent == "medical":
        reply = ("I can't give medical advice. For symptoms or medication questions, please call your doctor. "
                 "If it's an emergency, call 911. I can help you find a free clinic near you.")
        return finish(reply + ("\n\n" + PROMPTS[profile["stage"]] if profile["stage"] != "done" else ""))

    # Clinic question: answer it, then continue onboarding if unfinished.
    if intent == "ask_clinic" and not first_contact:
        reply, match = _clinic_answer(plans, profile, parsed, text, preview)
        if profile["stage"] != "done":
            reply += ("\n\nTo tailor the demo, " if preview else "\n\nTo text you updates, I need a bit more info. ") + PROMPTS[profile["stage"]]
        return finish(reply)

    if first_contact:
        intro = ("Hi! I'm ReachCare. I help you explore proposed free clinic plans in North Carolina. "
                 "This browser demo does not send text alerts." if preview else
                 "Hi! I'm ReachCare. I help you find free community health clinics in North Carolina, "
                 "and I can text you when one is scheduled near you.")
        if intent == "ask_clinic":
            reply, match = _clinic_answer(plans, profile, parsed, text, preview)
            return finish(f"{reply}\n\n{intro}{'' if preview else ' Reply STOP anytime to opt out.'}\n\n{PROMPTS['name']}")
        return finish(f"{intro}{'' if preview else ' Reply STOP anytime to opt out.'}\n\nFirst, {PROMPTS['name'][0].lower()}{PROMPTS['name'][1:]}")

    stage = profile["stage"]
    if stage == "name":
        if parsed["name"]:
            profile["name"], profile["stage"] = parsed["name"], "place"
            return finish(f"Nice to meet you, {profile['name']}! {PROMPTS['place']}")
        return finish("Sorry, I didn't catch your name. " + PROMPTS["name"])
    if stage == "place":
        where = geocode.locate_place(parsed["place"]) if parsed["place"] else None
        if where:
            profile.update(place=where["label"].replace(", USA", ""), lat=where["lat"], lon=where["lon"], stage="services")
            return finish(f"Got it, {profile['place']}. {PROMPTS['services']}")
        return finish("I couldn't find that in North Carolina. Try a city name or a 5-digit ZIP code.")
    if stage == "services":
        if parsed["services"]:
            profile.update(services=parsed["services"], stage="done", stopped=False)
            matches = upcoming(plans, None, {"lat": profile["lat"], "lon": profile["lon"]})
            matches = [m for m in matches if m["service"] in profile["services"] and m["miles"] <= 60]
            reply = (f"You're all set, {profile['name']}! I can help you explore proposed {_service_text(profile['services'])} "
                     f"clinics near {profile['place']}." if preview else
                     f"You're all set, {profile['name']}! I'll text you when a free {_service_text(profile['services'])} clinic "
                     f"is scheduled near {profile['place']}. Reply STOP anytime to unsubscribe.")
            if matches:
                match = matches[0]
                reply += ("\n\nOne proposal is " if preview else "\n\nOne is already coming up: ") + f"{_pretty(match['date'])} at {match['name']}, {match.get('city') or match['county']}."
            else:
                reply += "\n\nNo matching proposal is available in this demo yet." if preview else "\n\nNothing is scheduled near you yet, but I'll let you know."
            return finish(reply)
        return finish("Sorry, I didn't catch that. " + PROMPTS["services"])

    # Registered and chatting.
    if intent == "help":
        if preview:
            return finish("Ask me about a proposed clinic plan, or send a voice note. Reply UPDATE to change your info.")
        return finish("Ask me \"when is the next free blood pressure clinic?\" or send a voice note in any language. "
                      "Reply UPDATE to change your info, or STOP to unsubscribe.")
    if intent == "greeting":
        return finish(f"Hi {profile['name']}! Ask me about the next {'proposed ' if preview else 'free '}clinic near {profile['place']}, "
                      "or reply UPDATE to change your info.")
    reply, match = _clinic_answer(plans, profile, parsed, text, preview)
    if intent == "other" and match is not None and not any(w in text.lower() for w in ("clinic", "screen", "blood", "diabet", "asthma", "copd")):
        return finish("I can help you find free clinics. Try \"when is the next clinic near me?\" or reply HELP.")
    return finish(reply)


def translate(reply, language):
    if language_name(language) == "English" or not gemini.available():
        return reply
    try:
        return gemini.generate(
            f"Rewrite this text message in {language_name(language)}. Translate weekday and month names, but keep every number, "
            f"clinic name, hospital name, street address and the words STOP, START, UPDATE, HELP and ALL exactly as written. "
            f"Reply with only the message.\n\n{reply}").strip()
    except RuntimeError:
        return reply


def handle(plans, store, payload, preview=False):
    """payload: {"sender", "text"} or {"sender", "audio_b64", "mime"}. Voice in gets a voice memo back."""
    sender = str(payload.get("sender") or "anonymous")[:80]
    if payload.get("reset") and sender.startswith("web-"):
        store.delete(sender)
        return {"mode": "text", "reply": "Conversation reset.", "language": "eng", "transcript": "", "reset": True}
    if payload.get("audio_b64"):
        try:
            audio = base64.b64decode(payload["audio_b64"], validate=True)
        except ValueError:
            raise ValueError("Audio must be base64 encoded.") from None
        heard = elevenlabs.transcribe(audio, payload.get("mime") or "audio/mp4")
        if not heard["text"]:
            raise ValueError("I couldn't hear anything in that voice note.")
        english, language, match = converse(plans, store, sender, heard["text"], heard["language"], preview)
        reply = translate(english, language)
        result = {"mode": "voice", "reply": reply, "language": language, "transcript": heard["text"],
                  "match": match, "sender": sender}
        try:
            mp3 = elevenlabs.synthesize(reply, language)
            audio_out, mime = elevenlabs.to_m4a(mp3)
            result.update(audio_b64=base64.b64encode(audio_out).decode(), audio_mime=mime)
        except RuntimeError:
            pass
        return result
    text = str(payload.get("text", "")).strip()
    if not text:
        raise ValueError("Send a text message or a voice note.")
    english, language, match = converse(plans, store, sender, text, preview=preview)
    return {"mode": "text", "reply": translate(english, language), "language": language, "transcript": text,
            "match": match, "sender": sender}


def normalize_phone(raw):
    """Return an E.164 US/CA number like +15551234567, or None."""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if str(raw).strip().startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits[0] == "1":
        return "+" + digits
    return None


def signup(store, payload):
    """Web sign-up: add someone to the announcement list and queue a welcome text."""
    phone = normalize_phone(payload.get("phone", ""))
    if not phone:
        raise ValueError("Enter a valid mobile number, like (919) 555-0123.")
    if payload.get("consent") is not True:
        raise ValueError("Please agree to receive text messages to sign up.")
    name = " ".join(str(payload.get("name", "")).split())[:40]
    if not name:
        raise ValueError("Enter your first name.")
    services = [s for s in payload.get("services", []) if s in SERVICES] or list(SERVICES)
    where = geocode.locate_place(str(payload.get("place", "")))
    if not where:
        raise ValueError("We couldn't find that place in North Carolina. Try a city name or ZIP code.")
    language = str(payload.get("language") or "eng")[:3].lower()
    existing = store.get(phone)
    now = datetime.now(timezone.utc).isoformat()
    store.put(phone, {"name": name, "place": where["label"].replace(", USA", ""), "lat": where["lat"], "lon": where["lon"],
                      "services": services, "language": language, "stage": "done", "stopped": False,
                      "source": "web", "consent_at": now, "created_at": (existing or {}).get("created_at", now),
                      "notified": (existing or {}).get("notified", [])})
    if not existing or existing.get("stopped"):
        welcome = (f"Hi {name}, you're signed up for free clinic alerts from ReachCare. We'll text you when a free "
                   f"{_service_text(services)} clinic is scheduled near {where['label'].replace(', USA', '')}. "
                   "Ask me anytime \"when is the next clinic?\" Reply STOP to unsubscribe.")
        store.queue([{"sender": phone, "text": translate(welcome, language)}])
    return {"ok": True, "phone": phone[:-4].replace(phone[1:-4], "•" * len(phone[1:-4])) + phone[-4:], "place": where["label"]}


def notifications(store, clinic, hospital=None, radius_miles=40, mark=False):
    """Queue a text for every opted-in person near `clinic` who wants its service."""
    out = []
    for sender, p in store.all().items():
        if p.get("stage") != "done" or p.get("stopped") or p.get("lat") is None:
            continue
        if clinic["service"] not in (p.get("services") or []):
            continue
        if _miles(p["lat"], p["lon"], clinic["lat"], clinic["lon"]) > radius_miles:
            continue
        key = f"{clinic['id']}:{clinic['date']}"
        if key in (p.get("notified") or []):
            continue
        if mark:
            p.setdefault("notified", []).append(key)
            store.put(sender, p)
        text = (f"Hi {p['name']}, a free {SERVICES[clinic['service']]['name'].lower()} clinic is scheduled for "
                f"{_pretty(clinic['date'])} at {clinic['name']}, {clinic.get('address') or ''} {clinic.get('city') or ''}. "
                "Reply STOP to unsubscribe.").replace("  ", " ")
        out.append({"sender": sender, "text": translate(text, p.get("language") or "eng")})
    return out
