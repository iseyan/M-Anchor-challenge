"""Load the unchanged initial-study library; no model calls or file writes on import."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parent
LEGACY = ROOT.parent / "harness"
sys.path.insert(0, str(LEGACY))
from lib import anchor, common, gate, pipeline, prompts, score  # noqa: E402
from lib.world import World  # noqa: E402


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def config():
    c = common.load_config()
    c.update(read(ROOT / "config.json"))
    return c


def cases(split):
    return [json.loads(s) for s in (ROOT / "cases" / (split + ".jsonl")).read_text(encoding="utf-8").splitlines() if s]


def sources():
    """Everything that can change requests, execution, scoring or analysis; exclude generated run files."""
    out = {}
    for parent, prefix in ((LEGACY, "harness"), (ROOT, "full-study")):
        for p in sorted(parent.rglob("*")):
            rel = p.relative_to(parent)
            if not p.is_file() or any(x in rel.parts for x in ("__pycache__", "runs", "results", "checks")):
                continue
            if p.suffix not in (".py", ".js", ".html", ".txt", ".json", ".jsonl", ".md", ".R"):
                continue
            if rel.as_posix() == "freeze.json":
                continue
            out[prefix + "/" + rel.as_posix()] = sha(p)
    return out
