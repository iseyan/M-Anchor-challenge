import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
import rate_diff as rd  # noqa: E402


class RateDiffTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(1)
        self.d = rng.integers(-3, 4, size=(4, 10, 5, 6, 2)) / 3.

    def test_complete_case_t_matches_closed_form(self):
        r = rd.estimate(self.d, "case_t")
        dk = self.d.mean(axis=-1).reshape(4, 10, 30)
        np.testing.assert_allclose(r["estimate"], dk.mean(axis=(1, 2)))
        var = (dk.var(axis=-1, ddof=1) / 30).sum(axis=1) / 100
        np.testing.assert_allclose(r["se"], np.sqrt(var))
        self.assertEqual(r["df"], 290)

    def test_complete_jackknife_matches_situation_closed_form(self):
        r = rd.estimate(self.d, "jackknife_t")
        v = self.d.mean(axis=(3, 4))                          # situation means (R, H, M)
        var = (v.var(axis=-1, ddof=1) / 5).sum(axis=1) / 100
        np.testing.assert_allclose(r["se"], np.sqrt(var))
        np.testing.assert_allclose(r["estimate"], v.mean(axis=(1, 2)))
        self.assertEqual(r["df"], 40)

    def test_missing_uses_per_sender_means_with_equal_weights(self):
        d = self.d.copy()
        d[0, 0, 0, 0, 1] = np.nan
        d[0, 3, 2, :, 0] = np.nan
        r = rd.estimate(d, "case_t")
        per_sender = [np.mean([np.nanmean(d[0, h, :, :, s]) for h in range(10)]) for s in range(2)]
        self.assertAlmostEqual(r["estimate"][0], np.mean(per_sender))
        rj = rd.estimate(d, "jackknife_t")
        self.assertAlmostEqual(rj["estimate"][0], np.mean(per_sender))
        # brute-force jackknife on replicate 0
        def D(x):
            return np.mean([np.mean([np.nanmean(x[h][..., s]) for h in range(10)]) for s in range(2)])
        base = [d[0, h] for h in range(10)]
        var = 0.
        for h in range(10):
            reps = []
            for i in range(5):
                xs = list(base)
                xs[h] = np.delete(base[h], i, axis=0)
                reps.append(D(xs))
            reps = np.array(reps)
            var += 4 / 5 * ((reps - reps.mean()) ** 2).sum()
        self.assertAlmostEqual(rj["se"][0], np.sqrt(var))

    def test_all_zero_is_not_estimable_and_never_equivalent(self):
        d = np.zeros((1, 10, 5, 6, 2))
        for m in rd.METHODS:
            r = rd.estimate(d, m)
            self.assertFalse(r["estimable"][0])
            self.assertTrue(np.isnan(r["lo90"][0]))
            t = rd.tost(r, np.ones(1))
            self.assertFalse(t["equivalent"][0])
            self.assertFalse(rd.noninferior(r)[0])

    def test_type_without_defined_case_is_not_estimable(self):
        d = self.d.copy()
        d[:, 2, :, :, 0] = np.nan
        for m in rd.METHODS:
            self.assertFalse(rd.estimate(d, m)["estimable"].any())

    def test_holm(self):
        p = np.array([[.01, .04, .03], [np.nan, .001, .2]])
        adj = rd.holm(p)
        np.testing.assert_allclose(adj[0], [.03, .06, .06])
        np.testing.assert_allclose(adj[1], [1., .003, .4])

    def test_from_cells(self):
        pos = np.array([1, 0, 2.]).reshape(1, 1, 1, 1, 1, 3)
        obs = np.array([3, 0, 2.]).reshape(1, 1, 1, 1, 1, 3)
        self.assertAlmostEqual(float(rd.from_cells(pos, obs, 0, 2).squeeze()), 1 - 1 / 3)
        self.assertTrue(np.isnan(rd.from_cells(pos, obs, 0, 1)).all())


if __name__ == "__main__":
    unittest.main()
