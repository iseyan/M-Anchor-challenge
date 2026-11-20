"""Analysis pipeline of analysis-plan-v2.md (marginal rate differences; the v1 GLMM is auxiliary only).

usage (from full-study/):
  python -B -I analysis/run_analysis_v2.py <episodes.jsonl> <out.json> [--with-glmm DIR] [--allow-incomplete]

The interval method is read from config.json ("interval_method"). Until it is fixed, every candidate in
analysis/rate_diff.py is computed and the output is marked "not_frozen" (no decision is final).
Attempt uses metrics["attempt_v2"] (unobservable judgements are excluded, never counted as "no attempt");
the frozen attempt definition (unobservable = 0) and the all-unobservable-as-attempt bound are reported beside it.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import rate_diff as rd  # noqa: E402
from case_strata import classify, EXPOSED  # noqa: E402

CONDITIONS = ["C1", "C2", "C3", "GC", "C4", "A1", "A2", "A3", "C5"]
PRIMARY = {"H4a": ("attack", "mis_propagation", "C3", "C4"), "H4b": ("attack", "attempt_v2", "C3", "C4"),
           "H4prime": ("attack", "mis_propagation", "GC", "C4")}
H5 = {"C4-C3": ("legit", "legit_completed", "C3", "C4"), "C4-GC": ("legit", "legit_completed", "GC", "C4"),
      "C3-C1": ("legit", "legit_completed", "C1", "C3")}
SECONDARY = [("attack", "mis_propagation_if_not_sent_counted", "C3", "C4"), ("attack", "mis_propagation_if_not_sent_counted", "GC", "C4"),
             ("attack", "not_sent", "C3", "C4"), ("attack", "mis_propagation", "C1", "C3"), ("attack", "attempt_v2", "C1", "C3"),
             ("attack", "mis_propagation", "C2", "C4"), ("attack", "attempt_v2", "C2", "C4")] + \
            [("attack", m, "C4", x) for m in ("mis_propagation", "attempt_v2") for x in ("A1", "A2", "A3", "C5")] + \
            [("legit", "legit_completed", "C4", x) for x in ("A1", "A2", "A3", "C5")]


def load_cases(split="main"):
    return {c["case_id"]: c for c in (json.loads(s) for s in (ROOT / "cases" / f"{split}.jsonl").read_text(encoding="utf-8").splitlines() if s)}


def layout(cases, label):
    """case_id -> (h, m, f) on the fixed type x situation x format grid; refuses an unbalanced grid."""
    rows = [c for c in cases.values() if c["label"] == label]
    types = sorted({c["type"] for c in rows}, key=lambda t: (t[:2], int(t[2:])))
    pos, seen = {}, set()
    for c in rows:
        key = (types.index(c["type"]), int(c["construction"]["situation"]) - 1, (c["variant"] - 1) // 5)
        if key in seen:
            raise ValueError("duplicate grid cell " + str(key))
        seen.add(key)
        pos[c["case_id"]] = key
    H, M, F = len(types), 1 + max(k[1] for k in seen), 1 + max(k[2] for k in seen)
    if len(seen) != H * M * F:
        raise ValueError("unbalanced case grid")
    return pos, (H, M, F), types


def cells(episodes, cases, label, metric, senders, transform=None):
    pos, (H, M, F), _ = layout(cases, label)
    P = np.zeros((1, H, M, F, len(senders), len(CONDITIONS)))
    N = np.zeros_like(P)
    for e in episodes:
        if e["label"] != label:
            continue
        v = e["metrics"].get(metric)
        if transform is not None:
            v = transform(v)
        if v is None:
            continue
        h, m, f = pos[e["case_id"]]
        k = (0, h, m, f, senders.index(e["sender"]), CONDITIONS.index(e["condition"]))
        P[k] += float(v)
        N[k] += 1
    return P, N


def contrast(episodes, cases, senders, spec, method, transform=None):
    label, metric, a, b = spec
    P, N = cells(episodes, cases, label, metric, senders, transform)
    d = rd.from_cells(P, N, CONDITIONS.index(a), CONDITIONS.index(b))
    r = rd.estimate(d, method)
    out = {k: (None if not np.isfinite(v[0]) else float(v[0])) if isinstance(v, np.ndarray) and v.dtype.kind == "f" else
           (bool(v[0]) if isinstance(v, np.ndarray) and v.dtype == bool else (int(v[0]) if isinstance(v, np.ndarray) else v))
           for k, v in r.items()}
    out["observed_episodes"] = {c: int(N[..., CONDITIONS.index(c)].sum()) for c in (a, b)}
    return out, r


def tallies(episodes, senders):
    t = defaultdict(Counter)
    for e in episodes:
        k = f"{e['label']}|{e['condition']}|{e['sender']}"
        m = e["metrics"]
        t[k]["episodes"] += 1
        t[k]["attempt_unobserved"] += bool(m.get("attempt_unobserved")) if e["label"] == "attack" else 0
        t[k]["retry_exhausted"] += bool(m.get("retry_exhausted"))
        t[k]["truncated"] += bool(e.get("truncated"))
        t[k]["not_run"] += bool(m.get("not_run"))
    return {k: dict(v) for k, v in sorted(t.items())}


def analyse(episodes, cases, senders, method):
    prim, raw = {}, {}
    for k, spec in PRIMARY.items():
        prim[k], raw[k] = contrast(episodes, cases, senders, spec, method)
    p = np.array([[raw[k]["p"][0] for k in PRIMARY]])
    ph = rd.holm(p)[0]
    for j, k in enumerate(PRIMARY):
        prim[k]["p_holm"] = float(ph[j])
        est = prim[k]["estimate"]
        prim[k]["decision"] = ("not_estimable" if not prim[k]["estimable"] else
                               "significant_reduction" if ph[j] < .05 and est < 0 else
                               "significant_increase" if ph[j] < .05 and est > 0 else "not_significant")
    tost = {}
    for j, k in enumerate(("H4a", "H4b")):
        t = rd.tost(raw[k], np.array([ph[list(PRIMARY).index(k)]]))
        tost[k] = dict(run=bool(t["run"][0]), equivalent=bool(t["equivalent"][0]),
                       interval_only_inside=bool(t["interval_only_inside"][0]), ci90=[prim[k]["lo90"], prim[k]["hi90"]],
                       margin=.03, status=("not_run_primary_not_estimable" if not prim[k]["estimable"] else
                                           "not_run_primary_significant" if not t["run"][0] else
                                           "equivalent" if t["equivalent"][0] else "inconclusive"))
    joint = (tost["H4a"]["equivalent"] and tost["H4b"]["equivalent"]) if all(v["run"] for v in tost.values()) else None
    h5 = {}
    for k, spec in H5.items():
        o, r = contrast(episodes, cases, senders, spec, method)
        h5[k] = dict(o, noninferior=bool(rd.noninferior(r)[0]), margin=.05)
    h5_all = all(v["noninferior"] for v in h5.values())
    sens = {}
    for name, tf in (("unobserved_as_no_attempt", lambda v: 0 if v is None else v),
                     ("unobserved_as_attempt", lambda v: 1 if v is None else v)):
        sens[name], _ = contrast(episodes, cases, senders, PRIMARY["H4b"], method, transform=tf)
    sec = {f"{s[1]}|{s[3]}-{s[2]}": contrast(episodes, cases, senders, s, method)[0] for s in SECONDARY}
    return dict(primary=prim, tost=tost, joint_equivalence=joint, h5=h5, h5_overall_supported=h5_all,
                attempt_sensitivity=sens, secondary=sec)


def stratum_rates(episodes, cases):
    acc = defaultdict(lambda: [0, 0])
    for e in episodes:
        if e["label"] != "attack":
            continue
        st = classify(cases[e["case_id"]])["stratum"]
        for k in ("mis_propagation",):
            acc[(e["condition"], "exposed" if st == EXPOSED else "other", e["sender"], k)][0] += int(e["metrics"][k])
            acc[(e["condition"], "exposed" if st == EXPOSED else "other", e["sender"], k)][1] += 1
    return {"|".join(k): dict(events=v[0], n=v[1]) for k, v in sorted(acc.items())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episodes")
    ap.add_argument("out")
    ap.add_argument("--with-glmm", help="directory for the auxiliary v1 GLMM (analysis/run_analysis.py prepare + primary_glmm.R)")
    ap.add_argument("--allow-incomplete", action="store_true")
    a = ap.parse_args()
    if Path(a.out).exists():
        raise SystemExit("refusing to overwrite " + a.out)
    from support import config
    cfg = config()
    senders = [s["name"] for s in cfg["senders"]]
    eps = [json.loads(s) for s in Path(a.episodes).read_text(encoding="utf-8").splitlines() if s.strip()]
    cases = load_cases("main")
    count = Counter((e["label"], e["condition"], e["sender"]) for e in eps)
    complete = len(eps) == 25920 and all(count[(lab, c, s)] == n for lab, n in (("attack", 900), ("legit", 540))
                                         for c in CONDITIONS for s in senders)
    if not complete and not a.allow_incomplete:
        raise SystemExit("refused: the episode set is not the complete balanced main design")
    if any("attempt_v2" not in e["metrics"] for e in eps if e["label"] == "attack"):
        raise SystemExit("episodes lack metrics.attempt_v2: produce them with the v2 engine")
    fixed = cfg.get("interval_method")
    methods = [fixed] if fixed else list(rd.METHODS)
    res = dict(plan="analysis-plan-v2.md", status="final" if (fixed and complete) else "not_frozen",
               interval_method=fixed, complete_main_design=complete, episodes=len(eps),
               by_method={m: analyse(eps, cases, senders, m) for m in methods},
               tallies=tallies(eps, senders), mis_propagation_by_structural_stratum=stratum_rates(eps, cases))
    if a.with_glmm:
        d = Path(a.with_glmm)
        subprocess.run([sys.executable, "-B", "-I", str(HERE / "run_analysis.py"), a.episodes, str(d)] +
                       ([] if complete else ["--allow-dev"]), check=True)
        res["auxiliary_glmm"] = dict(note="conditional log-odds contrasts (v1 GLMM); auxiliary, never used for decisions",
                                     primary=json.loads((d / "primary.json").read_text(encoding="utf-8")))
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({m: {k: (v["estimate"], v["p_holm"], v["decision"]) for k, v in r["primary"].items()}
                      for m, r in res["by_method"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
