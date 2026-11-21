"""再集計（recount.json）と凍結済み集計の出力（report3.json）をセルごとに突き合わせる。
  python3 -B -I compare.py recount.json report3.json compare.json
"""
import json, sys
new, old = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
CONDS = ["C3", "GC", "GCP", "C4", "C4P"]; S = ["qwen3", "gemma3", "all"]
rows = []
def add(group, metric, cd, s, o, n, den):
    rows.append(dict(group=group, metric=metric, cond=cd, sender=s, old=o, new=n, diff=(None if o is None or n is None else n - o), denom=den))
def grid(group, oblock, nblock, keymap):
    for nk, ok in keymap.items():
        for cd in CONDS:
            for s in S:
                o = oblock[ok][cd][s]; n = nblock[nk][cd][s]
                add(group, nk, cd, s, o["n"], n["n"], n["planned"])
                if o["planned"] != n["planned"]: add(group, nk + ":planned", cd, s, o["planned"], n["planned"], None)
grid("LT6", old["lt6"], new["lt6"], {k: k for k in ("primary", "primary_no_denied", "primary_ledger", "primary_complete")})
for k in ("primary", "primary_no_denied", "primary_ledger", "primary_complete"):
    for pair, d in new["lt6_paired"][k].items():
        for s in ("qwen3", "gemma3"):
            for c in ("both", "only_first", "only_second", "neither"):
                add("LT6 paired " + pair, k + ":" + c, pair, s, old["lt6_paired"][k][pair][s][c], d[s][c], d[s]["planned"])
for key, d in new["lt6_breakdown"].items():
    o = old["lt6_breakdown"][key]
    for b in sorted(set(d) | set(o)):
        add("LT6 breakdown", b, key.split("|")[0], key.split("|")[1], o.get(b, 0), d.get(b, 0), 90)
for key, d in new["lt6_denied_tools_in_joint"].items():
    o = old["lt6_denied_tools_in_joint_episodes"].get(key, {})
    for b in sorted(set(d) | set(o)):
        add("LT6 denied tools in joint", b, key.split("|")[0], key.split("|")[1], o.get(b, 0), d.get(b, 0), None)
pm = {"correct_proposal": "correct_proposal", "correct_with_extra": "correct_with_extra", "gate_allowed": "gate_allowed", "allowed_and_applied": "allowed_and_applied",
      "ledger_verified": "ledger_verified", "action_ok": "legit_action_ok", "completed": "legit_completed"}
for ty in ("LT1", "LT4", "LT5", "LT6"):
    grid("payment " + ty, old["payment_by_type"][ty], new["payment_by_type"][ty], pm)
for k, ok in (("correct_proposal", "correct_proposal"), ("ledger_verified", "ledger_verified"), ("action_ok", "legit_action_ok"), ("completed", "legit_completed")):
    for cd in CONDS:
        for s in S:
            o, n = old["payment_type_equal_weight"][ok][cd][s], new["payment_type_equal_weight"][k][cd][s]
            rows.append(dict(group="payment type-equal-weight rate", metric=k, cond=cd, sender=s, old=round(o, 6), new=round(n, 6), diff=round(n - o, 6), denom="4類型の率の平均"))
for k, ok in (("correct_proposal", "correct_proposal"), ("action_ok", "legit_action_ok")):
    for pair, d in new["payment_paired"][k].items():
        for s in ("qwen3", "gemma3"):
            for c in ("both", "only_first", "only_second", "neither"):
                add("payment paired " + pair, k + ":" + c, pair, s, old["payment_paired"][ok][pair][s][c], d[s][c], d[s]["planned"])
grid("LT4 early", old["lt4_early"], new["lt4_early"], {"early_proposal": "early_proposal", "early_applied": "early_applied"})
for ty in ("lt2", "lt3"):
    grid(ty.upper(), old[ty], new[ty], {"action_ok": "legit_action_ok", "completed": "legit_completed", "payment_proposed": "payment_proposed", "payment_rows": "payment_rows", "any_denied": "denied_any"})
grid("attack", old["attack"], new["attack"], {"mis_propagation": "mis_propagation", "not_sent": "not_sent", "mis_or_not_sent": "mis_propagation_if_not_sent_counted", "attempt": "attempt_v2",
     "attempt_unobserved": "attempt_unobserved", "denied_read_only": "denied_read_only", "unauthorized_state_change": "unauthorized_state_change", "external_executed": "external_executed",
     "receiver_false_approval": "receiver_false_approval", "receiver_decided": "receiver_decided"})
am = {"mis_propagation": "mis_propagation", "not_sent": "not_sent", "mis_or_not_sent": "mis_propagation_if_not_sent_counted", "attempt": "attempt_v2", "receiver_false_approval": "receiver_false_approval"}
for k, ok in am.items():
    for pair, d in new["attack_paired"][k].items():
        for s in ("qwen3", "gemma3"):
            for c in ("both", "only_first", "only_second", "neither"):
                add("attack paired " + pair, k + ":" + c, pair, s, old["attack_paired"][ok][pair][s][c], d[s][c], d[s]["planned"])
for cd in CONDS:
    for s in ("qwen3", "gemma3"):
        add("attack denied actions total", "denied_actions", cd, s, old["attack_denied_actions_total"][cd][s], new["attack_denied_actions_total"][cd][s], None)
for st, d in new["attack_by_stratum"].items():
    for cd in CONDS:
        add("attack mis-propagation by stratum", st, cd, "all", old["attack_mis_propagation_by_stratum"][st][cd]["n"], d[cd]["all"]["n"], d[cd]["all"]["planned"])
grid("health", old["health"], new["health"], {k: k for k in ("not_run", "retry_exhausted", "truncated", "schema_error")})
bad = [r for r in rows if r["diff"] not in (0, 0.0)]
by = {}
for r in rows:
    g = by.setdefault(r["group"].split(" paired")[0] if False else r["group"], [0, 0]); g[0] += 1; g[1] += r["diff"] not in (0, 0.0)
json.dump(dict(cells=len(rows), differing=len(bad), by_group={k: dict(cells=v[0], differing=v[1]) for k, v in by.items()}, differences=bad, rows=rows), open(sys.argv[3], "w"), ensure_ascii=False, indent=1)
print("cells", len(rows), "differing", len(bad))
for r in bad[:40]: print(r)
