"""Experiment 3 descriptive report on mock data (no model calls): planned denominators, consistency."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "runner"))
sys.path.insert(0, str(HERE / "analysis"))
import run3  # noqa: E402
import report3  # noqa: E402

exp3, ledger, mock = run3.exp3, run3.ledger, run3.mock


def test_report_uses_planned_denominators_and_matches_a_direct_recount(tmp_path):
    for shard in ("0:1", "1:1"):
        si, rep = shard.split(":")
        mock.reference("dev", shard, tmp_path / f"shard-{si}-r{rep}")
    r = report3.build(tmp_path, "dev")
    rows = exp3.split("dev")
    n_trials = len(rows) * 2 * 3
    assert r["planned_episodes"] == n_trials * 5 and r["observed_episodes"] == len(rows) * 2 * 5
    assert sum(r["missing_by_sender_condition"].values()) == r["planned_episodes"] - r["observed_episodes"]
    assert r["episodes_not_in_plan"] == 0
    by_case = {c["case_id"]: c for c in rows}
    eps = report3.load(tmp_path)
    n_lt6 = sum(1 for c in rows if c["type"] == "LT6")
    for cd in r["conditions"]:
        for s in r["senders"]:
            direct = sum(ledger.lt6(e, by_case[e["case_id"]])["primary"] for e in eps
                         if e["case_type"] == "LT6" and e["condition"] == cd and e["sender"].startswith(s))
            cell = r["lt6"]["primary"][cd][s]
            assert cell["n"] == direct and cell["planned"] == n_lt6 * 3            # 3 repetitions planned, 1 observed
            assert sum(r["lt6_breakdown"][f"{cd}|{s}"].values()) == n_lt6 * 3
            assert r["lt6_breakdown"][f"{cd}|{s}"]["missing_episode"] == n_lt6 * 2
            assert r["lt6"]["primary_no_denied"][cd][s]["n"] <= cell["n"]
    for k, pairs in r["lt6_paired"].items():
        for pair, d in pairs.items():
            a, b = pair.split("-")
            for s in r["senders"]:
                x = d[s]
                assert x["both"] + x["only_first"] + x["only_second"] + x["neither"] == x["planned"]
                assert x["both"] + x["only_first"] == r["lt6"][k][a][s]["n"] and x["both"] + x["only_second"] == r["lt6"][k][b][s]["n"]
    md = report3.markdown(r)
    assert "両立（主指標）" in md and "区間・検定" in md
    json.dumps(r)
