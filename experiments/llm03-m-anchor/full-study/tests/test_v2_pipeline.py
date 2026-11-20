"""v2: attempt observability (never counted as 'no attempt') and the fixed case grid of the main split."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "runner"))
sys.path.insert(0, str(ROOT / "analysis"))
import mock  # noqa: E402
import run_analysis_v2 as ra  # noqa: E402
import report_final as rf  # noqa: E402


class V2PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        mock.reference("dev", "1:1", cls.tmp.name, None)
        cls.eps = [json.loads(x) for x in (Path(cls.tmp.name) / "episodes.jsonl").read_text(encoding="utf-8").splitlines() if x]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_attempt_v2_rules(self):
        seen_none = False
        for e in self.eps:
            m = e["metrics"]
            if m["attempt"]:
                self.assertIs(m["attempt_v2"], True)
            elif e["actions_observable"] and all(e["actions_observable"]):
                self.assertIs(m["attempt_v2"], False)
            else:
                self.assertIsNone(m["attempt_v2"])
                self.assertTrue(m["attempt_unobserved"])
                seen_none = True
            if e["stage2_status"] not in ("OK", "INCOMPLETE", "SCHEMA_ERROR", "INVALID_OUTPUT", "NOT_RUN") and not m["attempt"]:
                self.assertIsNone(m["attempt_v2"])        # a failed call is never 'no attempt'
        self.assertTrue(seen_none, "mock responses should include unobservable submissions")

    def test_main_grid(self):
        cases = ra.load_cases("main")
        self.assertEqual(ra.layout(cases, "attack")[1], (10, 5, 6))
        self.assertEqual(ra.layout(cases, "legit")[1], (6, 5, 6))


    def test_final_report_focus_counts_partition_lt6(self):
        f = rf.focus(self.eps, ["gemma3:4b"])
        for c, row in f["q3_lt6_cause_record_in_final_handoff"].items():
            x = row["gemma3:4b"]
            parts = sum(v for k, v in x.items() if k != "episodes")
            self.assertEqual(parts, x.get("episodes", 0))
        lt6 = sum(1 for e in self.eps if e["case_type"] == "LT6")
        self.assertEqual(sum(r["gemma3:4b"].get("episodes", 0) for r in f["q1_lt6_approved_payment_with_cause_open"].values()), lt6)


if __name__ == "__main__":
    unittest.main()
