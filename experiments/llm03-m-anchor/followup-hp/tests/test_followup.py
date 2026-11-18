"""Tests for the follow-up comparison (run from experiments/llm03-m-anchor/):
    python -I followup-hp/tests/test_followup.py
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import fu  # noqa: E402
import run_followup as rf  # noqa: E402
from lib import anchor, pipeline, prompts  # noqa: E402
from lib.common import load_cases, load_config, parse_json  # noqa: E402

CFG, FU = fu.load_cfg(), fu.load_fu()
SRC = fu.source_records(FU)
TRIALS = fu.plan_trials(CFG, FU, SRC)
MAIN_CFG = load_config()
NODE = shutil.which("node")


def clean(rid):
    shutil.rmtree(rf.RUNS / rid, ignore_errors=True)


class TestDesign(unittest.TestCase):
    def test_frozen_harness_is_unchanged_since_the_main_run(self):
        main_manifest = json.loads((fu.EXP / "main-runs/main-20261118-01/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(rf.harness_run.manifest(), main_manifest["files_sha256"])

    def test_case_selection_and_trial_plan(self):
        import numpy as np
        attack = [c["case_id"] for c in load_cases("main") if c["label"] == "attack"]
        picked = list(np.random.default_rng(20261119).choice(attack, 5, replace=False))
        self.assertEqual(sorted(picked, key=attack.index), FU["attack_selection"]["cases"])
        legit = [c["case_id"] for c in load_cases("main") if c["type"] in ("LT1", "LT4", "LT5", "LT6")]
        self.assertEqual(legit, FU["legit_cases"])
        self.assertEqual(len(TRIALS), 15 * 2 * 3)
        for t in TRIALS:
            self.assertEqual(t["seed"], 20261119 + 1000 * t["rep"] + t["case_index"])
            self.assertEqual(t["s1_content"], SRC["S1|" + t["trial_id"]]["content"])

    def test_C3R_and_C4R_texts_equal_the_main_run_C3_and_C4(self):
        for t in TRIALS:
            obj, _ = parse_json(t["s1_content"])
            block = anchor.render_block(prompts.templates()["c4_header"], anchor.build_state(t["case"], obj))
            self.assertEqual(fu.instruction(CFG, "C3R", t["case"], t["s1_content"]),
                             prompts.stage2_instruction(MAIN_CFG, "C3", None, t["case"]))
            self.assertEqual(fu.instruction(CFG, "C4R", t["case"], t["s1_content"]),
                             prompts.stage2_instruction(MAIN_CFG, "C4", block, t["case"]))

    def test_C4R_body_with_the_main_seed_reproduces_the_main_C4_request(self):
        old = dict(CFG, seed_base=MAIN_CFG["seed_base"])
        for t in TRIALS:
            t_old = dict(t, seed=pipeline.seed_for(MAIN_CFG, t["rep"], t["case_index"]))
            for new, main in (("C3R", "C3"), ("C4R", "C4")):
                self.assertEqual(fu.s2_spec(old, t_old, new)["request_sha256"],
                                 SRC[f"S2|{t['trial_id']}|{main}"]["request_sha256"])

    def test_H_and_P_differ_from_C4R_only_in_the_block(self):
        header = prompts.templates()["c4_header"].strip()
        permit = fu.permit_sentence()
        for t in TRIALS:
            base = prompts.stage2_base(t["case"])
            c4 = fu.instruction(CFG, "C4R", t["case"], t["s1_content"])
            h = fu.instruction(CFG, "H", t["case"], t["s1_content"])
            p = fu.instruction(CFG, "P", t["case"], t["s1_content"])
            self.assertEqual(h, header + "\n\n" + base)
            block = c4[: -len("\n\n" + base)]
            self.assertTrue(block.startswith(header + "\n- "))
            self.assertEqual(p, block + "\n" + permit + "\n\n" + base)
            specs = [fu.s2_spec(CFG, t, c) for c in CFG["conditions"]]
            msgs = [json.loads(s["body"])["messages"] for s in specs]
            for m in msgs[1:]:
                self.assertEqual(m[:-1], msgs[0][:-1])          # everything but the last user turn is shared
            self.assertEqual(len({json.loads(s["body"])["options"]["seed"] for s in specs}), 1)

    def test_extra_metrics(self):
        case = next(t["case"] for t in TRIALS if t["case"]["type"] == "LT1")
        want = {k: "confirmed" for k, v in case["gold"]["claims"].items() if v == "confirmed"}
        base = {"actions": [], "handoff": {"claims": want}, "handoff_status": "SENT"}
        self.assertEqual(fu.extra_metrics(base, case),
                         {"payment_proposed_gold": False, "payment_proposed_any": False, "confirmed_without_payment": True})
        pay = dict(base, actions=[{"tool": "approve_payment", "matches_gold": True}])
        self.assertEqual(fu.extra_metrics(pay, case)["confirmed_without_payment"], False)
        self.assertEqual(fu.extra_metrics(pay, case)["payment_proposed_gold"], True)
        atk = next(t["case"] for t in TRIALS if t["case"]["label"] == "attack")
        self.assertIsNone(fu.extra_metrics(base, atk)["payment_proposed_gold"])


class TestRuns(unittest.TestCase):
    def test_mock_run_verifies_and_run_ids_are_never_reused(self):
        rid = "unittest-fu-mock"
        clean(rid)
        ok = subprocess.run([sys.executable, "-I", str(HERE / "run_followup.py"), "mock", "--run-id", rid],
                            capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        v = json.loads((rf.RUNS / rid / "verification.json").read_text(encoding="utf-8"))
        self.assertEqual((v["duplicates"], v["unplanned"], v["missing"], v["hash_mismatch"], v["order_matches"]),
                         ([], [], [], [], True))
        again = subprocess.run([sys.executable, "-I", str(HERE / "run_followup.py"), "mock", "--run-id", rid],
                               capture_output=True, text=True)
        self.assertNotEqual(again.returncode, 0)
        clean(rid)


@unittest.skipIf(NODE is None, "node is not installed")
class TestBrowserParity(unittest.TestCase):
    def _run_node(self, rid, wrong=False):
        clean(rid)
        out = subprocess.run([sys.executable, "-I", str(HERE / "run_followup.py"), "prepare", "--run-id", rid],
                             capture_output=True, text=True, check=True)
        bundle = json.loads(out.stdout)["path"]
        recs = [r for t in TRIALS for r in fu.run_trial(CFG, t, rf.mock_sender(TRIALS))]
        resp = {r["request_sha256"]: {"content": r["content"], "done_reason": r["done_reason"],
                                      "eval_count": r["eval_count"], "prompt_eval_count": r["prompt_eval_count"],
                                      "model": r["response_model"]} for r in recs}
        rp, sp = rf.RUNS / rid / "_responses.json", rf.RUNS / rid / "_state.json"
        rp.write_text(json.dumps(resp, ensure_ascii=False), encoding="utf-8")
        args = [NODE, str(HERE / "tests" / "node_parity_followup.js"), bundle, str(rp), str(sp)] + (["wrong"] if wrong else [])
        res = json.loads(subprocess.run(args, capture_output=True, text=True, check=True).stdout)
        return res, sp, recs

    def test_js_builds_the_same_bodies_and_order_as_python(self):
        rid = "unittest-fu-parity"
        res, sp, recs = self._run_node(rid)
        self.assertIsNone(res["fatal"])
        self.assertEqual(res["records"], len(recs))
        r = subprocess.run([sys.executable, "-I", str(HERE / "run_followup.py"), "ingest", "--run-id", rid,
                            "--state", str(sp)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        v = json.loads((rf.RUNS / rid / "verification.json").read_text(encoding="utf-8"))
        self.assertEqual((v["duplicates"], v["unplanned"], v["missing"], v["hash_mismatch"], v["order_matches"]),
                         ([], [], [], [], True))
        js_ids = [x["call_id"] for x in json.loads(sp.read_text(encoding="utf-8"))["records"]]
        self.assertEqual(js_ids, [x["call_id"] for x in recs])
        self.assertFalse(any(i.startswith("S1|") for i in js_ids))      # stage 1 is never called
        clean(rid)

    def test_environment_mismatch_stops_before_any_inference(self):
        rid = "unittest-fu-env"
        res, sp, _ = self._run_node(rid, wrong=True)
        self.assertIn("ENV_MISMATCH", res["fatal"])
        self.assertEqual(res["chat_calls"], 0)
        clean(rid)


if __name__ == "__main__":
    unittest.main()
