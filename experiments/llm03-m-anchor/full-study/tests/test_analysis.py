"""Checks of pairing, interval indexing and the pre-specified conditional decision logic."""
from pathlib import Path
import sys
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"analysis"))
from cluster_intervals import intervals
from decisions import conditional_tost, joint_equivalence, h5, rate_interaction


class AnalysisTests(unittest.TestCase):
    def test_pairing_preserves_zero_and_constant_effect(self):
        a=np.zeros((10,5,2));a[:,:,1]=.02
        r=intervals(a,np.random.default_rng(1),999)
        np.testing.assert_allclose(r["estimate"],[0,.02])
        for m in ("percentile","bca"):
            np.testing.assert_allclose(r[m],[[0]*4,[.02]*4],atol=1e-12)

    def test_same_cluster_weights_preserve_contrast_dependence(self):
        a=np.random.default_rng(10).normal(size=(6,5,1))
        r=intervals(np.concatenate([a,2*a],axis=2),np.random.default_rng(3),999)
        np.testing.assert_allclose(r["percentile"][1],2*r["percentile"][0],atol=1e-12)

    def test_significant_primary_never_formally_runs_tost(self):
        self.assertEqual(conditional_tost(dict(p_holm=.01,log_or=-.1),[-.01,.01])["status"],"not_run")
        self.assertFalse(joint_equivalence({"H4a":dict(status="not_run"),"H4b":dict(status="equivalent")}))

    def test_unestimable_is_not_nonsignificant(self):
        self.assertEqual(conditional_tost(dict(p_holm=None),[-.01,.01])["reason"],"primary_not_estimable")

    def test_tost_boundary_does_not_pass(self):
        self.assertEqual(conditional_tost(dict(p_holm=.5),[-.03,.02])["status"],"inconclusive")
        self.assertEqual(conditional_tost(dict(p_holm=.5),[-.02,.02])["status"],"equivalent")

    def test_h5_overall_requires_all_three(self):
        self.assertFalse(h5({"C4-C3":-.01,"C4-GC":-.06,"C3-C1":.1})["overall_supported"])
        self.assertTrue(h5({"C4-C3":-.01,"C4-GC":-.04,"C3-C1":.1})["overall_supported"])

    def test_structural_zeros_do_not_require_logit_interaction(self):
        self.assertAlmostEqual(rate_interaction({"C1":.7,"C2":.4,"C3":0.,"C4":0.}),.3)


if __name__=="__main__":unittest.main()
