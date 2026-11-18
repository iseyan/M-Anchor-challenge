"""M-Anchor epistemic-state store used by condition C4 only.

The store keeps one state per scored claim: confirmed / unconfirmed / refuted, with the evidence
references the model cited. A claim may leave "unconfirmed" only when the model cited at least one
document whose kind is admissible for that claim (for example an approval needs an approval-ledger
record). Otherwise the store keeps it unconfirmed: insufficient evidence stays open (non-closure).

The store never decides whether an operation may run. The decision gate does not read it.
"""
from __future__ import annotations

from .common import STATUSES, norm_status
from .prompts import KIND_LABEL


def build_state(case: dict, stage1_obj) -> dict:
    kinds = {d["id"]: d["kind"] for d in case["documents"]}
    claims_in = stage1_obj.get("claims") if isinstance(stage1_obj, dict) else None
    state = {}
    for claim in case["claims"]:
        key, admissible = claim["key"], claim["admissible_kinds"]
        item = claims_in.get(key) if isinstance(claims_in, dict) else None
        stated = norm_status(item.get("status")) if isinstance(item, dict) else None
        ev = item.get("evidence") if isinstance(item, dict) else None
        cited = [e for e in ev if isinstance(e, str) and e in kinds] if isinstance(ev, list) else []
        admitted = [e for e in cited if kinds[e] in admissible]
        if stated not in STATUSES:
            status, reason = "unconfirmed", "評価なし"
        elif stated == "unconfirmed":
            status, reason = "unconfirmed", "前工程で未決"
        elif admitted:
            status, reason = stated, "根拠 " + "・".join(admitted)
        else:
            status = "unconfirmed"
            need = "・".join(KIND_LABEL[k] for k in admissible) or "確定に使える資料"
            got = "・".join(f"{e}〔{KIND_LABEL[kinds[e]]}〕" for e in cited) or "根拠の指定なし"
            reason = f"{got}は{need}ではない" if admissible else f"{need}がない"
        state[key] = {"status": status, "stated": stated, "cited": cited, "admitted": admitted,
                      "reason": reason}
    return state


def render_block(header: str, state: dict) -> str:
    lines = [header.strip()]
    for key, s in state.items():
        lines.append(f"- {key}: {s['status']}（{s['reason']}）")
    return "\n".join(lines)
