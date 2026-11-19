"""Local service + page loop (V1 page transport): interruption of the service and page reloads."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "runner"))
sys.path.insert(0, str(HERE / "tests"))
import mock  # noqa: E402
from test_runner import CASES, digests, read_jsonl, strip  # noqa: E402

NODE = shutil.which("node")


@unittest.skipIf(NODE is None, "node is not installed")
class ServeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        mock.reference("dev", "0:1", self.tmp / "ref", set(CASES.split(",")))
        self.ref = read_jsonl(self.tmp / "ref" / "records.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sim(self, name, kill_prob, seed, port):
        p = subprocess.run([NODE, str(HERE / "tests" / "node_page_sim.js"), sys.executable, str(HERE / "runner" / "serve.py"),
                            str(self.tmp / name), "0:1", "dev", str(self.tmp / "ref" / "responses.json"), str(port), str(seed),
                            str(kill_prob), CASES], capture_output=True, text=True, encoding="utf-8", timeout=1500,
                           env={**os.environ, "EXPECTED_DIGESTS": json.dumps(digests())})
        self.assertEqual(p.returncode, 0, p.stderr[-3000:])
        out = json.loads(p.stdout)
        d = self.tmp / name / "shard-0-r1"
        recs = read_jsonl(d / "records.jsonl")
        self.assertTrue((d / "done.json").exists(), out)
        self.assertEqual([r["call_id"] for r in recs], [r["call_id"] for r in self.ref])
        ok = set()
        for ev in read_jsonl(d / "attempts.jsonl"):
            if ev["event"] == "intent":
                self.assertNotIn(ev["call_id"], ok)
            elif ev["status"] == "OK":
                ok.add(ev["call_id"])
        return out, recs

    def test_page_transport_without_interruption_equals_reference(self):
        out, recs = self.sim("c0", 0.0, 1, 18765)
        self.assertEqual(out["final"]["type"], "done")
        self.assertEqual([strip(r) for r in recs], [strip(r) for r in self.ref])

    def test_service_kills_and_page_reloads(self):
        out, recs = self.sim("c1", 0.06, 7, 18766)
        self.assertGreater(out["restarts"] + out["reloads"], 0)
        ref = {r["call_id"]: r for r in self.ref}
        for r in recs:
            if r["call_status"] == "OK" and ref[r["call_id"]]["call_status"] == "OK":
                self.assertEqual(r["content"], ref[r["call_id"]]["content"])



class ChannelTests(unittest.TestCase):
    """Hand-out rules of the local service without HTTP."""
    def setUp(self):
        import importlib
        os.environ["FS_LEASE_S"] = "0.2"
        os.environ["FS_POLL_S"] = "0.2"
        import serve
        self.serve = importlib.reload(serve)
        self.ch = self.serve.HttpChannel()

    def attempt(self, n):
        self.ch.emit(dict(type="attempt", call_id=f"S2|x|C4|p0|s1", attempt=n, request_sha256="a" * 64, body="{}", timeout_ms=1))

    def test_reload_mid_attempt_is_answered_as_transport_lost_and_never_handed_out_again(self):
        self.attempt(0)
        m = self.ch.next("page1")
        self.assertEqual(m["type"], "attempt")
        w = self.ch.next("page1")                # the same page asks again without replying (reloaded)
        self.assertEqual(w["type"], "wait")      # nothing else pending; the attempt is not re-delivered
        r = self.ch.replies.get_nowait()
        self.assertEqual((r["status"], r["attempt"]), ("TRANSPORT_LOST", 0))
        self.assertFalse(self.ch.reply(dict(m, type="attempt_result", status="OK")))   # late reply is stale

    def test_second_page_is_refused_until_the_lease_expires(self):
        import time
        self.attempt(0)
        m = self.ch.next("page1")
        self.assertEqual(self.ch.next("page2")["type"], "locked")
        time.sleep(0.3)
        self.assertEqual(self.ch.next("page2")["type"], "wait")
        self.assertEqual(self.ch.replies.get_nowait()["status"], "TRANSPORT_LOST")
        self.assertFalse(self.ch.reply(dict(m, type="attempt_result", status="OK")))

    def test_environment_request_is_handed_out_again_after_a_reload(self):
        self.ch.emit(dict(type="env_request"))
        self.assertEqual(self.ch.next("page1")["type"], "env_request")
        self.assertEqual(self.ch.next("page1")["type"], "env_request")      # nothing was sent to Ollama



@unittest.skipIf(NODE is None, "node is not installed")
class PersistTests(unittest.TestCase):
    """Two shards in a row with one page loop (persist: true), as on the PC with a PowerShell loop."""
    def test_two_shards_one_page_loop(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            resp = {}
            refs = {}
            for sh in ("0:1", "0:2"):
                mock.reference("dev", sh, tmp / ("ref" + sh[-1]), set(CASES.split(",")))
                resp.update(json.loads((tmp / ("ref" + sh[-1]) / "responses.json").read_text(encoding="utf-8")))
                refs[sh] = read_jsonl(tmp / ("ref" + sh[-1]) / "records.jsonl")
            (tmp / "responses.json").write_text(json.dumps(resp), encoding="utf-8")
            p = subprocess.run([NODE, str(HERE / "tests" / "node_page_sim.js"), sys.executable, str(HERE / "runner" / "serve.py"),
                                str(tmp / "camp"), "0:1,0:2", "dev", str(tmp / "responses.json"), "18771", "3", "0", CASES],
                               capture_output=True, text=True, encoding="utf-8", timeout=1500,
                               env={**os.environ, "EXPECTED_DIGESTS": json.dumps(digests())})
            self.assertEqual(p.returncode, 0, p.stderr[-3000:])
            out = json.loads(p.stdout)
            self.assertEqual([f["type"] for f in out["finals"]], ["done", "done"], out)
            for sh, d in (("0:1", "shard-0-r1"), ("0:2", "shard-0-r2")):
                recs = read_jsonl(tmp / "camp" / d / "records.jsonl")
                self.assertEqual([strip(r) for r in recs], [strip(r) for r in refs[sh]])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
