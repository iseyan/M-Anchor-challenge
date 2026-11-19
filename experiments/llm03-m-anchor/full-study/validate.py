"""Offline structural/oracle checks. Never calls a model or changes a case file."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import argparse
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import ROOT, World, cases, common, config, gate, prompts, read, sha, sources, write_new
from engine import late_case

EXPECTED_PROMPTS = {
    "stage2_c4_header.txt": "74d96fb5638ad34f1f72f9fd767b57c89489996a3ba4c4fdba3c273d169037a2",
    "stage2_base.txt": "eaefe802531c08c1d64a63e573ab4dc59ad0aaf31fed5169df97bed22c78b091",
}


def verify_cases(rows, split):
    counts = Counter(c["type"] for c in rows)
    want = 30 if split == "main" else 2
    assert len(counts) == 16 and set(counts.values()) == {want}, counts
    ids = [c["case_id"] for c in rows]
    assert len(ids) == len(set(ids))
    checks = []
    for c in rows:
        assert c["split"] == split
        docs = {d["id"]: d for d in c["documents"]}
        assert len(docs) == len(c["documents"])
        assert {x["key"] for x in c["claims"]} == set(c["gold"]["claims"])
        assert set(c["gold"]["claims"].values()) <= set(common.STATUSES)
        # Only public, task-relevant projections may be rendered, never hidden gold/world events.
        rendered = json.dumps(prompts.stage1_messages(c), ensure_ascii=False)
        assert "allowed_state_changes" not in rendered and "gold_initial" not in rendered
        assert "claim_basis" not in rendered and "before_first_execution" not in rendered
        effective = late_case(c) if c["type"] == "LT4" else deepcopy(c)
        for e in c["events"]:
            if e["kind"] in ("revoke", "consume"):
                effective["world"]["approvals"][0]["revoked" if e["kind"] == "revoke" else "used"] = True
        w = World(effective["world"])
        try:
            r = effective["world"]["requests"][0]
            apr = effective["world"]["approvals"]
            expected = "allow" if c["gold"]["action"] == "execute" else "deny"
            decisions = [gate.decide(w, dict(tool="approve_payment", args=dict(request_id=r["request_id"], amount=r["amount"], payee=r["payee"], approval_id=aid)))["decision"]
                         for aid in ([a["approval_id"] for a in apr] or ["missing"])]
            actual = "allow" if "allow" in decisions else "deny"
            assert actual == expected, (c["case_id"], decisions, expected)
            for allowed in c["gold"]["allowed_state_changes"]:
                assert gate.decide(w, allowed)["decision"] == "allow", c["case_id"]
            checks.append(dict(case_id=c["case_id"], payment_decision=actual))
        finally:
            w.close()
    return dict(cases=len(rows), by_type=dict(counts), oracle_checks=len(checks),
                presentation_counts=dict(Counter(c["construction"]["presentation"] for c in rows)),
                frames=len({(c["type"], c["construction"]["situation"]) for c in rows}))


def verify():
    from support import LEGACY
    cfg = config()
    for name, expected in EXPECTED_PROMPTS.items():
        assert sha(LEGACY / "prompts" / name) == expected, name
    main, dev = cases("main"), cases("dev")
    old = common.load_cases("main") + common.load_cases("dev")
    def entities(cs):
        values = set()
        for c in cs:
            values |= {r["request_id"] for r in c["world"]["requests"]}
            values |= {r["payee"] for r in c["world"]["requests"]}
            values |= set(c["world"]["approvers"])
            values |= {a["approval_id"] for a in c["world"]["approvals"]}
        return values
    assert not (entities(main) & entities(dev))
    assert not ((entities(main) | entities(dev)) & entities(old))
    main_frames = {(c["type"], str(c["construction"]["situation"])) for c in main}
    dev_frames = {(c["type"], str(c["construction"]["situation"])) for c in dev}
    assert not (main_frames & dev_frames), "development situations overlap main"
    for c in dev:
        assert c.get("scenario_contract", {}).get("scope") == "development_only_compound_fixture"
        assert len(c["world"]["requests"]) > 1 or len(c["world"]["approvals"]) > 1 or any(
            "旧原本" in d["text"] for d in c["documents"]), c["case_id"]
    for c in main + dev:
        for name, snap in c["gold_snapshots"].items():
            for key, basis in snap["gold"]["claim_basis"].items():
                if basis["expected"] == "confirmed":
                    assert basis["evidence_ids"], (c["case_id"], name, key)
    assert len(cfg["conditions"]) == len(set(cfg["conditions"])) == 9
    ntrials = len(main) * len(cfg["senders"]) * cfg["repetitions"]
    initial_calls = ntrials + 30 * len(cfg["senders"]) * cfg["repetitions"]  # LT4 late update
    max_s2 = ntrials * 9 + 30 * 6 * 9 * 2 + 30 * 6 * 9
    return dict(status="offline_structure_passed_not_frozen", config_status=cfg["protocol_status"],
                main=verify_cases(main, "main"), dev=verify_cases(dev, "dev"),
                model_names=[s["name"] for s in cfg["senders"]], conditions=cfg["conditions"],
                trials=ntrials, episodes=ntrials * 9, episodes_attack=300 * 6 * 9,
                episodes_legit=180 * 6 * 9, shared_initial_calls=initial_calls,
                max_submission_calls=max_s2, max_receiver_calls=ntrials * 9,
                max_logical_calls=initial_calls+max_s2+ntrials*9,
                note="Structural/oracle checks do not establish scenario independence, language quality or empirical model behavior.",
                sources=sources())


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    result = verify()
    if args.output:
        write_new(args.output, result)
    print(json.dumps({k: v for k, v in result.items() if k != "sources"}, ensure_ascii=False, indent=2))
