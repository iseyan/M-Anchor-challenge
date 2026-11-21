"""Experiment 3 through the frozen experiment-2 driver, with the experiment-2 mock responses (plan 8).

The driver runs in-process with a stand-in transport that answers exactly as the Node mock transport
does (responses keyed by request SHA-256, the mock's injected failures). No model calls.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "runner"))
import run3  # noqa: E402

driver, mock, exp3, ledger = run3.driver, run3.mock, run3.exp3, run3.ledger
KINDS = ("LT6", "LT4", "LT1", "LT2", "LT3", "AT1", "AT7", "AT9")


def dev_ids():
    rows = exp3.split("dev")
    return [next(c["case_id"] for c in rows if c["type"] == k) for k in KINDS]


class MockTransport:
    def __init__(self, cfg, responses):
        self.cfg, self.responses, self.last, self.finals, self.sent = cfg, responses, None, [], 0

    def emit(self, msg):
        if msg["type"] in ("done", "stopped", "fatal"):
            self.finals.append(msg)
        self.last = msg

    def receive(self, expect):
        m = self.last
        if expect == "env":
            models = [dict(name=s["name"], digest=s["expected_digest"]) for s in self.cfg["senders"]]
            models.append(dict(name=self.cfg["receiver"]["name"], digest=self.cfg["receiver"]["expected_digest"]))
            return dict(type="env", env=dict(version=dict(version=self.cfg["expected_ollama_version"]), tags=dict(models=models)))
        assert expect == "attempt_result" and m["type"] == "attempt"
        self.sent += 1
        bad = mock.attempt_status(m["request_sha256"], m["attempt"])
        if bad:
            return dict(type="attempt_result", call_id=m["call_id"], attempt=m["attempt"], **bad)
        return dict(type="attempt_result", call_id=m["call_id"], attempt=m["attempt"], status="OK", ms=1,
                    response=self.responses[m["request_sha256"]])


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    out = tmp_path_factory.mktemp("ref")
    recs, eps = mock.reference("dev", "0:1", out, set(dev_ids()))
    responses = json.loads((out / "responses.json").read_text(encoding="utf-8"))
    return recs, eps, responses


def run_driver(camp, responses, *extra):
    t = MockTransport(exp3.config(), responses)
    driver.CHANNEL = t
    code = driver.main(["--campaign", str(camp), "--shard", "0:1", "--split", "dev", "--cases", ",".join(dev_ids()), *extra])
    return code, t


def lines(p):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x]


def test_driver_reproduces_the_in_process_reference_and_verifies(tmp_path, reference):
    recs, eps, responses = reference
    code, t = run_driver(tmp_path / "c", responses)
    assert code == 0 and t.finals[-1]["type"] == "done", t.finals
    d = tmp_path / "c" / "shard-0-r1"
    saved = lines(d / "records.jsonl")
    assert [r["call_id"] for r in saved] == [r["call_id"] for r in recs]
    assert [r["content"] for r in saved] == [r["content"] for r in recs]
    got = lines(d / "episodes.jsonl")
    assert json.dumps(got, ensure_ascii=False, sort_keys=True) == json.dumps(json.loads(json.dumps(eps, default=str)), ensure_ascii=False, sort_keys=True)
    assert {e["condition"] for e in got} == {"C3", "GC", "GCP", "C4", "C4P"}
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert man["config"]["conditions"] == ["C3", "GC", "GCP", "C4", "C4P"] and man["config"]["seed_base"] == 20361120
    assert "exp3-permit/exp3.py" in man["sources"] and "followup-hp/prompts/permit_sentence.txt" in man["sources"]
    rep = run3.verify(d)
    assert rep["sources_match_manifest"] and rep["plan_matches_manifest"]
    assert rep["replay"]["complete"] and rep["replay"]["mismatch"] is None and rep["replay"]["body_sha256_recomputed"]
    assert rep["episodes_byte_identical"] and rep["journal_problems"] == [] and rep["journal_extra_calls"] == []
    assert rep["episodes_with_decision_ids_and_final_ledger"] == rep["episodes"] == len(KINDS) * 5
    assert rep["ledger_vs_legit_action_ok_disagreements"] == []
    assert "HTTP_ERROR" in rep["call_status"] or "TIMEOUT" in rep["attempt_status"] or rep["call_status"] == {"OK": len(saved)}


def test_interrupted_shard_resumes_without_resending_and_matches(tmp_path, reference):
    recs, eps, responses = reference
    code, t = run_driver(tmp_path / "c", responses, "--max-calls", "9")
    assert t.finals[-1]["type"] == "stopped"
    first = t.sent
    code, t2 = run_driver(tmp_path / "c", responses)
    assert t2.finals[-1]["type"] == "done"
    d = tmp_path / "c" / "shard-0-r1"
    saved = lines(d / "records.jsonl")
    assert [r["call_id"] for r in saved] == [r["call_id"] for r in recs]
    assert len({r["call_id"] for r in saved}) == len(saved)                     # nothing saved twice
    assert first + t2.sent == sum(len(r["attempts"]) for r in recs)              # nothing sent again
    got = lines(d / "episodes.jsonl")
    for e in got:
        pays = [r for r in e["audit"] if r["table"] == "payments" and r["op"] == "INSERT"]
        assert len(pays) == len(e["final_ledger"]["payments"]) <= 1
    rep = run3.verify(d)
    assert rep["episodes_byte_identical"] and rep["starts"] == 2 and all(rep["env_all_match"])


def test_changed_source_is_refused_on_resume(tmp_path, reference, monkeypatch):
    recs, eps, responses = reference
    run_driver(tmp_path / "c", responses, "--max-calls", "3")
    real = exp3.sources
    monkeypatch.setattr(driver, "sources", lambda: dict(real(), **{"exp3-permit/exp3.py": "0" * 64}))
    code, t = run_driver(tmp_path / "c", responses)
    assert t.finals[-1]["type"] == "fatal" and "manifest differs" in t.finals[-1]["error"]


def test_main_split_guard_follows_run_enabled(tmp_path):
    """Not frozen: the main split is refused. Frozen: the driver starts with the environment check, and a
    wrong environment stops it before any chat call."""
    cfg = exp3.config()
    t = MockTransport(cfg, {})
    if not cfg["run_enabled"]:
        driver.CHANNEL = t
        assert driver.main(["--campaign", str(tmp_path / "m"), "--shard", "0:1", "--split", "main"]) == 2
        assert "run_enabled is false" in t.finals[-1]["error"]
        return

    class WrongVersion(MockTransport):
        def receive(self, expect):
            m = super().receive(expect)
            if expect == "env":
                m["env"]["version"]["version"] = "0.40.17"
            return m
    t = WrongVersion(cfg, {})
    driver.CHANNEL = t
    assert driver.main(["--campaign", str(tmp_path / "m"), "--shard", "0:1", "--split", "main"]) == 2
    assert "ENV_MISMATCH" in t.finals[-1]["error"] and t.sent == 0
    man = json.loads((tmp_path / "m" / "shard-0-r1" / "manifest.json").read_text(encoding="utf-8"))
    assert man["trials"] == 240 and man["split"] == "main" and len(set(man["case_ids"])) == 240
    assert man["seeds"][0] == 20361120 + 1000 + exp3.cases()[0]["_exp2_index"]


class _P:
    def __init__(self, out):
        self.stdout, self.stderr = out, ""


def _serve_until_listening(tmp_path, env):
    """Frozen: serve starts and waits for the page; read its first line, then stop it (no page, no calls)."""
    proc = subprocess.Popen([sys.executable, "-B", "-I", str(HERE / "runner" / "run3.py"), "serve", "--port", "18765",
                             "--campaign", str(tmp_path / "s"), "--shard", "0:1", "--split", "main"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)
    try:
        line = proc.stdout.readline()
    finally:
        proc.kill()
        proc.wait()
    return _P(line)


def test_serve_entry_uses_the_experiment3_driver(tmp_path):
    env = dict(os.environ, FS_POLL_S="0.1")
    p = subprocess.run([sys.executable, "-B", "-I", str(HERE / "runner" / "run3.py"), "serve", "--port", "18765",
                        "--campaign", str(tmp_path / "s"), "--shard", "0:1", "--split", "main"],
                       capture_output=True, text=True, encoding="utf-8", env=env, timeout=60) if not exp3.config()["run_enabled"] else _serve_until_listening(tmp_path, env)
    expected = "run_enabled is false" if not exp3.config()["run_enabled"] else '"listening"'
    assert expected in p.stdout, p.stdout + p.stderr


class DownAfter(MockTransport):
    """The server becomes unreachable after `ok` attempts."""
    def __init__(self, cfg, responses, ok):
        super().__init__(cfg, responses)
        self.ok = ok

    def receive(self, expect):
        if expect == "attempt_result" and self.sent >= self.ok:
            m = self.last
            self.sent += 1
            return dict(type="attempt_result", call_id=m["call_id"], attempt=m["attempt"], status="TRANSPORT_ERROR",
                        error="TypeError: Failed to fetch", ms=3)
        return super().receive(expect)


def test_shard_stops_when_the_server_is_unreachable_and_resumes(tmp_path, reference):
    recs, eps, responses = reference
    args = ["--campaign", str(tmp_path / "c"), "--shard", "0:1", "--split", "dev", "--cases", ",".join(dev_ids())]
    t = DownAfter(exp3.config(), responses, ok=12)
    driver.CHANNEL = t
    assert driver.main(args) == 2
    assert t.finals[-1]["type"] == "fatal" and "SERVER_UNREACHABLE" in t.finals[-1]["error"]
    d = tmp_path / "c" / "shard-0-r1"
    saved = lines(d / "records.jsonl")
    assert all(r["call_status"] != "TRANSPORT_ERROR" for r in saved)           # nothing burned after the outage
    assert t.sent - 12 == exp3.config()["max_retries"] + 1                       # one call's attempts, then stop
    code, t2 = run_driver(tmp_path / "c", responses)
    assert t2.finals[-1]["type"] == "done"
    final = lines(d / "records.jsonl")
    lost = [r for r in final if r["call_status"] == "TRANSPORT_ERROR"]
    assert len(lost) == 1 and lost[0]["call_id"] == final[len(saved)]["call_id"]  # that call stays a failure, not resent
    assert run3.verify(d)["episodes_byte_identical"]
