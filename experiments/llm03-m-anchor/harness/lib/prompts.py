"""Render the messages each call sends. Nothing from `world` or `gold` is ever rendered.

transport/browser_loop.js re-implements stage2_instruction, render_handoff and receiver_messages; the
ingest step rebuilds every request body in Python and compares hashes, so the two must agree exactly.
Placeholders are replaced with str.replace (all occurrences); the JS side uses split/join for the same.
"""
from __future__ import annotations

from .common import read_text

KIND_LABEL = {
    "request": "申請システムの記録", "ledger": "承認台帳の記録", "authority": "権限台帳の記録",
    "email": "メール", "pdf": "添付PDF", "wiki": "社内Wiki", "agent": "エージェント連絡", "memory": "長期記憶",
}
MISSING = "（記載なし）"
INVALID = "（不正な値）"


def documents_block(case: dict) -> str:
    return "\n".join(f"{d['id']}〔{KIND_LABEL[d['kind']]}〕: {d['text']}" for d in case["documents"])


def claims_block(case: dict) -> str:
    return "\n".join(f"- {c['key']}: {c['statement']}" for c in case["claims"])


def stage1_messages(case: dict) -> list[dict]:
    user = (read_text("prompts/stage1_user_template.txt")
            .replace("{task}", case["task"])
            .replace("{documents}", documents_block(case))
            .replace("{claims_block}", claims_block(case))).strip()
    return [{"role": "system", "content": read_text("prompts/stage1_system.txt").strip()},
            {"role": "user", "content": user}]


def templates() -> dict:
    """The fixed texts the browser loop needs, already stripped exactly as the Python side uses them."""
    return {"stage2_base": read_text("prompts/stage2_base.txt").strip(),
            "gc_block": read_text("prompts/stage2_gc.txt").strip(),
            "c4_header": read_text("prompts/stage2_c4_header.txt").strip(),
            "receiver_system": read_text("prompts/receiver_system.txt").strip(),
            "receiver_user_template": read_text("prompts/receiver_user_template.txt"),
            "kind_label": KIND_LABEL, "missing": MISSING, "invalid": INVALID}


def stage2_base(case: dict) -> str:
    """The shared stage-2 text with this case's claim keys filled in (identical in all four conditions)."""
    keys = [c["key"] for c in case["claims"]]
    example = "{" + ", ".join(f'"{k}": "状態"' for k in keys) + "}"
    return (templates()["stage2_base"].replace("{claim_keys}", "、".join(keys))
            .replace("{claims_example}", example))


def stage2_instruction(cfg: dict, condition: str, anchor_block: str | None, case: dict) -> str:
    t = templates()
    base = stage2_base(case)
    block = cfg["stage2_block"][condition]
    if block is None:
        return base
    if block == "anchor":
        if anchor_block is None:
            raise ValueError("C4 needs the M-Anchor block")
        return anchor_block + "\n\n" + base
    return t["gc_block"] + "\n\n" + base


def stage2_messages(stage1: list[dict], stage1_content: str, instruction: str) -> list[dict]:
    """V1 branch shape: [system, stage1_user, assistant(stage-1 raw content), user(stage-2 instruction)]."""
    return stage1 + [{"role": "assistant", "content": stage1_content}, {"role": "user", "content": instruction}]


def request_summary(case: dict) -> str:
    r = case["world"]["requests"][0]
    return f"{r['request_id']}／金額 {r['amount']:,}円／支払先 {r['payee']}／用途 {case['purpose']}"


def render_handoff(case: dict, claims: dict) -> str:
    """Only the scored claim keys, in case order. This is exactly what the receiver sees and what is scored."""
    lines = []
    for c in case["claims"]:
        v = claims.get(c["key"], None) if isinstance(claims, dict) else None
        if c["key"] not in claims:
            shown = MISSING
        elif isinstance(v, str):
            shown = v
        else:
            shown = INVALID
        lines.append(f"- {c['key']}: {shown}")
    return "\n".join(lines)


def receiver_messages(case: dict, claims: dict) -> list[dict]:
    t = templates()
    user = (t["receiver_user_template"]
            .replace("{request_summary}", request_summary(case))
            .replace("{handoff}", render_handoff(case, claims))
            .replace("{claims_block}", claims_block(case))).strip()
    return [{"role": "system", "content": t["receiver_system"]}, {"role": "user", "content": user}]
