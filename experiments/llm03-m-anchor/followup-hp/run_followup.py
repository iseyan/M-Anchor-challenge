"""Follow-up comparison runner.

usage (from experiments/llm03-m-anchor/):
  python -I followup-hp/run_followup.py manifest
  python -I followup-hp/run_followup.py mock    --run-id <id>            # mock sender, for tests
  python -I followup-hp/run_followup.py prepare --run-id <id>            # bundle for the browser loop
  python -I followup-hp/run_followup.py ingest  --run-id <id> --state <state.json>

A run directory is followup-hp/runs/<run_id>/ and is never reused. The manifest fixes the SHA-256 of every
harness file (via harness/run.py) and every follow-up file; ingest refuses if any of them changed after
prepare. The source run's records are checked against config_followup.json before anything is planned.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # python -I does not add the script dir
import fu  # noqa: E402

sys.path.insert(0, str(fu.HARNESS))
import run as harness_run  # noqa: E402
from lib import prompts  # noqa: E402
from lib.common import sha256_text  # noqa: E402
from transport import executors  # noqa: E402

RUNS = fu.HERE / "runs"
FU_FILES = ["fu.py", "run_followup.py", "config_followup.json", "prompts/permit_sentence.txt",
            "transport/browser_loop_followup.js"]


def manifest() -> dict:
    return {"harness": harness_run.manifest(),
            "followup": {f: fu.sha256_file(fu.HERE / f) for f in FU_FILES}}


def die(msg: str):
    sys.exit("refused: " + msg)


def write_json(p: pathlib.Path, obj) -> None:
    if p.exists():
        die(f"{p.name} already exists in this run")
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def new_run(run_id: str, executor: str) -> pathlib.Path:
    d = RUNS / run_id
    if d.exists():
        die(f"run {run_id} already exists; use a new run id")
    d.mkdir(parents=True)
    write_json(d / "manifest.json", {"run_id": run_id, "executor": executor, "files": manifest()})
    return d


def open_run(run_id: str) -> tuple[pathlib.Path, dict]:
    d = RUNS / run_id
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    if m["files"] != manifest():
        die("harness or follow-up files changed since this run was prepared")
    return d, m


def finalize(cfg, fucfg, d, records, env, meta) -> dict:
    src = fu.source_records(fucfg)
    trials = fu.plan_trials(cfg, fucfg, src)
    v = fu.verify(cfg, trials, records)
    v["env_check"] = env
    write_json(d / "verification.json", v)
    bad = [k for k in ("duplicates", "unplanned", "hash_mismatch", "missing") if v[k]]
    if not v["order_matches"]:
        bad.append("order")
    if env is not None and not env.get("all_match"):
        bad.append("environment")
    if bad:
        die("verification failed (" + ", ".join(bad) + "); see verification.json. No episodes were written.")
    by_id = {r["call_id"]: r for r in records}
    eps = fu.episodes(cfg, trials, by_id)
    (d / "episodes.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in eps), encoding="utf-8")
    write_json(d / "summary.json", fu.summarize(cfg, eps))
    from lib import score
    ts = sorted(r["ts"] for r in records if r.get("ts") and r["ts"] != "mock")
    ops = {**meta, "first_call_ts": ts[0] if ts else None, "last_call_ts": ts[-1] if ts else None,
           "stages": score.stage_report(records, harness_run_parsed(cfg, trials, by_id)),
           "verification": {k: (len(x) if isinstance(x, list) else x) for k, x in v.items() if k != "env_check"}}
    write_json(d / "operation_report.json", ops)
    print(json.dumps(ops, ensure_ascii=False, indent=1))
    return ops


def harness_run_parsed(cfg, trials, by_id) -> dict:
    """Parsed status per call (as harness/run.py parsed_status), for the stage-2 and receiver calls."""
    from lib import pipeline
    from lib.common import parse_json
    out = {}
    for t in trials:
        for p in ("S2", "RX"):
            for c in cfg["conditions"]:
                r = by_id.get(f"{p}|{t['trial_id']}|{c}")
                if not r or r.get("call_status") != "OK" or r.get("content") is None:
                    continue
                obj, _ = parse_json(r["content"])
                if obj is None:
                    out[r["call_id"]] = "INVALID_OUTPUT"
                elif p == "RX":
                    dd = obj.get("decision") if isinstance(obj, dict) else None
                    out[r["call_id"]] = ("OK" if isinstance(dd, str) and dd.strip().lower() in ("approve", "hold", "reject")
                                         else "SCHEMA_ERROR")
                else:
                    out[r["call_id"]] = pipeline.stage_check(t["case"], obj, 2)["status"]
    return out


def build_payload(cfg, fucfg, run_id: str, trials: list[dict]) -> dict:
    digests = {s["name"]: s["expected_digest"] for s in cfg["senders"]}
    digests[cfg["receiver"]["name"]] = cfg["receiver"]["expected_digest"]
    templates = dict(prompts.templates(), permit_sentence=fu.permit_sentence())
    payload = {
        "run_id": run_id, "experiment": fucfg["experiment"], "timeout_ms": cfg["timeout_ms"],
        "max_retries": cfg["max_retries"], "endpoint": "/api/chat",
        "expected_env": {"version": cfg["expected_ollama_version"], "digests": digests},
        "backend": {k: cfg["backend"][k] for k in ("stream", "format", "keep_alive")}, "options": cfg["options"],
        "conditions": cfg["conditions"], "stage2_block": cfg["stage2_block"],
        "receiver": {"name": cfg["receiver"]["name"], "think": cfg["receiver"]["think"]},
        "templates": templates,
        "trials": [{"trial_id": t["trial_id"], "seed": t["seed"],
                    "sender": {"name": t["sender"]["name"], "think": t["sender"]["think"]},
                    "s1_messages": prompts.stage1_messages(t["case"]), "s1_content": t["s1_content"],
                    "case": {"claims": [{"key": c["key"], "admissible_kinds": c["admissible_kinds"]}
                                        for c in t["case"]["claims"]],
                             "doc_kinds": {x["id"]: x["kind"] for x in t["case"]["documents"]},
                             "request_summary": prompts.request_summary(t["case"]),
                             "claims_block": prompts.claims_block(t["case"])}} for t in trials]}
    payload["payload_sha256"] = sha256_text(json.dumps(payload, ensure_ascii=False))
    return payload


def mock_sender(trials: list[dict]):
    """Deterministic stand-in for tests: the content is chosen from the SHA-256 of the request body.
    Stage 2: 0-2 actions (the gold payment, read_record, or delete_record) and, for 1 in 8 bodies, no handoff
    (so the not-sent path runs); receiver: approve / hold / reject."""
    by_tid = {t["trial_id"]: t["case"] for t in trials}

    def send(c: dict) -> dict:
        h = int(sha256_text(c["body"])[:12], 16)
        case = by_tid[c["trial_id"]]
        rid = case["world"]["requests"][0]["request_id"]
        if c["stage"] == 2:
            pool = [{"tool": "read_record", "args": {"request_id": rid}},
                    {"tool": "delete_record", "args": {"request_id": rid}}]
            pool += [{"tool": a["tool"], "args": a["args"]} for a in case["gold"]["allowed_state_changes"]]
            acts = [pool[(h >> (4 * i)) % len(pool)] for i in range(h % 3)]
            st = ["confirmed", "unconfirmed", "refuted"]
            obj = {"actions": acts}
            if h % 8:
                obj["handoff"] = {"claims": {k["key"]: st[(h >> (3 * j + 7)) % 3] for j, k in enumerate(case["claims"])}}
            content = json.dumps(obj, ensure_ascii=False)
        else:
            content = json.dumps({"decision": ["approve", "hold", "reject"][h % 3], "reason": "mock"})
        return {"call_id": c["call_id"], "trial_id": c["trial_id"], "stage": c["stage"], "condition": c["condition"],
                "model": c["model"], "seed": c["seed"], "ts": "mock", "request_sha256": c["request_sha256"],
                "call_status": "OK", "attempts": [{"attempt": 0, "status": "OK", "ms": 0}], "content": content,
                "done_reason": "stop", "eval_count": 100, "prompt_eval_count": 1000, "response_model": c["model"]}
    return send


def cmd_mock(args):
    cfg, fucfg = fu.load_cfg(), fu.load_fu()
    trials = fu.plan_trials(cfg, fucfg, fu.source_records(fucfg))
    d = new_run(args.run_id, "mock")
    send = mock_sender(trials)
    t0 = time.time()
    records = [r for t in trials for r in fu.run_trial(cfg, t, send)]
    write_json(d / "records.json", records)
    finalize(cfg, fucfg, d, records, None, {"run_id": args.run_id, "executor": "mock",
                                            "wall_clock_s": round(time.time() - t0, 1)})


def cmd_prepare(args):
    cfg, fucfg = fu.load_cfg(), fu.load_fu()
    trials = fu.plan_trials(cfg, fucfg, fu.source_records(fucfg))
    d = new_run(args.run_id, "browser")
    payload = build_payload(cfg, fucfg, args.run_id, trials)
    loop = (fu.HERE / "transport" / "browser_loop_followup.js").read_text(encoding="utf-8").strip()
    out = d / f"bundle_{args.run_id}.json"
    out.write_text(json.dumps({"payload": payload, "loop_js": loop}, ensure_ascii=False), encoding="utf-8")
    info = {"bundle": out.name, "bundle_sha256": fu.sha256_file(out), "payload_sha256": payload["payload_sha256"],
            "trials": len(payload["trials"]), "stage2_calls": len(payload["trials"]) * len(cfg["conditions"])}
    write_json(d / "bundle_info.json", info)
    print(json.dumps(dict(info, path=str(out))))


def cmd_ingest(args):
    cfg, fucfg = fu.load_cfg(), fu.load_fu()
    d, m = open_run(args.run_id)
    if m["executor"] != "browser":
        die("ingest is only for browser runs")
    st = json.loads(pathlib.Path(args.state).read_text(encoding="utf-8"))
    info = json.loads((d / "bundle_info.json").read_text(encoding="utf-8"))
    if st.get("run_id") != m["run_id"] or st.get("payload_sha256") != info["payload_sha256"]:
        die("state file does not belong to this run's bundle")
    if st.get("running"):
        die("the browser loop was still running when the state was saved")
    write_json(d / "state_raw.json", st)
    write_json(d / "records.json", st["records"])
    env = executors.env_check(cfg, st.get("env", {}))
    meta = {"run_id": m["run_id"], "executor": "browser", "started_at": st.get("started_at"),
            "finished_at": st.get("finished_at"), "fatal": st.get("fatal"), "done_trials": st.get("done_trials"),
            "total_trials": st.get("total_trials"), "env": env}
    finalize(cfg, fucfg, d, st["records"], env, meta)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("manifest")
    for name in ("mock", "prepare"):
        p = sub.add_parser(name)
        p.add_argument("--run-id", required=True)
    p = sub.add_parser("ingest")
    p.add_argument("--run-id", required=True)
    p.add_argument("--state", required=True)
    a = ap.parse_args()
    if a.cmd == "manifest":
        print(json.dumps(manifest(), ensure_ascii=False, indent=1))
    else:
        {"mock": cmd_mock, "prepare": cmd_prepare, "ingest": cmd_ingest}[a.cmd](a)


if __name__ == "__main__":
    main()
