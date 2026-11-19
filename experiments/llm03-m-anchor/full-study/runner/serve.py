"""Local service that exposes one shard's driver to the Ollama page (runner/page_loop.js).

The page on http://localhost:11434 polls this service for the next message and posts the reply. Every
chat request is therefore the page's own fetch('/api/chat'), as in the V1 loop; this process only
persists, replays and decides the order (runner/driver.py). Start it on the PC that runs Ollama:

  python -B -I runner/serve.py --campaign DIR --shard 0:1 --split dev [--port 8765] [--max-calls N] [--cases ...]

HTTP (127.0.0.1 only; CORS for http://localhost:11434 only):
  GET  /next?client=ID   -> the pending message for the page ({"type":"wait"} after a 20 s poll)
  POST /reply            -> {"token":..., ...} reply to the pending message
  GET  /status           -> progress, for the operator
Each message is handed out once. If the bound page asks for the next message while an attempt it was
given is still unanswered (the page reloaded), that attempt is answered for the driver as
TRANSPORT_LOST: it counts as a used attempt whose outcome is unknown, and it is never handed out again.
Only one page (client id) is served; another client is refused until the bound one has been silent for
60 s, at which point an outstanding attempt is likewise closed as TRANSPORT_LOST.
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import driver  # noqa: E402

ORIGIN = "http://localhost:11434"
import os
LEASE_S = float(os.environ.get("FS_LEASE_S", 60))     # shortened only in tests
POLL_S = float(os.environ.get("FS_POLL_S", 20))


class HttpChannel:
    def __init__(self):
        self.instance = uuid.uuid4().hex[:12]
        self.seq = 0
        self.lock = threading.Condition()
        self.pending = None          # message waiting to be handed out
        self.outstanding = None      # message handed out, reply not yet received
        self.replies = queue.Queue()
        self.final = None
        self.client = None
        self.last_seen = 0.0
        self.counts = dict(handed=0, replies=0, lost=0, stale=0)

    # driver side ------------------------------------------------------------------------------
    def emit(self, msg):
        with self.lock:
            if msg["type"] in ("done", "stopped", "fatal"):
                self.final = msg
                self.lock.notify_all()
                return
            self.seq += 1
            self.pending = dict(msg, token=f"{self.instance}:{self.seq}")
            self.lock.notify_all()

    def receive(self, expect):
        msg = self.replies.get()
        if msg.get("type") != expect:
            raise driver.Fatal(f"expected {expect}, got {msg.get('type')}")
        return msg

    # page side --------------------------------------------------------------------------------
    def _lose_outstanding(self, why):
        m = self.outstanding
        self.outstanding = None
        if m and m["type"] == "attempt":
            self.counts["lost"] += 1
            self.replies.put(dict(type="attempt_result", call_id=m["call_id"], attempt=m["attempt"],
                                  status="TRANSPORT_LOST", error=why))
        elif m and m["type"] == "env_request":
            self.pending = m            # nothing was sent to Ollama; hand the environment request out again

    def next(self, client):
        deadline = time.time() + POLL_S
        with self.lock:
            now = time.time()
            if self.client is None or (client != self.client and now - self.last_seen > LEASE_S):
                if self.client is not None:
                    self._lose_outstanding("page lease expired")
                self.client = client
            if client != self.client:
                return dict(type="locked", bound_client=self.client)
            self.last_seen = now
            if self.outstanding is not None:              # the bound page reloaded mid-attempt
                self._lose_outstanding("page asked for the next message before replying")
            while self.pending is None and self.final is None:
                left = deadline - time.time()
                if left <= 0:
                    return dict(type="wait")
                self.lock.wait(left)
            if self.pending is None:
                return self.final
            m, self.pending = self.pending, None
            self.outstanding = m
            self.counts["handed"] += 1
            return m

    def reply(self, msg):
        with self.lock:
            if self.outstanding is None or msg.get("token") != self.outstanding["token"]:
                self.counts["stale"] += 1
                return False
            self.outstanding = None
            self.counts["replies"] += 1
            self.last_seen = time.time()
        self.replies.put(msg)
        return True


def make_handler(ch, status):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", ORIGIN)
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", ORIGIN)
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/next":
                client = (parse_qs(u.query).get("client") or [""])[0]
                if not client:
                    return self._send(400, dict(error="client id required"))
                return self._send(200, ch.next(client))
            if u.path == "/status":
                return self._send(200, dict(status, counts=ch.counts, final=ch.final, client=ch.client,
                                            pending=bool(ch.pending), outstanding=bool(ch.outstanding)))
            self._send(404, dict(error="not found"))

        def do_POST(self):
            if urlparse(self.path).path != "/reply":
                return self._send(404, dict(error="not found"))
            n = int(self.headers.get("Content-Length") or 0)
            msg = json.loads(self.rfile.read(n).decode("utf-8"))
            ok = ch.reply(msg)
            self._send(200 if ok else 409, dict(accepted=ok))
    return H


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    port = 8765
    if "--port" in argv:
        i = argv.index("--port")
        port = int(argv[i + 1])
        del argv[i:i + 2]
    ch = HttpChannel()
    driver.CHANNEL = ch
    status = dict(instance=ch.instance, argv=argv, started=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    result = {}

    def work():
        try:
            result["code"] = driver.main(argv)
        except Exception as e:          # noqa: BLE001 - reported to the page and the operator
            ch.emit(dict(type="fatal", error=f"driver: {e!r}"))
            result["code"] = 2
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(ch, status))
    t = threading.Thread(target=work, daemon=True)
    t.start()
    srv_thread = threading.Thread(target=srv.serve_forever, daemon=True)
    srv_thread.start()
    print(json.dumps(dict(listening=f"http://127.0.0.1:{port}", **status)), flush=True)
    t.join()
    time.sleep(POLL_S + 2)               # let the page collect the final message
    srv.shutdown()
    print(json.dumps(dict(final=ch.final, counts=ch.counts)), flush=True)
    return result.get("code", 2)


if __name__ == "__main__":
    sys.exit(main())
