"""Durable, resumable driver for one shard (sender x repetition) of the expanded study.

The engine (engine.trial) decides every request; this driver only persists and replays. A transport
process (runner/transport.js, which sends from the Ollama page exactly as the V1 browser loop did)
talks to this driver over JSON lines:

  driver -> {"type":"env_request"}                               transport -> {"type":"env","env":{...}}
  driver -> {"type":"attempt","call_id","attempt","request_sha256","body","timeout_ms"}
                                                                 transport -> {"type":"attempt_result",...}
  driver -> {"type":"done",...} | {"type":"stopped",...} | {"type":"fatal","error":...}

Persistence (shard directory, never overwritten):
  manifest.json   fixed at the first start: source hashes, config, shard, split, case ids. A resume
                  refuses if anything differs.
  attempts.jsonl  {"event":"intent"} is written and fsynced BEFORE an attempt is handed to the transport;
                  {"event":"result"} after it returns.
  records.jsonl   one finalized record per call (V1 record fields), appended and fsynced BEFORE the next
                  request is produced.
  env-<n>.json    the environment check of each start; a mismatch stops before any chat call.
  episodes.jsonl / done.json  written once the shard is complete.

Resume: records.jsonl is replayed through the engine as a strict prefix (call_id and request SHA-256 in
the planned order; duplicates or extra records stop the run). A saved record is never sent again. The
result event of a successful attempt carries the full response, so a stop between that event and the
record is finalized from the journal without sending again. An intent without a result (the outcome is
unknown) counts as a used attempt; the call continues from the next attempt number within the usual
retry limit (1 + max_retries), and if no attempt is left the record is finalized from the journal.

usage: python -B -I runner/driver.py --campaign DIR --shard SENDER_INDEX:REP --split main|dev
                                     [--max-calls N] [--cases ID,ID,...]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from support import cases as load_split, config, sources  # noqa: E402
from support import common  # noqa: E402
import engine  # noqa: E402

sys.path.insert(0, str(ROOT.parent / "harness"))
from transport import executors  # noqa: E402

RECORD_FIELDS = ("content", "thinking", "done_reason", "eval_count", "prompt_eval_count", "total_duration_ns",
                 "load_duration_ns", "response_model")


class Fatal(Exception):
    pass


def emit(msg):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def receive(expect):
    line = sys.stdin.readline()
    if not line:
        raise Fatal("transport closed the channel")
    msg = json.loads(line)
    if msg.get("type") != expect:
        raise Fatal(f"expected {expect}, got {msg.get('type')}")
    return msg


def append_durable(path, obj):
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_jsonl(path):
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    lines = raw.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    elif lines:
        raise Fatal(f"{path.name} ends with an incomplete line; inspect it before resuming")
    return [json.loads(x) for x in lines]


def shard_trials(cfg, rows, sender_index, rep):
    name = cfg["senders"][sender_index]["name"]
    # plan over ALL cases of the split so case_index and seeds are the campaign-wide ones
    return [t for t in engine.plans(cfg, rows) if t["sender"]["name"] == name and t["rep"] == rep]


def manifest_for(cfg, split, shard, rows, trials):
    return dict(study=cfg["study"], harness_version=cfg["harness_version"], protocol_status=cfg["protocol_status"],
                split=split, shard=list(shard), sender=cfg["senders"][shard[0]]["name"], rep=shard[1],
                case_ids=[t["case"]["case_id"] for t in trials], trials=len(trials),
                seeds=[t["seed"] for t in trials], sources=sources(),
                config={k: cfg[k] for k in sorted(cfg) if k not in ("senders", "receiver")},
                senders=cfg["senders"], receiver=cfg["receiver"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--shard", required=True)
    ap.add_argument("--split", choices=["main", "dev"], required=True)
    ap.add_argument("--max-calls", type=int)
    ap.add_argument("--cases")
    a = ap.parse_args()
    cfg = config()
    if a.split == "main" and not cfg.get("run_enabled"):
        emit(dict(type="fatal", error="run_enabled is false in config.json: the main study is not frozen"))
        return 2
    si, rep = (int(x) for x in a.shard.split(":"))
    rows = load_split(a.split)
    trials = shard_trials(cfg, rows, si, rep)
    if a.cases:
        keep = set(a.cases.split(","))
        trials = [t for t in trials if t["case"]["case_id"] in keep]
    d = Path(a.campaign) / f"shard-{si}-r{rep}"
    d.mkdir(parents=True, exist_ok=True)
    man = manifest_for(cfg, a.split, (si, rep), rows, trials)
    mp = d / "manifest.json"
    if mp.exists():
        old = json.loads(mp.read_text(encoding="utf-8"))
        if old != man:
            diff = sorted(k for k in set(old) | set(man) if old.get(k) != man.get(k))
            emit(dict(type="fatal", error=f"manifest differs from the first start: {diff}"))
            return 2
    else:
        with open(mp, "x", encoding="utf-8") as f:
            json.dump(man, f, ensure_ascii=False, indent=1)
    if (d / "done.json").exists():
        emit(dict(type="done", already=True, shard=a.shard))
        return 0
    try:
        return run(cfg, d, trials, a.max_calls)
    except Fatal as e:
        emit(dict(type="fatal", error=str(e)))
        return 2


def run(cfg, d, trials, max_calls):
    rec_path, att_path = d / "records.jsonl", d / "attempts.jsonl"
    saved = read_jsonl(rec_path)
    attempts_log = read_jsonl(att_path)
    journal = {}
    for ev in attempts_log:
        journal.setdefault(ev["call_id"], []).append(ev)
    # environment check, every start, before any chat call
    starts = len(list(d.glob("env-*.json")))
    emit(dict(type="env_request"))
    env = receive("env")["env"]
    chk = executors.env_check(cfg, env)
    with open(d / f"env-{starts + 1}.json", "x", encoding="utf-8") as f:
        json.dump(dict(env=env, check=chk, ts=time.strftime("%Y-%m-%dT%H:%M:%S%z")), f, ensure_ascii=False, indent=1)
    if not chk["all_match"]:
        raise Fatal("ENV_MISMATCH " + json.dumps(chk, ensure_ascii=False))
    i, seen, episodes, new_calls = 0, set(), [], 0
    for t in trials:
        g = engine.trial(cfg, t)
        try:
            sp = next(g)
            while True:
                if sp["call_id"] in seen:
                    raise Fatal("duplicate call_id " + sp["call_id"])
                seen.add(sp["call_id"])
                if i < len(saved):                       # replay the persisted prefix, never resend
                    rec = saved[i]
                    if rec["call_id"] != sp["call_id"] or rec["request_sha256"] != sp["request_sha256"]:
                        raise Fatal(f"saved record {i} ({rec['call_id']}) does not match the planned request {sp['call_id']}")
                    i += 1
                else:
                    if max_calls is not None and new_calls >= max_calls:
                        emit(dict(type="stopped", reason="max_calls", saved=len(saved) + new_calls))
                        return 0
                    rec = call(cfg, sp, journal.get(sp["call_id"], []), att_path)
                    append_durable(rec_path, rec)
                    new_calls += 1
                sp = g.send(rec)
        except StopIteration as end:
            episodes.extend(end.value)
        finally:
            g.close()
    if i != len(saved):
        raise Fatal("records.jsonl has records beyond the plan")
    with open(d / "episodes.jsonl", "x", encoding="utf-8", newline="\n") as f:
        for e in episodes:
            f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
    done = dict(trials=len(trials), records=len(saved) + new_calls, episodes=len(episodes),
                ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    with open(d / "done.json", "x", encoding="utf-8") as f:
        json.dump(done, f, ensure_ascii=False, indent=1)
    emit(dict(type="done", **done))
    return 0


def result_from_response(j):
    msg = j.get("message") or {}
    return dict(call_status="OK", content=msg.get("content"), thinking=msg.get("thinking"),
                done_reason=j.get("done_reason"), eval_count=j.get("eval_count"),
                prompt_eval_count=j.get("prompt_eval_count"), total_duration_ns=j.get("total_duration"),
                load_duration_ns=j.get("load_duration"), response_model=j.get("model"))


def call(cfg, sp, prior, att_path):
    """One logical call with the V1 retry rule (1 + max_retries attempts); every attempt is journaled."""
    attempts, result, unknown = [], None, 0
    results = {ev["attempt"]: ev for ev in prior if ev["event"] == "result"}
    for ev in (e for e in prior if e["event"] == "intent"):      # attempts made before a restart
        if ev["request_sha256"] != sp["request_sha256"]:
            raise Fatal("journaled attempt does not match the planned request " + sp["call_id"])
        res = results.get(ev["attempt"])
        if res is None:
            attempts.append(dict(attempt=ev["attempt"], status="UNKNOWN_AFTER_RESTART"))
            unknown += 1
            continue
        attempts.append({k: res[k] for k in ("attempt", "status", "http", "error", "ms") if res.get(k) is not None})
        if res["status"] == "OK":
            result = result_from_response(res["response"])
            break
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    for a in ([] if result else range(len(attempts), cfg["max_retries"] + 1)):
        append_durable(att_path, dict(event="intent", call_id=sp["call_id"], attempt=a,
                                      request_sha256=sp["request_sha256"], ts=time.strftime("%Y-%m-%dT%H:%M:%S%z")))
        emit(dict(type="attempt", call_id=sp["call_id"], attempt=a, request_sha256=sp["request_sha256"],
                  body=sp["body"], timeout_ms=cfg["timeout_ms"]))
        r = receive("attempt_result")
        if r.get("call_id") != sp["call_id"] or r.get("attempt") != a:
            raise Fatal("attempt result does not match the attempt sent")
        entry = {k: r[k] for k in ("status", "http", "error", "ms") if r.get(k) is not None}
        entry["attempt"] = a
        journal = dict(event="result", call_id=sp["call_id"], **entry)
        if r["status"] == "OK":
            journal["response"] = r.get("response") or {}
        append_durable(att_path, journal)
        attempts.append(entry)
        if r["status"] == "OK":
            result = result_from_response(journal["response"])
            break
    if result is None:
        result = dict(call_status=attempts[-1]["status"], content=None)
    rec = dict(call_id=sp["call_id"], trial_id=sp["trial_id"], stage=sp["stage"], condition=sp["condition"],
               phase=sp.get("phase"), step=sp.get("step"), case_id=sp.get("case_id"), model=sp["model"],
               seed=sp["seed"], ts=ts, request_sha256=sp["request_sha256"], attempts=attempts, **result)
    if unknown:
        rec["attempts_unknown_after_restart"] = unknown
    return rec


if __name__ == "__main__":
    sys.exit(main())
