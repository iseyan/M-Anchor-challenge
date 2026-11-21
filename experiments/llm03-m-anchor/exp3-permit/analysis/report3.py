"""Experiment 3 descriptive report (plan.md sections 5 and 6). Counts, rates and paired rate differences only:
no intervals, tests, equivalence or non-inferiority.

  python -B -I exp3-permit/analysis/report3.py --campaign DIR --split main|dev --out OUT.json [--md OUT.md]

Reads DIR/shard-S-rR/episodes.jsonl(.gz) of every shard. The denominator of every rate is the PLANNED
number of episodes (trials of the split x conditions); episodes of shards that are missing are reported
as missing and stay in the denominator.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import exp3  # noqa: E402
import ledger  # noqa: E402

PAY = ("LT1", "LT4", "LT5", "LT6")
PAIRS = (("C4P", "C4"), ("C4P", "C3"), ("GCP", "GC"), ("C4", "C3"), ("GC", "C3"))
ATTACK = ("mis_propagation", "not_sent", "mis_propagation_if_not_sent_counted", "attempt_v2", "unauthorized_state_change",
          "external_executed", "receiver_false_approval")


def load(campaign):
    eps = []
    for d in sorted(Path(campaign).glob("shard-*")):
        p = d / "episodes.jsonl"
        if p.exists():
            lines = p.read_text(encoding="utf-8").splitlines()
        elif (d / "episodes.jsonl.gz").exists():
            with gzip.open(d / "episodes.jsonl.gz", "rt", encoding="utf-8") as f:
                lines = f.read().splitlines()
        else:
            continue
        eps += [json.loads(x) for x in lines if x]
    return eps


def short(sender):
    return sender.split(":")[0]


def rate(n, d):
    return None if not d else n / d


def flags(e, c):
    """Per-episode booleans used in the tables."""
    m = e["metrics"]
    f = dict(not_run=bool(m.get("not_run")), retry_exhausted=bool(m.get("retry_exhausted")),
             truncated=bool(m.get("truncated")), schema_error=bool(m.get("stage2_schema_error")))
    if c["label"] == "attack":
        for k in ATTACK:
            f[k] = bool(m.get(k))
        f["attempt_unobserved"] = bool(m.get("attempt_unobserved"))
        f["receiver_decided"] = (e.get("receiver") or {}).get("status") == "OK"
        f["denied_read_only"] = bool(m.get("attempt_only_read_record"))
        f["denied_actions"] = len(ledger.denied_actions(e))
    else:
        f["legit_action_ok"] = bool(m.get("legit_action_ok"))
        f["legit_completed"] = bool(m.get("legit_completed"))
        f["payment_proposed"] = any(a["tool"] == "approve_payment" for a in e["actions"])
        f["payment_rows"] = sum(1 for r in e["audit"] if r["table"] == "payments" and r["op"] == "INSERT"
                                and not str(r["decision_id"]).startswith("ENV:"))
        f["denied_any"] = bool(ledger.denied_actions(e))
        if c["type"] in PAY:
            st = ledger.stages(e, c)
            for k in ("correct_proposal", "correct_with_extra", "gate_allowed", "allowed_and_applied", "ledger_verified",
                      "early_proposal", "early_applied"):
                f[k] = st[k]
            f["ledger_disagrees"] = st["ledger_verified"] != f["legit_action_ok"]
        if c["type"] == "LT6":
            r = ledger.lt6(e, c)
            for k in ("primary", "primary_no_denied", "primary_ledger", "primary_complete"):
                f[k] = r[k]
            f["breakdown"] = r["breakdown"]
            f["denied_tools"] = sorted(set(r["denied_actions"]))
    return f


def build(campaign, split):
    cfg = exp3.config()
    rows = exp3.split(split)
    by_case = {c["case_id"]: c for c in rows}
    trials = exp3.plans(cfg, rows)
    conds = cfg["conditions"]
    senders = [short(s["name"]) for s in cfg["senders"]]
    got = {(e["trial_id"], e["condition"]): e for e in load(campaign)}
    F = {}
    missing = Counter()
    for t in trials:
        for cd in conds:
            e = got.get((t["trial_id"], cd))
            if e is None:
                missing[(short(t["sender"]["name"]), cd)] += 1
                continue
            F[(t["trial_id"], cd)] = flags(e, t["case"])
    unknown = sorted(set(got) - {(t["trial_id"], cd) for t in trials for cd in conds})

    def cell(types, key, cd, sender=None, pred=None):
        ts = [t for t in trials if t["case"]["type"] in types and (sender is None or short(t["sender"]["name"]) == sender)]
        n = sum(1 for t in ts if (F.get((t["trial_id"], cd)) or {}).get(key) and (pred is None or pred(F[(t["trial_id"], cd)])))
        return dict(n=n, planned=len(ts), rate=rate(n, len(ts)))

    def table(types, keys):
        return {k: {cd: dict(all=cell(types, k, cd), **{s: cell(types, k, cd, s) for s in senders}) for cd in conds} for k in keys}

    def paired(types, key, a, b):
        out = {}
        for s in senders + ["all"]:
            ts = [t for t in trials if t["case"]["type"] in types and (s == "all" or short(t["sender"]["name"]) == s)]
            c = Counter((bool((F.get((t["trial_id"], a)) or {}).get(key)), bool((F.get((t["trial_id"], b)) or {}).get(key))) for t in ts)
            n = len(ts)
            out[s] = dict(planned=n, both=c[(True, True)], only_first=c[(True, False)], only_second=c[(False, True)],
                          neither=c[(False, False)], rate_first=rate(c[(True, True)] + c[(True, False)], n),
                          rate_second=rate(c[(True, True)] + c[(False, True)], n),
                          diff=None if not n else (c[(True, False)] - c[(False, True)]) / n)
        ds = [out[s]["diff"] for s in senders if out[s]["diff"] is not None]
        out["equal_weight_diff"] = sum(ds) / len(ds) if ds else None
        return out

    rep = dict(plan="exp3-permit/plan.md sections 5-6", campaign=str(campaign), split=split, conditions=conds, senders=senders,
               planned_episodes=len(trials) * len(conds), observed_episodes=len(F),
               missing_by_sender_condition={f"{k[0]}|{k[1]}": v for k, v in sorted(missing.items())},
               episodes_not_in_plan=len(unknown), note="descriptive only; planned denominators; no intervals or tests")
    lt6_keys = ("primary", "primary_no_denied", "primary_ledger", "primary_complete")
    rep["lt6"] = table(("LT6",), lt6_keys)
    rep["lt6_paired"] = {k: {f"{a}-{b}": paired(("LT6",), k, a, b) for a, b in PAIRS} for k in lt6_keys}
    bd, dt = defaultdict(Counter), defaultdict(Counter)
    for t in trials:
        if t["case"]["type"] != "LT6":
            continue
        for cd in conds:
            f = F.get((t["trial_id"], cd))
            s = short(t["sender"]["name"])
            bd[f"{cd}|{s}"][f["breakdown"] if f else "missing_episode"] += 1
            if f and f["primary"]:
                dt[f"{cd}|{s}"].update(f["denied_tools"])
    rep["lt6_breakdown"] = {k: dict(sorted(v.items())) for k, v in sorted(bd.items())}
    rep["lt6_denied_tools_in_joint_episodes"] = {k: dict(sorted(v.items())) for k, v in sorted(dt.items())}
    pay_keys = ("correct_proposal", "correct_with_extra", "gate_allowed", "allowed_and_applied", "ledger_verified",
                "legit_action_ok", "legit_completed", "ledger_disagrees")
    rep["payment_by_type"] = {ty: table((ty,), pay_keys) for ty in PAY}
    rep["payment_type_equal_weight"] = {}
    for k in ("correct_proposal", "ledger_verified", "legit_action_ok", "legit_completed"):
        rep["payment_type_equal_weight"][k] = {}
        for cd in conds:
            rep["payment_type_equal_weight"][k][cd] = {
                s: sum(rep["payment_by_type"][ty][k][cd][s]["rate"] or 0 for ty in PAY) / len(PAY) for s in senders + ["all"]}
    rep["payment_paired"] = {k: {f"{a}-{b}": paired(PAY, k, a, b) for a, b in PAIRS} for k in ("correct_proposal", "legit_action_ok")}
    rep["lt4_early"] = table(("LT4",), ("early_proposal", "early_applied"))
    rep["lt2"] = table(("LT2",), ("legit_action_ok", "legit_completed", "payment_proposed", "payment_rows", "denied_any"))
    rep["lt3"] = table(("LT3",), ("legit_action_ok", "legit_completed", "payment_proposed", "payment_rows", "denied_any"))
    at = tuple(f"AT{i}" for i in range(1, 11))
    rep["attack"] = table(at, ATTACK + ("attempt_unobserved", "receiver_decided", "denied_read_only"))
    rep["attack_paired"] = {k: {f"{a}-{b}": paired(at, k, a, b) for a, b in PAIRS[:3]} for k in ATTACK}
    rep["attack_denied_actions_total"] = {cd: {s: sum((F.get((t["trial_id"], cd)) or {}).get("denied_actions", 0) for t in trials
                                               if t["case"]["type"] in at and short(t["sender"]["name"]) == s) for s in senders}
                                          for cd in conds}
    sel = {r["case_id"]: r.get("stratum") for r in exp3.selection()["cases"]} if split == "main" else {}
    strata = sorted({v for v in sel.values() if v})
    rep["attack_mis_propagation_by_stratum"] = {}
    for st in strata:
        ids = {k for k, v in sel.items() if v == st}
        rep["attack_mis_propagation_by_stratum"][st] = {}
        for cd in conds:
            ts = [t for t in trials if t["case"]["case_id"] in ids]
            n = sum(1 for t in ts if (F.get((t["trial_id"], cd)) or {}).get("mis_propagation"))
            rep["attack_mis_propagation_by_stratum"][st][cd] = dict(n=n, planned=len(ts), rate=rate(n, len(ts)))
    alltypes = tuple(sorted({c["type"] for c in rows}))
    rep["health"] = table(alltypes, ("not_run", "retry_exhausted", "truncated", "schema_error"))
    return rep


def pct(x):
    return "—" if x is None else f"{100 * x:.1f}%"


def markdown(rep):
    conds, senders = rep["conditions"], rep["senders"]
    L = ["# 実験3 記述報告", "", f"分割：{rep['split']}。予定 {rep['planned_episodes']:,} エピソード、観測 {rep['observed_episodes']:,}。"
         "率の分母はすべて予定エピソード数。区間・検定・同等性・非劣性の判定は行わない。", ""]

    def tab(title, block, keys, names):
        L.extend([f"## {title}", "", "| 指標 | 送り手 | " + " | ".join(conds) + " |", "| --- | --- | " + " | ".join("---" for _ in conds) + " |"])
        for k in keys:
            for s in senders + ["all"]:
                L.append(f"| {names.get(k, k)} | {'計' if s == 'all' else s} | " + " | ".join(
                    f"{block[k][cd][s]['n']}/{block[k][cd][s]['planned']} ({pct(block[k][cd][s]['rate'])})" for cd in conds) + " |")
        L.append("")
    tab("LT6（主指標と併記・副次）", rep["lt6"], ("primary", "primary_no_denied", "primary_ledger", "primary_complete"),
        dict(primary="両立（主指標）", primary_no_denied="ゲートに拒否された操作を伴わない両立", primary_ledger="台帳照合で確かめた両立",
             primary_complete="完全な引き継ぎを伴う両立"))
    L.extend(["## LT6 の対応のある比較（同じ試行どうし）", "", "| 指標 | 比較 | 送り手 | 両方 | 前者のみ | 後者のみ | どちらもなし | 率差 |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"])
    for k in ("primary", "primary_no_denied"):
        for pair, d in rep["lt6_paired"][k].items():
            for s in senders:
                x = d[s]
                L.append(f"| {k} | {pair} | {s} | {x['both']} | {x['only_first']} | {x['only_second']} | {x['neither']} | {pct(x['diff'])} |")
            L.append(f"| {k} | {pair} | 2モデル等重み | | | | | {pct(d['equal_weight_diff'])} |")
    L.extend(["", "## LT6 の未達成の内訳", "", "| 条件・送り手 | 内訳 |", "| --- | --- |"])
    for k, v in rep["lt6_breakdown"].items():
        L.append(f"| {k} | " + "、".join(f"{a} {b}" for a, b in v.items()) + " |")
    L.append("")
    for ty, block in rep["payment_by_type"].items():
        tab(f"{ty}（提案・許可・台帳）", block, ("correct_proposal", "gate_allowed", "ledger_verified", "legit_action_ok", "legit_completed", "ledger_disagrees"),
            dict(correct_proposal="正しい支払いの提案", gate_allowed="ゲートで許可", ledger_verified="台帳照合で確かめた支払い",
                 legit_action_ok="正常な支払い完了", legit_completed="正当タスク完了", ledger_disagrees="台帳照合と完了判定の食い違い"))
    tab("LT4 の早すぎる支払い", rep["lt4_early"], ("early_proposal", "early_applied"), dict(early_proposal="到着前の提案", early_applied="到着前の反映"))
    for ty in ("lt2", "lt3"):
        tab(ty.upper(), rep[ty], ("legit_action_ok", "payment_proposed", "payment_rows", "denied_any"),
            dict(legit_action_ok="操作条件", payment_proposed="支払いの提案", payment_rows="支払いの反映", denied_any="拒否された操作あり"))
    tab("攻撃ケース", rep["attack"], ATTACK + ("attempt_unobserved", "receiver_decided", "denied_read_only"),
        dict(mis_propagation="誤伝播", not_sent="未送信", mis_propagation_if_not_sent_counted="誤伝播または未送信", attempt_v2="実行の試行",
             unauthorized_state_change="権限のない状態変更", external_executed="外部への送信の実行", receiver_false_approval="受け手の誤承認",
             attempt_unobserved="試行を観測できない", receiver_decided="受け手が判断した", denied_read_only="拒否は照会のみ"))
    tab("実行の健全性（全類型）", rep["health"], ("not_run", "retry_exhausted", "truncated", "schema_error"),
        dict(not_run="未実行", retry_exhausted="再試行の枯渇", truncated="出力の打ち切り", schema_error="形式誤り"))
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--split", choices=["main", "dev"], required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--md")
    a = ap.parse_args(argv)
    rep = build(a.campaign, a.split)
    Path(a.out).write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if a.md:
        Path(a.md).write_text(markdown(rep), encoding="utf-8")
    print(json.dumps(dict(planned=rep["planned_episodes"], observed=rep["observed_episodes"], missing=sum(rep["missing_by_sender_condition"].values())), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
