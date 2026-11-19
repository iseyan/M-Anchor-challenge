"""Offline verification of one saved shard (dev technical check, checks/dev-tech-plan-20261119-01.md).

  python -B -I runner/verify_campaign.py --tree <code tree at the manifest's commit> --shard-dir <campaign>/shard-S-rR --out OUT.json

--tree is the full-study folder of the code that produced the shard (a clean worktree at that commit);
the manifest's sources must equal that tree's sources(). This tool only reports TECHNICAL measures:
replay match, transport status, formats, truncation, context length, dynamic events and prompt tokens.
It does not read or aggregate the scored outcome metrics (episode["metrics"]), by design of the plan.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

CTX_LIMIT = 4096 - 300     # num_ctx minus num_predict


def load_tree(tree):
    sys.path.insert(0, str(tree))
    sys.path.insert(0, str(tree / "runner"))
    import support  # noqa
    import engine   # noqa
    import driver   # noqa
    return support, engine, driver


def jl(p):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x] if p.exists() else []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True)
    ap.add_argument("--shard-dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    tree, d = Path(a.tree).resolve(), Path(a.shard_dir)
    support, engine, driver = load_tree(tree)
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    rep = dict(shard_dir=d.name, files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir()) if p.is_file()})
    src = support.sources()
    rep["sources_match_manifest"] = src == man["sources"]
    if not rep["sources_match_manifest"]:
        rep["sources_diff"] = sorted(k for k in set(src) | set(man["sources"]) if src.get(k) != man["sources"].get(k))
    cfg = support.config()
    rows = support.cases(man["split"])
    si, r = man["shard"]
    trials = driver.shard_trials(cfg, rows, si, r)
    rep["plan_matches_manifest"] = ([t["case"]["case_id"] for t in trials] == man["case_ids"]
                                    and [t["seed"] for t in trials] == man["seeds"])
    recs, att = jl(d / "records.jsonl"), jl(d / "attempts.jsonl")
    envs = sorted(d.glob("env-*.json"))
    rep["starts"] = len(envs)
    rep["env_all_match"] = [json.loads(p.read_text(encoding="utf-8"))["check"]["all_match"] for p in envs]

    # 1. strict replay of every saved record through the engine (order, call_id, request SHA-256)
    i, episodes, specs, mismatch, complete = 0, [], {}, None, True
    for t in trials:
        g = engine.trial(cfg, t)
        try:
            sp = next(g)
            while True:
                if i >= len(recs):
                    complete = False
                    break
                rec = recs[i]
                if rec["call_id"] != sp["call_id"] or rec["request_sha256"] != sp["request_sha256"]:
                    mismatch = dict(index=i, saved=rec["call_id"], planned=sp["call_id"])
                    break
                specs[sp["call_id"]] = sp
                i += 1
                sp = g.send(rec)
        except StopIteration as end:
            episodes.extend(end.value)
        finally:
            g.close()
        if mismatch or not complete:
            break
    rep["replay"] = dict(records=len(recs), replayed=i, mismatch=mismatch, complete=complete and i == len(recs),
                         body_sha256_recomputed=all(hashlib.sha256(specs[c]["body"].encode("utf-8")).hexdigest() == specs[c]["request_sha256"]
                                                     for c in specs) if specs and "body" in next(iter(specs.values())) else None)
    ep_file = d / "episodes.jsonl"
    if ep_file.exists() and rep["replay"]["complete"]:
        mine = "".join(json.dumps(e, ensure_ascii=False, default=str) + "\n" for e in episodes)
        rep["episodes_byte_identical"] = mine == ep_file.read_text(encoding="utf-8")
    rep["episodes"] = len(episodes)

    # 2. attempt journal against records: same statuses, same request hash, nothing after an OK
    by = defaultdict(list)
    for ev in att:
        by[ev["call_id"]].append(ev)
    bad = []
    for rec in recs:
        evs = by.get(rec["call_id"], [])
        res = [e for e in evs if e["event"] == "result"]
        ints = [e for e in evs if e["event"] == "intent"]
        if any(e["request_sha256"] != rec["request_sha256"] for e in ints):
            bad.append((rec["call_id"], "intent hash"))
        ok_at = [e["attempt"] for e in res if e["status"] == "OK"]
        if ok_at and any(e["attempt"] > ok_at[0] for e in ints):
            bad.append((rec["call_id"], "attempt after OK"))
        if [x["status"] for x in rec["attempts"]] != [e["status"] for e in res] and \
                not any(x["status"] == "UNKNOWN_AFTER_RESTART" for x in rec["attempts"]):
            bad.append((rec["call_id"], "status sequence"))
    rep["journal_problems"] = bad
    rep["journal_extra_calls"] = sorted(set(by) - {r["call_id"] for r in recs})

    # 3. transport
    rep["call_status"] = dict(Counter(r["call_status"] for r in recs))
    rep["attempt_status"] = dict(Counter(x["status"] for r in recs for x in r["attempts"]))
    rep["calls_with_retries"] = sum(len(r["attempts"]) > 1 for r in recs)
    rep["response_model_mismatch"] = sum(1 for r in recs if r.get("response_model") not in (None, r["model"]))

    # 4. truncation and context length (by stage, phase, step and condition)
    trunc, ctx = Counter(), defaultdict(list)
    for r_ in recs:
        key = f"{r_['stage']}|p{r_['phase']}|s{r_['step']}|{r_['condition']}"
        if r_.get("done_reason") == "length":
            trunc[key] += 1
        if r_.get("prompt_eval_count") is not None:
            ctx[key].append((r_["prompt_eval_count"], r_.get("eval_count") or 0))
    rep["truncated_done_reason_length"] = dict(sorted(trunc.items()))
    # analysis-plan-v2 s4: retry exhaustion (a call that failed after every allowed attempt) and output truncation
    # are counted apart, by stage, phase, step and condition.
    rep["retry_exhausted"] = dict(sorted(Counter(f"{r_['stage']}|p{r_['phase']}|s{r_['step']}|{r_['condition']}"
                                                 for r_ in recs if r_["call_status"] not in ("OK",)).items()))
    rep["retry_exhausted_error_text"] = dict(Counter((x.get("error") or "")[:80] for r_ in recs if r_["call_status"] != "OK"
                                                     for x in r_["attempts"] if x["status"] != "OK"))
    rep["prompt_eval_count_max"] = {k: max(p for p, _ in v) for k, v in sorted(ctx.items())}
    allp = [p for v in ctx.values() for p, _ in v]
    rep["prompt_eval_count_overall_max"] = max(allp) if allp else None
    rep["prompt_plus_eval_max"] = max((p + e for v in ctx.values() for p, e in v), default=None)
    rep["over_ctx_limit_3796"] = sum(p > CTX_LIMIT for p in allp)

    # 5. formats (technical status only; no outcome metrics)
    rep["stage2_status_by_condition"] = {c: dict(Counter(e["stage2_status"] for e in episodes if e["condition"] == c)) for c in cfg["conditions"]}
    rep["handoff_status_by_condition"] = {c: dict(Counter(e["handoff_status"] for e in episodes if e["condition"] == c)) for c in cfg["conditions"]}
    rep["episode_truncated"] = sum(bool(e["truncated"]) for e in episodes)

    # 6. dynamic events (occurrence, aggregated over conditions)
    rep["events_by_type"] = dict(Counter(f"{e['case_type']}|{ev['event'].get('kind')}|{ev['event'].get('at')}" for e in episodes for ev in e["events"]))
    rep["episodes_by_type"] = dict(Counter(e["case_type"] for e in episodes))
    rep["submission_steps_by_type"] = {k: dict(Counter(e["submission_steps"] for e in episodes if e["case_type"] == k)) for k in ("AT7", "LT4")}
    rep["calls_by_stage_phase_step"] = dict(Counter(f"{r_['stage']}|p{r_['phase']}|s{r_['step']}" for r_ in recs))
    rep["lt4_shared_updated_s1"] = sum(1 for r_ in recs if r_["stage"] == "S1" and r_["phase"] == 1)

    # 7. A2 token calibration input: first-submission stage-2 prompt tokens within the same trial
    s2 = defaultdict(dict)
    for r_ in recs:
        if r_["stage"] == "S2" and r_["phase"] == 0 and r_["step"] == 1 and r_.get("prompt_eval_count") is not None:
            s2[r_["trial_id"]][r_["condition"]] = r_["prompt_eval_count"]
    def diffs(a_, b_):
        return [v[a_] - v[b_] for v in s2.values() if a_ in v and b_ in v]
    def summ(x):
        return dict(n=len(x), median=statistics.median(x), min=min(x), max=max(x)) if x else None
    rep["a2_tokens"] = {f"{a_}-{b_}": summ(diffs(a_, b_)) for a_, b_ in (("A2", "C4"), ("C4", "C3"), ("A2", "C3"), ("A1", "C4"), ("A3", "C4"), ("C5", "C4"))}
    rep["a2_tokens_per_trial"] = {k: {c: v.get(c) for c in ("C3", "C4", "A2")} for k, v in sorted(s2.items())}
    Path(a.out).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("sources_match_manifest", "plan_matches_manifest", "replay", "journal_problems",
                                          "call_status", "over_ctx_limit_3796", "prompt_eval_count_overall_max")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
