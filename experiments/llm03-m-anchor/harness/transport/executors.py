"""Python-side senders. Each takes one call spec and returns a record in the shape the browser loop writes.

  http  : POSTs the frozen body string to Ollama's /api/chat (urllib, no dependencies). `preflight_http`
          checks the Ollama version and the model digests before any inference call.
  mock  : deterministic stand-ins for tests and the pipeline check. Their behaviour says nothing about
          real models. "qwen" senders act credulously, "gemma" senders act carefully.
The browser transport (V1's) is transport/browser_loop.js.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from lib.common import sha256_text


# --------------------------------------------------------------------------------------------- http
def _get(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def preflight_http(cfg: dict, base: str) -> dict:
    """Ollama version and the digests of every model the run uses, compared with config.json."""
    version = _get(base + "/api/version").get("version")
    tags = {m["name"]: m.get("digest") for m in _get(base + "/api/tags").get("models", [])}
    return env_check(cfg, {"version": {"version": version}, "tags": {"models": [{"name": k, "digest": v}
                                                                             for k, v in tags.items()]}})


def env_check(cfg: dict, env: dict) -> dict:
    want = {s["name"]: s["expected_digest"] for s in cfg["senders"]}
    want[cfg["receiver"]["name"]] = cfg["receiver"]["expected_digest"]
    tags = {m["name"]: m.get("digest") for m in (env.get("tags") or {}).get("models", [])}
    v = (env.get("version") or {}).get("version")
    res = {"ollama_version": v, "ollama_version_matches": v == cfg["expected_ollama_version"],
           "models": {n: {"expected": w, "found": tags.get(n), "matches": tags.get(n) == w} for n, w in want.items()}}
    res["all_match"] = res["ollama_version_matches"] and all(m["matches"] for m in res["models"].values())
    return res


def http_sender(cfg: dict, endpoint: str):
    def send(c: dict) -> dict:
        attempts, result = [], None
        ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        for a in range(cfg["max_retries"] + 1):
            t0 = time.perf_counter()
            req = urllib.request.Request(endpoint, data=c["body"].encode("utf-8"), method="POST",
                                         headers={"Content-Type": "application/json"})
            ms = lambda: round((time.perf_counter() - t0) * 1000)  # noqa: E731
            try:
                with urllib.request.urlopen(req, timeout=cfg["timeout_ms"] / 1000) as r:
                    j = json.loads(r.read().decode("utf-8"))
                if j.get("error"):
                    attempts.append({"attempt": a, "status": "HTTP_ERROR", "error": str(j["error"])[:500], "ms": ms()})
                    continue
                attempts.append({"attempt": a, "status": "OK", "ms": ms()})
                msg = j.get("message") or {}
                result = {"call_status": "OK", "content": msg.get("content"), "thinking": msg.get("thinking"),
                          "done_reason": j.get("done_reason"), "eval_count": j.get("eval_count"),
                          "prompt_eval_count": j.get("prompt_eval_count"), "total_duration_ns": j.get("total_duration"),
                          "load_duration_ns": j.get("load_duration"), "response_model": j.get("model")}
                break
            except urllib.error.HTTPError as e:
                attempts.append({"attempt": a, "status": "HTTP_ERROR", "http": e.code, "ms": ms()})
            except TimeoutError:
                attempts.append({"attempt": a, "status": "TIMEOUT", "ms": ms()})
            except (urllib.error.URLError, OSError, ValueError) as e:
                status = "TIMEOUT" if "timed out" in str(e) else "TRANSPORT_ERROR"
                attempts.append({"attempt": a, "status": status, "error": str(e)[:300], "ms": ms()})
        if result is None:
            result = {"call_status": attempts[-1]["status"], "content": None}
        return dict(result, call_id=c["call_id"], ts=ts, request_sha256=sha256_text(c["body"]), attempts=attempts)
    return send


# --------------------------------------------------------------------------------------------- mock
DOC = re.compile(r"^(D\d+)〔(.+?)〕: (.*)$", re.M)
CLAIM = re.compile(r"^- (\w+): (.*)$", re.M)
ANCHOR = re.compile(r"^- (\w+): (confirmed|unconfirmed|refuted)（", re.M)
KIND_FOR = {"approver_authorized": "権限台帳の記録", "request_recorded": "申請システムの記録"}


def _kind_for(key: str):
    return None if key == "cause_identified" else KIND_FOR.get(key, "承認台帳の記録")


def _stage1(user: str, careful: bool) -> dict:
    docs = DOC.findall(user)
    keys = [k for k, _ in CLAIM.findall(user.split("【評価する主張】", 1)[1].split("【出力】", 1)[0])]
    claims = {}
    for k in keys:
        if careful:
            ev = [d for d, kd, _ in docs if kd == _kind_for(k)]
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
    amount, payee = re.search(r"金額 ([\d,]+)円", req), re.search(r"支払先 ([^／]+)", req)
    apr = re.search(r"APR-\d+", user1)
    approval_keys = [k for k in statuses if k not in ("cause_identified", "request_recorded")]
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
    return {"actions": acts[:3], "handoff": {"claims": statuses}}


def _receiver(user: str) -> dict:
    part = user.split("【前工程からの引き継ぎ（各主張の状態）】", 1)[1].split("【主張キーの意味】", 1)[0]
    stated = dict(re.findall(r"^- (\w+): (\S+)$", part, re.M))
    keys = [k for k in stated if k not in ("cause_identified", "request_recorded")]
    ok = keys and all(stated[k] == "confirmed" for k in keys)
    return {"decision": "approve" if ok else "hold", "reason": "模擬出力"}


def mock_content(body: dict, call_id: str) -> str:
    careful = "gemma" in body["model"]
    msgs = body["messages"]
    if call_id.startswith("S1|"):
        content = _stage1(msgs[1]["content"], careful)
    elif call_id.startswith("S2|"):
        content = _stage2(msgs, careful)
    else:
        content = _receiver(msgs[1]["content"])
    return json.dumps(content, ensure_ascii=False)


def mock_sender(cfg: dict, overrides: dict | None = None):
    """overrides: call_id -> partial record, to inject timeouts, truncation or bad output in tests."""
    def send(c: dict) -> dict:
        body = json.loads(c["body"])
        rec = {"call_id": c["call_id"], "call_status": "OK", "request_sha256": sha256_text(c["body"]),
               "ts": "mock", "attempts": [{"attempt": 0, "status": "OK", "ms": 1}],
               "content": mock_content(body, c["call_id"]), "done_reason": "stop", "eval_count": 100,
               "prompt_eval_count": 500, "response_model": body["model"]}
        if overrides and c["call_id"] in overrides:
            rec.update(overrides[c["call_id"]])
        return rec
    return send
