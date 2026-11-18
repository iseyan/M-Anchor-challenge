"""Exploratory analysis of the follow-up run (design-v0.2.md, section 7). No hypothesis tests.

usage (from experiments/llm03-m-anchor/):
  python -I followup-hp/analysis/analyze.py <followup_run_dir> <out_json>

For each outcome: the rate per condition (two-sender mean, and per sender) and the paired differences
H - C3R, C4R - H, P - C4R and P - C3R, each with a case-cluster bootstrap percentile 95% interval
(cases resampled with replacement within the label, each with all its episodes; B = 10,000;
numpy default_rng(20261119); legit draws first, then attack). All rates use every planned episode as the
denominator. With 10 legit and 5 attack cases, the intervals are coarse.

Also reported, as history only: the main run's C3 and C4 rates on the same cases and senders.
"""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np

B, SEED = 10_000, 20261119
CONDS = ["C3R", "C4R", "H", "P"]
DIFFS = [("C3R", "H"), ("H", "C4R"), ("C4R", "P"), ("C3R", "P")]     # (a, b): b - a
OUTCOMES = {
    "legit": [("legit_completed", "metrics"), ("legit_action_ok", "metrics"), ("legit_epistemic_ok", "metrics"),
              ("payment_proposed_gold", "metrics_followup"), ("payment_proposed_any", "metrics_followup"),
              ("confirmed_without_payment", "metrics_followup")],
    "attack": [("mis_propagation", "metrics"), ("attempt", "metrics"), ("unauthorized_state_change", "metrics"),
               ("not_sent", "metrics"), ("attempt_only_read_record", "metrics"),
               ("mis_propagation_if_not_sent_counted", "metrics"), ("receiver_false_approval", "metrics")],
}


def cube(eps, label, key, field, senders, conds):
    cases = sorted({e["case_id"] for e in eps if e["label"] == label})
    ci = {c: i for i, c in enumerate(cases)}
    s = np.zeros((len(cases), len(conds), len(senders)))
    n = np.zeros_like(s)
    for e in eps:
        if e["label"] != label or e["condition"] not in conds:
            continue
        i, j, k = ci[e["case_id"]], conds.index(e["condition"]), senders.index(e["sender"])
        s[i, j, k] += float(bool(e[field][key]))
        n[i, j, k] += 1
    return cases, s, n


def rates(w, s, n):
    return np.einsum("bi,ijk->bjk", w, s) / np.einsum("bi,ijk->bjk", w, n)


def ci95(x):
    return [float(np.percentile(x, 2.5)), float(np.percentile(x, 97.5))]


def main(run_dir: pathlib.Path, out_path: str):
    eps = [json.loads(l) for l in (run_dir / "episodes.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    senders = sorted({e["sender"] for e in eps})
    rng = np.random.default_rng(SEED)
    res = {"B": B, "seed": SEED, "method": "case-cluster bootstrap, percentile, within label; two-sender mean"}
    for label in ("legit", "attack"):           # fixed draw order
        cases = sorted({e["case_id"] for e in eps if e["label"] == label})
        w = rng.multinomial(len(cases), [1 / len(cases)] * len(cases), size=B).astype(float)
        one = np.ones((1, len(cases)))
        block = {"cases": cases}
        for key, field in OUTCOMES[label]:
            _, s, n = cube(eps, label, key, field, senders, CONDS)
            pt, bt = rates(one, s, n)[0], rates(w, s, n)
            e = {"counts": {c: {"events": int(s[:, j, :].sum()), "n": int(n[:, j, :].sum()),
                                **{sd: [int(s[:, j, k].sum()), int(n[:, j, k].sum())] for k, sd in enumerate(senders)}}
                            for j, c in enumerate(CONDS)},
                 "rates": {c: {"two_sender_mean": {"estimate": float(pt[j].mean()), "ci95": ci95(bt[:, j, :].mean(axis=1))},
                               **{sd: {"estimate": float(pt[j, k]), "ci95": ci95(bt[:, j, k])} for k, sd in enumerate(senders)}}
                           for j, c in enumerate(CONDS)},
                 "differences_b_minus_a": {}}
            for a, b in DIFFS:
                ja, jb = CONDS.index(a), CONDS.index(b)
                e["differences_b_minus_a"][f"{b}-{a}"] = {
                    "estimate": float(pt[jb].mean() - pt[ja].mean()),
                    "ci95": ci95(bt[:, jb, :].mean(axis=1) - bt[:, ja, :].mean(axis=1)),
                    "per_sender": {sd: {"estimate": float(pt[jb, k] - pt[ja, k]), "ci95": ci95(bt[:, jb, k] - bt[:, ja, k])}
                                   for k, sd in enumerate(senders)}}
            block[key] = e
        res[label] = block
    # history only: the main run's C3 / C4 on the same cases (no comparison is drawn from it)
    exp = run_dir.parents[2]
    main_eps = [json.loads(l) for l in (exp / "main-runs/main-20261118-01/episodes.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    keep = {e["case_id"] for e in eps}
    hist = {}
    for e in main_eps:
        if e["case_id"] in keep and e["condition"] in ("C3", "C4"):
            for key, field in OUTCOMES[e["label"]]:
                if field != "metrics":
                    continue
                c = hist.setdefault(f"{e['label']}|{e['condition']}|{key}", [0, 0])
                c[0] += int(bool(e["metrics"][key]))
                c[1] += 1
    res["history_main_run_same_cases"] = hist
    pathlib.Path(out_path).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("written", out_path)


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1]), sys.argv[2])
