"""Checks of pairing, interval indexing and the pre-specified conditional decision logic."""
from pathlib import Path
import sys
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"analysis"))
from cluster_intervals import intervals, jackknife_statistics, student_t_quantile
from decisions import conditional_tost, joint_equivalence, h5, rate_interaction
from simulate_intervals import setup,generate


class AnalysisTests(unittest.TestCase):
    def test_pairing_preserves_zero_and_constant_effect(self):
        a=np.zeros((10,5,2));a[:,:,1]=.02
        r=intervals(a,np.random.default_rng(1),999)
        np.testing.assert_allclose(r["estimate"],[0,.02])
        for m in ("percentile","bca","raowu","jackknife_t"):
            np.testing.assert_allclose(r[m],[[0]*4,[.02]*4],atol=1e-12)
        self.assertTrue(r["degenerate"].all())

    def test_t_quantiles_against_cauchy_and_reference_values(self):
        import math
        self.assertAlmostEqual(student_t_quantile(.95,1),math.tan(math.pi*(.95-.5)),places=10)
        for df,p,expected in ((24,.95,1.7108820799),(24,.975,2.0638985616),
                              (40,.95,1.6838510133),(40,.975,2.0210753903)):
            self.assertAlmostEqual(student_t_quantile(p,df),expected,places=9)

    def test_jackknife_matches_delete_one_and_raowu_variance(self):
        from itertools import product
        v=np.array([[[.1,.4],[.2,.3],[.8,.2],[.4,.8],[.9,.6]]])
        theta=v.mean(axis=(0,1))
        j=np.array([np.delete(v,i,axis=1).mean(axis=(0,1)) for i in range(5)])
        variance,df,_=jackknife_statistics(v)
        np.testing.assert_allclose(variance,4/5*((j-theta)**2).sum(axis=0))
        # Enumerate the entire empirical resampling distribution; no Monte Carlo assertion.
        rw=np.array([v[0,list(i)].mean(axis=0) for i in product(range(5),repeat=4)])
        naive=np.array([v[0,list(i)].mean(axis=0) for i in product(range(5),repeat=5)])
        np.testing.assert_allclose(rw.var(axis=0),variance,rtol=1e-12)
        np.testing.assert_allclose(naive.var(axis=0),.8*variance,rtol=1e-12)
        self.assertEqual(df,4)

    def test_jackknife_design_df_and_one_sided_index(self):
        v=np.random.default_rng(41).normal(size=(6,5,2))
        r=intervals(v,np.random.default_rng(42),999)
        self.assertEqual(r["jackknife_df"],24)
        expected=r["estimate"]-student_t_quantile(.95,24)*np.sqrt(r["jackknife_variance"])
        np.testing.assert_allclose(r["jackknife_t"][:,1],expected)

    def test_structural_mixture_preserves_target_and_exposes_heterogeneity(self):
        s=setup("mis_propagation","null",.7,5,3,"kind_mixture",3.)
        strata=s["stratum_truth"]
        self.assertAlmostEqual(strata["exposed"]["case_fraction"],.36)
        recombined=sum(strata[k]["case_fraction"]*np.array(strata[k]["differences_two_sender_mean"])
                       for k in strata)
        np.testing.assert_allclose(recombined,s["truth"],atol=1e-12)
        self.assertGreater(abs(strata["exposed"]["differences_two_sender_mean"][0]-
                               strata["other"]["differences_two_sender_mean"][0]),.1)
        self.assertEqual(generate(s,np.random.default_rng(42)).shape,(10,5,7))

    def test_unrepresented_mixture_allocation_is_rejected(self):
        with self.assertRaises(ValueError):setup("mis_propagation","null",0.,8,3,"kind_mixture",3.)

    def test_nonfinite_or_negative_slope_is_rejected_before_generation(self):
        for sd in (float("nan"),float("inf"),-1.):
            with self.subTest(sd=sd):
                with self.assertRaises(ValueError):setup("mis_propagation","null",sd,5,3)

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
