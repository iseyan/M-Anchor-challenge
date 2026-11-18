"""Command-line entry point for the LLM03 x M-Anchor harness.

  python -I run.py manifest
  python -I run.py run    --split dev --executor mock --run-id dev-mock-001
  python -I run.py run    --split dev --executor http --run-id dev-smoke-001 [--endpoint URL]
  python -I run.py bundle --split dev --phase 1 --run-id dev-smoke-001      (browser transport, as in V1)
  python -I run.py ingest --phase 1 --run-id dev-smoke-001 --records state.json
  python -I run.py score  --run-id dev-smoke-001
  python -I run.py check-env --run-id dev-smoke-001 --env state.json

The main split (the 40 frozen cases) is refused unless --allow-main is given, so that operation checks
cannot touch it by accident. Every run writes its files under runs/<run-id>/.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lib import pipeline, score  # noqa: E402
from lib.common import load_cases, load_config, read_json, sha256_file, write_json, write_jsonl  # noqa: E402
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


def guard_split(split: str, allow_main: bool) -> None:
    if split == "main" and not allow_main:
        sys.exit("refused: the main split is the frozen measurement set; pass --allow-main only after the "
                 "specification freeze")


def by_id(records: list[dict]) -> dict:
    return {r["call_id"]: r for r in records}


def verify(calls: list[dict], records: list[dict]) -> dict:
    """Every record must belong to a planned call and carry the planned request hash."""
    spec = {c["call_id"]: c["request_sha256"] for c in calls}
    seen = {r["call_id"]: r.get("request_sha256") for r in records}
    return {"planned": len(spec), "returned": len(seen),
            "missing": sorted(set(spec) - set(seen)), "unplanned": sorted(set(seen) - set(spec)),
            "hash_mismatch": sorted(k for k, v in seen.items() if k in spec and v != spec[k])}


def load_state(path: str) -> dict:
    obj = read_json(pathlib.Path(path))
    return obj if isinstance(obj, dict) and "records" in obj else {"records": obj, "env": {}}


# --------------------------------------------------------------------------------------------- phases
def build_phase(cfg, cases, d: pathlib.Path, phase: int):
    trials = pipeline.plan_trials(cfg, cases)
    if phase == 1:
        return pipeline.phase1_calls(cfg, trials), {}
    if phase == 2:
        s1 = by_id(read_json(d / "phase1_records.json"))
        calls, not_run, states = pipeline.phase2_calls(cfg, trials, s1)
        write_json(d / "phase2_not_run.json", not_run)
        write_json(d / "anchor_states.json", states)
        return calls, {}
    s2 = by_id(read_json(d / "phase2_records.json") + read_json(d / "phase2_not_run.json"))
    episodes, calls = pipeline.phase3(cfg, trials, s2)
    write_json(d / "episodes_gate.json", episodes)
    return calls, {}


def finish(cfg, cases, d: pathlib.Path) -> dict:
    episodes = read_json(d / "episodes_gate.json")
    rx = by_id(read_json(d / "phase3_records.json")) if (d / "phase3_records.json").exists() else {}
    pipeline.attach_receiver(episodes, rx)
    summary = score.summarize(episodes, {c["case_id"]: c for c in cases})
    write_jsonl(d / "episodes.jsonl", episodes)
    write_json(d / "summary.json", summary)
    return summary


def start(args, cfg) -> tuple[list, pathlib.Path]:
    guard_split(args.split, args.allow_main)
    d = run_dir(args.run_id)
    m = d / "manifest.json"
    if m.exists():
        prev = read_json(m)
        if prev["split"] != args.split:
            sys.exit(f"refused: run {args.run_id} is for split {prev['split']}")
    else:
        write_json(m, {"run_id": args.run_id, "split": args.split, "harness_version": cfg["harness_version"],
                       "files_sha256": manifest()})
    return load_cases(args.split), d


def cmd_run(args):
    cfg = load_config()
    cases, d = start(args, cfg)
    ex = executors.run_mock if args.executor == "mock" else (
        lambda calls, cfg: executors.run_http(calls, cfg, args.endpoint))
    report = {}
    for phase in (1, 2, 3):
        calls, _ = build_phase(cfg, cases, d, phase)
        write_json(d / f"phase{phase}_calls.json", calls)
        recs = ex(calls, cfg)
        write_json(d / f"phase{phase}_records.json", recs)
        report[f"phase{phase}"] = verify(calls, recs)
    summary = finish(cfg, cases, d)
    write_json(d / "verification.json", report)
    print(json.dumps({"verification": {k: {kk: (len(vv) if isinstance(vv, list) else vv) for kk, vv in v.items()}
                                       for k, v in report.items()}}, ensure_ascii=False))
    print(json.dumps(summary["all"], ensure_ascii=False, indent=1))


def cmd_bundle(args):
    cfg = load_config()
    cases, d = start(args, cfg)
    calls, _ = build_phase(cfg, cases, d, args.phase)
    write_json(d / f"phase{args.phase}_calls.json", calls)
    payload = {"run_id": args.run_id, "phase": args.phase, "timeout_ms": cfg["timeout_ms"],
               "max_retries": cfg["max_retries"],
               "models_to_show": sorted({s["name"] for s in cfg["senders"]} | {cfg["receiver"]["name"]}),
               "calls": [{"call_id": c["call_id"], "body": c["body"]} for c in calls]}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    payload["payload_sha256"] = pipeline.sha256_text(blob)
    loop = (HERE / "transport" / "browser_loop.js").read_text(encoding="utf-8").strip()
    out = d / f"bundle_phase{args.phase}.json"
    out.write_text(json.dumps({"payload": payload, "loop_js": loop}, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"bundle": str(out), "calls": len(calls), "payload_sha256": payload["payload_sha256"]}))


def cmd_ingest(args):
    d = run_dir(args.run_id)
    calls = read_json(d / f"phase{args.phase}_calls.json")
    st = load_state(args.records)
    recs = st["records"]
    v = verify(calls, recs)
    write_json(d / f"phase{args.phase}_records.json", recs)
    write_json(d / f"phase{args.phase}_env.json", st.get("env", {}))
    write_json(d / f"phase{args.phase}_verification.json", v)
    print(json.dumps({k: (len(x) if isinstance(x, list) else x) for k, x in v.items()}))
    if v["missing"] or v["unplanned"] or v["hash_mismatch"]:
        sys.exit("verification failed; see phase verification file")


def cmd_score(args):
    cfg = load_config()
    d = run_dir(args.run_id)
    cases = load_cases(read_json(d / "manifest.json")["split"])
    print(json.dumps(finish(cfg, cases, d)["all"], ensure_ascii=False, indent=1))


def cmd_check_env(args):
    cfg = load_config()
    env = load_state(args.env).get("env", {})
    tags = {m["name"]: m.get("digest") for m in (env.get("tags") or {}).get("models", [])}
    want = {s["name"]: s["expected_digest"] for s in cfg["senders"]}
    want[cfg["receiver"]["name"]] = cfg["receiver"]["expected_digest"]
    res = {"ollama_version": (env.get("version") or {}).get("version"),
           "ollama_version_matches": (env.get("version") or {}).get("version") == cfg["expected_ollama_version"],
           "models": {n: {"expected": w, "found": tags.get(n), "matches": tags.get(n) == w} for n, w in want.items()}}
    write_json(run_dir(args.run_id) / "env_check.json", res)
    print(json.dumps(res, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("manifest")
    for name in ("run", "bundle"):
        p = sub.add_parser(name)
        p.add_argument("--split", required=True, choices=["dev", "main"])
        p.add_argument("--run-id", required=True)
        p.add_argument("--allow-main", action="store_true")
        if name == "run":
            p.add_argument("--executor", required=True, choices=["mock", "http"])
            p.add_argument("--endpoint")
        else:
            p.add_argument("--phase", required=True, type=int, choices=[1, 2, 3])
    p = sub.add_parser("ingest")
    p.add_argument("--phase", required=True, type=int, choices=[1, 2, 3])
    p.add_argument("--run-id", required=True)
    p.add_argument("--records", required=True)
    p = sub.add_parser("score")
    p.add_argument("--run-id", required=True)
    p = sub.add_parser("check-env")
    p.add_argument("--run-id", required=True)
    p.add_argument("--env", required=True)
    a = ap.parse_args()
    if a.cmd == "manifest":
        print(json.dumps(manifest(), indent=1))
        return
    {"run": cmd_run, "bundle": cmd_bundle, "ingest": cmd_ingest, "score": cmd_score,
     "check-env": cmd_check_env}[a.cmd](a)


if __name__ == "__main__":
    main()
