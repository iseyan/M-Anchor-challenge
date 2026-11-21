"""Experiment 3 entry points over the frozen experiment-2 runner (full-study/runner, unchanged).

The experiment-2 driver, local service (serve.py) and page loop (page_loop.js) are reused as they are.
This module installs the experiment-3 engine additions (exp3.install) and points the driver at the
experiment-3 config, case lists and source hashes, then hands over to the frozen code:

  python -B -I exp3-permit/runner/run3.py serve --campaign DIR --shard 0:1 --split dev|main [--port 8765] [--max-calls N] [--cases ...]
      same as full-study/runner/serve.py (the Ollama page runs full-study/runner/page_loop.js unchanged)
  python -B -I exp3-permit/runner/run3.py verify --shard-dir DIR/shard-S-rR --out OUT.json
      offline check of a saved shard: sources and plan against the manifest, strict replay of every record,
      byte-identical episodes, the attempt journal, transport statuses, truncation and context length, and
      (implementation health, plan 5.5) episodes where the ledger reconciliation and legit_action_ok disagree
  python -B -I exp3-permit/runner/run3.py mock-reference --split dev --shard 0:1 --out DIR [--cases ...]
      the experiment-2 mock responses run in-process (for tests)

--split main is refused while config.json has run_enabled false (the frozen driver's own guard).
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import exp3  # noqa: E402
import ledger  # noqa: E402

engine = exp3.install()
sys.path.insert(0, str(exp3.FULL / "runner"))
import driver  # noqa: E402  (full-study/runner/driver.py)

driver.config = exp3.config
driver.load_split = exp3.split
driver.sources = exp3.sources
_FROZEN_CALL = driver.call


def guarded_call(cfg, sp, prior, att_path):
    """Stop the shard when the model server cannot be reached (added 2026-11-21 after the dev check, where
    Ollama restarted mid-shard and the remaining 22 trials failed within five seconds).

    The frozen call and its retry rule run unchanged. If every attempt of a call ended in TRANSPORT_ERROR
    (the page could not connect at all) and at least one of them was sent in this start, the shard stops
    with a fatal message instead of going on to the next call. That call stays a failure: its attempts are
    in the journal, and a resume finalizes it from the journal without sending it again."""
    rec = _FROZEN_CALL(cfg, sp, prior, att_path)
    sent_now = len(rec["attempts"]) > sum(1 for e in prior if e["event"] == "intent")
    if sent_now and rec["call_status"] == "TRANSPORT_ERROR" and all(a["status"] == "TRANSPORT_ERROR" for a in rec["attempts"]):
        raise driver.Fatal("SERVER_UNREACHABLE: every attempt of " + sp["call_id"] + " failed to connect; "
                           "shard stopped. Check the model server, then start the same shard again to resume")
    return rec


driver.call = guarded_call
import mock  # noqa: E402  (full-study/runner/mock.py)

mock.config = exp3.config
mock.load_split = exp3.split

CTX_LIMIT = 4096 - 300


def jl(p):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x] if p.exists() else []


def verify(shard_dir, out=None):
    d = Path(shard_dir)
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    rep = dict(shard_dir=d.name, files={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                        for p in sorted(d.iterdir()) if p.is_file()})
    src = exp3.sources()
    rep["sources_match_manifest"] = src == man["sources"]
    if not rep["sources_match_manifest"]:
        rep["sources_diff"] = sorted(k for k in set(src) | set(man["sources"]) if src.get(k) != man["sources"].get(k))
    cfg = exp3.config()
    rows = exp3.split(man["split"])
    si, r = man["shard"]
    trials = driver.shard_trials(cfg, rows, si, r)
    trials = [t for t in trials if t["case"]["case_id"] in set(man["case_ids"])]
    rep["plan_matches_manifest"] = ([t["case"]["case_id"] for t in trials] == man["case_ids"]
                                    and [t["seed"] for t in trials] == man["seeds"])
    recs, att = jl(d / "records.jsonl"), jl(d / "attempts.jsonl")
    rep["starts"] = len(list(d.glob("env-*.json")))
    rep["env_all_match"] = [json.loads(p.read_text(encoding="utf-8"))["check"]["all_match"] for p in sorted(d.glob("env-*.json"))]

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
                         body_sha256_recomputed=all(hashlib.sha256(s["body"].encode("utf-8")).hexdigest() == s["request_sha256"]
                                                    for s in specs.values()) if specs else None)
    ep_file = d / "episodes.jsonl"
    if ep_file.exists() and rep["replay"]["complete"]:
        mine = "".join(json.dumps(e, ensure_ascii=False, default=str) + "\n" for e in episodes)
        rep["episodes_byte_identical"] = mine == ep_file.read_text(encoding="utf-8")
    rep["episodes"] = len(episodes)

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
    rep["journal_extra_calls"] = sorted(set(by) - {x["call_id"] for x in recs})

    key = lambda x: f"{x['stage']}|p{x['phase']}|s{x['step']}|{x['condition']}"  # noqa: E731
    rep["call_status"] = dict(Counter(x["call_status"] for x in recs))
    rep["attempt_status"] = dict(Counter(a["status"] for x in recs for a in x["attempts"]))
    rep["retry_exhausted"] = dict(sorted(Counter(key(x) for x in recs if x["call_status"] != "OK").items()))
    rep["truncated_done_reason_length"] = dict(sorted(Counter(key(x) for x in recs if x.get("done_reason") == "length").items()))
    allp = [x["prompt_eval_count"] for x in recs if x.get("prompt_eval_count") is not None]
    rep["prompt_eval_count_overall_max"] = max(allp) if allp else None
    rep["over_ctx_limit_3796"] = sum(p > CTX_LIMIT for p in allp)
    rep["stage2_status_by_condition"] = {c: dict(Counter(e["stage2_status"] for e in episodes if e["condition"] == c))
                                         for c in cfg["conditions"]}
    rep["handoff_status_by_condition"] = {c: dict(Counter(e["handoff_status"] for e in episodes if e["condition"] == c))
                                          for c in cfg["conditions"]}

    # implementation health only (plan 5.5): no success rates are reported here
    by_case = {c["case_id"]: c for c in rows}
    disagree, recorded = [], 0
    for e in episodes:
        c = by_case[e["case_id"]]
        recorded += "final_ledger" in e and all("decision_id" in a for a in e["actions"])
        if ledger.gold_payment(c) is None:
            continue
        rec_ok = ledger.reconcile(e, c)["ok"]
        if rec_ok != bool(e["metrics"].get("legit_action_ok")):
            disagree.append(e["episode_id"])
    rep["episodes_with_decision_ids_and_final_ledger"] = recorded
    rep["ledger_vs_legit_action_ok_disagreements"] = disagree
    if out:
        Path(out).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return rep


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "serve":
        import serve  # noqa: E402  (full-study/runner/serve.py; uses the driver configured above)
        return serve.main(rest)
    if cmd == "driver":
        return driver.main(rest)
    if cmd == "verify":
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--shard-dir", required=True)
        ap.add_argument("--out", required=True)
        a = ap.parse_args(rest)
        rep = verify(a.shard_dir, a.out)
        print(json.dumps({k: rep[k] for k in ("sources_match_manifest", "plan_matches_manifest", "replay", "journal_problems",
                                              "call_status", "over_ctx_limit_3796", "ledger_vs_legit_action_ok_disagreements")},
                         ensure_ascii=False))
        return 0
    if cmd == "mock-reference":
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--split", default="dev")
        ap.add_argument("--shard", default="0:1")
        ap.add_argument("--out", required=True)
        ap.add_argument("--cases")
        a = ap.parse_args(rest)
        recs, eps = mock.reference(a.split, a.shard, a.out, set(a.cases.split(",")) if a.cases else None)
        print(json.dumps(dict(records=len(recs), episodes=len(eps))))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
