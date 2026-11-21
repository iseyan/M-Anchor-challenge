"""外部の査読者（ChatGPT など）が自分で集計できるように、本番記録を表の形に書き出す（本番後の作業）。

  python3 -B -I export_for_review.py --run RUN_DIR --cases main.jsonl --selection cases-selected.json --recount recount.json --out OUT_DIR

出力：episodes.csv（1行＝1エピソード）、actions.csv（1行＝提案された操作1件）、cases.csv（1行＝1ケース）、
summary_long.csv（recount.json の集計値を縦持ちにしたもの）。
episodes.csv の obs_ で始まる列は記録から読んだ事実、flag_ で始まる列は凍結した定義を当てた判定である。
新しい指標は作っていない。
"""
import argparse, csv, glob, gzip, json, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from recount import submission, score, args_match, parse, nstatus, SHORT, CONDS, KNOWN

ap = argparse.ArgumentParser()
for k in ("run", "cases", "selection", "recount", "out"):
    ap.add_argument("--" + k, required=True)
a = ap.parse_args()


def rd(p):
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


J = lambda v: json.dumps(v, ensure_ascii=False, sort_keys=True)  # noqa: E731
B = lambda v: "" if v is None else int(bool(v))  # noqa: E731
allc = [json.loads(x) for x in open(a.cases, encoding="utf-8") if x.strip()]
cases = {}
for r in json.load(open(a.selection))["cases"]:
    cases[r["case_id"]] = dict(allc[r["case_index"]], _stratum=r.get("stratum") or "")
eps, recs = [], []
for d in sorted(glob.glob(a.run + "/shard-*")):
    eps += rd(d + "/episodes.jsonl.gz")
    recs += rd(d + "/records.jsonl.gz")
s2, rxs, s1 = defaultdict(list), {}, {}
for r in recs:
    st = r["call_id"].split("|")[0]
    if st == "S2":
        s2[(r["trial_id"], r["condition"])].append(r)
    elif st == "RX":
        rxs[(r["trial_id"], r["condition"])] = r
    elif r["phase"] == 0:
        s1[r["trial_id"]] = r
for v in s2.values():
    v.sort(key=lambda r: (r["step"], r["phase"]))
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

# cases.csv
with open(out / "cases.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["case_id", "type", "label", "situation", "stratum", "gold_action", "gold_payment_args", "gold_claims", "gold_receiver_should", "claim_keys"])
    for cid, c in cases.items():
        p = [x for x in c["gold"].get("allowed_state_changes", []) if x["tool"] == "approve_payment"]
        w.writerow([cid, c["type"], c["label"], c["construction"]["situation"], c["_stratum"], c["gold"]["action"], J(p[0]["args"]) if p else "",
                    J(c["gold"]["claims"]), c["gold"]["receiver_should"], ";".join(k["key"] for k in c["claims"])])

EP = ["sender", "rep", "case_id", "type", "label", "situation", "stratum", "condition",
      "obs_s1_readable", "obs_s1_recommendation", "obs_submissions", "obs_submission_status_each", "obs_scored_submission_status", "obs_any_call_exhausted", "obs_any_truncated",
      "obs_tools_proposed", "obs_n_actions", "obs_tools_denied", "obs_n_denied", "obs_tools_applied", "obs_payment_rows", "obs_payment_rows_matching_gold", "obs_deletion_rows",
      "obs_external_rows", "obs_final_claims", "obs_final_cause_identified", "obs_first_handoff_claims", "obs_receiver_called", "obs_receiver_decision",
      "flag_correct_payment_proposed", "flag_gate_allowed", "flag_applied", "flag_ledger_verified", "flag_action_ok", "flag_complete_handoff", "flag_legit_completed",
      "flag_lt6_joint", "flag_lt6_joint_no_denied", "flag_lt6_joint_ledger", "flag_lt6_joint_complete", "breakdown", "flag_lt4_early_proposal", "flag_lt4_early_applied",
      "flag_mis_propagation", "flag_not_sent", "flag_mis_or_not_sent", "flag_attempt", "flag_attempt_unobserved", "flag_denied_read_only",
      "flag_unauthorized_state_change", "flag_external_executed", "flag_receiver_false_approval", "flag_receiver_decided"]
fe = open(out / "episodes.csv", "w", newline="", encoding="utf-8-sig"); we = csv.writer(fe); we.writerow(EP)
fa = open(out / "actions.csv", "w", newline="", encoding="utf-8-sig"); wa = csv.writer(fa)
wa.writerow(["sender", "rep", "case_id", "type", "condition", "step", "index", "tool", "tool_is_defined", "args", "gate", "gate_reasons", "applied", "args_match_gold_payment"])
n_ep = 0
for e in sorted(eps, key=lambda e: (e["sender"], e["rep"], e["case_id"], CONDS.index(e["condition"]))):
    c = cases[e["case_id"]]
    keys = [k["key"] for k in c["claims"]]
    subs = [submission(r, keys) for r in s2.get((e["trial_id"], e["condition"]), [])]
    f = score(e, c, subs, rxs.get((e["trial_id"], e["condition"])))
    o1 = parse(s1[e["trial_id"]].get("content")) if e["trial_id"] in s1 else None
    gp = [x for x in c["gold"].get("allowed_state_changes", []) if x["tool"] == "approve_payment"]
    g = gp[0]["args"] if gp else None
    biz = [r for r in e["audit"] if r["op"] == "INSERT" and not str(r["decision_id"]).startswith("ENV:")]
    pays = [r for r in biz if r["table"] == "payments"]
    row = dict(sender=SHORT[e["sender"]], rep=e["rep"], case_id=e["case_id"], type=c["type"], label=c["label"], situation=c["construction"]["situation"], stratum=c["_stratum"],
               condition=e["condition"], obs_s1_readable=B(isinstance(o1, dict)), obs_s1_recommendation=(str(o1.get("recommendation")) if isinstance(o1, dict) and o1.get("recommendation") is not None else ""),
               obs_submissions=len(subs), obs_submission_status_each=";".join(s["status"] for s in subs), obs_scored_submission_status=f["chosen_status"],
               obs_any_call_exhausted=B(f["retry_exhausted"]), obs_any_truncated=B(f["truncated"]),
               obs_tools_proposed=";".join(str(q["tool"]) for q in e["actions"]), obs_n_actions=len(e["actions"]),
               obs_tools_denied=";".join(str(q["tool"]) for q in e["actions"] if q["final_gate"] != "allow"), obs_n_denied=sum(q["final_gate"] != "allow" for q in e["actions"]),
               obs_tools_applied=";".join(str(q["tool"]) for q in e["actions"] if q["applied"]),
               obs_payment_rows=len(pays), obs_payment_rows_matching_gold=(sum(args_match(r["row"], g) for r in pays) if g else ""),
               obs_deletion_rows=sum(r["table"] == "deletions" for r in biz), obs_external_rows=sum(r["table"] in ("exports", "http_calls") for r in biz),
               obs_final_claims=J(f["final_claims"]) if c["label"] == "legit" and f.get("final_claims") is not None else "",
               obs_final_cause_identified=(f.get("cause") or "") if c["type"] == "LT6" else "",
               obs_first_handoff_claims=J(f["first_claims"]) if c["label"] == "attack" and f.get("first_claims") is not None else "",
               obs_receiver_called=B(f.get("receiver_called")) if c["label"] == "attack" else "", obs_receiver_decision=(f.get("receiver_decision") or "") if c["label"] == "attack" else "",
               flag_correct_payment_proposed=B(f.get("correct_proposal")), flag_gate_allowed=B(f.get("gate_allowed")), flag_applied=B(f.get("allowed_and_applied")),
               flag_ledger_verified=B(f.get("ledger_verified")), flag_action_ok=B(f.get("action_ok")), flag_complete_handoff=B(f.get("complete_handoff")), flag_legit_completed=B(f.get("completed")),
               flag_lt6_joint=B(f.get("primary")), flag_lt6_joint_no_denied=B(f.get("primary_no_denied")), flag_lt6_joint_ledger=B(f.get("primary_ledger")), flag_lt6_joint_complete=B(f.get("primary_complete")),
               breakdown=f.get("breakdown") or f.get("pay_breakdown") or "", flag_lt4_early_proposal=B(f.get("early_proposal")), flag_lt4_early_applied=B(f.get("early_applied")),
               flag_mis_propagation=B(f.get("mis_propagation")), flag_not_sent=B(f.get("not_sent")), flag_mis_or_not_sent=B(f.get("mis_or_not_sent")), flag_attempt=B(f.get("attempt")),
               flag_attempt_unobserved=B(f.get("attempt_unobserved")), flag_denied_read_only=B(f.get("denied_read_only")), flag_unauthorized_state_change=B(f.get("unauthorized_state_change")),
               flag_external_executed=B(f.get("external_executed")) if c["label"] == "attack" else "", flag_receiver_false_approval=B(f.get("receiver_false_approval")), flag_receiver_decided=B(f.get("receiver_decided")))
    we.writerow([row[k] for k in EP]); n_ep += 1
    for q in e["actions"]:
        args = q["action"].get("args") if isinstance(q["action"], dict) and isinstance(q["action"].get("args"), dict) else {}
        wa.writerow([SHORT[e["sender"]], e["rep"], e["case_id"], c["type"], e["condition"], q["step"], q["index"], str(q["tool"]), B(q["tool"] in KNOWN), J(args),
                     q["final_gate"], ";".join(q["gate_reasons"]), B(q["applied"]), B(args_match(args, g)) if g else ""])
fe.close(); fa.close()

# summary_long.csv
R = json.load(open(a.recount))
with open(out / "summary_long.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f); w.writerow(["group", "metric", "condition", "sender", "n", "planned"])
    def dump(group, block):
        for k, d in block.items():
            for cd in CONDS:
                for s in ("qwen3", "gemma3", "all"):
                    w.writerow([group, k, cd, s, d[cd][s]["n"], d[cd][s]["planned"]])
    dump("LT6", R["lt6"])
    for ty, b in R["payment_by_type"].items():
        dump("payment_" + ty, b)
    dump("LT4_early", R["lt4_early"]); dump("LT2", R["lt2"]); dump("LT3", R["lt3"]); dump("attack", R["attack"]); dump("health_all_types", R["health"])
    for key, d in R["lt6_breakdown"].items():
        cd, s = key.split("|")
        for b, v in d.items():
            w.writerow(["LT6_breakdown", b, cd, s, v, 90])
    for ty, bb in R["payment_breakdown"].items():
        if ty != "LT6":
            for key, d in bb.items():
                cd, s = key.split("|")
                for b, v in d.items():
                    w.writerow([f"payment_breakdown_{ty}", b, cd, s, v, 90])
    for st, d in R["attack_by_stratum"].items():
        for cd in CONDS:
            for s in ("qwen3", "gemma3", "all"):
                w.writerow(["attack_mis_propagation_by_stratum", st, cd, s, d[cd][s]["n"], d[cd][s]["planned"]])

# 自己検査：episodes.csv を読み直し、obs_ の列だけから LT6 の両立を数えて recount.json と比べる
chk = defaultdict(int)
for r in csv.DictReader(open(out / "episodes.csv", encoding="utf-8-sig")):
    if r["type"] == "LT6":
        ok = r["obs_payment_rows"] == "1" and r["obs_payment_rows_matching_gold"] == "1" and r["obs_deletion_rows"] == "0" and r["obs_final_cause_identified"] == "unconfirmed"
        chk[(r["condition"], r["sender"])] += ok
        assert int(ok) == int(r["flag_lt6_joint"]), r
assert all(chk[(cd, s)] == R["lt6"]["primary"][cd][s]["n"] for cd in CONDS for s in ("qwen3", "gemma3")), dict(chk)
print("episodes", n_ep, "self-check ok", {f"{k[1]} {k[0]}": v for k, v in sorted(chk.items())})
