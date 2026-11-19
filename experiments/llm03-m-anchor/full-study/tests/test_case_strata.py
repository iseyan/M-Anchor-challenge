"""Pre-output structural classification and the documented isolated CLI entry point."""
from copy import deepcopy
from pathlib import Path
import json
import subprocess
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from case_strata import EXPOSED,FILTERED,classify,case_manifest,pilot_differences
from support import ROOT,cases


class CaseStrataTests(unittest.TestCase):
    def test_expanded_pre_output_counts_and_witnesses(self):
        result=case_manifest(cases("main"))
        self.assertEqual(result["counts"],{FILTERED:192,EXPOSED:108})
        self.assertEqual(result["exposed_by_type"],dict(AT3=30,AT4=18,AT8=30,AT9=30))
        self.assertTrue(all(c["witnesses"] for c in result["cases"] if c["stratum"]==EXPOSED))

    def test_refuted_or_future_documents_do_not_define_primary_stratum(self):
        c=dict(case_id="test",type="AT1",label="attack",claims=[dict(key="a",admissible_kinds=["ledger"])],
               documents=[dict(id="d",kind="ledger")],gold=dict(claims=dict(a="refuted")))
        self.assertEqual(classify(c)["stratum"],FILTERED)
        c["gold"]["claims"]["a"]="unconfirmed"
        self.assertEqual(classify(c)["stratum"],EXPOSED)
        c["gold_snapshots"]=dict(first_handoff=dict(evidence_ids=[],gold=deepcopy(c["gold"])))
        self.assertEqual(classify(c)["stratum"],FILTERED)

    def test_development_set_is_not_effect_validation_sample(self):
        result=case_manifest(cases("dev"))
        self.assertEqual(result["attack_cases"],20)
        self.assertEqual(result["counts"],{EXPOSED:20})

    def test_documented_isolated_validator_entry_point(self):
        r=subprocess.run([sys.executable,"-X","utf8","-B","-I",str(ROOT/"validate.py")],capture_output=True,text=True,encoding="utf-8")
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(json.loads(r.stdout)["status"],"offline_structure_passed_not_frozen")

    def test_pilot_missing_or_duplicate_pairs_are_rejected(self):
        main=cases("main")
        rows=[next(c for c in main if c["type"]==t) for t in ("AT1","AT3")]
        eps=[dict(case_id=c["case_id"],sender=s,rep=1,condition=k,metrics=dict(mis_propagation=False))
             for c in rows for s in ("a","b") for k in ("C3","C4")]
        self.assertEqual(len(pilot_differences(rows,eps)),6)
        with self.assertRaises(ValueError):pilot_differences(rows,eps[:-1])
        with self.assertRaises(ValueError):pilot_differences(rows,eps+[eps[0]])


if __name__ == "__main__": unittest.main()
