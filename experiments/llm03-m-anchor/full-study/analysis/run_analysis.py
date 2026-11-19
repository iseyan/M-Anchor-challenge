"""Formal analysis pipeline of the expanded study (analysis-plan-v1-draft.md, sections 1-7).

usage (from full-study/):
  python -B -I analysis/run_analysis.py <episodes.jsonl> <out_dir> [--interval-method NAME] [--allow-dev]

Steps
  1. prepare: episodes (engine replay output) -> out_dir/analysis_episodes.csv, with the pre-output
     structural stratum (case_strata.classify) and the fixed format of every case. Refuses unless the
     main design is complete and balanced (25,920 episodes) - or --allow-dev for development records.
  2. primary: Rscript analysis/primary_glmm.R (H4a, H4b, H4' with Holm)            -> primary.json
  3. rates: situation-level paired rate differences, equal sender weights, with the interval method
     fixed in config.json ("interval_method"); stratum-wise rates for mis-propagation  -> rates.json
  4. decisions: conditional TOST (only when the matching H4 Holm p >= .05), joint equivalence, H5
     (three individual one-sided tests; overall support needs all three)              -> decisions.json
The interval method is chosen by the pre-specified simulation; until config.json names it, steps 3-4
run only with an explicit --interval-method and the output is marked "not_frozen".
C5's epistemic denials are reported separately and never enter attempt (common-gate judgement).
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from case_strata import classify  # noqa: E402
from cluster_intervals import intervals  # noqa: E402
import decisions  # noqa: E402

METRICS = ["mis_propagation", "mis_propagation_if_not_sent_counted", "not_sent", "attempt", "attempt_only_read_record",
           "forbidden_requests", "unauthorized_state_change", "receiver_false_approval", "legit_completed",
           "legit_action_ok", "legit_epistemic_ok", "legit_gate_denied_correct_payment", "epistemic_gate_denials",
           "correct_payment_epistemically_denied"]
CONDITIONS = ["C1", "C2", "C3", "GC", "C4", "A1", "A2", "A3", "C5"]
METHODS = {"percentile", "bca", "raowu", "jackknife_t"}
RATE_CONTRASTS = {
    "attack": [("mis_propagation", "C3", "C4"), ("attempt", "C3", "C4"), ("mis_propagation", "GC", "C4"),
               ("mis_propagation_if_not_sent_counted", "C3", "C4"), ("mis_propagation_if_not_sent_counted", "GC", "C4"),
               ("mis_propagation", "C1", "C3"), ("mis_propagation", "C4", "A1"), ("mis_propagation", "C4", "A2"),
               ("mis_propagation", "C4", "A3"), ("mis_propagation", "C4", "C5"), ("attempt", "C4", "C5")],
    "legit": [("legit_completed", "C3", "C4"), ("legit_completed", "GC", "C4"), ("legit_completed", "C1", "C3"),
              ("legit_completed", "C4", "A1"), ("legit_completed", "C4", "A2"), ("legit_completed", "C4", "A3"),
              ("legit_completed", "C4", "C5")],
}
H5 = {("legit_completed", "C3", "C4"): "C4-C3", ("legit_completed", "GC", "C4"): "C4-GC",
      ("legit_completed", "C1", "C3"): "C3-C1"}


def load_cases(split):
    return {c["case_id"]: c for c in (json.loads(s) for s in (ROOT / "cases" / f"{split}.jsonl").read_text(encoding="utf-8").splitlines() if s)}


def prepare(episodes, cases, allow_dev):
    rows = []
    for e in episodes:
        c = cases[e["case_id"]]
        m = e["metrics"]
        row = dict(episode_id=e["episode_id"], trial_id=e["trial_id"], sender=e["sender"], rep=e["rep"],
                   case_id=e["case_id"], case_type=e["case_type"], label=e["label"], condition=e["condition"],
                   situation=e["frame_id"], format=c["construction"]["presentation"],
                   stratum=classify(c)["stratum"] if c["label"] == "attack" else "legit")
        for k in METRICS:
            v = m.get(k)
            row[k] = "" if v is None else int(v)
        rows.append(row)
    cells = defaultdict(int)
    for r in rows:
        cells[(r["label"], r["condition"], r["sender"])] += 1
    expected = {"attack": 300 * 3, "legit": 180 * 3}
    complete = len(rows) == 25920 and all(cells[(lab, c, s)] == expected[lab] for lab in expected
                                          for c in CONDITIONS for s in {r["sender"] for r in rows})
    if not complete and not allow_dev:
        raise SystemExit("refused: the episode set is not the complete balanced main design")
    return rows, complete


def situation_differences(rows, label, outcome, a, b):
    """v[type, situation] = equal-sender mean of (b - a), averaged over formats and repetitions in a situation."""
    sums = defaultdict(lambda: [0.0, 0])
    for r in rows:
        if r["label"] != label or r["condition"] not in (a, b) or r[outcome] == "":
            continue
        sign = 1 if r["condition"] == b else -1
        key = (r["case_type"], r["situation"], r["sender"], r["condition"])
        sums[key][0] += r[outcome]
        sums[key][1] += 1
    types = sorted({k[0] for k in sums})
    sits = {t: sorted({k[1] for k in sums if k[0] == t}) for t in types}
    senders = sorted({k[2] for k in sums})
    if len({len(v) for v in sits.values()}) != 1:
        raise ValueError("unbalanced situations per type")
    v = []
    for t in types:
        row = []
        for s in sits[t]:
            per_sender = []
            for sd in senders:
                ra = sums[(t, s, sd, a)]
                rb = sums[(t, s, sd, b)]
                if not ra[1] or not rb[1] or ra[1] != rb[1]:
                    raise ValueError(f"unpaired cell {t} {s} {sd}")
                per_sender.append(rb[0] / rb[1] - ra[0] / ra[1])
            row.append(sum(per_sender) / len(per_sender))
        v.append(row)
    return v, types


def rates_block(rows, method, rng_seed=20261120, draws=10000):
    import numpy as np
    rng = np.random.default_rng(rng_seed)
    out = {}
    for label in ("attack", "legit"):            # fixed draw order: attack, then legit
        specs = RATE_CONTRASTS[label]
        arrays = [situation_differences(rows, label, *s)[0] for s in specs]
        v = np.stack([np.asarray(x) for x in arrays], axis=-1)
        res = intervals(v, rng, draws=draws)
        sel = res[method] if method in ("percentile", "raowu", "jackknife_t") else res["bca"]
        for i, (outcome, a, b) in enumerate(specs):
            key = f"{outcome}|{b}-{a}"
            if sel is None:
                out[key] = dict(estimate=float(res["estimate"][i]), method=method, status="interval_unavailable",
                                errors=res.get("bca_errors"))
                continue
            lo95, lo90, hi90, hi95 = (float(x) for x in sel[i])
            out[key] = dict(estimate=float(res["estimate"][i]), method=method, ci95=[lo95, hi95], ci90=[lo90, hi90],
                            one_sided_95_lower=lo90, degenerate=bool(res["degenerate"][i]))
    return out


def observed_rates(rows):
    cells = defaultdict(lambda: [0, 0])
    for r in rows:
        for k in ("mis_propagation", "mis_propagation_if_not_sent_counted", "not_sent", "attempt", "attempt_only_read_record",
                  "receiver_false_approval", "legit_completed", "legit_action_ok", "legit_epistemic_ok",
                  "epistemic_gate_denials", "correct_payment_epistemically_denied", "unauthorized_state_change"):
            if r[k] == "":
                continue
            for scope in ("all", r["sender"]):
                for strat in ("all", r["stratum"]):
                    c = cells[(r["label"], r["condition"], scope, strat, k)]
                    c[0] += r[k]
                    c[1] += 1
    return {"|".join(map(str, k)): {"events": v[0], "n": v[1]} for k, v in sorted(cells.items())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episodes")
    ap.add_argument("out_dir")
    ap.add_argument("--interval-method", choices=sorted(METHODS))
    ap.add_argument("--allow-dev", action="store_true")
    ap.add_argument("--split", default="main")
    a = ap.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=False)
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    frozen_method = cfg.get("interval_method")
    method = frozen_method or a.interval_method
    eps = [json.loads(s) for s in Path(a.episodes).read_text(encoding="utf-8").splitlines() if s.strip()]
    rows, complete = prepare(eps, load_cases(a.split), a.allow_dev)
    with (out / "analysis_episodes.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    subprocess.run(["Rscript", str(HERE / "primary_glmm.R"), str(out / "analysis_episodes.csv"), str(out / "primary.json")],
                   check=True)
    primary = {r["id"]: r for r in json.loads((out / "primary.json").read_text(encoding="utf-8"))["primary"]}
    (out / "observed_rates.json").write_text(json.dumps(observed_rates(rows), ensure_ascii=False, indent=1), encoding="utf-8")
    if method is None:
        print("primary tests written; no interval method fixed, rates and decisions not computed")
        return
    rates = rates_block(rows, method)
    (out / "rates.json").write_text(json.dumps(rates, ensure_ascii=False, indent=1), encoding="utf-8")
    tost = {"H4a": decisions.conditional_tost(primary["H4a"], rates["mis_propagation|C4-C3"].get("ci90")),
            "H4b": decisions.conditional_tost(primary["H4b"], rates["attempt|C4-C3"].get("ci90"))}
    joint = decisions.joint_equivalence(tost) if all(v["status"] in ("equivalent", "inconclusive") for v in tost.values()) else None
    h5 = decisions.h5({H5[k]: rates[f"{k[0]}|{k[2]}-{k[1]}"].get("one_sided_95_lower") for k in H5})
    status = "final" if (frozen_method and complete) else "not_frozen"
    (out / "decisions.json").write_text(json.dumps(dict(status=status, interval_method=method, complete_main_design=complete,
        primary={k: dict(decision=v["decision"], p_holm=v.get("p_holm"), method=v["method"]) for k, v in primary.items()},
        tost=tost, joint_equivalence=joint, h5=h5), ensure_ascii=False, indent=1), encoding="utf-8")
    print("written", out)


if __name__ == "__main__":
    main()
