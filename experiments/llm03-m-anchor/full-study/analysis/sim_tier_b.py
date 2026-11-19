"""Stage B of simulation-spec-v1.md: export calibrated generator parameters, run sim_glmm.R, summarize.

usage: python -B -I analysis/sim_tier_b.py run <scenario> <work_dir> --method NAME [--reps N] [--workers 2]
       python -B -I analysis/sim_tier_b.py summarize <work_dir> <out.json>
Scenarios: B1 B2 B3 (spec section 4; B4 is defined but not run in v1). Per-replicate results are appended to <work_dir>/<scenario>.jsonl,
so an interrupted run resumes from the replicates already written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import simulate_intervals as si  # noqa: E402

SEED = 20261125
SCENARIOS = {
    "B1": dict(mis=("kind_mixture", "pilot_reweighted"), att=("normal", "pilot"), reps=40),
    "B2": dict(mis=("normal", "null"), att=("normal", "null"), reps=60),
    "B3": dict(mis=("normal", "tost_plus_boundary"), att=("normal", "tost_plus_boundary"), reps=40),
    "B4": dict(mis=("normal", "tost_minus_boundary"), att=("normal", "tost_minus_boundary"), reps=100),   # not run in v1
}
CONDS = ["C3", "GC", "C4"]


def export(scenario, path):
    sc = SCENARIOS[scenario]
    out = dict(scenario=scenario, conditions=CONDS, mask_exposed=si.structural_mask(5).astype(int).tolist())
    for key, outcome in (("mis", "mis_propagation"), ("att", "attempt")):
        structure, effect = sc[key]
        s = si.setup(outcome, effect, .7, 5, 3, structure, 3.0)
        idx = [si.IDX[c] for c in CONDS]
        out[key] = dict(outcome=outcome, structure=structure, effect=effect,
                        predictor=s["predictor"][..., idx].tolist(),            # type, situation, format, sender, cond
                        slope_sd=[float(s["sd"][i]) for i in idx], trial_var=s["trial_var"], case_var=s["case_var"],
                        truth_c4_minus_c3=float(s["truth"][0]), truth_c4_minus_gc=float(s["truth"][1]),
                        stratum_truth=s["stratum_truth"])
    Path(path).write_text(json.dumps(out), encoding="utf-8")
    return out


def run(a):
    w = Path(a.work_dir)
    w.mkdir(parents=True, exist_ok=True)
    params = w / f"{a.scenario}-params.json"
    if not params.exists():
        export(a.scenario, params)
    reps = a.reps or SCENARIOS[a.scenario]["reps"]
    seed = SEED + 1000 * int(a.scenario[1:])
    subprocess.run(["Rscript", str(HERE / "sim_glmm.R"), str(params), str(w / f"{a.scenario}.jsonl"), str(reps), str(seed),
                    a.method, str(a.workers), str(HERE / "primary_glmm.R")], check=True)


def prop(x):
    x = np.asarray(x, dtype=float)
    n = len(x)
    p = float(x.mean()) if n else float("nan")
    return dict(value=p, mc_se=float(np.sqrt(p * (1 - p) / n)) if n else None, n=n)


def summarize(a):
    w = Path(a.work_dir)
    out = {}
    for sc in SCENARIOS:
        f = w / f"{sc}.jsonl"
        if not f.exists():
            continue
        allrows = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x]
        rows = [r for r in allrows if "error" not in r]
        params = json.loads((w / f"{sc}-params.json").read_text(encoding="utf-8"))
        res = dict(replicates=len(rows), errors=[r for r in allrows if "error" in r], truth=dict(mis_c4_minus_c3=params["mis"]["truth_c4_minus_c3"],
                                                     mis_c4_minus_gc=params["mis"]["truth_c4_minus_gc"],
                                                     att_c4_minus_c3=params["att"]["truth_c4_minus_c3"]))
        for test in ("H4a", "H4b", "H4prime"):
            t = [r["primary"][test] for r in rows]
            res[test] = dict(not_estimable=prop([x["method"] == "not_estimable" for x in t]),
                             not_estimable_separation=prop([x.get("reason") == "separation" for x in t]),
                             not_estimable_not_converged=prop([x.get("reason") == "not_converged" for x in t]),
                             step2=prop([x.get("step") == 2 for x in t]), singular=prop([bool(x.get("singular")) for x in t]),
                             significant_reduction=prop([x["decision"] == "significant_reduction" for x in t]),
                             significant_increase=prop([x["decision"] == "significant_increase" for x in t]),
                             mean_log_or=float(np.mean([x["log_or"] for x in t if x.get("log_or") is not None] or [np.nan])))
        res["family_any_significant"] = prop([any(r["primary"][k]["decision"].startswith("significant") for k in ("H4a", "H4b", "H4prime"))
                                             for r in rows])
        for k in ("H4a", "H4b"):
            tt = [r["tost"][k] for r in rows]
            res[f"tost_{k}"] = dict(run=prop([x["status"] in ("equivalent", "inconclusive") for x in tt]),
                                    equivalent_unconditional=prop([x["status"] == "equivalent" for x in tt]),
                                    equivalent_given_run=prop([x["status"] == "equivalent" for x in tt if x["status"] in ("equivalent", "inconclusive")]),
                                    interval_only_inside=prop([r["interval_only_inside"][k] for r in rows]),
                                    mean_rate_diff=float(np.mean([r["rate_diff"][k] for r in rows])))
        out[sc] = res
    meta = dict(spec="simulation-spec-v1.md", seed=SEED,
                code_sha256={f: hashlib.sha256((HERE / f).read_bytes()).hexdigest() for f in ("sim_tier_b.py", "sim_glmm.R", "primary_glmm.R", "simulate_intervals.py")})
    Path(a.out).write_text(json.dumps(dict(meta=meta, scenarios=out), ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: dict(reps=v["replicates"], H4a_ne=v["H4a"]["not_estimable"]["value"]) for k, v in out.items()}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("scenario", choices=list(SCENARIOS))
    r.add_argument("work_dir")
    r.add_argument("--method", required=True, choices=["percentile", "raowu", "jackknife_t"])
    r.add_argument("--reps", type=int)
    r.add_argument("--workers", type=int, default=2)
    s = sub.add_parser("summarize")
    s.add_argument("work_dir")
    s.add_argument("out")
    a = ap.parse_args()
    run(a) if a.cmd == "run" else summarize(a)
