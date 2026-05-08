import json
import sqlite3
import unittest

from scripts.clean_bad_aprs_decoded_metadata import (
    apply_cleanup,
    cleanup_candidates,
)


STATION = {"lat": 39.9612, "lon": -82.9988}


def row_for(metadata: dict, *, event_lat=None, event_lon=None, callsign="BAD-9") -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE rows (id INTEGER, callsign TEXT, lat REAL, lon REAL, metadata_json TEXT)"
    )
    conn.execute(
        "INSERT INTO rows VALUES (?, ?, ?, ?, ?)",
        (1, callsign, event_lat, event_lon, json.dumps(metadata)),
    )
    return conn.execute("SELECT * FROM rows").fetchone()


class AprsMaintenanceTests(unittest.TestCase):
    def test_cleanup_candidate_removes_positive_longitude_bad_decode(self):
        row = row_for({
            "decoded_lat": 53.26,
            "decoded_lon": 147.21,
            "distance_miles": 5335,
            "distance_km": 8585,
            "distance_nmi": 4635,
            "bearing_degrees": 321,
            "distance_quality": "questionable",
            "direwolf_decoded_text": "N 40 46.9100, W 081 53.2600, 147.210 MHz",
        })

        candidates = cleanup_candidates([row], STATION)

        self.assertEqual(len(candidates), 1)
        cleaned = candidates[0]["cleaned_metadata"]
        for key in ("decoded_lat", "decoded_lon", "distance_miles", "distance_quality", "bearing_degrees"):
            self.assertNotIn(key, cleaned)
        self.assertIn("direwolf_decoded_text", cleaned)

    def test_cleanup_does_not_touch_plausible_decoded_mobile(self):
        row = row_for({
            "decoded_lat": 40.784833,
            "decoded_lon": -82.255167,
            "speed_mph": 64.0,
            "course_degrees": 98,
            "altitude_ft": 1115,
            "distance_miles": 45.0,
            "distance_quality": "normal",
        })

        self.assertEqual(cleanup_candidates([row], STATION), [])

    def test_apply_can_clear_event_position_when_it_came_from_bad_decode(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE events (id INTEGER, event_type TEXT, callsign TEXT, lat REAL, lon REAL, metadata_json TEXT)"
        )
        metadata = {
            "decoded_lat": 53.26,
            "decoded_lon": 147.21,
            "distance_miles": 5335,
            "distance_quality": "questionable",
        }
        conn.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
            (1, "aprs_packet", "BAD-9", 53.26, 147.21, json.dumps(metadata)),
        )

        rows = conn.execute("SELECT id, callsign, lat, lon, metadata_json FROM events").fetchall()
        candidates = cleanup_candidates(rows, STATION)
        apply_cleanup(conn, candidates)
        cleaned = conn.execute("SELECT lat, lon, metadata_json FROM events WHERE id = 1").fetchone()
        cleaned_metadata = json.loads(cleaned["metadata_json"])

        self.assertIsNone(cleaned["lat"])
        self.assertIsNone(cleaned["lon"])
        self.assertNotIn("decoded_lat", cleaned_metadata)
        self.assertNotIn("distance_miles", cleaned_metadata)


if __name__ == "__main__":
    unittest.main()
