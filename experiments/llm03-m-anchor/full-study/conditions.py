"""Nine conditions. Existing C4 wording and the legacy strict handoff parser are unchanged."""
from copy import deepcopy
from support import anchor, common, prompts

M_CONDITIONS = {"C2", "C4", "A1", "A2", "A3", "C5"}


def state_for(case, s1_content):
    obj, _ = common.parse_json(s1_content)
    return anchor.build_state(case, obj)


def block(case, condition, state):
    header = prompts.templates()["c4_header"]
    if condition in ("C2", "C4", "C5"):
        return anchor.render_block(header, state)
    if condition == "A3":
        false_state = deepcopy(state)
        for item in false_state.values():
            item["status"] = "confirmed"
            item["reason"] = "確認済みとして保存"
        # A false-confirmation MESSAGE control; not a label-only effect. No invented source IDs.
        return anchor.render_block(header, false_state)
    if condition == "A1":
        # Reason strings can themselves reveal statuses (e.g. 前工程で未決); remove these too.
        lines = [header.strip()]
        for key, s in state.items():
            lines.append(f"- {key}: 根拠参照 " + ("・".join(s["cited"]) or "なし"))
        return "\n".join(lines)
    if condition == "A2":
        # Fixed whitelist. No state-derived padding, IDs, sources, kinds, reasons or timestamps.
        # Length calibrated in checks/dev-plan-v2-20261119.md (candidate v3, checks/dev-a2-probe-20261119-01.json):
        # the claim key and a fixed row number only; re-measured on development cases before it counts as done.
        lines = [header.strip()]
        for i, claim in enumerate(case["claims"]):
            lines.append(f"- {claim['key']}: 整理行 {i+1:02d}")
        return "\n".join(lines)
    return None


def instruction(case, condition, s1_content):
    base = prompts.stage2_base(case)
    if condition in ("C1", "C3"):
        return base
    if condition == "GC":
        return prompts.templates()["gc_block"] + "\n\n" + base
    state = state_for(case, s1_content)
    return block(case, condition, state) + "\n\n" + base


def epistemic_denials(cfg, case, state, action):
    """C5 supplement only. Non-authority facts (e.g. cause_identified) never grant/deny authority."""
    if not isinstance(action, dict):
        return []
    deps = cfg["c5_dependencies"].get(action.get("tool"), [])
    keys = {c["key"] for c in case["claims"]}
    return ["epistemic:" + k for k in deps if k in keys and state[k]["status"] != "confirmed"]
