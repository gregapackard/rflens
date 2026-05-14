import unittest

from backend.db import aprs_igate_status
from backend.ingestors.aprs_direwolf import parse_gate_confirmation


class AprsIgateStatusTests(unittest.TestCase):
    def test_qar_qao_qas_proof_confirms_local_gating(self):
        for q_construct in ("qAR", "qAO", "qAS"):
            with self.subTest(q_construct=q_construct):
                result = parse_gate_confirmation(
                    "ig",
                    f"KD8NVS-1>APRS,TCPIP*,{q_construct},KF8GBU-10:!4000.00N/08300.00W-test",
                    "kf8gbu-10",
                )

                self.assertIsNotNone(result)
                self.assertTrue(result["confirmed_gated_by_me"])

    def test_confirmed_status_requires_qpath_proof(self):
        status = aprs_igate_status({"heard_over_rf": 3, "gate_eligible": 2, "gate_confirmed": 1}, "KF8GBU-10")

        self.assertEqual(status["status_level"], "ok_confirmed")
        self.assertTrue(status["aprs_is_qpath_proof_today"])
        self.assertTrue(status["likely_gated_today"])
        self.assertEqual(
            status["status_message"],
            "Confirmed: RF Lens captured APRS-IS proof that KF8GBU-10 gated packets today.",
        )
        self.assertIsNone(status["aprs_is_qpath_proof_detail"])

    def test_other_q_construct_does_not_confirm_local_gating(self):
        result = parse_gate_confirmation(
            "ig",
            "KD8NVS-1>APRS,TCPIP*,qAC,KF8GBU-10:!4000.00N/08300.00W-test",
            "KF8GBU-10",
        )

        self.assertIsNone(result)

    def test_rf_and_gate_eligible_without_qpath_proof_is_likely(self):
        status = aprs_igate_status({"heard_over_rf": 3, "gate_eligible": 2, "gate_confirmed": 0}, "KF8GBU-10")

        self.assertEqual(status["status_level"], "ok_likely")
        self.assertTrue(status["rf_heard_today"])
        self.assertTrue(status["gate_eligible_heard_today"])
        self.assertTrue(status["likely_gated_today"])
        self.assertFalse(status["aprs_is_qpath_proof_today"])
        self.assertIn("appears active", status["status_message"])
        self.assertIn("qAR/qAO/qAS,KF8GBU-10", status["aprs_is_qpath_proof_detail"])

    def test_rf_only_is_warning(self):
        status = aprs_igate_status({"heard_over_rf": 3, "gate_eligible": 0, "gate_confirmed": 0}, "KF8GBU-10")

        self.assertEqual(status["status_level"], "warn_rf_only")
        self.assertTrue(status["rf_heard_today"])
        self.assertFalse(status["gate_eligible_heard_today"])
        self.assertFalse(status["likely_gated_today"])
        self.assertEqual(
            status["status_message"],
            "APRS RF packets were heard today, but no gate-eligible packets have been detected yet.",
        )

    def test_no_rf_is_warning(self):
        status = aprs_igate_status({"heard_over_rf": 0, "gate_eligible": 0, "gate_confirmed": 0}, "KF8GBU-10")

        self.assertEqual(status["status_level"], "warn_no_rf")
        self.assertFalse(status["rf_heard_today"])
        self.assertFalse(status["gate_eligible_heard_today"])
        self.assertFalse(status["likely_gated_today"])
        self.assertEqual(status["status_message"], "No APRS RF packets heard today.")


if __name__ == "__main__":
    unittest.main()
