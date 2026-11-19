"""Behavioral regressions for new condition and dynamic paths; no empirical model claims."""
from pathlib import Path
import hashlib
import importlib.util
import json
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support import LEGACY, ROOT, World, anchor, cases, common, config, gate, pipeline, prompts, score
from conditions import block, epistemic_denials, instruction, state_for
from engine import Branch, initial_case, late_case, plans, replay, trial


def case(kind):
    return next(c for c in cases("dev") if c["type"] == kind)


def s1(c, statuses=None):
    return json.dumps(dict(claims={x["key"]: dict(status=(statuses or c["gold"]["claims"])[x["key"]],
                     evidence=[d["id"] for d in c["documents"] if d["kind"] in x["admissible_kinds"]]) for x in c["claims"]}), ensure_ascii=False)


def record(sp, content):
    return dict(call_id=sp["call_id"], request_sha256=sp["request_sha256"], call_status="OK", content=content)


def proposal(c, actions=None, claims=None):
    return json.dumps(dict(actions=actions if actions is not None else c["gold"]["allowed_state_changes"],
                           handoff=dict(claims=claims or c["gold"]["claims"])), ensure_ascii=False)


def play(c, responses):
    cfg = config()
    t = plans(cfg, [c])[0]
    g = trial(cfg, t)
    rs = []
    try:
        sp = next(g)
        while True:
            text = responses(sp, c)
            rec = record(sp, text)
            rs.append(rec)
            sp = g.send(rec)
    except StopIteration as end:
        return rs, end.value
    finally:
        g.close()


class FullStudyTests(unittest.TestCase):
    def test_dimensions_and_disjoint_ids(self):
        from validate import verify
        r = verify()
        self.assertEqual(r["episodes"], 25920)
        self.assertEqual(r["max_logical_calls"], 59760)
        self.assertEqual(r["main"]["frames"], 80)

    def test_current_c4_exact_legacy_instruction(self):
        cfg = common.load_config()
        for c in cases("dev"):
            content = s1(c)
            ab = anchor.render_block(prompts.templates()["c4_header"], anchor.build_state(c, common.parse_json(content)[0]))
            self.assertEqual(instruction(c, "C4", content), prompts.stage2_instruction(cfg, "C4", ab, c))

    def test_c2_and_c5_same_input_as_c4(self):
        c = case("AT1")
        self.assertEqual(instruction(c, "C2", s1(c)), instruction(c, "C4", s1(c)))
        self.assertEqual(instruction(c, "C5", s1(c)), instruction(c, "C4", s1(c)))

    def test_c1_c3_same_input(self):
        c = case("AT1")
        self.assertEqual(instruction(c, "C1", s1(c)), instruction(c, "C3", s1(c)))

    def test_a1_removes_epistemic_rows_not_handoff_schema(self):
        c = case("AT1")
        b = block(c, "A1", state_for(c, s1(c)))
        rows = b.splitlines()[1:]
        self.assertTrue(rows)
        for row in rows:
            self.assertNotIn("confirmed", row)
            self.assertNotIn("未決", row)
            self.assertNotIn("ではない", row)
        self.assertIn('"handoff"', instruction(c, "A1", s1(c)))

    def test_a2_does_not_encode_state_or_state_dependent_length(self):
        c = case("AT1")
        st = state_for(c, s1(c))
        b = block(c, "A2", st)
        mutated = json.loads(json.dumps(c))
        mutated["gold"]["claims"] = {k: "confirmed" for k in mutated["gold"]["claims"]}
        self.assertEqual(b, block(mutated, "A2", st))
        other = json.loads(json.dumps(st))
        for x in other.values():
            x.update(status="confirmed", reason="秘密の根拠種類", cited=["D999"], admitted=["D999"])
        self.assertEqual(b, block(c, "A2", other))
        self.assertNotIn("D999", b)

    def test_a3_does_not_retain_contradictory_reasons(self):
        c = case("AT1")
        st = state_for(c, s1(c))
        rows = block(c, "A3", st).splitlines()[1:]
        self.assertTrue(all(": confirmed（" in x for x in rows))
        self.assertTrue(all("確認済みとして保存" in x for x in rows))
        self.assertNotIn("ではない", "\n".join(rows))
        self.assertNotIn("未決", "\n".join(rows))

    def test_strict_format_no_normalization(self):
        c = case("AT1")
        bad = dict(call_status="OK", content=json.dumps({"actions": [], "handoff": {"approval_valid": "confirmed"}}))
        self.assertIsNone(pipeline.handoff_claims(bad))
        bad["content"] = json.dumps({"actions": [], "handoff": {"claims": {"approval_valid": {"status": "confirmed"}}}})
        ep = pipeline.gate_step(common.load_config(), c, "C4", bad)
        self.assertEqual(ep["handoff_status"], "SENT_INCOMPLETE")
        self.assertTrue(score.episode_metrics(ep, c)["not_sent"])

    def test_c5_ignores_unrelated_cause(self):
        c = case("LT6")
        st = state_for(c, s1(c))
        self.assertEqual(epistemic_denials(config(), c, st, c["gold"]["allowed_state_changes"][0]), [])
        st["approval_valid"]["status"] = "unconfirmed"
        self.assertEqual(epistemic_denials(config(), c, st, c["gold"]["allowed_state_changes"][0]), ["epistemic:approval_valid"])

    def test_revocation_is_an_event_not_initial_flag(self):
        c = case("AT8")
        self.assertFalse(c["world"]["approvals"][0]["revoked"])
        a = c["world"]["approvals"][0]
        pay = dict(tool="approve_payment", args={k: a[k] for k in ("request_id", "amount", "payee", "approval_id")})
        b = Branch(config(), c, "C4")
        try:
            b.submit(dict(call_status="OK", content=proposal(c, [pay])), c, s1(c), 1)
            self.assertEqual(b.events[0]["pre_event_checks"][0]["decision"], "allow")
            self.assertIn("not_revoked", b.actions[0]["gate_reasons"])
            self.assertFalse(b.actions[0]["applied"])
        finally:
            b.world.close()

    def test_replay_consumes_real_prior_token(self):
        c = case("AT9")
        a = c["world"]["approvals"][0]
        self.assertFalse(a["used"])
        pay = dict(tool="approve_payment", args={k: a[k] for k in ("request_id", "amount", "payee", "approval_id")})
        b = Branch(config(), c, "C4")
        try:
            b.submit(dict(call_status="OK", content=proposal(c, [pay])), c, s1(c), 1)
            self.assertIn("not_used", b.actions[0]["gate_reasons"])
            self.assertEqual(b.world.db.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 1)
            self.assertFalse(b.episode(plans(config(), [c])[0], None)["metrics"]["unauthorized_state_change"])
        finally:
            b.world.close()

    def test_late_approval_requires_update_before_payment(self):
        c = case("LT4")
        def respond(sp, c):
            cc = late_case(c) if sp["phase"] else initial_case(c)
            if sp["stage"] == "S1": return s1(cc)
            if sp["stage"] == "RX": return '{"decision":"hold"}'
            if sp["phase"]: return proposal(cc)
            rid = cc["world"]["requests"][0]["request_id"]
            return proposal(cc, [dict(tool="request_approval", args=dict(request_id=rid))])
        rs, eps = play(c, respond)
        self.assertEqual(sum(r["call_id"].startswith("S1|") for r in rs), 2)
        self.assertTrue(all(e["metrics"]["legit_completed"] for e in eps))
        self.assertTrue(all(e["submission_steps"] == 2 for e in eps))
        ids = [r["call_id"] for r in rs]
        self.assertLess(max(i for i, x in enumerate(ids) if x.startswith("RX|")), next(i for i,x in enumerate(ids) if x.startswith("S1|") and "|p1|" in x))
        for ep in eps:
            self.assertEqual(ep["first_handoff"]["gold"]["receiver_should"], "not_approve")
            self.assertEqual(ep["first_handoff"]["claims"]["approval_valid"], "unconfirmed")
            self.assertEqual(ep["final_handoff"]["claims"]["approval_valid"], "confirmed")
            if ep["condition"] == "C5":
                states = [x for x in ep["state_trace"] if x["point"] == "at_execution"]
                self.assertEqual([x["state"]["approval_valid"]["status"] for x in states], ["unconfirmed", "confirmed"])

    def test_early_receiver_false_approval_not_erased_by_late_approval(self):
        c = case("LT4")
        def respond(sp, c):
            cc = late_case(c) if sp["phase"] else initial_case(c)
            if sp["stage"] == "S1": return s1(cc)
            if sp["stage"] == "RX": return '{"decision":"approve"}'
            return proposal(cc, [] if not sp["phase"] else None)
        _, eps = play(c, respond)
        self.assertTrue(all(e["metrics"]["receiver_false_approval"] for e in eps))
        from summarize import summarize
        self.assertTrue(all(x["means"]["receiver_false_approval"] == 1. for x in summarize(eps)))

    def test_c5_independent_attempt_and_correct_payment_veto(self):
        c = case("LT1")
        states = dict(c["gold"]["claims"], approval_valid="unconfirmed")
        b = Branch(config(), c, "C5")
        try:
            b.submit(dict(call_status="OK", content=proposal(c)), c, s1(c, states), 1)
            m = b.episode(plans(config(), [c])[0], None)["metrics"]
            self.assertFalse(m["attempt"])
            self.assertFalse(m["attempt_only_read_record"])
            self.assertEqual(m["epistemic_gate_denials"], 1)
            self.assertEqual(m["correct_payment_epistemically_denied"], 1)
        finally:
            b.world.close()

    def test_c5_invalidates_revoked_and_consumed_state(self):
        for kind, key in (("AT8", "approval_still_valid"), ("AT9", "approval_unused")):
            c = case(kind)
            b = Branch(config(), c, "C5")
            try:
                b.submit(dict(call_status="OK", content=proposal(c, [])), c, s1(c, {k:"confirmed" for k in c["gold"]["claims"]}), 1)
                self.assertEqual(b.state_trace[-1]["state"][key]["status"], "refuted")
            finally:
                b.world.close()

    def test_dev_contracts_are_compound_and_not_main_situations(self):
        main_frames = {(c["type"], str(c["construction"]["situation"])) for c in cases("main")}
        for c in cases("dev"):
            self.assertNotIn((c["type"],str(c["construction"]["situation"])), main_frames)
            self.assertEqual(c["scenario_contract"]["scope"], "development_only_compound_fixture")
            self.assertTrue(len(c["world"]["requests"]) > 1 or any("旧原本" in d["text"] for d in c["documents"]))
            w=World(c["world"])
            try:
                for req in c["world"]["requests"][1:]:
                    control=next(a for a in c["world"]["approvals"] if a["request_id"] == req["request_id"])
                    pay=dict(tool="approve_payment",args={k:control[k] for k in ("request_id","amount","payee","approval_id")})
                    self.assertEqual(gate.decide(w,pay)["decision"],"allow",c["case_id"])
            finally:
                w.close()

    def test_late_gold_evidence_is_phase_specific(self):
        for c in [x for x in cases("main")+cases("dev") if x["type"] == "LT4"]:
            self.assertEqual(c["gold_snapshots"]["initial"]["gold"]["claims"]["approval_valid"], "unconfirmed")
            self.assertIn("N1", c["gold_snapshots"]["final"]["gold"]["claim_basis"]["approval_valid"]["evidence_ids"])

    def test_late_payment_cannot_erase_early_unauthorized_payment(self):
        c = case("LT4")
        b = Branch(config(), c, "C1")
        try:
            b.submit(dict(call_status="OK", content=proposal(c)), initial_case(c), s1(initial_case(c)), 1)
            b.approval_arrives()
            b.submit(dict(call_status="OK", content=proposal(c)), late_case(c), s1(late_case(c)), 2)
            ep = b.episode(plans(config(), [c])[0], None)
            self.assertTrue(ep["metrics"]["unauthorized_state_change"])
            self.assertFalse(ep["metrics"]["legit_completed"])
        finally:
            b.world.close()

    def test_retry_is_conditional_and_bounded(self):
        c = case("AT7")
        r = c["world"]["requests"][0]
        pay = dict(tool="approve_payment", args=dict(r, approval_id="absent"))
        def respond(sp, c):
            if sp["stage"] == "S1": return s1(c)
            if sp["stage"] == "RX": return '{"decision":"hold"}'
            return proposal(c, [pay])
        rs, eps = play(c, respond)
        for e in eps:
            self.assertEqual(e["submission_steps"], 1 if e["condition"] in ("C1", "C2") else 3)
            self.assertTrue(e["metrics"]["attempt"])
        _, again = replay(dict(config(), repetitions=1, senders=[config()["senders"][0]]), [c], rs)
        self.assertEqual(eps, again)
        changed = [dict(x) for x in rs]
        changed[1]["request_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "mismatch"):
            replay(dict(config(), repetitions=1, senders=[config()["senders"][0]]), [c], changed)

    def test_first_handoff_cannot_be_repaired_for_primary(self):
        c = case("AT7")
        b = Branch(config(), c, "C4")
        try:
            conf = {k: "confirmed" for k in c["gold"]["claims"]}
            b.submit(dict(call_status="OK", content=proposal(c, [], conf)), c, s1(c), 1)
            b.submit(dict(call_status="OK", content=proposal(c, [])), c, s1(c), 2)
            self.assertTrue(b.episode(plans(config(), [c])[0], None)["metrics"]["mis_propagation"])
        finally:
            b.world.close()

    def test_independent_audit_catches_ungated_write(self):
        c = case("LT1")
        b = Branch(config(), c, "C4")
        try:
            b.world.db.execute("INSERT INTO payments(request_id,amount,payee,approval_id) VALUES ('x',1,'y','z')")
            self.assertTrue(b.world.bypass_writes())
            self.assertFalse(b.episode(plans(config(), [c])[0], None)["audit_consistent"])
        finally:
            b.world.close()

    def test_request_serialization_node_parity(self):
        c = case("LT6")
        rows = []
        for cond in config()["conditions"]:
            for sender in config()["senders"]:
                body = common.body_str(config(), sender, prompts.stage2_messages(prompts.stage1_messages(c), s1(c), instruction(c, cond, s1(c))), 20262120)
                rows.append(body)
        p = subprocess.run(["node", "-e", "let s='';process.stdin.setEncoding('utf8');process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>{for(const x of JSON.parse(s)){if(JSON.stringify(JSON.parse(x))!==x)process.exit(2)};process.stdout.write('ok')});"],
                           input=json.dumps(rows, ensure_ascii=False), text=True, encoding="utf-8", capture_output=True, check=True)
        self.assertEqual(p.stdout, "ok")


if __name__ == "__main__":
    unittest.main()
