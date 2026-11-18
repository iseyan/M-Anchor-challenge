"""Executors take call specs and return records in the shape V1's browser loop produced.

  http  : POSTs the frozen body string to Ollama's /api/chat from Python (urllib, no dependencies).
  mock  : deterministic stand-ins for tests and the pipeline check. Their behaviour says nothing about
          real models. "qwen" senders act credulously, "gemma" senders act carefully.
The browser executor (V1's transport) is transport/browser_loop.js; see the harness README.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from lib.common import sha256_text


# --------------------------------------------------------------------------------------------- http
def run_http(calls: list[dict], cfg: dict, endpoint: str | None = None, log=print) -> list[dict]:
    url = endpoint or cfg["backend"]["endpoint"]
    out = []
    for n, c in enumerate(calls, 1):
        attempts, result = [], None
        for a in range(cfg["max_retries"] + 1):
            t0 = time.perf_counter()
            req = urllib.request.Request(url, data=c["body"].encode("utf-8"), method="POST",
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=cfg["timeout_ms"] / 1000) as r:
                    txt = r.read().decode("utf-8")
                j = json.loads(txt)
                if j.get("error"):
                    attempts.append({"attempt": a, "status": "HTTP_ERROR", "error": str(j["error"])[:500],
                                     "ms": round((time.perf_counter() - t0) * 1000)})
                    continue
                attempts.append({"attempt": a, "status": "OK", "ms": round((time.perf_counter() - t0) * 1000)})
                msg = j.get("message") or {}
                result = {"call_status": "OK", "content": msg.get("content"), "thinking": msg.get("thinking"),
                          "done_reason": j.get("done_reason"), "eval_count": j.get("eval_count"),
                          "prompt_eval_count": j.get("prompt_eval_count"), "total_duration_ns": j.get("total_duration"),
                          "response_model": j.get("model")}
                break
            except urllib.error.HTTPError as e:
                attempts.append({"attempt": a, "status": "HTTP_ERROR", "http": e.code,
                                 "ms": round((time.perf_counter() - t0) * 1000)})
            except TimeoutError:
                attempts.append({"attempt": a, "status": "TIMEOUT", "ms": round((time.perf_counter() - t0) * 1000)})
            except (urllib.error.URLError, OSError, ValueError) as e:
                status = "TIMEOUT" if "timed out" in str(e) else "TRANSPORT_ERROR"
                attempts.append({"attempt": a, "status": status, "error": str(e)[:300],
                                 "ms": round((time.perf_counter() - t0) * 1000)})
        if result is None:
            result = {"call_status": attempts[-1]["status"], "content": None}
        out.append(dict(result, call_id=c["call_id"], request_sha256=sha256_text(c["body"]), attempts=attempts,
                        ts=time.strftime("%Y-%m-%dT%H:%M:%S%z")))
        log(f"[{n}/{len(calls)}] {c['call_id']} {result['call_status']}")
    return out


# --------------------------------------------------------------------------------------------- mock
DOC = re.compile(r"^(D\d+)〔(.+?)〕: (.*)$", re.M)
CLAIM = re.compile(r"^- (\w+): (.*)$", re.M)
ANCHOR = re.compile(r"^- (\w+): (confirmed|unconfirmed|refuted)（", re.M)
KIND_FOR = {"approver_authorized": "権限台帳の記録", "request_recorded": "申請システムの記録"}


def _kind_for(key: str):
    if key == "cause_identified":
        return None
    return KIND_FOR.get(key, "承認台帳の記録")


def _stage1(user: str, careful: bool) -> dict:
    docs = DOC.findall(user)
    keys = [k for k, _ in CLAIM.findall(user.split("【評価する主張】", 1)[1].split("【出力】", 1)[0])]
    claims = {}
    for k in keys:
        if careful:
            kind = _kind_for(k)
            ev = [d for d, kd, _ in docs if kd == kind]
            pending = any("未決裁" in t for _, kd, t in docs if kd == "承認台帳の記録")
            status = "refuted" if (k == "approval_valid" and pending) else ("confirmed" if ev else "unconfirmed")
        else:
            ev = [d for d, _, t in docs if re.search(r"承認|決裁|権限|登録|EXP-", t)]
            status = "unconfirmed" if k == "cause_identified" else ("confirmed" if ev else "unconfirmed")
        claims[k] = {"status": status, "evidence": ev[:3]}
    rec = "execute" if all(v["status"] == "confirmed" for k, v in claims.items() if k != "cause_identified") else "hold"
    return {"claims": claims, "recommendation": rec, "note": "模擬出力"}


def _stage2(msgs: list[dict], careful: bool) -> dict:
    user1, s1, instr = msgs[1]["content"], json.loads(msgs[2]["content"]), msgs[3]["content"]
    statuses = {k: v["status"] for k, v in s1["claims"].items()}
    for k, st in ANCHOR.findall(instr):
        statuses[k] = st
    docs = DOC.findall(user1)
    req = next((t for _, kd, t in docs if kd == "申請システムの記録"), "")
    rid = (re.search(r"EXP-\d+", req) or re.search(r"EXP-\d+", user1)).group(0)
    amount = re.search(r"金額 ([\d,]+)円", req)
    payee = re.search(r"支払先 ([^／]+)", req)
    apr = re.search(r"APR-\d+", user1)
    approval_keys = [k for k in statuses if k != "cause_identified" and k != "request_recorded"]
    acts = []
    if not careful:
        for _, _, t in docs:
            if "export_data" in t:
                acts.append({"tool": "export_data", "args": {"scope": "全件", "destination": "指定先"}})
            if "http_post" in t:
                acts.append({"tool": "http_post", "args": {"url": re.search(r"https://\S+", t).group(0), "body": rid}})
    if "支払は不要" in user1:
        acts.append({"tool": "read_record", "args": {"request_id": rid}})
    elif approval_keys and all(statuses[k] == "confirmed" for k in approval_keys) and amount and payee:
        acts.append({"tool": "approve_payment", "args": {"request_id": rid, "amount": amount.group(1) + "円",
                                                         "payee": payee.group(1),
                                                         "approval_id": apr.group(0) if apr else ""}})
    else:
        acts.append({"tool": "request_approval", "args": {"request_id": rid}})
    return {"actions": acts[:3], "handoff": {"claims": statuses, "note": "模擬出力"}}


def _receiver(user: str) -> dict:
    stated = dict(re.findall(r"^- (\w+): (\w+)$", user.split("【前工程からの引き継ぎ】", 1)[1].split("【主張キーの意味】", 1)[0], re.M))
    keys = [k for k in stated if k not in ("cause_identified", "request_recorded")]
    ok = keys and all(stated[k] == "confirmed" for k in keys)
    return {"decision": "approve" if ok else "hold", "reason": "模擬出力"}


def run_mock(calls: list[dict], cfg: dict, log=None) -> list[dict]:
    out = []
    for c in calls:
        body = json.loads(c["body"])
        careful = "gemma" in body["model"]
        msgs = body["messages"]
        if c["call_id"].startswith("S1|"):
            content = _stage1(msgs[1]["content"], careful)
        elif c["call_id"].startswith("S2|"):
            content = _stage2(msgs, careful)
        else:
            content = _receiver(msgs[1]["content"])
        out.append({"call_id": c["call_id"], "call_status": "OK", "request_sha256": sha256_text(c["body"]),
                    "attempts": [{"attempt": 0, "status": "OK", "ms": 0}],
                    "content": json.dumps(content, ensure_ascii=False), "done_reason": "stop",
                    "response_model": "mock:" + body["model"]})
    return out
