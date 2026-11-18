"""Step 1 of the analysis (analysis-plan-v1.md): check the input hashes, recompute every episode's metrics with
the frozen scorer (harness/lib/score.py, v0.2.3), and write one row per episode to episodes.csv.

usage: python -I analysis/prep.py <run_dir> <out_dir> [--no-hash-check]

--no-hash-check is accepted only for the dev split (trying the scripts on the dev operation-check records).
The main split is always checked against the SHA-256 values fixed in section 2 of the plan; with
--no-hash-check on main the script refuses to run.
"""
from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
HARNESS = HERE.parent / "harness"
sys.path.insert(0, str(HARNESS))

from lib import score  # noqa: E402
from lib.common import load_cases  # noqa: E402

PLAN_HASHES = {  # analysis-plan-v1.md, section 2
    "episodes.jsonl": "ef8fd5c74031716b6fabe0f659e182debf43b32ed86d1451c0c9b2acc09c4c31",
    "summary.json": "a9cb48ada48cdf69e073a1ffc25572f8800dc2dd7f5a650c72e04ebfa84ce7e0",
    "records.json": "757298e5d91a41f43e2e0e317cd99bc59ebf5da2708e287ea1310110027e6758",
}
METRICS = ["attempt", "attempt_only_read_record", "forbidden_requests", "unauthorized_state_change",
           "external_attempt", "external_executed", "mis_propagation", "not_sent",
           "mis_propagation_if_not_sent_counted", "transmission_failure", "handoff_incomplete", "not_run",
           "stage2_call_failure", "stage2_schema_error", "false_confirmations", "expected_unconfirmed_claims",
           "receiver_false_approval", "bypass_writes", "audit_inconsistent",
           "legit_completed", "legit_action_ok", "legit_epistemic_ok", "legit_gate_denied_correct_payment"]


def sha256(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(run_dir: pathlib.Path, out_dir: pathlib.Path, check: bool) -> None:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    hashes = {f: sha256(run_dir / f) for f in PLAN_HASHES}
    if not check and manifest["split"] != "dev":
        sys.exit("refused: --no-hash-check is only for the dev split; the main run is always checked")
    if check:
        bad = {f: h for f, h in hashes.items() if h != PLAN_HASHES[f]}
        if bad or manifest["split"] != "main":
            sys.exit(f"refused: input does not match the plan: {bad or manifest['split']}")
    cases = {c["case_id"]: c for c in load_cases(manifest["split"])}
    eps = [json.loads(l) for l in (run_dir / "episodes.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    rows, mismatched = [], 0
    for ep in eps:
        case = cases[ep["case_id"]]
        m = score.episode_metrics(ep, case)
        stored = ep.get("metrics")
        if stored is not None:
            keys = set(m) if check else set(m) & set(stored)  # dev records predate v0.2.3's added key
            if any(stored.get(k) != m[k] for k in keys):
                mismatched += 1
        row = {"episode_id": ep["episode_id"], "trial_id": ep["trial_id"], "sender": ep["sender"],
               "rep": ep["rep"], "case_id": ep["case_id"], "case_type": case["type"], "label": case["label"],
               "condition": ep["condition"], "handoff_status": ep["handoff_status"],
               "stage2_status": ep["stage2_status"]}
        for k in METRICS:
            v = m.get(k)
            row[k] = "" if v is None else int(v)
        rows.append(row)
    if mismatched:
        sys.exit(f"refused: {mismatched} episodes differ from the stored metrics")
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "episodes.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    info = {"run_id": manifest["run_id"], "split": manifest["split"], "episodes": len(rows),
            "input_sha256": hashes, "hash_check": check, "stored_metrics_match": True,
            "harness_version_scorer": json.loads((HARNESS / "config.json").read_text(encoding="utf-8"))["harness_version"]}
    (out_dir / "prep_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(pathlib.Path(args[0]), pathlib.Path(args[1]), "--no-hash-check" not in sys.argv)
