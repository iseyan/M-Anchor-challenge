"""Stage A of simulation-spec-v1.md: interval-method selection. numpy only, no model calls.

usage: python -B -I analysis/sim_tier_a.py <out.json> <selection.json> [--simulations 2000] [--workers 2]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import simulate_intervals as si  # noqa: E402
from cluster_intervals import intervals  # noqa: E402

SEED = 20261124
DRAWS = 999
METHODS = ["percentile", "bca", "raowu", "jackknife_t"]
EFFECTS = ["null", "tost_plus_boundary", "tost_minus_boundary", "ni_boundary", "pilot"]
SLOPES = [0.0, .35, .7, 1.0]
SELECT = {"mis_propagation": [0], "attempt": [0], "legit_completed": [0, 1, 2]}   # pair indices used for selection
PAIR_NAMES = ["C4-C3", "C4-GC", "C3-C1", "C4-A1", "C4-A2", "C4-A3", "C5-C4"]
THRESH = dict(failure=.01, cov90=.875, lower95=.935, false_equiv=.065, false_ni=.065)


def grid():
    out = []
    for outcome, structure, effects in (("mis_propagation", "normal", EFFECTS),
                                        ("mis_propagation", "kind_mixture", EFFECTS + ["pilot_reweighted"]),
                                        ("attempt", "normal", EFFECTS), ("legit_completed", "normal", EFFECTS)):
        for e in effects:
            for sd in SLOPES:
                out.append(dict(outcome=outcome, structure=structure, effect=e, slope=sd, shared=.5, block="main"))
            for sh in (0.0, 1.0):
                out.append(dict(outcome=outcome, structure=structure, effect=e, slope=.7, shared=sh, block="dependence"))
    return out


def run_setting(args):
    idx, st, sims = args
    s = si.setup(st["outcome"], st["effect"], st["slope"], 5, 3, st["structure"], 3.0)
    rng = np.random.default_rng(SEED + 1000 * idx)
    truth = s["truth"]
    k = len(truth)
    acc = {m: dict(fail=0, n=0, cov90=np.zeros(k), lower95=np.zeros(k), cov95=np.zeros(k), w90=np.zeros(k),
                   w95=np.zeros(k), inside3=np.zeros(k), lo_gt_m5=np.zeros(k)) for m in METHODS}
    est = np.zeros(k)
    for _ in range(sims):
        v = si.generate(s, rng, st["shared"])
        r = intervals(v, rng, draws=DRAWS)
        est += r["estimate"]
        for m in METHODS:
            a = acc[m]
            ci = r["bca"] if m == "bca" else r[m]
            if ci is None or not np.isfinite(ci).all():
                a["fail"] += 1
                continue
            lo95, lo90, hi90, hi95 = ci[:, 0], ci[:, 1], ci[:, 2], ci[:, 3]
            a["n"] += 1
            a["cov90"] += (lo90 <= truth) & (truth <= hi90)
            a["lower95"] += lo90 <= truth
            a["cov95"] += (lo95 <= truth) & (truth <= hi95)
            a["w90"] += hi90 - lo90
            a["w95"] += hi95 - lo95
            a["inside3"] += (lo90 > -.03) & (hi90 < .03)
            a["lo_gt_m5"] += lo90 > -.05
    res = dict(setting=st, simulations=sims, truth=truth.tolist(), mean_estimate=(est / sims).tolist(), methods={})
    for m, a in acc.items():
        n = max(a["n"], 1)
        p = lambda x: [dict(value=float(v), mc_se=float(np.sqrt(v * (1 - v) / n))) for v in x / n]  # noqa: E731
        res["methods"][m] = dict(failure_rate=a["fail"] / sims, evaluated=a["n"], coverage90=p(a["cov90"]),
                                 lower95_coverage=p(a["lower95"]), coverage95=p(a["cov95"]),
                                 mean_width90=(a["w90"] / n).tolist(), mean_width95=(a["w95"] / n).tolist(),
                                 ci90_inside_pm3=p(a["inside3"]), lower95_above_minus5=p(a["lo_gt_m5"]))
    return res


def evaluate(results):
    verdict = {}
    for m in METHODS:
        fails = []
        widths = []
        for r in results:
            st, mm = r["setting"], r["methods"][m]
            sel = SELECT[st["outcome"]]
            tag = f"{st['outcome']}/{st['structure']}/{st['effect']}/sd{st['slope']}/sh{st['shared']}"
            if mm["failure_rate"] > THRESH["failure"]:
                fails.append(f"{tag}: failure {mm['failure_rate']:.3f}")
            for i in sel:
                c = PAIR_NAMES[i]
                if mm["coverage90"][i]["value"] < THRESH["cov90"]:
                    fails.append(f"{tag} {c}: coverage90 {mm['coverage90'][i]['value']:.3f}")
                if st["outcome"] == "legit_completed" and mm["lower95_coverage"][i]["value"] < THRESH["lower95"]:
                    fails.append(f"{tag} {c}: lower95 {mm['lower95_coverage'][i]['value']:.3f}")
                if st["outcome"] != "legit_completed" and st["effect"] in ("tost_plus_boundary", "tost_minus_boundary") \
                        and mm["ci90_inside_pm3"][i]["value"] > THRESH["false_equiv"]:
                    fails.append(f"{tag} {c}: false equivalence {mm['ci90_inside_pm3'][i]['value']:.3f}")
                if st["outcome"] == "legit_completed" and st["effect"] == "ni_boundary" \
                        and mm["lower95_above_minus5"][i]["value"] > THRESH["false_ni"]:
                    fails.append(f"{tag} {c}: false non-inferiority {mm['lower95_above_minus5'][i]['value']:.3f}")
                if st["block"] == "main":
                    widths.append(mm["mean_width90"][i])
        verdict[m] = dict(passes=not fails, failures=fails, mean_width90_main=float(np.mean(widths)))
    passing = [m for m in METHODS if verdict[m]["passes"]]
    chosen = None
    if passing:
        best = min(passing, key=lambda m: verdict[m]["mean_width90_main"])
        jk = verdict["jackknife_t"]
        chosen = "jackknife_t" if jk["passes"] and jk["mean_width90_main"] <= 1.02 * verdict[best]["mean_width90_main"] else best
    return dict(thresholds=THRESH, verdicts=verdict, passing=passing, selected=chosen,
                rule="smallest mean 90% width among passing methods; jackknife_t if within 2% of the smallest")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("selection")
    ap.add_argument("--simulations", type=int, default=2000)
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    t0 = time.time()
    settings = grid()
    with Pool(a.workers) as pool:
        results = pool.map(run_setting, [(i, st, a.simulations) for i, st in enumerate(settings)])
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=HERE).stdout.strip()
    except OSError:
        commit = None
    meta = dict(spec="simulation-spec-v1.md", git_head=commit, seed=SEED, bootstrap_draws=DRAWS, simulations=a.simulations,
                settings=len(settings), python=platform.python_version(), numpy=np.__version__,
                code_sha256={f: sha(HERE / f) for f in ("sim_tier_a.py", "simulate_intervals.py", "cluster_intervals.py")},
                wall_seconds=round(time.time() - t0, 1))
    Path(a.out).write_text(json.dumps(dict(meta=meta, results=results), ensure_ascii=False, indent=1), encoding="utf-8")
    sel = dict(meta=meta, **evaluate(results))
    Path(a.selection).write_text(json.dumps(sel, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(dict(selected=sel["selected"], passing=sel["passing"],
                          failures={m: len(v["failures"]) for m, v in sel["verdicts"].items()}, wall=meta["wall_seconds"])))


if __name__ == "__main__":
    main()
