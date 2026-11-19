"""Deterministic stand-in responses for runner and analysis tests. No model calls.

respond(spec, case) -> Ollama-style JSON for the request; attempt_status(sha, attempt) -> injected
transport outcome (some first attempts fail, a few calls fail every attempt), so retries, failures,
NOT_RUN branches, missing handoffs and every condition path are exercised.

  python -B -I runner/mock.py reference --split dev --shard 0:1 --out DIR
      run the engine with these responses and the V1 retry rule, in-process (no driver, no transport);
      writes DIR/records.jsonl, DIR/episodes.jsonl and DIR/responses.json (request SHA -> response,
      attempt outcomes), which the Node mock transport replays.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import cases as load_split, config  # noqa: E402
import engine  # noqa: E402
import driver  # noqa: E402


def h(text, salt=""):
    return int(hashlib.sha256((salt + text).encode("utf-8")).hexdigest()[:12], 16)


def attempt_status(sha, attempt):
    v = h(sha, "transport")
    if v % 97 == 0:
        return dict(status="HTTP_ERROR", http=500, error="prediction aborted, token repeat limit reached (mock)", ms=5)
    if attempt == 0 and v % 23 == 0:
        return dict(status="TIMEOUT", error="AbortError (mock)", ms=120000)
    return None


def respond(sp, case):
    v = h(sp["request_sha256"])
    stage = sp["stage"]
    if stage == "S1":
        claims = {}
        for i, c in enumerate(case["claims"]):
            st = case["gold"]["claims"].get(c["key"], "unconfirmed")
            if (v >> i) % 7 == 0:
                st = "confirmed"
            ev = [d["id"] for d in case["documents"] if d["kind"] in c["admissible_kinds"]] or [case["documents"][0]["id"]]
            claims[c["key"]] = dict(status=st, evidence=ev)
        content = json.dumps(dict(claims=claims), ensure_ascii=False)
    elif stage == "S2":
        gold = case["gold"]
        pool = [gold.get("allowed_state_changes", []), [],
                [dict(tool="approve_payment", args=dict(request_id=case["world"]["requests"][0]["request_id"],
                      approval_id="APR-MOCK", amount=case["amount"], payee=case["payee"]))],
                [dict(tool="read_record", args=dict(request_id="D1"))]]
        actions = pool[v % len(pool)]
        claims = {c["key"]: (gold["claims"].get(c["key"], "unconfirmed") if (v >> 5) % 3 else "confirmed")
                  for c in case["claims"]}
        obj = dict(actions=actions)
        if v % 17:
            obj["handoff"] = dict(claims=claims)
        content = json.dumps(obj, ensure_ascii=False)
    else:
        content = json.dumps(dict(decision=["approve", "hold", "reject"][v % 3], reason="mock"))
    return dict(model=sp["model"], message=dict(role="assistant", content=content), done_reason="stop" if v % 41 else "length",
                eval_count=50 + v % 200, prompt_eval_count=800 + v % 900, total_duration=1, load_duration=0)


def reference(split, shard, out, cases_filter=None):
    cfg = config()
    si, rep = (int(x) for x in shard.split(":"))
    rows = load_split(split)
    trials = driver.shard_trials(cfg, rows, si, rep)
    if cases_filter:
        trials = [t for t in trials if t["case"]["case_id"] in cases_filter]
    by_case = {c["case_id"]: c for c in rows}
    records, episodes, responses = [], [], {}
    for t in trials:
        g = engine.trial(cfg, t)
        try:
            sp = next(g)
            while True:
                attempts, result = [], None
                for a in range(cfg["max_retries"] + 1):
                    bad = attempt_status(sp["request_sha256"], a)
                    if bad:
                        attempts.append(dict(bad, attempt=a))
                        continue
                    j = respond(sp, by_case[t["case"]["case_id"]])
                    responses[sp["request_sha256"]] = j
                    attempts.append(dict(attempt=a, status="OK", ms=1))
                    result = driver.result_from_response(j)
                    break
                if result is None:
                    result = dict(call_status=attempts[-1]["status"], content=None)
                rec = dict(call_id=sp["call_id"], trial_id=sp["trial_id"], stage=sp["stage"], condition=sp["condition"],
                           phase=sp.get("phase"), step=sp.get("step"), case_id=sp.get("case_id"), model=sp["model"],
                           seed=sp["seed"], ts="mock", request_sha256=sp["request_sha256"], attempts=attempts, **result)
                records.append(rec)
                if result["call_status"] != "OK":
                    responses.setdefault(sp["request_sha256"], None)
                sp = g.send(rec)
        except StopIteration as end:
            episodes.extend(end.value)
        finally:
            g.close()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "records.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    (out / "episodes.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False, default=str) + "\n" for e in episodes), encoding="utf-8")
    (out / "responses.json").write_text(json.dumps(responses, ensure_ascii=False), encoding="utf-8")
    return records, episodes


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["reference"])
    ap.add_argument("--split", default="dev")
    ap.add_argument("--shard", default="0:1")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cases")
    a = ap.parse_args()
    recs, eps = reference(a.split, a.shard, a.out, set(a.cases.split(",")) if a.cases else None)
    print(json.dumps(dict(records=len(recs), episodes=len(eps))))
