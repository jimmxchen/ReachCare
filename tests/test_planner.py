import copy
import csv
import io
import itertools
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

import numpy as np

from planner.feeds import refresh, weather_blocks
from planner.model import baselines, coverage_matrix, default_config, greedy_schedule, normalize, run_plan, trend_multipliers, validate_config
from planner.store import Store
from scripts.reproduce import reproduce
from server import Application, Handler, PreviewStore, _same_origin

ROOT = Path(__file__).resolve().parents[1]


class OriginTests(unittest.TestCase):
    def test_same_origin_uses_https_on_vercel_and_http_locally(self):
        with patch.dict("os.environ", {"VERCEL": "1"}):
            self.assertTrue(_same_origin("https://reachcare.example", "reachcare.example"))
            self.assertFalse(_same_origin("http://reachcare.example", "reachcare.example"))
            self.assertFalse(_same_origin("https://other.example", "reachcare.example"))
        with patch.dict("os.environ", {"VERCEL": ""}):
            self.assertTrue(_same_origin("http://127.0.0.1:8000", "127.0.0.1:8000"))
            self.assertFalse(_same_origin("https://127.0.0.1:8000", "127.0.0.1:8000"))

    def test_hosted_browser_chat_runs_but_alert_outbox_is_blocked(self):
        request = {"sender": "web-test", "text": "Hi", "preview": True, "profile": None, "plans": []}
        body = json.dumps(request).encode()
        handler = Handler.__new__(Handler)
        handler.path = "/api/bot/message"
        handler.headers = {"Content-Length": str(len(body)), "Origin": "https://reachcare.example", "Host": "reachcare.example"}
        handler.rfile = io.BytesIO(body)
        handler.authorized = lambda: True
        responses = []
        handler.json_response = lambda value, status=200: responses.append((status, value))
        with patch.dict("os.environ", {"VERCEL": "1"}), patch("planner.bot.gemini.available", return_value=False):
            handler.do_POST()
            self.assertEqual(responses[-1][0], 200)
            self.assertEqual(responses[-1][1]["profile"]["stage"], "chat")
            handler.path = "/api/bot/outbox"
            handler.do_POST()
            self.assertEqual(responses[-1][0], 503)

    def test_hosted_signup_is_advertised_as_preview_only(self):
        handler = Handler.__new__(Handler)
        handler.path = "/api/clinic-options"
        handler.app = type("DemoApp", (), {"catalog": type("Catalog", (), {"states": lambda self: []})()})()
        handler.authorized = lambda: True
        responses = []
        handler.json_response = lambda value, status=200: responses.append((status, value))
        with patch.dict("os.environ", {"VERCEL": "1"}):
            handler.do_GET()
        self.assertEqual(responses[-1][0], 200)
        self.assertFalse(responses[-1][1]["alerts"])
        self.assertTrue(responses[-1][1]["hosted_preview"])
        with patch.dict("os.environ", {"VERCEL": ""}):
            handler.do_GET()
        self.assertTrue(responses[-1][1]["alerts"])
        self.assertFalse(responses[-1][1]["hosted_preview"])


def small_case():
    tracts = []
    for i, population, priority in [(1, 1000, .8), (2, 500, .6)]:
        tracts.append({"id": f"37001000{i}00", "county_id": "37001", "county": "Example", "eligible_population": population, "population_moe": 0, "no_vehicle_pct": 20 * priority, "no_vehicle_moe": 0, "indicators": {"bphigh": {"value": 15 + 40 * priority, "lower": 15 + 40 * priority, "upper": 15 + 40 * priority}}})
    venues = [{"id": f"venue-{i}", "name": f"Venue {i}", "county": "Example", "address": "Example address", "city": "Example", "lat": 35, "lon": -80, "source": "synthetic"} for i in [1, 2]]
    care = [{"id": "care-1", "name": "Verified example care", "service_verified": False, "eligibility_verified": False, "schedule_verified": False}]
    state = {"tracts": tracts, "venues": venues, "existing_services": care, "counties": {"37001": "Example"}, "sources": [], "candidate_selection": "Synthetic worked example"}
    times = np.array([[20., 40., 40.], [40., 20., 45.]])
    cfg = default_config(date(2026, 10, 10))
    cfg["travel_variation"] = 0
    cfg["region"] = "US"
    return state, times, cfg


class ModelTests(unittest.TestCase):
    def test_prd_worked_example_and_capacity(self):
        state, times, config = small_case()
        result = run_plan(state, times, config)
        a, b = result["recommendations"]
        self.assertEqual((a["score"], b["score"]), (800, 300))
        self.assertEqual((a["approx_population_in_newly_covered_tracts"], b["approx_population_in_newly_covered_tracts"]), (1000, 500))
        self.assertEqual(a["event_capacity"], 48)
        self.assertEqual(a["clinical_throughput"], 60)
        self.assertIsNone(a["expected_attendance"])
        self.assertFalse(a["robust"], "Top-N cannot discriminate when candidates <= N")

    def test_multi_event_no_double_counting_and_budget(self):
        state, times, cfg = small_case()
        cfg["events"] = 2
        result = run_plan(state, times, cfg)
        self.assertEqual(result["schedule_score"], 1100)
        self.assertEqual(result["schedule_population"], 1500)
        self.assertEqual(len({s["date"] for s in result["schedule"]}), 2)
        times[1, 0] = 20
        result = run_plan(state, times, cfg)
        self.assertEqual(result["schedule_population"], 1500)
        self.assertEqual(result["schedule_score"], 1100)
        cfg["budget"] = 650
        result = run_plan(state, times, cfg)
        self.assertLessEqual(result["schedule_cost"], cfg["budget"])

    def test_unapproved_venue_never_ranks(self):
        state, times, cfg = small_case()
        cfg["venue_overrides"] = {"venue-1": {"host_approved": None}}
        result = run_plan(state, times, cfg)
        self.assertEqual([r["site"]["id"] for r in result["recommendations"]], ["venue-2"])
        self.assertIn("not confirmed", " ".join(result["excluded"][0]["reasons"]))

    def test_unknown_baseline_and_no_route_are_not_zero(self):
        baseline, _ = baselines(np.array([[np.nan], [20.]]), [0])
        self.assertTrue(np.isnan(baseline[0]))
        self.assertFalse(coverage_matrix(baseline, np.array([[10.], [np.nan]]), 30).any())
        state, times, cfg = small_case()
        cfg["excluded_services"] = ["care-1"]
        result = run_plan(state, times, cfg)
        self.assertEqual(result["scope"]["unknown_baseline_tracts"], 2)
        self.assertEqual(result["schedule_population"], 0)
        self.assertTrue(all(r["score"] == 0 for r in result["recommendations"]))

    def test_same_seed_and_inputs_exactly_reproduce(self):
        state, times, cfg = small_case()
        a = run_plan(state, times, cfg)
        b = run_plan(state, times, cfg)
        self.assertEqual(a, b)

    def test_missing_indicator_is_excluded_in_scores_and_simulation(self):
        state, times, cfg = small_case()
        state["tracts"][0]["indicators"]["bphigh"]["value"] = None
        result = run_plan(state, times, cfg)
        self.assertEqual(result["recommendations"][0]["site"]["id"], "venue-2")
        self.assertEqual(result["recommendations"][1]["score"], 0)
        self.assertEqual(result["recommendations"][0]["first_frequency"], 1)

    def test_closed_venue_and_insufficient_staff(self):
        state, times, cfg = small_case()
        cfg["venue_overrides"] = {"venue-1": {"available": False}}
        self.assertEqual(run_plan(state, times, cfg)["recommendations"][0]["site"]["id"], "venue-2")
        cfg["staff_count"] = 1
        self.assertEqual(run_plan(state, times, cfg)["candidates"], 0)

    def test_weight_endpoints_and_indicator_removal(self):
        state, times, cfg = small_case()
        for w in [0, .2, .8, 1]:
            cfg.update(health_weight=w, weight_min=0, weight_max=1)
            result = run_plan(state, times, cfg)
            self.assertEqual(result["recommendations"][0]["score"], 800)
        np.testing.assert_equal(normalize(np.array([0, 35, 90]), [15, 55]), [0, .5, 1])

    def test_unrelated_stale_and_suppressed_trends_have_no_effect(self):
        state, _, cfg = small_case()
        cfg["trend_strength"] = .2
        series = {"service": "respiratory", "valid": True, "geography_type": "county", "geography_id": "37001", "recent_rate": 2, "baseline_rate": 1}
        np.testing.assert_equal(trend_multipliers(state["tracts"], cfg, [series])[0], [1, 1])
        series["service"] = "blood-pressure"
        np.testing.assert_equal(trend_multipliers(state["tracts"], cfg, [series])[0], [1.2, 1.2])
        series["stale"] = True
        np.testing.assert_equal(trend_multipliers(state["tracts"], cfg, [series])[0], [1, 1])

    def test_greedy_matches_small_exact_optimum(self):
        coverage = np.array([[1, 0, 1], [1, 0, 0], [0, 1, 0]], dtype=bool)
        weights = np.array([800, 300, 400])
        candidates = [{"id": str(i), "name": str(i), "cost": 100, "available_dates": ["2026-10-20", "2026-10-21"]} for i in range(3)]
        cfg = default_config(); cfg["events"] = 2
        schedule, selected = greedy_schedule(coverage, weights, candidates, cfg, {str(i): 10 for i in range(3)})
        exact = max(weights @ coverage[:, combo].any(axis=1) for combo in itertools.combinations(range(3), 2))
        self.assertEqual(weights @ selected, exact)
        self.assertEqual(sum(s["cost"] for s in schedule), 200)

    def test_weather_policy_requires_county_date_type_and_freshness(self):
        venues = [{"id": "v1", "county_id": "37001"}, {"id": "v2", "county_id": "37003"}]
        signals = [{"feed": "nws-alerts", "stale": False, "data": {"items": [{"id": "a", "event": "Tornado Warning", "onset": "2026-10-20T10:00:00-04:00", "expires": "2026-10-20T13:00:00-04:00", "geocode": {"SAME": ["037001"]}}]}}]
        cfg = {"date_start": "2026-10-20", "date_end": "2026-10-21", "weather_block_events": ["Tornado Warning"]}
        self.assertEqual(weather_blocks(venues, signals, cfg)[0], {"v1": ["2026-10-20"]})
        signals[0]["stale"] = True
        self.assertEqual(weather_blocks(venues, signals, cfg)[0], {})

    def test_weather_utc_timestamps_use_north_carolina_calendar_dates(self):
        venues = [{"id": "v1", "county_id": "37001"}]
        signals = [{"feed": "nws-alerts", "stale": False, "data": {"items": [{"id": "a", "event": "Tornado Warning", "onset": "2026-10-21T01:00:00+00:00", "expires": "2026-10-21T02:00:00+00:00", "geocode": {"SAME": ["037001"]}}]}}]
        cfg = {"date_start": "2026-10-20", "date_end": "2026-10-21", "weather_block_events": ["Tornado Warning"]}
        self.assertEqual(weather_blocks(venues, signals, cfg)[0], {"v1": ["2026-10-20"]})

    def test_invalid_contract_inputs(self):
        _, _, cfg = small_case()
        for updates in [{"budget": -1}, {"events": 1.5}, {"seed": float("nan")}, {"region": "27514"}, {"venue_overrides": {"x": {"host_approved": "yes"}}}]:
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                validate_config({**cfg, **updates}, {"37001": "Example"})


class PersistenceTests(unittest.TestCase):
    def test_failed_feed_retains_last_known_and_stale_state(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db.sqlite")
            store.observation("feed", {"count": 7}, "2026-10-10T10:00:00Z")
            store.observation("feed", None, error="Feed unavailable")
            current = store.feed_state("feed")
            self.assertTrue(current["stale"])
            self.assertEqual(current["data"]["count"], 7)

    def test_nws_alerts_are_stored_with_source_and_no_credentials(self):
        features = [{"id": "a1", "properties": {"event": "Tornado Warning", "effective": "2026-10-20T10:00:00-04:00", "geocode": {"SAME": ["037001"]}}, "geometry": None}]
        with tempfile.TemporaryDirectory() as folder, patch.dict("os.environ", {"DRIVENC_API_KEY": "test-key"}), \
                patch("planner.feeds.get_json", return_value={"features": features}) as fetch:
            store = Store(Path(folder) / "db.sqlite")
            signals = refresh(store)
        alerts = next(s for s in signals if s["feed"] == "nws-alerts")
        self.assertFalse(alerts["stale"])
        self.assertEqual(alerts["data"]["count"], 1)
        self.assertEqual(alerts["data"]["items"][0]["event"], "Tornado Warning")
        self.assertNotIn("test-key", json.dumps(signals))
        self.assertNotIn("key=", fetch.call_args.args[0])

    def test_nws_failure_marks_feed_stale_without_data(self):
        with tempfile.TemporaryDirectory() as folder, patch("planner.feeds.get_json", side_effect=ValueError("NWS unavailable")):
            store = Store(Path(folder) / "db.sqlite")
            alerts = next(s for s in refresh(store) if s["feed"] == "nws-alerts")
        self.assertTrue(alerts["stale"])
        self.assertIn("NWS unavailable", alerts["error"])
        self.assertIsNone(alerts["data"])


class InterfaceContractTests(unittest.TestCase):
    def test_default_configuration_matches_static_select_values(self):
        class SelectOptions(HTMLParser):
            def __init__(self):
                super().__init__()
                self.name = None
                self.options = {}

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "select":
                    self.name = attrs.get("name")
                    if self.name:
                        self.options[self.name] = []
                elif tag == "option" and self.name:
                    self.options[self.name].append(attrs.get("value"))

            def handle_endtag(self, tag):
                if tag == "select":
                    self.name = None

        parser = SelectOptions()
        parser.feed((ROOT / "web" / "index.html").read_text())
        for key, value in default_config().items():
            if key in parser.options and parser.options[key]:
                with self.subTest(field=key):
                    self.assertIn(str(value), parser.options[key])


class SitingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.app = Application(runtime_dir=cls.folder.name)
        cls.durham = cls.app.county_centers("NC")["37063"]

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def params(self, **kw):
        return {"state": "NC", "lat": self.durham[0], "lon": self.durham[1], "radius": 30, "travel_target": 20, "days": 3, "staff_count": 3, "clinical_hours": 4, "services_per_staff_hour": 5, **kw}

    def test_clinics_are_inside_radius_and_unique(self):
        plan = self.app.site(self.params())
        self.assertTrue(1 <= len(plan["clinics"]) <= 3)
        self.assertEqual(len({c["id"] for c in plan["clinics"]}), len(plan["clinics"]))
        self.assertTrue(all(c["distance_from_hospital_miles"] <= plan["radius_miles"] + .1 for c in plan["clinics"]))
        self.assertEqual(plan["summary"]["estimated_cost"], len(plan["clinics"]) * 1060)
        self.assertEqual(plan["clinics"][0]["capacity"], 60)
        self.assertEqual(sum(c["new_adults"] for c in plan["clinics"]), plan["summary"]["adults_newly_served"])

    def test_deterministic_and_larger_radius_never_has_fewer_candidates(self):
        a, b = self.app.site(self.params()), self.app.site(self.params())
        self.assertEqual([c["id"] for c in a["clinics"]], [c["id"] for c in b["clinics"]])
        self.assertGreaterEqual(self.app.site(self.params(radius=60))["summary"]["candidate_sites"], a["summary"]["candidate_sites"])

    def test_hospital_name_is_geocoded(self):
        fake = {"lat": self.durham[0], "lon": self.durham[1], "address": "1 Test Rd, Durham, NC"}
        with patch("server.geocode.locate", return_value=fake) as locate:
            plan = self.app.site({"state": "NC", "hospital_name": "Test Hospital", "radius": 30, "travel_target": 20})
        locate.assert_called_once_with("Test Hospital", "NC")
        self.assertEqual(plan["hospital"]["address"], fake["address"])

    def test_dense_existing_care_lowers_priority_but_still_returns_a_plan(self):
        # Hundreds of health-center sites cover every NC tract at a 45-minute target; the plan must still come back.
        on = self.app.site(self.params(travel_target=45, use_existing_care=True))
        off = self.app.site(self.params(travel_target=45, use_existing_care=False))
        self.assertTrue(on["clinics"])
        self.assertGreater(on["summary"]["adults_in_reach_near_existing_care"], 0)
        self.assertEqual(off["summary"]["adults_in_reach_near_existing_care"], 0)
        self.assertEqual(on["summary"]["adults_newly_served"], sum(c["new_adults"] for c in on["clinics"]))

    def test_every_chronic_disease_service_can_be_planned(self):
        from planner.model import SERVICES
        self.assertEqual(len(SERVICES), 10)
        for key in SERVICES:
            with self.subTest(service=key):
                plan = self.app.site(self.params(service=key, travel_target=15, use_existing_care=False))
                self.assertTrue(plan["clinics"])
                self.assertEqual(plan["clinics"][0]["service"], key)
                self.assertIsNotNone(plan["clinics"][0]["avg_indicator"])

    def test_validation(self):
        for bad in [{"radius": 25}, {"lat": 10}, {"days": 11}, {"days": 0}, {"staff_count": 1}, {"clinical_hours": 20}, {"date_start": "2026-12-02", "date_end": "2026-12-01"}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.app.site(self.params(**bad))


class ElevenLabsTests(unittest.TestCase):
    def test_wav_voice_note_uses_matching_upload_filename(self):
        from planner import elevenlabs
        wav = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"audio samples"
        with patch("planner.elevenlabs._key", return_value="test-key"), \
                patch("planner.elevenlabs._call", return_value=b'{"text":"hola","language_code":"es"}') as send:
            result = elevenlabs.transcribe(wav, "audio/wav")
        self.assertEqual(result, {"text": "hola", "language": "es"})
        upload = send.call_args.args[0].data
        self.assertIn(b'filename="voice-note.wav"', upload)
        self.assertIn(b"Content-Type: audio/wav", upload)

    def test_corrupt_wav_is_rejected_before_upload(self):
        from planner import elevenlabs
        with patch("planner.elevenlabs._call") as send:
            with self.assertRaisesRegex(RuntimeError, "not a playable WAV"):
                elevenlabs.transcribe(b"broken audio", "audio/wav")
        send.assert_not_called()


class BotTests(unittest.TestCase):
    PLANS = [{"hospital": {"name": "Test Hospital"}, "clinics": [
        {"id": "a", "name": "Near Library", "address": "1 Main St", "city": "Raleigh", "county": "Wake", "lat": 35.78, "lon": -78.64, "date": "2026-11-02", "service": "blood-pressure"},
        {"id": "b", "name": "Far Library", "address": "2 Oak St", "city": "Asheville", "county": "Buncombe", "lat": 35.6, "lon": -82.55, "date": "2026-10-20", "service": "blood-pressure"},
        {"id": "c", "name": "Old Library", "address": "3 Elm St", "city": "Raleigh", "county": "Wake", "lat": 35.78, "lon": -78.64, "date": "2026-01-05", "service": "diabetes"}]}]

    def test_filters_past_dates_and_service_and_ranks_by_distance(self):
        from planner import bot
        today = date(2026, 10, 10)
        near = bot.upcoming(self.PLANS, "blood-pressure", {"lat": 35.78, "lon": -78.64}, today)
        self.assertEqual([c["id"] for c in near], ["a", "b"])
        self.assertEqual(bot.upcoming(self.PLANS, "diabetes", None, today), [])
        self.assertEqual([c["id"] for c in bot.upcoming(self.PLANS, None, None, today)], ["b", "a"])

    def test_every_service_is_recognized_from_plain_language(self):
        from planner import bot
        from planner.model import SERVICES
        self.assertEqual(set(bot.KEYWORDS), set(SERVICES))
        asks = {"obesity": "free weight screening", "highchol": "cholesterol check", "chd": "heart health clinic",
                "stroke": "stroke risk screening", "arthritis": "arthritis support", "depression": "depression screening"}
        with patch("planner.bot.gemini.available", return_value=False):
            for key, text in asks.items():
                self.assertEqual(bot.understand(text)["service"], key)

    def test_generic_clinic_answer_does_not_repeat_free_clinic(self):
        from planner import bot
        reply = bot.english_reply({"found": False, "service": "free clinic"})
        self.assertIn("upcoming free clinic", reply)
        self.assertNotIn("free free clinic", reply)

    def talk(self, store, text, **kw):
        from planner import bot
        with patch("planner.bot.gemini.available", return_value=False), \
                patch("planner.bot.geocode.locate_place", return_value={"lat": 35.78, "lon": -78.64, "label": "Raleigh, NC, USA"}):
            return bot.handle(self.PLANS, store, {"sender": "imessage:+15550001", "text": text, **kw})["reply"]

    def test_new_person_is_onboarded_and_acknowledged(self):
        from planner.subscribers import Subscribers
        with tempfile.TemporaryDirectory() as d:
            store = Subscribers(Path(d) / "s.json")
            self.assertIn("what's your name", self.talk(store, "yo").lower())     # greeting is not a name
            self.assertIn("city or ZIP", self.talk(store, "Maria"))
            self.assertIn("Which free clinics", self.talk(store, "Raleigh"))
            done = self.talk(store, "blood pressure and diabetes")
            self.assertIn("I'll text you", done)
            self.assertIn("Maria", done)
            person = store.get("imessage:+15550001")
            self.assertEqual((person["name"], person["stage"], sorted(person["services"])), ("Maria", "done", ["blood-pressure", "diabetes"]))
            self.assertIn("Hi Maria", self.talk(store, "hey"))
            self.assertIn("unsubscribed", self.talk(store, "STOP"))
            self.assertTrue(store.get("imessage:+15550001")["stopped"])

    def test_browser_preview_keeps_profile_without_promising_alerts(self):
        from planner import bot
        with patch("planner.bot.gemini.available", return_value=False):
            store = PreviewStore(None)
            first = bot.handle([], store, {"sender": "web-test", "text": "When is the next blood pressure clinic?"}, preview=True)
            self.assertIn("proposed", first["reply"])
            self.assertIn("Plan a clinic", first["reply"])
            self.assertNotIn("I'll text you", first["reply"])
            self.assertNotIn("what's your name", first["reply"].lower())
            self.assertEqual(store.profile["stage"], "chat")
            next_request = PreviewStore(store.profile)
            second = bot.handle([], next_request, {"sender": "web-test", "text": "Hello, how are you doing?"}, preview=True)
            self.assertIn("doing well", second["reply"])
            self.assertNotIn("what's your name", second["reply"].lower())
            bot.handle([], next_request, {"sender": "web-test", "reset": True}, preview=True)
            self.assertIsNone(next_request.profile)

    def test_local_browser_explains_empty_plan_store(self):
        from planner import bot
        with patch("planner.bot.gemini.available", return_value=True), \
                patch("planner.bot.gemini.generate", return_value='{"intent":"ask_clinic","services":["blood-pressure"],"place":"Raleigh"}'):
            result = bot.handle([], PreviewStore(None),
                                {"sender": "web-test", "text": "When is the next blood pressure clinic near Raleigh?"})
        self.assertIn("no clinic proposals saved here", result["reply"].lower())
        self.assertIn("Plan a clinic", result["reply"])
        self.assertNotIn("Please check back soon", result["reply"])

    def test_browser_first_contact_greeting_needs_no_gemini_or_signup(self):
        from planner import bot
        with patch("planner.bot.gemini.available", return_value=True), \
                patch("planner.bot.gemini.generate") as generate:
            result = bot.handle([], PreviewStore(None),
                                {"sender": "web-test", "text": "Hello, how are you doing?"}, preview=True)
        self.assertIn("doing well, thanks for asking", result["reply"])
        generate.assert_not_called()
        self.assertNotIn("phone number", result["reply"].lower())

    def test_browser_preview_answers_from_proposed_plan(self):
        from planner import bot
        profile = {"name": "Maria", "place": "Raleigh, NC", "lat": 35.78, "lon": -78.64,
                   "services": ["blood-pressure"], "language": "eng", "stage": "done", "stopped": False}
        with patch("planner.bot.gemini.available", return_value=False):
            result = bot.handle(self.PLANS, PreviewStore(profile),
                                {"sender": "web-test", "text": "When is the next blood pressure clinic?"}, preview=True)
        self.assertIn("Near Library", result["reply"])
        self.assertIn("proposed plan, not a confirmed event", result["reply"])

    def test_local_browser_labels_matching_plan_as_proposal(self):
        from planner import bot
        profile = {"name": "Maria", "place": "Raleigh, NC", "lat": 35.78, "lon": -78.64,
                   "services": ["blood-pressure"], "language": "eng", "stage": "done", "stopped": False}
        with patch("planner.bot.gemini.available", return_value=False):
            result = bot.handle(self.PLANS, PreviewStore(profile),
                                {"sender": "web-test", "text": "When is the next blood pressure clinic?"})
        self.assertIn("Near Library", result["reply"])
        self.assertIn("proposed plan, not a confirmed event", result["reply"])

    def test_browser_preview_uses_gemini_for_grounded_clinic_answer(self):
        from planner import bot
        profile = {"name": "Maria", "place": "Raleigh, NC", "lat": 35.78, "lon": -78.64,
                   "services": ["blood-pressure"], "language": "eng", "stage": "done", "stopped": False}
        with patch("planner.bot.gemini.available", return_value=True), \
                patch("planner.bot.gemini.generate", return_value="Near Library has a proposed clinic on November 2.") as generate:
            result = bot.handle(self.PLANS, PreviewStore(profile),
                                {"sender": "web-test", "text": "When is the next blood pressure clinic?"}, preview=True)
        self.assertIn("Near Library has a proposed clinic", result["reply"])
        self.assertIn("not a confirmed event", result["reply"])
        self.assertIn("Near Library", generate.call_args.args[0])

    def test_spanish_text_reply_survives_english_model_classification(self):
        from planner import bot
        parsed = {"intent": "ask_clinic", "name": None, "place": "Raleigh", "services": ["blood-pressure"], "language": "eng"}
        prompts = []

        def generate(prompt, **kwargs):
            prompts.append(prompt)
            return "La próxima clínica propuesta es en Near Library. Es un plan propuesto, no un evento confirmado."

        with patch("planner.bot._parse", return_value=parsed), \
                patch("planner.bot.geocode.locate_place", return_value={"lat": 35.78, "lon": -78.64, "label": "Raleigh"}), \
                patch("planner.bot.gemini.available", return_value=True), \
                patch("planner.bot.gemini.generate", side_effect=generate):
            result = bot.handle(self.PLANS, PreviewStore(None),
                                {"sender": "web-test", "text": "¿Cuándo es la próxima jornada de control de presión arterial cerca de Raleigh?"})
        self.assertEqual(result["language"], "spa")
        self.assertTrue(result["reply"].startswith("La próxima clínica"))
        self.assertEqual(len(prompts), 1)
        self.assertIn("Reply only in Spanish", prompts[0])
        self.assertEqual(result["reply"].count("no un evento confirmado"), 1)

    def test_notifications_only_reach_matching_opted_in_people(self):
        from planner import bot
        from planner.subscribers import Subscribers
        with tempfile.TemporaryDirectory() as d:
            store = Subscribers(Path(d) / "s.json")
            base = {"name": "A", "lat": 35.78, "lon": -78.64, "stage": "done", "stopped": False, "language": "eng"}
            store.put("near-bp", {**base, "services": ["blood-pressure"]})
            store.put("near-diabetes", {**base, "services": ["diabetes"]})
            store.put("far-bp", {**base, "lat": 35.6, "lon": -82.55, "services": ["blood-pressure"]})
            store.put("stopped", {**base, "services": ["blood-pressure"], "stopped": True})
            clinic = self.PLANS[0]["clinics"][0]
            with patch("planner.bot.gemini.available", return_value=False):
                out = bot.notifications(store, clinic)
        self.assertEqual([m["sender"] for m in out], ["near-bp"])

    def test_signup_normalizes_phone_requires_consent_and_queues_welcome(self):
        from planner import bot
        from planner.subscribers import Subscribers
        good = {"name": "Maria", "phone": "(919) 555-0123", "place": "Raleigh", "services": ["diabetes"], "consent": True, "language": "eng"}
        with tempfile.TemporaryDirectory() as d, patch("planner.bot.gemini.available", return_value=False), \
                patch("planner.bot.geocode.locate_place", return_value={"lat": 35.78, "lon": -78.64, "label": "Raleigh, NC, USA"}):
            store = Subscribers(Path(d) / "s.json")
            for bad in [{"consent": False}, {"phone": "123"}, {"name": ""}]:
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    bot.signup(store, {**good, **bad})
            bot.signup(store, good)
            self.assertEqual(store.get("+19195550123")["services"], ["diabetes"])
            self.assertEqual([m["sender"] for m in store.drain()], ["+19195550123"])
            bot.signup(store, good)                       # signing up twice does not text twice
            self.assertEqual(store.drain(), [])

    def test_voice_in_voice_out(self):
        import base64
        from planner import bot
        from planner.subscribers import Subscribers
        with tempfile.TemporaryDirectory() as d, patch("planner.bot.gemini.available", return_value=False), \
                patch("planner.bot.geocode.locate_place", return_value=None), \
                patch("planner.bot.elevenlabs.transcribe", return_value={"text": "presion arterial", "language": "spa"}), \
                patch("planner.bot.elevenlabs.synthesize", return_value=b"mp3"), patch("planner.bot.elevenlabs.to_m4a", return_value=(b"m4a", "audio/mp4")):
            voice = bot.handle(self.PLANS, Subscribers(Path(d) / "s.json"), {"sender": "v", "audio_b64": base64.b64encode(b"x").decode(), "mime": "audio/mp4"})
            self.assertEqual((voice["mode"], voice["language"], voice["audio_mime"]), ("voice", "spa", "audio/mp4"))
            self.assertEqual(base64.b64decode(voice["audio_b64"]), b"m4a")
            with self.assertRaises(ValueError):
                bot.handle(self.PLANS, Subscribers(Path(d) / "s.json"), {})


class PlanChatTests(unittest.TestCase):
    def test_context_has_what_ifs_and_communities_and_needs_gemini(self):
        from planner import planchat
        app = Application(runtime_dir=tempfile.mkdtemp())
        durham = app.county_centers("NC")["37063"]
        state = app.current_state("NC")
        params = {"state": "NC", "lat": durham[0], "lon": durham[1], "radius": 30, "travel_target": 20, "days": 2}
        ctx = planchat.context(state, None, params)
        changes = [s["change"] for s in ctx["what_if_scenarios"]]
        self.assertIn("current plan", changes)
        self.assertIn("patient travel target 15 min", changes)
        self.assertTrue(ctx["communities"]["prioritized"])
        self.assertEqual(ctx["what_if_scenarios"][0]["adults_newly_in_reach"], ctx["summary"]["adults_newly_served"])
        with patch("planner.planchat.gemini.available", return_value=False), self.assertRaises(RuntimeError):
            planchat.answer(state, None, params, [{"role": "user", "text": "why?"}])


class StatewideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.app = Application(runtime_dir=cls.folder.name)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_statewide_data_and_routes_contract(self):
        state = self.app.current_state("NC")
        self.assertEqual(len(state["counties"]), 100)
        self.assertGreater(len(state["tracts"]), 2000)
        ids = [t["id"] for t in state["tracts"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(len(i) == 11 and i.startswith("37") and i.isdigit() for i in ids))
        self.assertTrue(all("bookmobile" not in v["name"].lower() for v in state["venues"]))
        self.assertTrue(state["venues"] and state["existing_services"])
        self.assertTrue(all(t["state"] == "NC" for t in state["tracts"]))
        from planner.model import SERVICES
        for key, service in SERVICES.items():
            with self.subTest(service=key):
                values = [t["indicators"][service["indicator"]]["value"] for t in state["tracts"]
                          if t["indicators"].get(service["indicator"], {}).get("value") is not None]
                self.assertGreater(len(values), 2000)
                low, high = service["benchmark"]
                self.assertLess(low, np.median(values))
                self.assertGreater(high, np.median(values))
        self.assertTrue(all(t["population_source"] and t["transport_source"] for t in state["tracts"]))

    def test_export_reproduction_versioned_closure_and_outcomes(self):
        original = self.app.run({**default_config(), "region": "NC"}, "Initial check")
        saved = self.app.store.get_run(original["id"], export=True)
        reproduce(saved)
        first_id = original["recommendations"][0]["site"]["id"]
        cfg = {**original["config"], "venue_overrides": {first_id: {"available": False}}}
        updated = self.app.run(cfg, "Venue closed")
        self.assertNotIn(first_id, [r["site"]["id"] for r in updated["recommendations"]])
        self.assertEqual(self.app.store.get_run(original["id"])["recommendations"], original["recommendations"])
        self.app.store.save_outcome(original["id"], {"turnout": 15, "services_completed": 12})
        self.assertEqual(self.app.store.get_run(original["id"])["input_hash"], original["input_hash"])

    def test_confirmed_mode_does_not_use_sample_approvals(self):
        cfg = {**default_config(), "region": "NC", "mode": "confirmed"}
        result = self.app.run(cfg, "Confirmed-only check")
        self.assertEqual(result["candidates"], 0)
        self.assertEqual(result["scope"]["unknown_baseline_tracts"], len(self.app.current_state("NC")["tracts"]))

    def test_real_confirmations_produce_a_confirmed_plan(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Application(runtime_dir=folder)
            cfg = {**default_config(), "region": "NC", "mode": "confirmed"}
            state = app.current_state("NC")
            verified_at = date.today().isoformat()
            venue = {"id": state["venues"][0]["id"], "details_verified": True, "host_approved": True,
                     "accessible": True, "private_space": True, "power": True, "available": True,
                     "cost": 600, "space_throughput": 48, "verified_by": "Test verifier",
                     "verified_at": verified_at, "available_dates": cfg["date_start"]}
            service = {"id": state["existing_services"][0]["id"], "service": "blood-pressure",
                       "service_verified": True, "eligibility_verified": True, "schedule_verified": True,
                       "verified_by": "Test verifier", "verified_at": verified_at,
                       "schedule_start": cfg["date_start"], "schedule_end": cfg["date_end"]}
            for kind, record in [("venues", venue), ("services", service)]:
                contents = io.StringIO()
                writer = csv.DictWriter(contents, fieldnames=list(record))
                writer.writeheader()
                writer.writerow(record)
                self.assertEqual(app.import_csv(kind, contents.getvalue(), "NC"), {"imported": 1, "errors": []})
            result = app.run(cfg, "Real confirmation workflow test")
            self.assertEqual(result["candidates"], 1)
            self.assertEqual(result["scope"]["unknown_baseline_tracts"], 0)
            plan = result["recommendations"][0]
            self.assertEqual(plan["site"]["id"], venue["id"])
            self.assertEqual(plan["event_capacity"], 48)
            self.assertNotIn("sample", plan["feasibility"].lower())

    def test_csv_errors_are_atomic_and_no_zip_conversion(self):
        kind = "series"
        fields = ["signal", "service", "geography_type", "geography_id", "period_start", "period_end", "value", "denominator", "unit", "baseline_rate", "published_at", "source", "revision_id"]
        values = ["referrals", "blood-pressure", "tract", "27514", "2026-09-01", "2026-09-30", "10", "0", "referrals per adult", ".1", "2026-10-01", "Partner", "1"]
        response = self.app.import_csv(kind, ",".join(fields) + "\n" + ",".join(values), "NC")
        self.assertEqual(response["imported"], 0)
        self.assertTrue(response["errors"])

    def test_simulation_convergence(self):
        a = self.app.run({**default_config(), "region": "NC"}, "1000 draw convergence check")
        cfg = {**a["config"], "simulations": 5000}
        b = self.app.run(cfg, "5000 draw convergence check")
        by_id = {r["site"]["id"]: r for r in b["recommendations"]}
        changes = {r["site"]["name"]: abs(r["top_n_frequency"] - by_id[r["site"]["id"]]["top_n_frequency"]) for r in a["recommendations"][:3]}
        print("\nLeading-site convergence differences:", changes)
        self.assertLessEqual(max(changes.values()), .03, "Increase production draws and report instability if leading frequencies differ >3 points")


if __name__ == "__main__":
    unittest.main()
