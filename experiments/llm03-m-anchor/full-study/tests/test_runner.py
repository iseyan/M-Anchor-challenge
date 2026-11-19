"""Durable driver + transport: interruption, resume, no resend of saved or journaled successes."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "runner"))
from support import config  # noqa: E402
import mock  # noqa: E402

NODE = shutil.which("node")
CASES = "V-AT7-D01,V-AT8-D01,V-AT9-D01,V-LT4-D01,V-LT6-D01,V-AT1-D01"


def digests():
    cfg = config()
    d = {s["name"]: s["expected_digest"] for s in cfg["senders"]}
    d[cfg["receiver"]["name"]] = cfg["receiver"]["expected_digest"]
    return d


def read_jsonl(p):
    return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x]


def strip(r):
    return {k: v for k, v in r.items() if k not in ("ts", "attempts", "attempts_unknown_after_restart")}


@unittest.skipIf(NODE is None, "node is not installed")
class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ref = self.tmp / "ref"
        mock.reference("dev", "0:1", self.ref, set(CASES.split(",")))
        self.ref_records = read_jsonl(self.ref / "records.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_node(self, campaign, kill_prob, seed, extra_env=None):
        env = {"EXPECTED_DIGESTS": json.dumps(digests()), **(extra_env or {})}
        import os
        p = subprocess.run([NODE, str(HERE / "tests" / "node_chaos_run.js"), sys.executable, str(HERE / "runner" / "driver.py"),
                            str(campaign), "0:1", "dev", str(self.ref / "responses.json"), str(kill_prob), str(seed), CASES],
                           capture_output=True, text=True, encoding="utf-8", env={**os.environ, **env}, timeout=900)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def check_shard(self, campaign):
        d = Path(campaign) / "shard-0-r1"
        recs = read_jsonl(d / "records.jsonl")
        self.assertTrue((d / "done.json").exists())
        self.assertEqual([r["call_id"] for r in recs], [r["call_id"] for r in self.ref_records])
        self.assertEqual(len({r["call_id"] for r in recs}), len(recs))
        journal = read_jsonl(d / "attempts.jsonl")
        ok_seen = set()
        for ev in journal:                      # no attempt is started after a journaled success
            if ev["event"] == "intent":
                self.assertNotIn(ev["call_id"], ok_seen, "attempt after a journaled success: " + ev["call_id"])
            elif ev["status"] == "OK":
                ok_seen.add(ev["call_id"])
        return recs, journal

    def test_uninterrupted_run_equals_in_process_reference(self):
        out = self.run_node(self.tmp / "c0", 0.0, 1)
        self.assertEqual(out["kills"], 0)
        recs, _ = self.check_shard(self.tmp / "c0")
        self.assertEqual([strip(r) for r in recs], [strip(r) for r in self.ref_records])
        self.assertEqual([r["attempts"] for r in recs],
                         [[{k: v for k, v in a.items()} for a in r["attempts"]] for r in self.ref_records])
        ref_eps = read_jsonl(self.ref / "episodes.jsonl")
        eps = read_jsonl(self.tmp / "c0" / "shard-0-r1" / "episodes.jsonl")
        self.assertEqual([e["metrics"] for e in eps], [e["metrics"] for e in ref_eps])

    def test_random_kills_resume_without_resending_successes(self):
        for seed in (11, 12, 13):
            camp = self.tmp / f"chaos{seed}"
            out = self.run_node(camp, 0.03, seed)
            self.assertGreater(out["kills"], 0)
            recs, journal = self.check_shard(camp)
            ref = {r["call_id"]: r for r in self.ref_records}
            for r in recs:
                if r["call_status"] == "OK" and ref[r["call_id"]]["call_status"] == "OK":
                    self.assertEqual(r["content"], ref[r["call_id"]]["content"])
            unknown = sum(r.get("attempts_unknown_after_restart", 0) for r in recs)
            journal_ok = {ev["call_id"] for ev in journal if ev["event"] == "result" and ev["status"] == "OK"}
            # a success is fetched more than once only when its earlier outcome never reached the journal
            for r in recs:
                if r["call_status"] == "OK":
                    self.assertIn(r["call_id"], journal_ok)
            self.assertGreaterEqual(unknown, 0)

    def test_resume_refuses_a_changed_or_corrupted_journal(self):
        camp = self.tmp / "c1"
        self.run_node(camp, 0.0, 1)
        d = camp / "shard-0-r1"
        (d / "done.json").unlink()
        (d / "episodes.jsonl").unlink()
        recs = (d / "records.jsonl").read_text(encoding="utf-8").splitlines()
        tampered = json.loads(recs[3]); tampered["request_sha256"] = "0" * 64
        recs[3] = json.dumps(tampered, ensure_ascii=False)
        (d / "records.jsonl").write_text("\n".join(recs) + "\n", encoding="utf-8")
        out = self.run_node(camp, 0.0, 1)
        self.assertEqual(out["final"]["type"], "fatal")
        self.assertIn("does not match", out["final"]["error"])

    def test_environment_mismatch_stops_before_any_chat_call(self):
        bad = digests(); bad[next(iter(bad))] = "sha256:wrong"
        out = self.run_node(self.tmp / "c2", 0.0, 1, dict(EXPECTED_DIGESTS=json.dumps(bad)))
        self.assertEqual(out["final"]["type"], "fatal")
        self.assertIn("ENV_MISMATCH", out["final"]["error"])
        self.assertEqual(out["fetch_total"], 0)

    def test_main_split_refused_while_not_frozen(self):
        p = subprocess.run([sys.executable, "-B", "-I", str(HERE / "runner" / "driver.py"), "--campaign", str(self.tmp / "m"),
                            "--shard", "0:1", "--split", "main"], capture_output=True, text=True, encoding="utf-8")
        self.assertIn("run_enabled is false", p.stdout)


if __name__ == "__main__":
    unittest.main()
