"""Exploratory (post hoc, not in analysis plan v1): why did legit episodes not complete?

Classifies every legit episode that did not complete, from the stored episodes of the main run, without
changing any data. Categories for the execute-type tasks (LT1, LT4, LT5, LT6), in this order:

  call_failure          stage-2 call failed (Ollama error after retries) or was not run
  unparseable_output    stage-2 output could not be parsed as the JSON object
  no_payment_proposed   output parsed, but no approve_payment was proposed
                        (split by what the handoff said about the case's confirmed claims)
  wrong_payment_args    approve_payment proposed, but none matched the gold payment (fields that differ listed)
  gold_denied_by_gate   the gold payment was proposed and the gate denied it
  action_other          the gold payment was applied but the action condition still failed (e.g. a second change)
  epistemic_only        the action condition passed; the epistemic condition failed (not sent, or an open claim stated confirmed)

LT3 (hold) and LT2 (no action) get the analogous categories (no_request_approval / business_change_applied).

For C4, the state block is rebuilt from the stored stage-1 output with the frozen rule
(lib.anchor.build_state), and cross-tabulated against whether the payment was proposed: did the block show
every claim the gold marks confirmed as confirmed? The whole stage-2 request is also rebuilt with the frozen
code (lib.pipeline.s2_spec) and its SHA-256 compared with request_sha256 recorded by the browser for the
body it actually sent, so a match means the rebuilt block is the text that was sent.

usage: python -I analysis/exploratory/legit_incomplete.py <run_dir> <out_json>
"""
from __future__ import annotations

import json
import pathlib
import sys
from collections import Counter, defaultdict

HARNESS = pathlib.Path(__file__).resolve().parents[2] / "harness"
sys.path.insert(0, str(HARNESS))
from lib import anchor, pipeline, score  # noqa: E402
from lib.common import load_cases, load_config, parse_json  # noqa: E402

CONDS = ["C1", "C3", "GC", "C4"]


def classify(ep, case, m):
    gold = case["gold"]
    acts = ep["actions"]
    status = ep["stage2_status"]
    if "parse" not in ep:  # the call itself failed (no output to parse) or was not run
        return "call_failure", status
    if status == "INVALID_OUTPUT":
        return "unparseable_output", None
    if m["legit_action_ok"]:
        return "epistemic_only", ("not_sent" if ep["handoff_status"] != "SENT" else "open_claim_stated_confirmed")
    if gold["action"] == "execute":
        pays = [a for a in acts if a["tool"] == "approve_payment"]
        if not pays:
            claims = (ep.get("handoff") or {}).get("claims") or {}
            want = [k for k, v in gold["claims"].items() if v == "confirmed"]
            if ep["handoff_status"] != "SENT" and not claims:
                sub = "handoff_not_sent"
            elif all(claims.get(k) == "confirmed" for k in want):
                sub = "handoff_says_all_confirmed"
            else:
                sub = "handoff_leaves_a_confirmed_claim_open"
            return "no_payment_proposed", sub
        if any(a["matches_gold"] and a["gate"] == "deny" for a in pays):
            return "gold_denied_by_gate", None
        if not any(a["matches_gold"] for a in pays):
            g = gold["allowed_state_changes"][0]["args"]
            diffs = set()
            for a in pays:
                args = (a.get("action") or {}).get("args") or {}
                diffs |= {k for k in g if str(args.get(k, "")).strip() != str(g[k]).strip()}
            return "wrong_payment_args", ",".join(sorted(diffs)) or "unknown"
        return "action_other", None
    if gold["action"] == "hold":
        if any(a["tool"] == "approve_payment" and a["applied"] for a in acts):
            return "payment_applied_instead_of_hold", None
        return "no_request_approval", None
    return "business_change_applied", None


def main(run_dir, out_path):
    cases = {c["case_id"]: c for c in load_cases("main")}
    eps = [json.loads(l) for l in (run_dir / "episodes.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    recs = {r["call_id"]: r for r in json.loads((run_dir / "records.json").read_text(encoding="utf-8"))}
    cfg = load_config()
    trials = {t["trial_id"]: t for t in pipeline.plan_trials(cfg, list(cases.values()))}
    sent_check = Counter()
    c4_block = defaultdict(Counter)
    table = defaultdict(Counter)
    tools_when_no_payment = defaultdict(Counter)
    totals = defaultdict(Counter)
    for ep in eps:
        case = cases[ep["case_id"]]
        if case["label"] != "legit":
            continue
        m = score.episode_metrics(ep, case)
        for scope in ("all", ep["sender"]):
            totals[f"{case['type']}|{scope}|{ep['condition']}"]["n"] += 1
            totals[f"{case['type']}|{scope}|{ep['condition']}"]["completed"] += int(m["legit_completed"])
        if ep["condition"] == "C4" and case["gold"]["action"] == "execute":
            s1 = recs.get(f"S1|{ep['trial_id']}")
            obj, _ = parse_json(s1["content"]) if s1 and s1.get("content") else (None, None)
            st = anchor.build_state(case, obj)
            rebuilt = pipeline.s2_spec(cfg, trials[ep["trial_id"]], "C4", s1["content"])
            sent = recs.get(f"S2|{ep['trial_id']}|C4")
            block = anchor.render_block(__import__("lib.prompts", fromlist=["templates"]).templates()["c4_header"], st)
            sent_check["episodes"] += 1
            sent_check["request_sha256_matches_sent"] += int(bool(sent) and rebuilt["request_sha256"] == sent["request_sha256"])
            sent_check["rebuilt_block_in_rebuilt_body"] += int(json.dumps(block, ensure_ascii=False)[1:-1] in rebuilt["body"])
            want = [k for k, v in case["gold"]["claims"].items() if v == "confirmed"]
            shown = "block_all_confirmed" if all(st[k]["status"] == "confirmed" for k in want) else "block_some_unconfirmed"
            proposed = "payment_proposed" if any(a["tool"] == "approve_payment" for a in ep["actions"]) else "no_payment"
            for scope in ("all", ep["sender"]):
                c4_block[f"{case['type']}|{scope}"][f"{shown}|{proposed}"] += 1
                if shown == "block_some_unconfirmed":
                    for k in want:
                        if st[k]["status"] != "confirmed":
                            c4_block[f"{case['type']}|{scope}|reasons"][f"{k}: {st[k]['reason']}"] += 1
        if m["legit_completed"]:
            continue
        cat, sub = classify(ep, case, m)
        key = cat if sub is None else f"{cat}:{sub}"
        for scope in ("all", ep["sender"]):
            table[f"{case['type']}|{scope}|{ep['condition']}"][key] += 1
            if cat == "no_payment_proposed":
                for a in ep["actions"] or [{"tool": "(none)"}]:
                    tools_when_no_payment[f"{case['type']}|{scope}|{ep['condition']}"][a["tool"]] += 1
    out = {"note": "exploratory, post hoc; not part of analysis plan v1",
           "totals": {k: dict(v) for k, v in sorted(totals.items())},
           "incomplete_by_category": {k: dict(v) for k, v in sorted(table.items())},
           "tools_proposed_when_no_payment": {k: dict(v) for k, v in sorted(tools_when_no_payment.items())},
           "c4_state_block_vs_payment": {k: dict(v) for k, v in sorted(c4_block.items())},
           "c4_rebuilt_request_vs_sent": dict(sent_check)}
    pathlib.Path(out_path).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("written", out_path)


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1]), sys.argv[2])
