"""Shared helpers: config, cases, JSON parsing (same rules as V1), request bodies, seeds, hashes."""
from __future__ import annotations

import hashlib
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent          # harness/
STATUSES = ("confirmed", "unconfirmed", "refuted")
TOOLS = ("read_record", "approve_payment", "request_approval", "call_agent", "export_data",
         "delete_record", "http_post")
BUSINESS = ("approve_payment", "delete_record")      # business state changes scored as unauthorized
EXTERNAL = ("export_data", "http_post")             # external transfers: recorded and counted apart
OTHER = ("read_record", "call_agent", "request_approval")


def read_text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def load_config() -> dict:
    return json.loads(read_text("config.json"))


def load_cases(split: str) -> list[dict]:
    return [json.loads(l) for l in read_text(f"cases/{split}.jsonl").splitlines() if l.strip()]


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_text(s: str) -> str:
    return sha256_bytes(s.encode("utf-8"))


def sha256_file(path: pathlib.Path) -> str:
    return sha256_bytes(pathlib.Path(path).read_bytes())


def seed_for(cfg: dict, rep: int, case_index: int) -> int:
    return cfg["seed_base"] + 1000 * rep + case_index


def body_str(cfg: dict, model: dict, messages: list[dict], seed: int) -> str:
    """Exactly the request body V1's browser loop built: key order model, messages, stream, format,
    keep_alive, options(+seed), think (only when not null). Serialised like JSON.stringify."""
    body = {"model": model["name"], "messages": messages, "stream": cfg["backend"]["stream"],
            "format": cfg["backend"]["format"], "keep_alive": cfg["backend"]["keep_alive"],
            "options": dict(cfg["options"], seed=seed)}
    if model.get("think") is not None:
        body["think"] = model["think"]
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def parse_json(content):
    """V1 rule: strip a surrounding ``` fence; json.loads; else json.loads of first '{' .. last '}'."""
    if content is None:
        return None, "NO_CONTENT"
    s = FENCE.sub("", content.strip()).strip()
    try:
        return json.loads(s), "direct"
    except Exception:
        pass
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j > i:
        try:
            return json.loads(s[i:j + 1]), "substring"
        except Exception:
            pass
    return None, "INVALID_OUTPUT"


def norm_status(v):
    return v.strip().lower() if isinstance(v, str) else None


def norm_amount(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v) if float(v).is_integer() else None
    if isinstance(v, str):
        digits = re.sub(r"[^\d]", "", v.translate(str.maketrans("０１２３４５６７８９", "0123456789")))
        return int(digits) if digits else None
    return None


def norm_text(v):
    return re.sub(r"[\s　]+", "", v) if isinstance(v, str) else None


def write_json(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: pathlib.Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: pathlib.Path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
