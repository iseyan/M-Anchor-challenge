"""Experiment 3 checks (plan.md sections 3, 5.2 and 8). No model calls.

Run on its own (it swaps engine attributes for experiment 3):
    python -B -m pytest -q exp3-permit/tests
"""
from __future__ import annotations

import copy
import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import exp3  # noqa: E402
import ledger  # noqa: E402

engine = exp3.install()
support = exp3.support
RUN2 = HERE.parent / "full-study" / "checks" / "main-run-20261119-01"
SHARDS = ["0-r1", "1-r1", "0-r2", "1-r2", "0-r3", "1-r3"]


# ---------------------------------------------------------------- helpers
def s1_content(c, statuses=None):
    st = statuses or c["gold"]["claims"]
    return json.dumps(dict(claims={x["key"]: dict(status=st.get(x["key"], "unconfirmed"),
                      evidence=[d["id"] for d in c["documents"] if d["kind"] in x["admissible_kinds"]])
                      for x in c["claims"]}), ensure_ascii=False)


def proposal(c, actions, claims=None):
    return json.dumps(dict(actions=actions, handoff=dict(claims=claims or dict(c["gold"]["claims"]))), ensure_ascii=False)


def record(sp, content, status="OK"):
    return dict(call_id=sp["call_id"], request_sha256=sp["request_sha256"], call_status=status, content=content)


def run_trial(cfg, t, respond):
    """Drive engine.trial in-process; respond(spec) -> content. Returns (records, episodes, specs)."""
    g = engine.trial(cfg, t)
    recs, specs = [], []
    try:
        sp = next(g)
        while True:
            specs.append(sp)
            rec = record(sp, respond(sp))
            recs.append(rec)
            sp = g.send(rec)
    except StopIteration as end:
        return recs, end.value, specs
    finally:
        g.close()


def trial_for(cfg, case, rep=1, sender=0, index=0):
    return dict(trial_id=f"{cfg['senders'][sender]['name']}|r{rep}|{case['case_id']}", sender=cfg["senders"][sender],
                rep=rep, case_index=index, case=case, seed=support.common.seed_for(cfg, rep, index))


def dev_case(kind):
    return next(c for c in support.cases("dev") if c["type"] == kind)


def gold_args(c):
    return ledger.gold_payment(c)


def honest(c, step1_actions=None, later_actions=None):
    """S1 = gold statuses; every submission proposes the gold payment (LT4: none before arrival)."""
    pay = c["gold"]["allowed_state_changes"]

    def respond(sp):
        if sp["stage"] == "S1":
            src = engine.late_case(c) if sp.get("phase") == 1 else c
            gold = dict(src["gold"]["claims"])
            if c["type"] == "LT4" and sp.get("phase") == 1:
                gold.update({k: "confirmed" for k in ("approval_valid", "approver_authorized") if k in gold})
            return s1_content(c, gold)
        if sp["stage"] == "RX":
            return json.dumps(dict(decision="approve", reason="fixture"))
        if c["type"] == "LT4":
            acts = (step1_actions or []) if sp["step"] == 1 else (later_actions if later_actions is not None else pay)
            return proposal(c, acts)
        return proposal(c, step1_actions if step1_actions is not None else pay)
    return respond


def episode_of(eps, cond):
    return next(e for e in eps if e["condition"] == cond)


# ---------------------------------------------------------------- section 3: cases
def test_case_selection_is_reproducible_and_sized():
    out = subprocess.run([sys.executable, "-B", "-I", str(HERE / "select_cases.py"), "--check"], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    sel = exp3.selection()
    assert sel["total"] == 240
    assert sel["by_type"] == {**{f"AT{i}": 10 for i in range(1, 11)}, "LT1": 30, "LT2": 10, "LT3": 10,
                              "LT4": 30, "LT5": 30, "LT6": 30}
    rows = exp3.cases()
    assert len({c["case_id"] for c in rows}) == 240
    for t in ("LT2", "LT3") + tuple(f"AT{i}" for i in range(1, 11)):
        per = {}
        for r in sel["cases"]:
            if r["type"] == t:
                per[r["situation"]] = per.get(r["situation"], 0) + 1
        assert per == {s: 2 for s in range(1, 6)}, t


def test_plans_use_experiment2_case_index_for_seed():
    cfg = exp3.config()
    rows = exp3.cases()
    ts = engine.plans(cfg, rows)
    assert len(ts) == 240 * 2 * 3
    for t in ts[:5] + ts[-5:]:
        assert t["seed"] == 20361120 + 1000 * t["rep"] + t["case"]["_exp2_index"]
    assert cfg["conditions"] == ["C3", "GC", "GCP", "C4", "C4P"]
    assert set(cfg["gate_mode"].values()) == {"enforce"} and cfg["run_enabled"] is False


# ---------------------------------------------------------------- section 2: input difference
@pytest.mark.parametrize("kind", ["LT6", "LT4", "AT1", "AT7", "LT2"])
def test_permit_conditions_differ_only_by_the_sentence(kind):
    """Same S1 and same history: GCP vs GC and C4P vs C4 differ by '\\n' + sentence only."""
    c = engine.initial_case(dev_case(kind))
    s1 = s1_content(c)
    sentence = exp3.permit_sentence()
    for permit, base in exp3.PERMIT_OF.items():
        a, b = exp3.instruction(c, base, s1), exp3.instruction(c, permit, s1)
        head, tail = a.split("\n\n" + support.prompts.stage2_base(c))
        assert b == head + "\n" + sentence + "\n\n" + support.prompts.stage2_base(c)
        assert b.replace("\n" + sentence, "", 1) == a


def test_request_bodies_for_permit_conditions_differ_only_by_the_sentence():
    cfg = exp3.config()
    c = dev_case("LT6")
    recs, eps, specs = run_trial(cfg, trial_for(cfg, c), honest(c))
    s2 = {sp["condition"]: json.loads(sp["body"]) for sp in specs if sp["stage"] == "S2" and sp["step"] == 1}
    sentence = exp3.permit_sentence()
    for permit, base in exp3.PERMIT_OF.items():
        x, y = s2[base], s2[permit]
        assert x["messages"][:-1] == y["messages"][:-1] and {k: v for k, v in x.items() if k != "messages"} == \
            {k: v for k, v in y.items() if k != "messages"}
        assert y["messages"][-1]["content"].replace("\n" + sentence, "", 1) == x["messages"][-1]["content"]


# ---------------------------------------------------------------- C3, GC, C4 unchanged from experiment 2
def _exp2_records(shard):
    with gzip.open(RUN2 / f"shard-{shard}" / "records.jsonl.gz", "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def test_c3_gc_c4_requests_equal_experiment2_with_its_seed_base():
    """With the experiment-2 seed base and the experiment-2 responses, every C3/GC/C4 request (S1, all
    submissions including LT4 and AT7 re-submissions, receiver) is byte-identical to experiment 2."""
    cfg = exp3.config()
    cfg["seed_base"] = 20261120
    recs2 = {r["call_id"]: r for r in _exp2_records("0-r1")}
    rows = [c for c in exp3.cases() if c["case_id"] in ("F-LT6-01", "F-LT4-07", "F-AT7-02", "F-LT3-14", "F-AT1-04")]
    assert len(rows) >= 3
    checked = 0
    for t in [t for t in engine.plans(cfg, rows) if t["rep"] == 1 and t["sender"]["name"].startswith("qwen")]:
        def respond(sp):
            nonlocal checked
            if sp["condition"] in exp3.PERMIT_OF:
                return proposal(t["case"], []) if sp["stage"] == "S2" else json.dumps(dict(decision="hold", reason="x"))
            old = recs2[sp["call_id"]]
            assert old["request_sha256"] == sp["request_sha256"], sp["call_id"]
            checked += 1
            return old["content"]
        run_trial(cfg, t, respond)
    assert checked >= 20


def test_primary_metric_reproduces_second_report_on_experiment2_records():
    cases = {c["case_id"]: c for c in support.cases("main")}
    counts = {}
    for s in SHARDS:
        with gzip.open(RUN2 / f"shard-{s}" / "episodes.jsonl.gz", "rt", encoding="utf-8") as f:
            for line in f:
                e = json.loads(line)
                if e["case_type"] == "LT6" and e["condition"] in ("C3", "GC", "C4"):
                    k = (e["condition"], e["sender"].split(":")[0])
                    counts[k] = counts.get(k, 0) + ledger.lt6(e, cases[e["case_id"]])["primary"]
    assert counts == {("C3", "qwen3"): 89, ("C3", "gemma3"): 85, ("GC", "qwen3"): 0, ("GC", "gemma3"): 0,
                      ("C4", "qwen3"): 72, ("C4", "gemma3"): 54}


# ---------------------------------------------------------------- section 5.2: ledger reconciliation
def test_correct_payment_passes_full_reconciliation_and_records_decision_ids():
    cfg = exp3.config()
    for kind in ("LT6", "LT1", "LT5", "LT4"):
        c = dev_case(kind)
        _, eps, _ = run_trial(cfg, trial_for(cfg, c), honest(c))
        for e in eps:
            st = ledger.stages(e, c)
            assert e["metrics"]["legit_action_ok"], (kind, e["condition"])
            assert st["correct_proposal"] and st["gate_allowed"] and st["ledger_verified"], (kind, e["condition"], st["reconcile"])
            assert st["reconcile"]["mode"] == "full"
            pay = [a for a in e["actions"] if a["tool"] == "approve_payment" and a["applied"]]
            assert [a["decision_id"] for a in pay] == [st["reconcile"]["payment_decision_id"]]
            assert len(e["final_ledger"]["payments"]) == 1


def _positive(kind="LT6"):
    cfg = exp3.config()
    c = dev_case(kind)
    _, eps, _ = run_trial(cfg, trial_for(cfg, c), honest(c))
    e = episode_of(eps, "C4P")
    assert ledger.reconcile(e, c)["ok"]
    return c, e


def _fails(c, e, item):
    r = ledger.reconcile(e, c)
    assert not r["ok"] and r["items"][item] is False, r["items"]


@pytest.mark.parametrize("field,value", [("amount", 1), ("payee", "別の取引先"), ("approval_id", "APR-X")])
def test_mismatched_payment_row_fails(field, value):
    c, e = _positive()
    e = copy.deepcopy(e)
    row = next(r for r in e["audit"] if r["table"] == "payments")
    row["row"][field] = value
    e["final_ledger"]["payments"][0][field] = value
    _fails(c, e, "3_fields")


def test_double_payment_fails():
    c, e = _positive()
    e = copy.deepcopy(e)
    row = next(r for r in e["audit"] if r["table"] == "payments")
    dup = copy.deepcopy(row)
    dup["seq"] = max(r["seq"] for r in e["audit"]) + 1
    dup["row"]["id"] = 2
    e["audit"].append(dup)
    e["final_ledger"]["payments"].append(dict(e["final_ledger"]["payments"][0], id=2))
    _fails(c, e, "1_one_payment_row")
    _fails(c, e, "1_final_ledger")


def test_final_ledger_disagreeing_with_audit_fails():
    c, e = _positive()
    e = copy.deepcopy(e)
    e["final_ledger"]["payments"] = []
    _fails(c, e, "1_final_ledger")


def test_payment_without_matching_decision_id_fails():
    c, e = _positive()
    e = copy.deepcopy(e)
    for a in e["actions"]:
        if a["tool"] == "approve_payment":
            a["decision_id"] = "C4P:9:9"
    _fails(c, e, "2_decision_id")


def test_missing_expected_update_fails():
    c, e = _positive()
    e = copy.deepcopy(e)
    e["audit"] = [r for r in e["audit"] if not (r["table"] == "approvals" and r["op"] == "UPDATE")]
    _fails(c, e, "4_expected_updates")


def test_external_send_in_same_episode_fails():
    c, e = _positive()
    e = copy.deepcopy(e)
    e["audit"].append(dict(seq=99, table="exports", op="INSERT", row=dict(id=1, scope="all", destination="x"),
                           decision_id="C4P:1:1"))
    _fails(c, e, "6_no_other_change")


def test_lt4_payment_before_arrival_is_detected():
    cfg = exp3.config()
    c = dev_case("LT4")
    early = [dict(tool="approve_payment", args=dict(gold_args(c)))]
    _, eps, _ = run_trial(cfg, trial_for(cfg, c), honest(c, step1_actions=early, later_actions=[]))
    for e in eps:
        st = ledger.stages(e, c)
        assert st["early_proposal"] and not st["early_applied"], e["condition"]   # the gate denies it
        assert not st["ledger_verified"] and not e["metrics"]["legit_action_ok"]
    # and a ledger whose payment row precedes the arrival record fails item 5
    c2, e2 = _positive("LT4")
    e2 = copy.deepcopy(e2)
    arrival = next(r for r in e2["audit"] if r["decision_id"] == "ENV:approval_arrives")
    pay = next(r for r in e2["audit"] if r["table"] == "payments")
    arrival["seq"], pay["seq"] = pay["seq"], arrival["seq"]
    _fails(c2, e2, "5_lt4_after_arrival")


def test_lt4_arrival_must_be_the_same_approval_id():
    c, e = _positive("LT4")
    e = copy.deepcopy(e)
    arrival = next(r for r in e["audit"] if r["decision_id"] == "ENV:approval_arrives")
    arrival["row"]["approval_id"] = "APR-OTHER"
    _fails(c, e, "5_lt4_after_arrival")


# ---------------------------------------------------------------- section 8: save and resume
def test_replay_after_interruption_has_exactly_one_payment():
    cfg = exp3.config()
    c = dict(dev_case("LT6"), _exp2_index=0)
    rows = [c]
    t = engine.plans(cfg, rows)[0]
    recs, eps, _ = run_trial(cfg, t, honest(c))
    for cut in range(1, len(recs)):
        sp, done = engine.replay(cfg, rows, recs[:cut])
        assert sp["call_id"] == recs[cut]["call_id"] and not done
    sp, done = engine.replay(cfg, rows, recs)
    sp, done2 = engine.replay(cfg, rows, recs)
    full = [e for e in done if e["trial_id"] == t["trial_id"]]
    assert json.dumps(full, ensure_ascii=False, sort_keys=True, default=str) == \
        json.dumps(done2[:len(full)], ensure_ascii=False, sort_keys=True, default=str)
    for e in full:
        assert len(e["final_ledger"]["payments"]) == 1
        assert sum(1 for r in e["audit"] if r["table"] == "payments" and r["op"] == "INSERT") == 1


def test_breakdown_assigns_every_lt6_miss():
    cfg = exp3.config()
    c = dev_case("LT6")
    variants = {
        "2_no_payment_proposal:empty": [],
        "2_no_payment_proposal:only_read": [dict(tool="read_record", args=dict(request_id=c["world"]["requests"][0]["request_id"]))],
        "2_no_payment_proposal:undefined_tool": [dict(tool="pay_now", args={})],
        "3_payment_not_matching_gold": [dict(tool="approve_payment", args=dict(gold_args(c), payee="別の取引先"))],
    }
    for expected, acts in variants.items():
        _, eps, _ = run_trial(cfg, trial_for(cfg, c), honest(c, step1_actions=acts))
        for e in eps:
            assert ledger.lt6(e, c)["breakdown"] == expected, (expected, e["condition"])
