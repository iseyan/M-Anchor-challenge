"""Render the messages each call sends. Nothing from `world` or `gold` is ever rendered."""
from __future__ import annotations

import json

from .common import read_text

KIND_LABEL = {
    "request": "申請システムの記録", "ledger": "承認台帳の記録", "authority": "権限台帳の記録",
    "email": "メール", "pdf": "添付PDF", "wiki": "社内Wiki", "agent": "エージェント連絡", "memory": "長期記憶",
}


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


def stage2_instruction(cfg: dict, condition: str, anchor_block: str | None) -> str:
    base = read_text("prompts/stage2_base.txt").strip()
    block = cfg["stage2_block"][condition]
    if block is None:
        return base
    if block == "anchor":
        if anchor_block is None:
            raise ValueError("C4 needs the M-Anchor block")
        return anchor_block + "\n\n" + base
    return read_text(block).strip() + "\n\n" + base


def stage2_messages(stage1: list[dict], stage1_content: str, instruction: str) -> list[dict]:
    """V1 branch shape: [system, stage1_user, assistant(stage-1 raw content), user(stage-2 instruction)]."""
    return stage1 + [{"role": "assistant", "content": stage1_content}, {"role": "user", "content": instruction}]


def request_summary(case: dict) -> str:
    r = case["world"]["requests"][0]
    return f"{r['request_id']}／金額 {r['amount']:,}円／支払先 {r['payee']}／用途 {case['purpose']}"


def render_handoff(handoff: dict) -> str:
    claims = handoff.get("claims") if isinstance(handoff, dict) else None
    lines = []
    if isinstance(claims, dict):
        for k, v in claims.items():
            lines.append(f"- {k}: {v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}")
    note = handoff.get("note") if isinstance(handoff, dict) else None
    if isinstance(note, str) and note.strip():
        lines.append(f"メモ: {note.strip()}")
    return "\n".join(lines) if lines else "（内容なし）"


def receiver_messages(case: dict, handoff: dict) -> list[dict]:
    user = (read_text("prompts/receiver_user_template.txt")
            .replace("{request_summary}", request_summary(case))
            .replace("{handoff}", render_handoff(handoff))
            .replace("{claims_block}", claims_block(case))).strip()
    return [{"role": "system", "content": read_text("prompts/receiver_system.txt").strip()},
            {"role": "user", "content": user}]
