"""Experiment 3 case selection (plan.md section 3). Deterministic and independent of any result.

Usage: python -B -I select_cases.py [--check]
Writes cases-selected.json next to this file (or, with --check, verifies the committed file).

Rule
  candidates: the 6 cases with the same type and construction.situation, ordered by variant
  LT1, LT4, LT5, LT6: all 30 cases each
  attacks: one stream numpy.random.default_rng(20261121); AT1 -> AT10, situation 1 -> 5;
           rng.choice(6, size=2, replace=False) picks two candidate positions
  LT2, LT3: a separate stream default_rng(20261122); LT2 -> LT3, situation 1 -> 5; same call
The output keeps every selected case's index in the experiment-2 case file (the seed rule uses it).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "full-study"))
import case_strata  # noqa: E402  (experiment-2 frozen classifier, read only)

CASES = HERE.parent / "full-study" / "cases" / "main.jsonl"
OUT = HERE / "cases-selected.json"
FULL = ["LT1", "LT4", "LT5", "LT6"]
ATTACK = [f"AT{i}" for i in range(1, 11)]
SAMPLED_LEGIT = ["LT2", "LT3"]
SEED_ATTACK, SEED_LEGIT = 20261121, 20261122
EXPECTED_CASES_SHA256 = "9e9a9880b88b01239bdabeae4af06a0f8f266cd21bb8d34cfb49e01be2b1d017"


def load():
    raw = CASES.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_CASES_SHA256:
        raise SystemExit(f"case file hash {digest} != frozen experiment-2 hash {EXPECTED_CASES_SHA256}")
    cases = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    for i, c in enumerate(cases):
        c["_index"] = i
    return cases, digest


def candidates(cases, ctype, situation):
    cs = sorted((c for c in cases if c["type"] == ctype and c["construction"]["situation"] == situation),
                key=lambda c: c["variant"])
    if len(cs) != 6:
        raise SystemExit(f"{ctype} situation {situation}: {len(cs)} candidates, expected 6")
    return cs


def sample(cases, types, seed):
    rng = np.random.default_rng(seed)
    picked = []
    for t in types:
        for s in range(1, 6):
            cs = candidates(cases, t, s)
            pos = rng.choice(6, size=2, replace=False)
            for p in sorted(int(x) for x in pos):
                picked.append(dict(cs[p], _position=p))
    return picked


def select():
    cases, digest = load()
    full = [dict(c, _position=None) for c in cases if c["type"] in FULL]
    attacks = sample(cases, ATTACK, SEED_ATTACK)
    sampled = sample(cases, SAMPLED_LEGIT, SEED_LEGIT)
    chosen = sorted(full + sampled + attacks, key=lambda c: c["_index"])
    rows = [dict(case_id=c["case_id"], case_index=c["_index"], type=c["type"], label=c["label"],
                 situation=c["construction"]["situation"], variant=c["variant"],
                 rule="all" if c["_position"] is None else "sampled", candidate_position=c["_position"],
                 stratum=case_strata.classify(c)["stratum"] if c["label"] == "attack" else None)
            for c in chosen]
    strata = {}
    for r in rows:
        if r["stratum"]:
            strata[r["stratum"]] = strata.get(r["stratum"], 0) + 1
    counts = {}
    for r in rows:
        counts[r["type"]] = counts.get(r["type"], 0) + 1
    return dict(
        plan="exp3-permit/plan.md section 3",
        source=dict(path="full-study/cases/main.jsonl", sha256=digest, cases=len(cases)),
        numpy_version=np.__version__,
        seeds=dict(attack=SEED_ATTACK, legit_sampled=SEED_LEGIT),
        call="rng.choice(6, size=2, replace=False) per (type, situation); candidates ordered by variant",
        total=len(rows), attack_strata=strata, by_type=dict(sorted(counts.items(), key=lambda kv: (kv[0][:2], int(kv[0][2:])))),
        cases=rows)


def main():
    out = select()
    text = json.dumps(out, ensure_ascii=False, indent=1) + "\n"
    if "--check" in sys.argv:
        old = json.loads(OUT.read_text(encoding="utf-8"))
        keep = lambda d: {k: v for k, v in d.items() if k != "numpy_version"}  # noqa: E731
        if keep(old) != keep(out):
            raise SystemExit("cases-selected.json differs from a fresh selection")
        print(f"OK: {out['total']} cases match cases-selected.json (numpy {np.__version__}; file made with {old['numpy_version']})")
        return
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.name}: {out['total']} cases, {out['by_type']}")


if __name__ == "__main__":
    main()
