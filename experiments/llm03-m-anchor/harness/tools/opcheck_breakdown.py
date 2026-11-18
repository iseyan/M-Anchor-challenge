"""Operation-check breakdown by sender and condition (stage 2), read from a run's saved records.

usage: python -I tools/opcheck_breakdown.py <run_dir> [<run_dir> ...]

Counts come from the raw stage-2 outputs (every proposed action, not only the first three the gate
processes) and from episodes.jsonl for not-sent. Format errors and server-side call failures are kept
apart. "verification-purpose candidates" is a keyword match on http_post URL/body and call_agent
messages; it is a screening count for the operation check, not a scoring definition, and it is never
subtracted from the attempt rate. "attempt_only_read_record" counts episodes whose only denied
operations are read_record calls (e.g. an approval or document ID passed as request_id); it is
likewise a screening count and leaves the attempt definition unchanged.
"""
from __future__ import annotations

import collections
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from lib import score  # noqa: E402
from lib.common import STATUSES, load_cases, norm_status, parse_json  # noqa: E402

URL_VERIFY = re.compile(r"valid|verif|check|approv|gate|auth", re.I)          # http_post URL
MSG_VERIFY = re.compile(r"照合|検証|validate|verify|確認してください|確認を依頼|確認をお願い", re.I)   # call_agent message


def breakdown(run_dir: pathlib.Path) -> dict:
    m = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    cases = {c["case_id"]: c for c in load_cases(m["split"])}
    recs = json.loads((run_dir / "records.json").read_text(encoding="utf-8"))
    eps = {e["episode_id"]: e for e in map(json.loads, (run_dir / "episodes.jsonl").read_text(encoding="utf-8").splitlines())}
    tab = collections.defaultdict(collections.Counter)
    for r in recs:
        if not r["call_id"].startswith("S2|"):
            continue
        _, sender, rep, case_id, cond = r["call_id"].split("|")
        case = cases[case_id]
        c = tab[(sender, cond)]
        c["n"] += 1
        ep = eps[f"{sender}|{rep}|{case_id}|{cond}"]
        mt = score.episode_metrics(ep, case)
        c["not_sent"] += mt["not_sent"]
        c["transmission_failure"] += mt["transmission_failure"]
        if r.get("call_status") == "NOT_RUN":
            c["not_run"] += 1
            continue
        if r.get("call_status") != "OK" or r.get("content") is None:
            c["call_failure"] += 1
            c["timeout"] += r.get("call_status") == "TIMEOUT"
            continue
        c["truncated"] += r.get("done_reason") == "length"
        obj, _ = parse_json(r["content"])
        if not isinstance(obj, dict):
            c["invalid_json"] += 1
            continue
        acts = obj.get("actions") if isinstance(obj.get("actions"), list) else []
        h = obj.get("handoff")
        claims = h.get("claims") if isinstance(h, dict) else None
        if not isinstance(obj.get("actions"), list) or not isinstance(claims, dict):
            c["schema_error"] += 1
            if not isinstance(h, dict):
                c["handoff_missing"] += 1
            elif not isinstance(claims, dict):
                c["claims_nesting_missing"] += 1
        elif any(norm_status(claims.get(k["key"])) not in STATUSES for k in case["claims"]):
            c["incomplete"] += 1
        tools = [a.get("tool") for a in acts if isinstance(a, dict)]
        http = [a for a in acts if isinstance(a, dict) and a.get("tool") == "http_post"]
        agent = [a for a in acts if isinstance(a, dict) and a.get("tool") == "call_agent"]
        instructed = any("http_post" in d["text"] for d in case["documents"])
        if http:
            c["http_post_episodes"] += 1
            c["http_post_episodes_instructed_case" if instructed else "http_post_episodes_other_case"] += 1
        if agent:
            c["call_agent_episodes"] += 1
            if not isinstance(claims, dict):
                c["call_agent_without_handoff"] += 1
        url_hit = any(isinstance(a.get("args"), dict) and URL_VERIFY.search(str(a["args"].get("url", ""))) for a in http)
        msg_hit = any(isinstance(a.get("args"), dict) and MSG_VERIFY.search(str(a["args"].get("message", ""))) for a in agent)
        if not instructed:
            c["verification_candidate_http_post"] += bool(url_hit)
            c["verification_candidate_call_agent"] += bool(msg_hit)
        c["other_denied_episodes"] += any(a["gate"] == "deny" and a["tool"] != "http_post" for a in ep["actions"])
        denied = {a["tool"] for a in ep["actions"] if a["gate"] == "deny"}
        c["attempt_episodes"] += bool(denied)
        c["attempt_only_read_record"] += denied == {"read_record"}
        c["actions_over_limit"] += len(tools) > 3
    keys = ["n", "call_failure", "timeout", "not_run", "invalid_json", "schema_error", "handoff_missing",
            "claims_nesting_missing", "incomplete", "transmission_failure", "not_sent", "truncated",
            "http_post_episodes", "http_post_episodes_instructed_case", "http_post_episodes_other_case",
            "call_agent_episodes", "call_agent_without_handoff", "verification_candidate_http_post",
            "verification_candidate_call_agent",
            "other_denied_episodes", "attempt_episodes", "attempt_only_read_record", "actions_over_limit"]
    rows = {f"{s}|{cond}": {k: tab[(s, cond)][k] for k in keys} for (s, cond) in sorted(tab)}
    rx = [r for r in recs if r["call_id"].startswith("RX|")]
    return {"run_id": m["run_id"], "stage2": rows, "receiver_calls": len(rx),
            "receiver_ok": sum(1 for r in rx if r.get("call_status") == "OK")}


if __name__ == "__main__":
    out = [breakdown(pathlib.Path(p)) for p in sys.argv[1:]]
    print(json.dumps(out if len(out) > 1 else out[0], ensure_ascii=False, indent=1))
