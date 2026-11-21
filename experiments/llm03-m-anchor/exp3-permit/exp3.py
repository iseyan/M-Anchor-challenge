"""Experiment 3 adapter over the frozen experiment-2 engine (full-study at 8a7895b). See plan.md.

Nothing under full-study/ or harness/ is modified. The experiment-2 modules are imported read-only and
`install()` swaps three module attributes of `engine` for this process only:

  engine.instruction -> instruction(): C3, GC, C4 delegate to the frozen conditions.instruction
                        unchanged; GCP and C4P add the frozen H/P permit sentence after the GC / C4 block
                        (block + "\\n" + sentence + "\\n\\n" + base), exactly as followup-hp/fu.py did for P.
  engine.Branch      -> Branch: the frozen Branch plus two records the plan (section 5.2) needs and
                        experiment 2 did not keep: each action's decision ID (the ID the world was called
                        with, captured at the call) and the final ledger rows at episode end.
  engine.plans       -> plans(): trials over the 240 selected cases, seeded with the experiment-2 case
                        index (seed = seed_base + 1000*rep + index in full-study/cases/main.jsonl).

engine.trial and every request, gate, scoring and replay rule stay the frozen code.
"""
from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
FULL = HERE.parent / "full-study"
sys.path.insert(0, str(FULL))

import support  # noqa: E402
import conditions as frozen_conditions  # noqa: E402
import engine  # noqa: E402
from support import common, prompts  # noqa: E402

PERMIT_PATH = HERE.parent / "followup-hp" / "prompts" / "permit_sentence.txt"
PERMIT_SHA256 = "58262bd4f88fa96ff3aba3a1286a70f0cefd321d4dfc14ec82a465616dc3876c"  # file at 03ba1e7
CONDITIONS = ["C3", "GC", "GCP", "C4", "C4P"]
PERMIT_OF = {"GCP": "GC", "C4P": "C4"}
LEDGER_TABLES = ("payments", "approvals", "requests")

_FROZEN_INSTRUCTION = frozen_conditions.instruction
_FROZEN_BRANCH = engine.Branch


def permit_sentence() -> str:
    raw = PERMIT_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != PERMIT_SHA256:
        raise RuntimeError(f"permit sentence hash {digest} != frozen {PERMIT_SHA256}")
    return raw.decode("utf-8").strip()


def instruction(case, condition, s1_content):
    if condition not in PERMIT_OF:
        return _FROZEN_INSTRUCTION(case, condition, s1_content)
    base = prompts.stage2_base(case)
    frozen = _FROZEN_INSTRUCTION(case, PERMIT_OF[condition], s1_content)
    if not frozen.endswith("\n\n" + base):
        raise RuntimeError("frozen instruction no longer ends with the shared submission instruction")
    block = frozen[: -len("\n\n" + base)]
    return block + "\n" + permit_sentence() + "\n\n" + base


class Branch(_FROZEN_BRANCH):
    """Frozen Branch + decision IDs captured at the world call + the final ledger at episode end."""

    def __init__(self, cfg, case, cond):
        super().__init__(cfg, case, cond)
        self.apply_log = []
        world_apply = self.world.apply

        def logged_apply(decision_id, tool, args):
            effect = world_apply(decision_id, tool, args)
            self.apply_log.append(dict(decision_id=decision_id, tool=tool, applied=bool(effect and effect.get("applied"))))
            return effect

        self.world.apply = logged_apply

    def submit(self, rec, case, s1_content, step):
        before_actions, before_log = len(self.actions), len(self.apply_log)
        out = super().submit(rec, case, s1_content, step)
        new_log = iter(self.apply_log[before_log:])
        for pos, a in enumerate(self.actions[before_actions:]):
            # The frozen engine calls world.apply(f"{cond}:{step}:{i}", ...) for executed actions only,
            # in proposal order; pair them in that order and keep the ID the world actually received.
            a["proposal_position"] = pos
            a["decision_id"] = None
            if a["executed"] and a["tool"]:
                entry = next(new_log, None)
                if entry is None or entry["tool"] != a["tool"]:
                    raise RuntimeError("world call log does not match the executed actions")
                a["decision_id"] = entry["decision_id"]
        if next(new_log, None) is not None:
            raise RuntimeError("world was called for an action the engine did not record")
        return out

    def final_ledger(self):
        out = {}
        for table in LEDGER_TABLES:
            cur = self.world.db.execute(f"SELECT * FROM {table} ORDER BY rowid")
            cols = [d[0] for d in cur.description]
            out[table] = [dict(zip(cols, row)) for row in cur.fetchall()]
        return out

    def episode(self, t, rx):
        ep = super().episode(t, rx)
        ep["final_ledger"] = self.final_ledger()
        ep["case_index_exp2"] = t["case_index"]
        return ep


def config():
    cfg = support.config()
    cfg.update(support.read(HERE / "config.json"))
    return cfg


def selection():
    return json.loads((HERE / "cases-selected.json").read_text(encoding="utf-8"))


def cases():
    """The 240 selected cases in experiment-2 file order, each tagged with its experiment-2 index."""
    rows = support.cases("main")
    out = []
    for r in selection()["cases"]:
        c = deepcopy(rows[r["case_index"]])
        if c["case_id"] != r["case_id"]:
            raise RuntimeError(f"case index {r['case_index']} is {c['case_id']}, selection says {r['case_id']}")
        c["_exp2_index"] = r["case_index"]
        out.append(c)
    return out


def plans(cfg, rows):
    trials = []
    for sender in cfg["senders"]:
        for rep in range(1, cfg["repetitions"] + 1):
            for case in rows:
                idx = case["_exp2_index"]
                trials.append({"trial_id": f"{sender['name']}|r{rep}|{case['case_id']}", "sender": sender,
                               "rep": rep, "case_index": idx, "case": case, "seed": common.seed_for(cfg, rep, idx)})
    return trials


def install():
    engine.instruction = instruction
    engine.Branch = Branch
    engine.plans = plans
    return engine
