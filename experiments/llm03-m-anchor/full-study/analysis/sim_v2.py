"""Formal simulation of simulation-spec-v2.md (stage A: intervals; stage B: Holm and conditional TOST).

  python -B -I analysis/sim_v2.py select checks/simulation-v2-select.json checks/simulation-v2-selection.json [--reps 2000] [--workers 2]
  python -B -I analysis/sim_v2.py final  checks/simulation-v2-final.json --method NAME [--reps 2000] [--workers 2]

Generator: analysis/simulate_intervals.py (v1) calibration, case-level output, plus the v2 options
(equal-variance null, boundary/partial/sparse effects, MCAR unobserved attempt judgements).
Estimator and decisions: analysis/rate_diff.py (the same code the real analysis uses). numpy/scipy only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import zlib
from multiprocessing import Pool
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import rate_diff as rd  # noqa: E402
import simulate_intervals as si  # noqa: E402

SEED_SELECT, SEED_FINAL = 20261201, 20261211
REPS_PER_CASE = 3
MISSING = (.01, .05)                    # qwen, gemma (TM series only; MCAR assumption)
PAIRS = {"C4-C3": ("C3", "C4"), "C4-GC": ("GC", "C4"), "C3-C1": ("C1", "C3")}
SELECT = {"mis_propagation": ["C4-C3", "C4-GC"], "attempt": ["C4-C3"], "legit_completed": ["C4-C3", "C4-GC", "C3-C1"]}
TH = dict(nonest=.10, cov90=.885, lower95=.935, false_equiv=.06, false_ni=.06, fwer=.06)
GRID = [(0., .5), (.35, .5), (.7, .5), (1., .5), (.7, 0.), (.7, 1.)]
SERIES = {
    "M": ("mis_propagation", "normal", False, ["null_m", "null_e", "plus", "minus", "pilot", "sparse", "sparse_plus"]),
    "MK": ("mis_propagation", "kind_mixture", False, ["null_m", "null_e", "plus", "minus", "pilot_reweighted"]),
    "T": ("attempt", "normal", False, ["null_m", "null_e", "plus", "minus", "pilot", "sparse", "sparse_plus"]),
    "TM": ("attempt", "normal", True, ["null_m", "plus", "minus"]),
    "L": ("legit_completed", "normal", False, ["null_m", "null_e", "plus", "minus", "ni", "ni_c3c1", "pilot"]),
}
B_SCEN = {   # name: ((mis structure, mis effect), attempt effect, slope SDs, true-null tests for the family error)
    "B1": (("kind_mixture", "pilot_reweighted"), "pilot", [.7], None),
    "B1s": (("normal", "pilot"), "pilot", [.7], None),
    "B2m": (("normal", "null_m"), "null_m", [.7, 1.], ["H4a", "H4b", "H4prime"]),
    "B2e": (("normal", "null_e"), "null_e", [.7, 1.], ["H4a", "H4b", "H4prime"]),
    "B3": (("normal", "plus"), "plus", [.7], None),
    "B4": (("normal", "minus"), "minus", [.7], None),
    "B5": (("normal", "sparse"), "sparse", [.7], ["H4a", "H4b", "H4prime"]),
    "B6": (("normal", "sparse_plus"), "sparse_plus", [.7], None),
    "P1": (("normal", "pilot_gc_eq_c4"), "null_m", [.7], ["H4b", "H4prime"]),
    "P2": (("normal", "null_m"), "pilot", [.7], ["H4a", "H4prime"]),
}
TESTS = ["H4a", "H4b", "H4prime"]


def targets(outcome, effect):
    I = si.IDX
    if effect in ("pilot", "pilot_reweighted"):
        return si.rates(outcome, effect)
    base = np.array(si.PILOT[outcome]["C3"])
    r = np.repeat(base[:, None], 9, axis=1)
    new = ("C2", "A1", "A2", "A3", "C5")
    if effect in ("null_m", "null_e"):
        pass
    elif effect in ("plus", "minus"):
        r[:, I["C4"]] = np.clip(base + (.03 if effect == "plus" else -.03), .001, .999)
    elif effect == "ni":
        if outcome != "legit_completed":
            raise ValueError("ni is legit only")
        r[:, I["C4"]] = base - .05
    elif effect == "ni_c3c1":
        if outcome != "legit_completed":
            raise ValueError("ni_c3c1 is legit only")
        r[:, I["C1"]] = np.clip(base + .05, .001, .999)
        if np.any(base + .05 > .999):
            raise ValueError("C1 rate out of range")
    elif effect == "sparse":
        r[:] = .02
    elif effect == "sparse_plus":
        r[:] = .02
        r[:, I["C4"]] = .05
    elif effect == "pilot_gc_eq_c4":
        if outcome != "mis_propagation":
            raise ValueError("pilot_gc_eq_c4 is mis-propagation only")
        r = si.rates(outcome, "pilot")
        r[:, I["GC"]] = r[:, I["C4"]]
    else:
        raise ValueError(effect)
    if effect not in ("ni_c3c1",):
        for c in new:
            r[:, I[c]] = r[:, I["C4"]]
    return r


def setup(outcome, structure, effect, slope_sd, gap=3.):
    """v1 setup with the v2 targets; null_e gives C3 the same slope SD as every other condition."""
    nt = 6 if outcome == "legit_completed" else 10
    fixed_type = np.linspace(-1, 1, nt)
    fixed_type = fixed_type / fixed_type.std() * np.sqrt(si.TYPE_VARIANCE[outcome])
    fixed = fixed_type[:, None] + np.linspace(-.3, .3, 6)[None, :]
    target = targets(outcome, effect)
    sd = np.full(9, slope_sd)
    if effect != "null_e":
        sd[si.IDX["C3"]] = 0.
    variance = si.TRIAL_VARIANCE[outcome] + si.CASE_VARIANCE[outcome] + .25 + sd ** 2
    predictor = np.broadcast_to(fixed[:, None, :, None, None], (nt, 5, 6, 2, 9)).copy()
    if structure == "kind_mixture":
        if outcome != "mis_propagation":
            raise ValueError("kind_mixture is mis-propagation only")
        exposed = si.structural_mask(5)
        raw = np.array([[45 / 54, 31 / 54], [3 / 21, 18 / 21]])
        lg = np.log(raw / (1 - raw))
        shift = np.zeros(9)
        for c in ("C2", "C4", "A1", "A2", "A3", "C5"):
            shift[si.IDX[c]] = gap
        predictor += exposed[:, :, :, None, None] * ((lg[1] - lg[0])[None, None, None, :, None] + shift[None, None, None, None, :])
    intercept = np.array([[si.calibrate(target[s, c], predictor[:, :, :, s, c], variance[c]) for c in range(9)] for s in range(2)])
    predictor += intercept[None, None, None, :, :]
    # numerical check: marginal rates reproduce the targets (fixed design, integrated random effects)
    marg = (si.expit(predictor[..., None] + np.sqrt(2 * variance)[None, None, None, None, :, None] * si.GH_X) * si.GH_W).sum(-1).mean(axis=(0, 1, 2))
    truth = {k: float((target[:, si.IDX[b]] - target[:, si.IDX[a]]).mean()) for k, (a, b) in PAIRS.items()}
    return dict(nt=nt, predictor=predictor, sd=sd, case_var=si.CASE_VARIANCE[outcome], trial_var=si.TRIAL_VARIANCE[outcome],
                truth=truth, target=target, calib_error=float(np.abs(marg - target).max()))


def generate(s, rng, n, shared, missing=False):
    """positives and observed counts, shape (n, H, M, F, S, C)."""
    nt = s["nt"]
    frame = rng.normal(0, .5, (n, nt, 5, 1, 1, 1, 1))
    case = rng.normal(0, np.sqrt(s["case_var"]), (n, nt, 5, 6, 1, 1, 1))
    init = rng.normal(0, np.sqrt(s["trial_var"]), (n, nt, 5, 6, 2, REPS_PER_CASE, 1))
    slope = rng.normal(size=(n, nt, 5, 1, 1, 1, 9)) * s["sd"]
    probs = si.expit(s["predictor"][None, :, :, :, :, None, :] + frame + case + init + slope)
    common = rng.random((n, nt, 5, 6, 2, REPS_PER_CASE, 1))
    use = rng.random((n, nt, 5, 6, 2, REPS_PER_CASE, 1)) < shared
    u = np.where(use, common, rng.random(probs.shape))
    y = u < probs
    if missing:
        pm = np.array(MISSING)[None, None, None, None, :, None, None]
        seen = rng.random(probs.shape) >= pm
    else:
        seen = np.ones(probs.shape, dtype=bool)
    return (y & seen).sum(axis=5).astype(float), seen.sum(axis=5).astype(float)


def contrast(pos, obs, name):
    a, b = PAIRS[name]
    return rd.from_cells(pos, obs, si.IDX[a], si.IDX[b])


def p_(x):
    x = np.asarray(x, dtype=float)
    v = float(x.mean())
    return dict(value=v, mc_se=float(np.sqrt(v * (1 - v) / len(x))))


def run_a(job):
    key, outcome, structure, missing, effect, sd, sh, reps, seed, methods = job
    s = setup(outcome, structure, effect, sd)
    rng = np.random.default_rng([seed, zlib.crc32(key.encode())])
    acc = {m: {c: dict(est=[], lo=[], hi=[], ok=[], zero=[]) for c in SELECT[outcome]} for m in methods}
    done = 0
    while done < reps:
        n = min(250, reps - done)
        pos, obs = generate(s, rng, n, sh, missing)
        for c in SELECT[outcome]:
            d = contrast(pos, obs, c)
            for m in methods:
                r = rd.estimate(d, m)
                a = acc[m][c]
                a["est"].append(r["estimate"]); a["lo"].append(r["lo90"]); a["hi"].append(r["hi90"])
                a["ok"].append(r["estimable"]); a["zero"].append(r["zero_variance_strata"])
        done += n
    out = dict(key=key, outcome=outcome, structure=structure, missing=missing, effect=effect, slope=sd, shared=sh,
               reps=reps, truth=s["truth"], calib_error=s["calib_error"], methods={})
    for m in methods:
        out["methods"][m] = {}
        for c in SELECT[outcome]:
            a = {k: np.concatenate(v) for k, v in acc[m][c].items()}
            t = s["truth"][c]
            ok = a["ok"]
            cover = ok & (a["lo"] <= t) & (t <= a["hi"])
            low = ok & (a["lo"] <= t)
            inside = ok & (a["lo"] > -.03) & (a["hi"] < .03)
            ni = ok & (a["lo"] > -.05)
            est = a["est"][np.isfinite(a["est"])]
            mc_mean, mc_se = float(est.mean()), float(est.std(ddof=1) / np.sqrt(len(est)))
            out["methods"][m][c] = dict(nonestimable=p_(~ok), coverage90=p_(cover), lower95_coverage=p_(low),
                                        ci90_inside_pm3=p_(inside), lower95_above_minus5=p_(ni),
                                        mean_width90=float(np.nanmean(a["hi"] - a["lo"])) if ok.any() else None,
                                        mean_estimate=mc_mean, bias=mc_mean - t,
                                        truth_check=dict(mc_mean=mc_mean, mc_se=mc_se, ok=bool(abs(mc_mean - t) <= 3 * mc_se + 1e-12)),
                                        mean_zero_variance_strata=float(a["zero"].mean()))
    return out


def run_b(job):
    key, name, sd, reps, seed, methods = job
    (mstruct, meff), aeff, _, nulls = B_SCEN[name]
    sm = setup("mis_propagation", mstruct, meff, sd)
    sa = setup("attempt", "normal", aeff, sd)
    rng = np.random.default_rng([seed, zlib.crc32(key.encode())])
    acc = {m: dict(p=[], est=[], ok=[], lo=[], hi=[]) for m in methods}
    done = 0
    while done < reps:
        n = min(250, reps - done)
        pm, om = generate(sm, rng, n, .5)
        pa, oa = generate(sa, rng, n, .5)
        ds = dict(H4a=contrast(pm, om, "C4-C3"), H4b=contrast(pa, oa, "C4-C3"), H4prime=contrast(pm, om, "C4-GC"))
        for m in methods:
            rs = {t: rd.estimate(ds[t], m) for t in TESTS}
            acc[m]["p"].append(np.stack([rs[t]["p"] for t in TESTS], axis=1))
            for k, f in (("est", "estimate"), ("ok", "estimable"), ("lo", "lo90"), ("hi", "hi90")):
                acc[m][k].append(np.stack([rs[t][f] for t in TESTS], axis=1))
        done += n
    truth = dict(H4a=sm["truth"]["C4-C3"], H4b=sa["truth"]["C4-C3"], H4prime=sm["truth"]["C4-GC"])
    out = dict(key=key, scenario=name, slope=sd, reps=reps, truth=truth, true_nulls=nulls,
               calib_error=max(sm["calib_error"], sa["calib_error"]), methods={})
    for m in methods:
        a = {k: np.concatenate(v) for k, v in acc[m].items()}
        ph = rd.holm(a["p"])
        sig = ph < .05
        res = {}
        for j, t in enumerate(TESTS):
            r = dict(estimable=a["ok"][:, j], lo90=a["lo"][:, j], hi90=a["hi"][:, j])
            res[t] = dict(nonestimable=p_(~a["ok"][:, j]),
                          significant_reduction=p_(sig[:, j] & (a["est"][:, j] < 0)),
                          significant_increase=p_(sig[:, j] & (a["est"][:, j] > 0)),
                          mean_estimate=float(np.nanmean(a["est"][:, j])), truth=truth[t])
            if t in ("H4a", "H4b"):
                tt = rd.tost(r, ph[:, j])
                res[t]["tost_run"] = p_(tt["run"])
                res[t]["tost_equivalent"] = p_(tt["equivalent"])
                res[t]["interval_only_inside"] = p_(tt["interval_only_inside"])
        fam = None
        if nulls:
            idx = [TESTS.index(t) for t in nulls]
            fam = p_(sig[:, idx].any(axis=1))
        out["methods"][m] = dict(tests=res, family_error_true_nulls=fam, any_significant=p_(sig.any(axis=1)))
    return out


def jobs(seed, reps, methods):
    ja, jb = [], []
    for series, (outcome, structure, missing, effects) in SERIES.items():
        for e in effects:
            for sd, sh in GRID:
                if e == "null_e" and sd == 0:
                    continue
                key = f"{series}|{e}|sd{sd}|sh{sh}"
                ja.append((key, outcome, structure, missing, e, sd, sh, reps, seed, methods))
    for name, (_, _, sds, _) in B_SCEN.items():
        for sd in sds:
            jb.append((f"{name}|sd{sd}", name, sd, reps, seed, methods))
    return ja, jb


def evaluate(A, B, method):
    fails, borderline = [], []

    def chk(tag, val, th, upper):
        bad = val > th if upper else val < th
        if bad:
            fails.append(f"{tag}: {val:.4f}")
        if abs(val - th) < 2 * np.sqrt(max(val * (1 - val), 1e-12) / A[0]["reps"]):
            borderline.append(f"{tag}: {val:.4f}")
    for r in A:
        for c, x in r["methods"][method].items():
            tag = f"{r['key']} {c}"
            chk(tag + " nonestimable", x["nonestimable"]["value"], TH["nonest"], True)
            chk(tag + " coverage90", x["coverage90"]["value"], TH["cov90"], False)
            if r["outcome"] == "legit_completed":
                chk(tag + " lower95", x["lower95_coverage"]["value"], TH["lower95"], False)
            if r["outcome"] != "legit_completed" and c == "C4-C3" and r["effect"] in ("plus", "minus", "sparse_plus"):
                chk(tag + " false_equivalence", x["ci90_inside_pm3"]["value"], TH["false_equiv"], True)
            if r["outcome"] == "legit_completed" and abs(r["truth"][c] + .05) < 1e-9:
                chk(tag + " false_ni", x["lower95_above_minus5"]["value"], TH["false_ni"], True)
    for r in B:
        x = r["methods"][method]
        if r["true_nulls"]:
            chk(f"{r['key']} family_error", x["family_error_true_nulls"]["value"], TH["fwer"], True)
        for t, y in x["tests"].items():
            chk(f"{r['key']} {t} nonestimable", y["nonestimable"]["value"], TH["nonest"], True)
        if r["scenario"] in ("B3", "B4", "B6"):
            for t in ("H4a", "H4b"):
                chk(f"{r['key']} {t} formal_tost_false_equivalence", x["tests"][t]["tost_equivalent"]["value"], TH["false_equiv"], True)
    return dict(passes=not fails, failures=fails, borderline=borderline)


def truth_checks(A, B):
    bad = []
    for r in A:
        if r["calib_error"] > 1e-6:
            bad.append(f"{r['key']}: calibration error {r['calib_error']:.2e}")
        for m, xs in r["methods"].items():
            for c, x in xs.items():
                if not x["truth_check"]["ok"]:
                    bad.append(f"{r['key']} {m} {c}: mean {x['truth_check']['mc_mean']:.5f} vs truth {r['truth'][c]:.5f}")
        if r["effect"] in ("null_m", "null_e", "sparse"):
            for c, t in r["truth"].items():
                if abs(t) > 1e-9:
                    bad.append(f"{r['key']} {c}: null truth {t}")
    for r in B:
        if r["calib_error"] > 1e-6:
            bad.append(f"{r['key']}: calibration error {r['calib_error']:.2e}")
    return bad


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def meta(seed, reps, methods, spec="simulation-spec-v2.md"):
    import scipy
    return dict(spec=spec, seed=seed, reps=reps, methods=methods, python=sys.version.split()[0], numpy=np.__version__,
                scipy=scipy.__version__, code_sha256={f: sha(HERE / f) for f in ("sim_v2.py", "rate_diff.py", "simulate_intervals.py")})


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select"); s.add_argument("out"); s.add_argument("selection")
    f = sub.add_parser("final"); f.add_argument("out"); f.add_argument("--method", required=True, choices=rd.METHODS)
    for p in (s, f):
        p.add_argument("--reps", type=int, default=2000); p.add_argument("--workers", type=int, default=2)
        p.add_argument("--only", help="comma list of job-key prefixes (for timing trials only)")
    a = ap.parse_args()
    for p in [a.out] + ([a.selection] if a.cmd == "select" else []):
        if Path(p).exists():
            raise SystemExit(f"refusing to overwrite {p}")
    seed = SEED_SELECT if a.cmd == "select" else SEED_FINAL
    methods = list(rd.METHODS) if a.cmd == "select" else [a.method]
    ja, jb = jobs(seed, a.reps, methods)
    if a.only:
        pre = tuple(a.only.split(","))
        ja = [j for j in ja if j[0].startswith(pre)]
        jb = [j for j in jb if j[0].startswith(pre)]
    t0 = time.time()
    with Pool(a.workers) as pool:
        A = pool.map(run_a, ja, chunksize=1)
        B = pool.map(run_b, jb, chunksize=1)
    res = dict(meta=meta(seed, a.reps, methods), wall_seconds=round(time.time() - t0, 1), stage_a=A, stage_b=B,
               truth_check_failures=truth_checks(A, B))
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    verdict = {m: evaluate(A, B, m) for m in methods}
    if a.cmd == "select":
        chosen = next((m for m in ("case_t", "jackknife_t") if verdict[m]["passes"]), None) if not res["truth_check_failures"] else None
        Path(a.selection).write_text(json.dumps(dict(thresholds=TH, verdicts=verdict, selected=chosen,
            truth_check_failures=res["truth_check_failures"],
            rule="case_t if it passes every A and B criterion, else jackknife_t if it passes, else none (freeze blocker)"),
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({m: dict(passes=v["passes"], failures=len(v["failures"])) for m, v in verdict.items()}), "selected", chosen)
    else:
        print(json.dumps({m: dict(passes=v["passes"], failures=v["failures"][:10]) for m, v in verdict.items()}))
        res["verdict"] = verdict
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wall", res["wall_seconds"])


if __name__ == "__main__":
    main()
