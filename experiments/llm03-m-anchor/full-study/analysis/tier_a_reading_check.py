"""Re-read the saved stage A results (no new simulation) with criterion 5 restricted to the contrasts whose
true value is at the -.05 boundary in the ni_boundary setting. This is a check of the v1 criterion text, not a
v2 specification and not a method selection: it only reports which failures remain under that reading.

  python -B -I analysis/tier_a_reading_check.py checks/simulation-tier-a-v1.json OUT.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sim_tier_a as A  # noqa: E402


def main(src, out):
    res = json.loads(Path(src).read_text(encoding="utf-8"))["results"]
    v1 = A.evaluate(res)
    adj = {}
    for m in A.METHODS:
        kept, dropped = [], []
        for f in v1["verdicts"][m]["failures"]:
            dropped.append(f) if ("false non-inferiority" in f and " C3-C1:" in f) else kept.append(f)
        adj[m] = dict(passes=not kept, failures=kept, dropped_failures=dropped,
                      mean_width90_main=v1["verdicts"][m]["mean_width90_main"])
    # Truth check backing the restriction: C3-C1 is 0 in every ni_boundary setting.
    truths = sorted({r["truth"][2] for r in res if r["setting"]["effect"] == "ni_boundary"})
    Path(out).write_text(json.dumps(dict(
        source=src, note="criterion 5 restricted to C4-C3 and C4-GC (true value -.05); C3-C1 true value in "
                         "ni_boundary is " + str(truths) + ", so a lower bound above -.05 there is a correct decision",
        v1_passing=v1["passing"], reading_passing=[m for m in A.METHODS if adj[m]["passes"]], verdicts=adj),
        ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:3])
