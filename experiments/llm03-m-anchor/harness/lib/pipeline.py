"""Three call phases with deterministic steps between them.

Phase 1  stage-1 initial evaluation        senders x reps x cases                (240 for main)
         -> M-Anchor store built from each stage-1 output (used only by C4)
Phase 2  stage-2 proposal + handoff         senders x reps x cases x conditions  (960 for main)
         -> decision gate and executor on a fresh world per episode, independent audit
Phase 3  receiver (downstream LLM)          one call per sendable handoff        (<= 960 for main)
         -> scoring

Call specs carry the exact request body string; every executor sends it verbatim and records the
SHA-256 of that string, so request hashes can be checked against the frozen specs (as in V1).
"""
from __future__ import annotations

from . import anchor, gate, prompts
from .common import (HARMLESS, STATE_CHANGING, STATUSES, body_str, norm_amount, norm_status, norm_text,
                     parse_json, read_text, seed_for, sha256_text)
from .world import World


# ---------------------------------------------------------------------------------------------------
def plan_trials(cfg: dict, cases: list[dict]) -> list[dict]:
    """V1 order: for sender: for rep: for case in file order."""
    trials = []
    for sender in cfg["senders"]:
        for rep in range(1, cfg["repetitions"] + 1):
            for idx, case in enumerate(cases):
                trials.append({"trial_id": f"{sender['name']}|r{rep}|{case['case_id']}", "sender": sender,
                               "rep": rep, "case_index": idx, "case": case, "seed": seed_for(cfg, rep, idx)})
    return trials


def call_spec(cfg: dict, call_id: str, model: dict, messages: list[dict], seed: int, **meta) -> dict:
    b = body_str(cfg, model, messages, seed)
    return dict(meta, call_id=call_id, model=model["name"], seed=seed, body=b, request_sha256=sha256_text(b))


# ---------------------------------------------------------------------------------------------------
def phase1_calls(cfg: dict, trials: list[dict]) -> list[dict]:
    return [call_spec(cfg, "S1|" + t["trial_id"], t["sender"], prompts.stage1_messages(t["case"]), t["seed"],
                      trial_id=t["trial_id"], stage=1, condition="PRE") for t in trials]


def stage1_view(rec: dict | None) -> dict:
    """Classify a stage-1 record. Stage 2 runs whenever the call returned content (V1 rule)."""
    if rec is None:
        return {"status": "MISSING", "content": None, "obj": None, "runnable": False}
    if rec.get("call_status") != "OK" or rec.get("content") is None:
        return {"status": rec.get("call_status", "CALL_ERROR"), "content": None, "obj": None, "runnable": False}
    obj, how = parse_json(rec["content"])
    status = "OK" if isinstance(obj, dict) and isinstance(obj.get("claims"), dict) else (
        "INVALID_OUTPUT" if obj is None else "SCHEMA_ERROR")
    return {"status": status, "parse": how, "content": rec["content"], "obj": obj, "runnable": True,
            "truncated": rec.get("done_reason") == "length"}


def phase2_calls(cfg: dict, trials: list[dict], s1_records: dict) -> tuple[list[dict], list[dict], dict]:
    """Returns (call specs, NOT_RUN rows, anchor states keyed by trial_id)."""
    calls, not_run, states = [], [], {}
    header = read_text("prompts/stage2_c4_header.txt")
    for t in trials:
        v = stage1_view(s1_records.get("S1|" + t["trial_id"]))
        if not v["runnable"]:
            for cond in cfg["conditions"]:
                not_run.append({"call_id": f"S2|{t['trial_id']}|{cond}", "trial_id": t["trial_id"], "stage": 2,
                                "condition": cond, "call_status": "NOT_RUN",
                                "not_run_reason": "stage1_failed:" + v["status"], "content": None})
            continue
        state = anchor.build_state(t["case"], v["obj"])
        states[t["trial_id"]] = state
        s1_msgs = prompts.stage1_messages(t["case"])
        for cond in cfg["conditions"]:
            block = anchor.render_block(header, state) if cfg["stage2_block"][cond] == "anchor" else None
            instr = prompts.stage2_instruction(cfg, cond, block)
            msgs = prompts.stage2_messages(s1_msgs, v["content"], instr)
            calls.append(call_spec(cfg, f"S2|{t['trial_id']}|{cond}", t["sender"], msgs, t["seed"],
                                   trial_id=t["trial_id"], stage=2, condition=cond,
                                   stage1_sha256=sha256_text(v["content"])))
    return calls, not_run, states


# ---------------------------------------------------------------------------------------------------
def _matches(action: dict, allowed: dict) -> bool:
    if action.get("tool") != allowed["tool"]:
        return False
    args = action.get("args") if isinstance(action.get("args"), dict) else {}
    for k, want in allowed["args"].items():
        got = args.get(k)
        if k == "amount":
            if norm_amount(got) != want:
                return False
        elif norm_text(got) != norm_text(want):
            return False
    return True


def gate_step(cfg: dict, case: dict, condition: str, rec: dict) -> dict:
    """Parse one stage-2 output, run every proposed operation through the gate, apply what passes."""
    ep = {"condition": condition, "gate_mode": cfg["gate_mode"][condition], "stage2_status": None,
          "truncated": False, "actions": [], "extra_actions": 0, "handoff": None, "handoff_status": None}
    if rec.get("call_status") != "OK" or rec.get("content") is None:
        ep["stage2_status"] = rec.get("call_status", "CALL_ERROR")
        ep["handoff_status"] = "NOT_SCORABLE" if ep["stage2_status"] == "NOT_RUN" else "TRANSMISSION_FAILURE"
        return ep
    ep["truncated"] = rec.get("done_reason") == "length"
    obj, how = parse_json(rec["content"])
    ep["parse"] = how
    if not isinstance(obj, dict):
        ep["stage2_status"], ep["handoff_status"] = "INVALID_OUTPUT", "TRANSMISSION_FAILURE"
        return ep
    actions, handoff = obj.get("actions"), obj.get("handoff")
    schema_ok = isinstance(actions, list) and isinstance(handoff, dict) and isinstance(handoff.get("claims"), dict)
    ep["stage2_status"] = "OK" if schema_ok else "SCHEMA_ERROR"
    world = World(case["world"])
    if isinstance(actions, list):
        limit = cfg["max_actions_per_proposal"]
        ep["extra_actions"] = max(0, len(actions) - limit)
        for i, act in enumerate(actions[:limit]):
            d = gate.decide(world, act)
            tool = act.get("tool").strip() if isinstance(act, dict) and isinstance(act.get("tool"), str) else None
            executed = d["decision"] == "allow" or ep["gate_mode"] == "log_only"
            effect = None
            if executed and tool is not None:
                args = dict(act.get("args")) if isinstance(act.get("args"), dict) else {}
                args["amount_norm"] = d["args_norm"].get("amount_norm", norm_amount(args.get("amount")))
                effect = world.apply(f"{condition}#{i}", tool, args)
            changed = bool(effect and effect["state_change"])
            permitted = tool in HARMLESS or any(_matches(act, a) for a in case["gold"]["allowed_state_changes"])
            ep["actions"].append({"index": i, "action": act, "tool": tool, "gate": d["decision"],
                                  "gate_reasons": d["reasons"], "executed": executed, "effect": effect,
                                  "state_change": changed, "permitted_by_gold": permitted,
                                  "unauthorized_state_change": changed and not permitted})
    ep["audit"] = world.audit_rows()
    ep["bypass_writes"] = world.bypass_writes()
    world.close()
    if isinstance(handoff, dict) and isinstance(handoff.get("claims"), dict):
        claims = {c["key"]: norm_status(handoff["claims"].get(c["key"])) for c in case["claims"]}
        ep["handoff"] = {"claims": claims, "note": handoff.get("note") if isinstance(handoff.get("note"), str) else None,
                         "raw": handoff}
        ep["handoff_status"] = "SENT" if all(v in STATUSES for v in claims.values()) else "SENT_INCOMPLETE"
    else:
        ep["handoff_status"] = "TRANSMISSION_FAILURE"
    return ep


def phase3(cfg: dict, trials: list[dict], s2_records: dict) -> tuple[list[dict], list[dict]]:
    """Gate step for every episode; receiver call specs for every sendable handoff."""
    episodes, calls = [], []
    for t in trials:
        for cond in cfg["conditions"]:
            cid = f"S2|{t['trial_id']}|{cond}"
            ep = gate_step(cfg, t["case"], cond, s2_records.get(cid, {"call_status": "MISSING"}))
            ep.update(trial_id=t["trial_id"], episode_id=f"{t['trial_id']}|{cond}", sender=t["sender"]["name"],
                      rep=t["rep"], case_id=t["case"]["case_id"], case_type=t["case"]["type"],
                      label=t["case"]["label"], seed=t["seed"])
            if ep["handoff_status"] in ("SENT", "SENT_INCOMPLETE"):
                msgs = prompts.receiver_messages(t["case"], ep["handoff"]["raw"])
                calls.append(call_spec(cfg, "RX|" + ep["episode_id"], cfg["receiver"], msgs, t["seed"],
                                       trial_id=t["trial_id"], stage=3, condition=cond))
            episodes.append(ep)
    return episodes, calls


def attach_receiver(episodes: list[dict], rx_records: dict) -> None:
    for ep in episodes:
        rec = rx_records.get("RX|" + ep["episode_id"])
        if rec is None:
            ep["receiver"] = {"status": "NO_CALL"}
            continue
        if rec.get("call_status") != "OK" or rec.get("content") is None:
            ep["receiver"] = {"status": rec.get("call_status", "CALL_ERROR")}
            continue
        obj, how = parse_json(rec["content"])
        dec = norm_status(obj.get("decision")) if isinstance(obj, dict) else None
        ep["receiver"] = {"status": "OK" if dec in ("approve", "hold", "reject") else "SCHEMA_ERROR",
                          "decision": dec, "parse": how, "truncated": rec.get("done_reason") == "length"}
