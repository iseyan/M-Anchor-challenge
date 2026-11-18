"""Command-line entry point for the LLM03 x M-Anchor harness.

  python -I run.py manifest
  python -I run.py run     --split dev --executor mock --run-id dev-mock-001
  python -I run.py run     --split dev --executor http --run-id dev-http-001 [--base http://localhost:11434]
  python -I run.py prepare --split dev --run-id dev-smoke-001          (browser transport, as in V1)
  python -I run.py ingest  --run-id dev-smoke-001 --state <saved window.__llm03 JSON>
  python -I run.py score   --run-id dev-smoke-001

Safeguards
  - A new run needs an unused run id; nothing is ever overwritten. Records are written once.
  - The manifest fixes the split, the executor and the SHA-256 of every harness file; every later command
    refuses to continue if any of them changed.
  - Before scoring, every request is rebuilt from the case, config, prompts and earlier contents of its trial
    and compared with the recorded SHA-256. Duplicate, unplanned, missing or mismatched records, a wrong call
    order, or an environment mismatch stop the run before any episode is written.
  - Inference never starts when the Ollama version or a model digest differs from config.json.
  - The main split (the 40 frozen cases) is refused unless --allow-main is given.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lib import pipeline, prompts, score  # noqa: E402
from lib.common import (load_cases, load_config, parse_json, read_json, sha256_file, sha256_text,  # noqa: E402
                        write_json, write_jsonl)
from transport import executors  # noqa: E402

TRACKED = ["config.json", "prompts", "cases/build_cases.py", "cases/main.jsonl", "cases/dev.jsonl", "lib",
           "transport/executors.py", "transport/browser_loop.js", "run.py"]


def manifest() -> dict:
    files = {}
    for t in TRACKED:
        p = HERE / t
        for f in sorted([p] if p.is_file() else p.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                files[str(f.relative_to(HERE)).replace("\\", "/")] = sha256_file(f)
    return files


def run_dir(run_id: str) -> pathlib.Path:
    return HERE / "runs" / run_id


def die(msg: str):
    sys.exit("refused: " + msg)


def new_run(args, cfg, executor: str) -> pathlib.Path:
    if args.split == "main" and not args.allow_main:
        die("the main split is the frozen measurement set; pass --allow-main only after the specification freeze")
    d = run_dir(args.run_id)
    if d.exists():
        die(f"run id {args.run_id} already exists; use a new run id (nothing is overwritten)")
    d.mkdir(parents=True)
    write_json(d / "manifest.json", {"run_id": args.run_id, "split": args.split, "executor": executor,
                                     "harness_version": cfg["harness_version"],
                                     "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "files_sha256": manifest()})
    return d


def open_run(run_id: str) -> tuple[pathlib.Path, dict]:
    d = run_dir(run_id)
    if not (d / "manifest.json").exists():
        die(f"no run {run_id}")
    m = read_json(d / "manifest.json")
    now = manifest()
    changed = sorted(k for k in set(now) | set(m["files_sha256"]) if now.get(k) != m["files_sha256"].get(k))
    if changed:
        die("harness files changed since the run was created: " + ", ".join(changed))
    return d, m


def write_once(path: pathlib.Path, obj) -> None:
    if path.exists():
        die(f"{path.name} already exists in this run")
    write_json(path, obj)


# --------------------------------------------------------------------------------------------- verification
def verify(cfg: dict, trials: list[dict], records: list[dict]) -> dict:
    ids = [r.get("call_id") for r in records]
    dup = sorted(k for k, n in collections.Counter(ids).items() if n > 1)
    by_id = {}
    for r in records:                       # keep the first record of a duplicated id; duplicates fail anyway
        by_id.setdefault(r["call_id"], r)
    expected, order = [], []
    for t in trials:
        for sp in pipeline.expected_calls(cfg, t, by_id):
            expected.append(sp)
            order.append(sp["call_id"])
        s1 = by_id.get("S1|" + t["trial_id"])
        if s1 is not None and not pipeline.s1_runnable(s1):
            order += [f"S2|{t['trial_id']}|{c}" for c in cfg["conditions"]]   # NOT_RUN rows
    exp_ids = set(order)
    mismatch = sorted(sp["call_id"] for sp in expected if sp["call_id"] in by_id and
                      by_id[sp["call_id"]].get("request_sha256") != sp["request_sha256"])
    sent_order = [i for i in ids if i in exp_ids]
    return {"records": len(records), "expected": len(order), "duplicates": dup,
            "unplanned": sorted(set(ids) - exp_ids), "missing": [i for i in order if i not in by_id],
            "hash_mismatch": mismatch, "order_matches": sent_order == [i for i in order if i in by_id]}


def verification_failed(v: dict, allow_incomplete: bool) -> list[str]:
    bad = [k for k in ("duplicates", "unplanned", "hash_mismatch") if v[k]]
    if not v["order_matches"]:
        bad.append("order")
    if v["missing"] and not allow_incomplete:
        bad.append("missing")
    return bad


def parsed_status(cfg, trials, by_id) -> dict:
    out = {}
    for t in trials:
        recs = [by_id.get("S1|" + t["trial_id"])] + [by_id.get(f"{p}|{t['trial_id']}|{c}")
                                                     for p in ("S2", "RX") for c in cfg["conditions"]]
        for r in recs:
            if not r or r.get("call_status") != "OK" or r.get("content") is None:
                continue
            obj, _ = parse_json(r["content"])
            if obj is None:
                out[r["call_id"]] = "INVALID_OUTPUT"
            elif r["call_id"].startswith("RX|"):
                d = obj.get("decision") if isinstance(obj, dict) else None
                out[r["call_id"]] = ("OK" if isinstance(d, str) and d.strip().lower() in ("approve", "hold", "reject")
                                     else "SCHEMA_ERROR")
            else:
                out[r["call_id"]] = pipeline.stage_check(t["case"], obj, 1 if r["call_id"].startswith("S1|") else 2)["status"]
    return out


def finalize(cfg, d: pathlib.Path, m: dict, records: list[dict], env_check, meta: dict, allow_incomplete: bool) -> dict:
    cases = load_cases(m["split"])
    trials = pipeline.plan_trials(cfg, cases)
    v = verify(cfg, trials, records)
    v["env_check"] = env_check
    write_once(d / "verification.json", v)
    bad = verification_failed(v, allow_incomplete)
    if env_check is not None and not env_check.get("all_match"):
        bad.append("environment")
    if bad:
        die("verification failed (" + ", ".join(bad) + "); see verification.json. No episodes were written.")
    by_id = {r["call_id"]: r for r in records}
    episodes = pipeline.episodes_from_records(cfg, trials, by_id)
    summary = score.summarize(episodes, {c["case_id"]: c for c in cases})
    ts = sorted(r["ts"] for r in records if r.get("ts") and r["ts"] != "mock")
    ops = {"run_id": m["run_id"], "split": m["split"], "executor": m["executor"], **meta,
           "first_call_ts": ts[0] if ts else None, "last_call_ts": ts[-1] if ts else None,
           "stages": score.stage_report(records, parsed_status(cfg, trials, by_id)),
           "not_run": sum(1 for r in records if r.get("call_status") == "NOT_RUN"),
           "verification": {k: (len(x) if isinstance(x, list) else x) for k, x in v.items() if k != "env_check"}}
    if (d / "episodes.jsonl").exists():
        die("episodes.jsonl already exists in this run")
    write_jsonl(d / "episodes.jsonl", episodes)
    write_once(d / "summary.json", summary)
    write_once(d / "operation_report.json", ops)
    print(json.dumps(ops, ensure_ascii=False, indent=1))
    return ops


# --------------------------------------------------------------------------------------------- commands
def cmd_run(args):
    cfg = load_config()
    base = args.base or cfg["backend"]["endpoint"].rsplit("/api/", 1)[0]
    env = None
    if args.executor == "http":
        env = executors.preflight_http(cfg, base)          # before creating anything and before any inference
        if not env["all_match"]:
            sys.exit("refused: environment differs from config.json: " + json.dumps(env, ensure_ascii=False))
    d = new_run(args, cfg, args.executor)
    _, m = open_run(args.run_id)
    send = executors.mock_sender(cfg) if args.executor == "mock" else executors.http_sender(cfg, base + "/api/chat")
    records = []
    t0 = time.time()
    for t in pipeline.plan_trials(cfg, load_cases(args.split)):
        records += pipeline.run_trial(cfg, t, send)
    write_once(d / "records.json", records)
    finalize(cfg, d, m, records, env, {"wall_clock_s": round(time.time() - t0, 1)}, allow_incomplete=False)


def build_payload(cfg, run_id: str, cases: list[dict]) -> dict:
    trials = pipeline.plan_trials(cfg, cases)
    digests = {s["name"]: s["expected_digest"] for s in cfg["senders"]}
    digests[cfg["receiver"]["name"]] = cfg["receiver"]["expected_digest"]
    payload = {
        "run_id": run_id, "timeout_ms": cfg["timeout_ms"], "max_retries": cfg["max_retries"],
        "endpoint": "/api/chat", "expected_env": {"version": cfg["expected_ollama_version"], "digests": digests},
        "backend": {k: cfg["backend"][k] for k in ("stream", "format", "keep_alive")}, "options": cfg["options"],
        "conditions": cfg["conditions"],
        "stage2_block": {c: (None if v is None else "anchor" if v == "anchor" else "gc")
                         for c, v in cfg["stage2_block"].items()},
        "receiver": {"name": cfg["receiver"]["name"], "think": cfg["receiver"]["think"]},
        "templates": prompts.templates(),
        "trials": [{"trial_id": t["trial_id"], "seed": t["seed"],
                    "sender": {"name": t["sender"]["name"], "think": t["sender"]["think"]},
                    "s1_messages": prompts.stage1_messages(t["case"]),
                    "case": {"claims": [{"key": c["key"], "admissible_kinds": c["admissible_kinds"]}
                                        for c in t["case"]["claims"]],
                             "doc_kinds": {x["id"]: x["kind"] for x in t["case"]["documents"]},
                             "request_summary": prompts.request_summary(t["case"]),
                             "claims_block": prompts.claims_block(t["case"])}} for t in trials]}
    payload["payload_sha256"] = sha256_text(json.dumps(payload, ensure_ascii=False))
    return payload


def cmd_prepare(args):
    cfg = load_config()
    d = new_run(args, cfg, "browser")
    payload = build_payload(cfg, args.run_id, load_cases(args.split))
    loop = (HERE / "transport" / "browser_loop.js").read_text(encoding="utf-8").strip()
    out = d / f"bundle_{args.run_id}.json"
    out.write_text(json.dumps({"payload": payload, "loop_js": loop}, ensure_ascii=False), encoding="utf-8")
    info = {"bundle": out.name, "bundle_sha256": sha256_file(out), "payload_sha256": payload["payload_sha256"],
            "trials": len(payload["trials"])}
    write_json(d / "bundle_info.json", info)
    print(json.dumps(dict(info, path=str(out))))


def cmd_ingest(args):
    cfg = load_config()
    d, m = open_run(args.run_id)
    if m["executor"] != "browser":
        die("ingest is only for browser runs")
    st = read_json(pathlib.Path(args.state))
    info = read_json(d / "bundle_info.json")
    if st.get("run_id") != m["run_id"] or st.get("payload_sha256") != info["payload_sha256"]:
        die("state file does not belong to this run's bundle")
    if st.get("running"):
        die("the browser loop was still running when the state was saved")
    write_once(d / "state_raw.json", st)
    write_once(d / "records.json", st["records"])
    env = executors.env_check(cfg, st.get("env", {}))
    meta = {"started_at": st.get("started_at"), "finished_at": st.get("finished_at"), "fatal": st.get("fatal"),
            "done_trials": st.get("done_trials"), "total_trials": st.get("total_trials"), "env": env}
    finalize(cfg, d, m, st["records"], env, meta, allow_incomplete=args.allow_incomplete)


def cmd_score(args):
    cfg = load_config()
    d, m = open_run(args.run_id)
    cases = load_cases(m["split"])
    episodes = pipeline.episodes_from_records(cfg, pipeline.plan_trials(cfg, cases),
                                              {r["call_id"]: r for r in read_json(d / "records.json")})
    print(json.dumps(score.summarize(episodes, {c["case_id"]: c for c in cases})["all"], ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("manifest")
    for name in ("run", "prepare"):
        p = sub.add_parser(name)
        p.add_argument("--split", required=True, choices=["dev", "main"])
        p.add_argument("--run-id", required=True)
        p.add_argument("--allow-main", action="store_true")
        if name == "run":
            p.add_argument("--executor", required=True, choices=["mock", "http"])
            p.add_argument("--base")
    p = sub.add_parser("ingest")
    p.add_argument("--run-id", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--allow-incomplete", action="store_true")
    p = sub.add_parser("score")
    p.add_argument("--run-id", required=True)
    a = ap.parse_args()
    if a.cmd == "manifest":
        print(json.dumps(manifest(), indent=1))
        return
    {"run": cmd_run, "prepare": cmd_prepare, "ingest": cmd_ingest, "score": cmd_score}[a.cmd](a)


if __name__ == "__main__":
    main()
