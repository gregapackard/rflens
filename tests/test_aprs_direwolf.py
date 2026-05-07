import unittest

from backend.ingestors.aprs_direwolf import enrich_packet, parse_gate_confirmation


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


if __name__ == "__main__":
    unittest.main()
