"""Runs the R checks of the primary GLMM (contrast weights, separation, Holm). Needs R with lme4/jsonlite."""
from pathlib import Path
import shutil
import subprocess
import unittest

HERE = Path(__file__).resolve().parents[1]


@unittest.skipIf(shutil.which("Rscript") is None, "R is not installed")
class PrimaryGlmmTests(unittest.TestCase):
    def test_r_checks(self):
        p = subprocess.run(["Rscript", str(HERE / "tests" / "test_primary_glmm.R"), str(HERE / "analysis" / "primary_glmm.R")],
                           capture_output=True, text=True, encoding="utf-8", timeout=1200)
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        self.assertIn("ok", p.stdout)


if __name__ == "__main__":
    unittest.main()
