import unittest
from pathlib import Path

import backend.db as db
from backend.ingestors.aprs_direwolf import (
    decoded_followup_metadata,
    enrich_packet,
    parse_decoded_followup_line,
    parse_gate_confirmation,
)


STATION = {"lat": 39.9612, "lon": -82.9988}


class AprsDirewolfParserTests(unittest.TestCase):
    def test_ig_tx_without_q_construct_is_not_confirmed_gating(self):
        result = parse_gate_confirmation(
            "ig>tx",
            "KD8NVS-1>APRS,WIDE1-1:!4000.00N/08300.00W-test",
            "KF8GBU-10",
        )

        self.assertIsNone(result)

    def test_q_construct_with_local_callsign_confirms_gating(self):
        result = parse_gate_confirmation(
            "ig",
            "KD8NVS-1>APRS,TCPIP*,qAR,KF8GBU-10:!4000.00N/08300.00W-test",
            " kf8gbu-10 ",
        )

        self.assertEqual(result["callsign"], "KD8NVS-1")
        self.assertEqual(result["gated_by"], "KF8GBU-10")
        self.assertTrue(result["confirmed_gated_by_me"])

    def test_q_construct_with_other_callsign_is_not_local_gating(self):
        result = parse_gate_confirmation(
            "ig",
            "KD8NVS-1>APRS,TCPIP*,qAR,OTHER-10:!4000.00N/08300.00W-test",
            "KF8GBU-10",
        )

        self.assertEqual(result["gated_by"], "OTHER-10")
        self.assertFalse(result["confirmed_gated_by_me"])

    def test_rf_packet_categories_stay_distinct(self):
        direct = enrich_packet(
            "[0] KD8NVS-1>APRS:!4000.00N/08300.00W-direct",
            None,
            STATION,
            True,
        )
        digipeated = enrich_packet(
            "[0] KC3ZLD-10>APRS,K8QIK-2*,WIDE1-1:!4000.00N/08300.00W-digi",
            None,
            STATION,
            True,
        )
        network = enrich_packet(
            "[ig] KD8NVS-1>APRS,TCPIP*,qAR,OTHER-10:!4000.00N/08300.00W-network",
            None,
            STATION,
            True,
        )

        self.assertEqual(direct["heard_category"], "direct_rf")
        self.assertTrue(direct["gate_eligible"])
        self.assertEqual(digipeated["heard_category"], "digipeated_rf")
        self.assertEqual(digipeated["preferred_heard_via"], "K8QIK-2")
        self.assertEqual(network["heard_category"], "aprs_is")
        self.assertFalse(network["heard_over_rf"])

    def test_aprs_status_hydrates_rf_total_without_network_side_packets(self):
        original_get_database_path = db.get_database_path
        test_db = Path.cwd() / "data" / "test-aprs-status.db"
        db.get_database_path = lambda config=None: test_db
        try:
            test_db.parent.mkdir(parents=True, exist_ok=True)
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(f"{test_db}{suffix}")
                if candidate.exists():
                    candidate.unlink()
            db.init_db(test_db)
            db.reset_aprs_status("KF8GBU-10")
            db.insert_event(
                event_type="aprs_packet",
                callsign="KD8NVS-1",
                metadata={"heard_category": "direct_rf", "heard_over_rf": True},
            )
            db.insert_event(
                event_type="aprs_packet",
                callsign="KC3ZLD-10",
                metadata={"heard_category": "digipeated_rf", "heard_over_rf": True},
            )
            db.insert_event(
                event_type="aprs_packet",
                callsign="NET-1",
                metadata={"heard_category": "aprs_is", "network_seen": True, "heard_over_rf": False},
            )

            hydrated = db.hydrate_aprs_status_from_recent_events("KF8GBU-10")
            status = db.fetch_aprs_status()
        finally:
            db.get_database_path = original_get_database_path
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(f"{test_db}{suffix}")
                if candidate.exists():
                    candidate.unlink()

        self.assertEqual(hydrated["rf_packets_heard_total"], 2)
        self.assertEqual(status["rf_packets_heard_total"], 2)
        self.assertEqual(status["unique_callsigns_seen"], 2)

    def test_decoded_followup_extracts_position_and_motion_fields(self):
        decoded = parse_decoded_followup_line(
            "MIC-E: En Route, 39 58.07 N 082 59.93 W, 35 MPH, course 123, altitude 902 ft, manufacturer=Kenwood"
        )

        self.assertIsNotNone(decoded)
        self.assertAlmostEqual(decoded["decoded_lat"], 39.967833, places=5)
        self.assertAlmostEqual(decoded["decoded_lon"], -82.998833, places=5)
        self.assertEqual(decoded["speed_mph"], 35.0)
        self.assertEqual(decoded["course_degrees"], 123)
        self.assertEqual(decoded["altitude_ft"], 902)
        self.assertEqual(decoded["mic_e_status"], "En Route")
        self.assertEqual(decoded["manufacturer"], "Kenwood")

    def test_unrelated_lines_do_not_parse_as_decoded_followup(self):
        self.assertIsNone(parse_decoded_followup_line("K8LU-9 audio level = 50(20/10) [+++]"))
        self.assertIsNone(parse_decoded_followup_line("Now connected to IGate server noam.aprs2.net (1.2.3.4)"))
        self.assertIsNone(parse_decoded_followup_line("KD8NVS-1>APRS:!4000.00N/08300.00W-test"))
        self.assertIsNone(parse_decoded_followup_line("[0.2] K8LU-9 audio level = 50(20/10) [+++]"))
        self.assertIsNone(parse_decoded_followup_line("[0.2] KD8NVS-1>APRS:!4000.00N/08300.00W-test"))
        self.assertIsNone(parse_decoded_followup_line("[ig] KD8NVS-1>APRS,TCPIP*,qAR,OTHER-10:!4000.00N/08300.00W-test"))
        self.assertIsNone(parse_decoded_followup_line("[ig>tx] KD8NVS-1>APRS,WIDE1-1:!4000.00N/08300.00W-test"))

    def test_prefixed_decoded_followup_still_parses(self):
        decoded = parse_decoded_followup_line(
            "[0.2] MIC-E: En Route, 39 58.07 N 082 59.93 W, 35 MPH, course 123"
        )

        self.assertIsNotNone(decoded)
        self.assertAlmostEqual(decoded["decoded_lat"], 39.967833, places=5)
        self.assertAlmostEqual(decoded["decoded_lon"], -82.998833, places=5)
        self.assertEqual(decoded["speed_mph"], 35.0)
        self.assertEqual(decoded["course_degrees"], 123)

    def test_decoded_followup_metadata_fills_missing_position_only(self):
        existing = {
            "heard_category": "digipeated_rf",
            "lat": None,
            "lon": None,
        }
        decoded = decoded_followup_metadata(
            "MIC-E: En Route, 39 58.07 N 082 59.93 W, 35 MPH, course 123",
            existing,
            STATION,
        )

        self.assertIsNotNone(decoded)
        updates, lat, lon = decoded
        self.assertEqual(updates["decoded_followup_lines"][0], "MIC-E: En Route, 39 58.07 N 082 59.93 W, 35 MPH, course 123")
        self.assertAlmostEqual(lat, 39.967833, places=5)
        self.assertAlmostEqual(lon, -82.998833, places=5)
        self.assertIn("distance_miles", updates)

        existing_position = {
            "heard_category": "direct_rf",
            "lat": 40.0,
            "lon": -83.0,
        }
        decoded_existing = decoded_followup_metadata(
            "MIC-E: En Route, 39 58.07 N 082 59.93 W",
            existing_position,
            STATION,
        )
        self.assertIsNotNone(decoded_existing)
        updates_existing, lat_existing, lon_existing = decoded_existing
        self.assertIsNone(lat_existing)
        self.assertIsNone(lon_existing)
        self.assertEqual(updates_existing["decoded_lat"], 39.967833)

    def test_update_aprs_event_metadata_preserves_existing_position(self):
        original_get_database_path = db.get_database_path
        test_db = Path.cwd() / "data" / "test-aprs-followup.db"
        db.get_database_path = lambda config=None: test_db
        try:
            test_db.parent.mkdir(parents=True, exist_ok=True)
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(f"{test_db}{suffix}")
                if candidate.exists():
                    candidate.unlink()
            db.init_db(test_db)
            null_event_id = db.insert_event(
                event_type="aprs_packet",
                callsign="K8LU-9",
                lat=None,
                lon=None,
                metadata={"heard_category": "digipeated_rf", "lat": None, "lon": None},
            )
            fixed_event_id = db.insert_event(
                event_type="aprs_packet",
                callsign="KD8NVS-1",
                lat=40.0,
                lon=-83.0,
                metadata={"heard_category": "direct_rf", "lat": 40.0, "lon": -83.0},
            )

            self.assertTrue(db.update_aprs_event_metadata(
                event_id=null_event_id,
                metadata_updates={"decoded_followup_lines": ["decoded"], "decoded_lat": 39.9, "decoded_lon": -82.9},
                lat=39.9,
                lon=-82.9,
            ))
            self.assertTrue(db.update_aprs_event_metadata(
                event_id=fixed_event_id,
                metadata_updates={"decoded_followup_lines": ["decoded"], "decoded_lat": 39.9, "decoded_lon": -82.9},
                lat=39.9,
                lon=-82.9,
            ))
            rows = {row["callsign"]: row for row in db.fetch_all("SELECT * FROM events")}
        finally:
            db.get_database_path = original_get_database_path
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(f"{test_db}{suffix}")
                if candidate.exists():
                    candidate.unlink()

        self.assertEqual(rows["K8LU-9"]["lat"], 39.9)
        self.assertEqual(rows["K8LU-9"]["lon"], -82.9)
        self.assertEqual(rows["KD8NVS-1"]["lat"], 40.0)
        self.assertEqual(rows["KD8NVS-1"]["lon"], -83.0)


if __name__ == "__main__":
    unittest.main()
