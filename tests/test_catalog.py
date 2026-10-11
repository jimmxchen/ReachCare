import sqlite3
import tempfile
import unittest
from pathlib import Path

from planner.catalog import NationalCatalog


ROOT = Path(__file__).resolve().parents[1]
STATE_CODES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())


class NationalCatalogTest(unittest.TestCase):
    def test_all_states_have_source_records_and_tract_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog = NationalCatalog(ROOT / "data/us.sqlite3.gz", folder)
            states = catalog.states()
            self.assertEqual({state["code"] for state in states}, STATE_CODES)
            for state in states:
                with self.subTest(state=state["code"]):
                    self.assertGreater(state["tracts"], 0)
                    self.assertGreater(state["libraries"], 0)
                    self.assertGreater(state["health_centers"], 0)
            with sqlite3.connect(catalog.path) as db:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(db.execute("SELECT COUNT(*) FROM tracts t LEFT JOIN tract_shapes s ON t.id=s.id WHERE s.id IS NULL").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM tracts WHERE json_extract(data,'$.transport_source')='acs_2024_B08201'").fetchone()[0], sum(s["tracts"] for s in states))

    def test_state_pages_are_scoped(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog = NationalCatalog(ROOT / "data/us.sqlite3.gz", folder)
            page = catalog.records("tracts", "CA", limit=2, offset=1)
            self.assertEqual(page["state"], "CA")
            self.assertEqual(len(page["records"]), 2)
            self.assertTrue(all(record["state"] == "CA" for record in page["records"]))
            with self.assertRaises(ValueError):
                catalog.records("tracts", "ZZ")


if __name__ == "__main__":
    unittest.main()
